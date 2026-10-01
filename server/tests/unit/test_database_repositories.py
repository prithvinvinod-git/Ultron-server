"""Repository layer tests (T015).

Runs against SQLite in-memory via ``session_scope``, the same unit of work
production uses, so these exercise the real transaction behaviour rather than a
hand-rolled session. What cannot be tested here is stated in the module and
deferred to the integration tests: ``SELECT ... FOR UPDATE`` is ignored by
SQLite, and JSONB containment needs a dialect-specific query.

The tests are weighted towards the things that would be *silently* wrong: an
unbounded read, a namespace that leaks across scopes, a permission decision
recorded without a level, a repository that commits behind the caller's back.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    CapabilityNotImplementedError,
    ConflictError,
    DatabaseError,
    InvalidInputError,
    NotFoundError,
)
from app.database.models import (
    Agent,
    AgentStatus,
    AuditOutcome,
    Conversation,
    ConversationStatus,
    Device,
    DeviceStatus,
    MessageRole,
    ModelUsageStatus,
    PermissionLevel,
    Project,
    Schedule,
    ScheduleKind,
    ScheduleStatus,
    Session,
    Task,
    TaskStatus,
    ToolExecution,
    ToolExecutionStatus,
    User,
    VerificationOutcome,
)
from app.database.repositories import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    AgentLogRepository,
    AgentRepository,
    AuditLogRepository,
    ConversationRepository,
    DeviceEventRepository,
    DeviceRepository,
    EventRepository,
    MemoryRepository,
    MessageRepository,
    ModelUsageRepository,
    ProjectRepository,
    ScheduleRepository,
    SessionRepository,
    TaskRepository,
    TaskStepRepository,
    ToolExecutionRepository,
    UserRepository,
    apply_limit,
    apply_offset,
    translate,
)
from app.database.session import Base, create_engine, dispose

pytestmark = pytest.mark.unit

SQLITE_URL = "sqlite+aiosqlite://"


@pytest.fixture
async def engine() -> AsyncIterator[object]:
    """A private in-memory database with the production schema.

    ``Base.metadata`` -- the real one, not a copy. A test schema that differs
    from the migrated schema can pass every test here and still fail against
    PostgreSQL, which is the failure mode a hand-built table list invites.
    """
    from app.config import DatabaseSettings

    created = create_engine(DatabaseSettings(url=SQLITE_URL))
    async with created.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield created
    finally:
        await dispose(created)


@pytest.fixture
async def db(engine: object) -> AsyncIterator[AsyncSession]:
    """A session bound to the test database, rolled back after each test.

    The schema is created once per engine and reused, so each test needs its rows
    gone afterwards.

    ``join_transaction_mode="create_savepoint"`` is what makes that work while
    still letting the tests call ``commit()``. Without it the session's commit
    tears down the outer transaction, so the rollback in teardown has nothing
    left to roll back and every row leaks into the next test. With a savepoint,
    a test's ``commit()`` ends only that savepoint, and the outer transaction --
    which is what the teardown rolls back -- stays open for the whole test.
    """
    connection = await engine.connect()  # type: ignore[attr-defined]
    transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


# --------------------------------------------------------------------------
# Base behaviour
# --------------------------------------------------------------------------


class TestBaseHelpers:
    def test_a_missing_limit_gets_the_default(self) -> None:
        from sqlalchemy import select

        from app.database.models import User

        statement = apply_limit(select(User))
        assert statement._limit == DEFAULT_LIMIT

    @pytest.mark.parametrize("given", [0, -1, None])
    def test_a_zero_or_negative_limit_means_the_default(self, given: int | None) -> None:
        from sqlalchemy import select

        from app.database.models import User

        statement = apply_limit(select(User), given)
        assert statement._limit == DEFAULT_LIMIT

    def test_a_limit_over_the_maximum_is_refused(self) -> None:
        from sqlalchemy import select

        from app.database.models import User

        with pytest.raises(ValueError, match="exceeds the maximum"):
            apply_limit(select(User), MAX_LIMIT + 1)

    def test_the_limit_survives_untouched(self) -> None:
        from sqlalchemy import select

        from app.database.models import User

        assert apply_limit(select(User), 7)._limit == 7

    def test_a_negative_offset_is_ignored_rather_than_applied(self) -> None:
        from sqlalchemy import select

        from app.database.models import User

        assert apply_offset(select(User), -5)._offset is None
        assert apply_offset(select(User), None)._offset is None
        assert apply_offset(select(User), 5)._offset == 5

    def test_apply_limit_preserves_the_concrete_statement_type(self) -> None:
        """The helper must return the same query type, not a widened one.

        Checked at runtime rather than left to the type checker: the helper
        reaches for ``cast`` internally, and a ``cast`` that is wrong at runtime
        still type-checks cleanly, so only an actual query can confirm the
        statement came back intact.
        """
        from sqlalchemy import Select, select

        from app.database.models import User

        statement = apply_limit(select(User.id))

        assert isinstance(statement, Select)
        assert list(statement.selected_columns.keys()) == ["id"]
        assert statement._limit_clause is not None, "a limit must actually be applied"

    def test_apply_offset_preserves_the_concrete_statement_type(self) -> None:
        from sqlalchemy import Select, select

        from app.database.models import User

        statement = apply_offset(select(User.id), 10)

        assert isinstance(statement, Select)
        assert list(statement.selected_columns.keys()) == ["id"]
        assert statement._offset is not None, "an offset must actually be applied"


class TestTranslate:
    def test_an_integrity_failure_becomes_a_non_retryable_conflict(self) -> None:
        from sqlalchemy.exc import IntegrityError

        error = IntegrityError("insert", {}, Exception("duplicate key"))
        translated = translate(error, "users.add")

        assert isinstance(translated, ConflictError)
        assert not isinstance(translated, DatabaseError)

    def test_a_connectivity_failure_becomes_a_retryable_database_error(self) -> None:
        from sqlalchemy.exc import OperationalError

        error = OperationalError("select", {}, Exception("server closed"))
        translated = translate(error, "users.get")

        assert isinstance(translated, DatabaseError)

    def test_the_driver_message_is_never_copied(self) -> None:
        """A driver message can embed the DSN, so it must not survive."""
        from sqlalchemy.exc import OperationalError

        secret = "postgresql://ultron:hunter2@db:5432/ultron"
        error = OperationalError("select", {}, Exception(f"could not connect: {secret}"))
        translated = translate(error, "users.get")

        assert isinstance(translated, DatabaseError)
        assert secret not in str(translated)
        assert secret not in str(translated.details)


# --------------------------------------------------------------------------
# Generic repository behaviour
# --------------------------------------------------------------------------


class TestGenericRepository:
    async def test_get_returns_none_rather_than_raising(self, db: AsyncSession) -> None:
        assert await UserRepository(db).get(uuid.uuid4()) is None

    async def test_get_required_names_the_missing_row(self, db: AsyncSession) -> None:
        missing = uuid.uuid4()
        with pytest.raises(NotFoundError) as caught:
            await UserRepository(db).get_required(missing)

        assert str(missing) in str(caught.value)

    async def test_a_malformed_id_is_a_value_error_not_a_not_found(
        self, db: AsyncSession
    ) -> None:
        """A bad id is the caller's mistake, so it must not read as 'no such row'."""
        with pytest.raises(ValueError, match="not a valid identifier"):
            await UserRepository(db).get_required("not-a-uuid")

    async def test_a_string_uuid_is_accepted(self, db: AsyncSession) -> None:
        user = User(username="alice", email="alice@example.com")
        await UserRepository(db).add(user)
        await db.commit()

        found = await UserRepository(db).get_required(str(user.id))
        assert found.username == "alice"

    async def test_add_flushes_so_later_reads_see_the_row(self, db: AsyncSession) -> None:
        users = UserRepository(db)
        user = await users.add(User(username="bob"))
        assert await users.exists(user.id)

    async def test_delete_reports_whether_anything_went(self, db: AsyncSession) -> None:
        users = UserRepository(db)
        user = await users.add(User(username="carol"))

        assert await users.delete(user.id) is True
        assert await users.delete(uuid.uuid4()) is False

    async def test_count_reflects_the_table(self, db: AsyncSession) -> None:
        users = UserRepository(db)
        assert await users.count() == 0
        await users.add(User(username="dave"))
        assert await users.count() == 1

    async def test_a_duplicate_becomes_a_conflict_not_a_driver_error(
        self, db: AsyncSession
    ) -> None:
        users = UserRepository(db)
        await users.add(User(username="erin"))

        with pytest.raises(ConflictError):
            await users.add(User(username="erin"))


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


class TestUserRepository:
    async def test_lookup_by_username(self, db: AsyncSession) -> None:
        users = UserRepository(db)
        await users.add(User(username="frank", email="f@example.com"))

        assert (await users.get_by_username("frank")) is not None
        assert await users.get_by_username("nobody") is None

    async def test_api_key_lookup_needs_an_active_service_account(self, db: AsyncSession) -> None:
        users = UserRepository(db)
        await users.add(
            User(
                username="svc",
                is_service_account=True,
                api_key_hash="hash-1",
            )
        )
        await users.add(
            User(
                username="svc-off",
                is_service_account=True,
                is_active=False,
                api_key_hash="hash-2",
            )
        )
        await users.add(User(username="human", api_key_hash="hash-3"))
        await db.commit()

        assert (await users.get_by_api_key_hash("hash-1")) is not None
        assert await users.get_by_api_key_hash("hash-2") is None, "inactive must not match"
        assert await users.get_by_api_key_hash("hash-3") is None, "a human has no API key"
        assert await users.get_by_api_key_hash("nope") is None

    async def test_active_listing_excludes_service_accounts(self, db: AsyncSession) -> None:
        users = UserRepository(db)
        await users.add(User(username="human-1"))
        await users.add(User(username="human-2", is_active=False))
        await users.add(User(username="svc-1", is_service_account=True))
        await db.commit()

        assert [u.username for u in await users.list_active()] == ["human-1"]


class TestSessionRepository:
    async def test_lookup_is_by_hash(self, db: AsyncSession) -> None:
        # The user is added first: ids are minted at flush, so `user.id` is only
        # populated once the parent row has actually been written.
        user = await UserRepository(db).add(User(username="grace"))
        session = Session(
            user_id=user.id,
            token_hash="tok-hash",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        await SessionRepository(db).add(session)
        await db.commit()

        assert (await SessionRepository(db).get_by_token_hash("tok-hash")) is not None
        assert await SessionRepository(db).get_by_token_hash("other") is None

    async def test_revoke_all_records_a_reason_and_counts(self, db: AsyncSession) -> None:
        user = User(username="heidi")
        await UserRepository(db).add(user)
        sessions = SessionRepository(db)
        for index in range(3):
            await sessions.add(
                Session(
                    user_id=user.id,
                    token_hash=f"tok-{index}",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
        await db.commit()

        revoked = await sessions.revoke_all_for_user(user.id, reason="password_change")
        await db.commit()

        assert revoked == 3
        for session in await sessions.list_for_user(user.id):
            assert session.revoked_at is not None
            assert session.revoke_reason == "password_change"

    async def test_revoke_all_is_idempotent(self, db: AsyncSession) -> None:
        user = User(username="ivan")
        await UserRepository(db).add(user)
        sessions = SessionRepository(db)
        await sessions.add(
            Session(
                user_id=user.id,
                token_hash="tok",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await db.commit()

        assert await sessions.revoke_all_for_user(user.id) == 1
        assert await sessions.revoke_all_for_user(user.id) == 0, "already-revoked rows"

    async def test_active_listing_excludes_expired_sessions(self, db: AsyncSession) -> None:
        user = User(username="judy")
        await UserRepository(db).add(user)
        sessions = SessionRepository(db)
        await sessions.add(
            Session(
                user_id=user.id,
                token_hash="live",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await sessions.add(
            Session(
                user_id=user.id,
                token_hash="dead",
                expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        await db.commit()

        assert [s.token_hash for s in await sessions.list_active_for_user(user.id)] == ["live"]


class TestAuditLogRepository:
    async def test_a_decision_needs_an_actor_and_a_verdict(self, db: AsyncSession) -> None:
        log = AuditLogRepository(db)
        entry = await log.record(
            actor_type="agent",
            actor_id="agent-1",
            action="fs.delete",
            decision=AuditOutcome.DENIED,
            reason="outside project root",
        )
        await db.commit()

        assert entry.decision is AuditOutcome.DENIED
        assert entry.occurred_at.tzinfo is not None, "must be timezone-aware"

    async def test_denials_are_listed_without_the_caller_filtering(self, db: AsyncSession) -> None:
        log = AuditLogRepository(db)
        await log.record(actor_type="agent", action="x", decision=AuditOutcome.ALLOWED)
        await log.record(actor_type="agent", action="y", decision=AuditOutcome.DENIED)
        await log.record(
            actor_type="agent",
            action="z",
            decision=AuditOutcome.CONFIRM_REQUIRED,
        )
        await db.commit()

        assert [e.action for e in await log.list_denied()] == ["y"]
        assert [e.action for e in await log.list_awaiting_confirmation()] == ["z"]

    async def test_a_request_id_ties_one_action_together(self, db: AsyncSession) -> None:
        log = AuditLogRepository(db)
        for action in ("read", "write", "denied"):
            await log.record(
                actor_type="user",
                action=action,
                decision=AuditOutcome.ALLOWED,
                request_id="req-7",
            )
        await log.record(actor_type="user", action="elsewhere", decision=AuditOutcome.ALLOWED)
        await db.commit()

        assert len(await log.list_for_request("req-7")) == 3

    async def test_resource_history_is_returned_newest_first(self, db: AsyncSession) -> None:
        log = AuditLogRepository(db)
        for offset in range(3):
            await log.record(
                actor_type="user",
                action="update",
                decision=AuditOutcome.ALLOWED,
                resource_type="project",
                resource_id="p-1",
                occurred_at=datetime.now(UTC) - timedelta(minutes=offset),
            )
        await db.commit()

        history = await log.list_for_resource("project", "p-1")
        assert len(history) == 3
        assert history[0].occurred_at >= history[-1].occurred_at


# --------------------------------------------------------------------------
# Agents -- permissions
# --------------------------------------------------------------------------


class TestAgentRepository:
    async def test_effective_permissions_are_the_agents_own(self, db: AsyncSession) -> None:
        agents = AgentRepository(db)
        parent = await agents.add(
            Agent(agent_type="supervisor", name="parent", permissions=["fs.read", "fs.write"])
        )

        assert await agents.get_effective_permissions(parent.id) == frozenset(
            {"fs.read", "fs.write"}
        )

    async def test_a_child_inherits_from_its_parent(self, db: AsyncSession) -> None:
        agents = AgentRepository(db)
        parent = await agents.add(
            Agent(agent_type="supervisor", name="parent", permissions=["fs.read"])
        )
        child = await agents.add(
            Agent(
                agent_type="worker",
                name="child",
                permissions=["net.fetch"],
                parent_agent_id=parent.id,
            )
        )

        assert await agents.get_effective_permissions(child.id) == frozenset(
            {"fs.read", "net.fetch"}
        )

    async def test_a_parent_does_not_inherit_from_its_child(self, db: AsyncSession) -> None:
        """Escalation only flows downward."""
        agents = AgentRepository(db)
        parent = await agents.add(
            Agent(agent_type="supervisor", name="parent", permissions=["fs.read"])
        )
        await agents.add(
            Agent(
                agent_type="worker",
                name="child",
                permissions=["system.config"],
                parent_agent_id=parent.id,
            )
        )

        assert await agents.get_effective_permissions(parent.id) == frozenset({"fs.read"})

    async def test_a_parent_cycle_terminates(self, db: AsyncSession) -> None:
        """A cycle must not hang the request, and must not escape the depth cap.

        The cycle is built across two flushes on purpose. Ids are minted at
        insert time, so a cycle has to be wired up in two steps -- setting both
        parents before either row exists would write two NULLs and leave a tree,
        which is precisely the case where the depth cap is never exercised.
        """
        agents = AgentRepository(db)
        first = await agents.add(
            Agent(agent_type="a", name="first", permissions=["one"])
        )
        second = await agents.add(
            Agent(agent_type="b", name="second", permissions=["two"])
        )
        first.parent_agent_id = second.id
        second.parent_agent_id = first.id
        await db.commit()

        # Terminates, and the walk stops at the cap rather than looping.
        assert await agents.get_effective_permissions(first.id) == frozenset(
            {"one", "two"}
        )

    async def test_claiming_a_second_task_is_refused(self, db: AsyncSession) -> None:
        agents = AgentRepository(db)
        agent = await agents.add(Agent(agent_type="worker", name="a"))
        await agents.claim_for_task(agent.id, uuid.uuid4())

        with pytest.raises(ConflictError, match="already claimed"):
            await agents.claim_for_task(agent.id, uuid.uuid4())

    async def test_claiming_the_same_task_twice_is_fine(self, db: AsyncSession) -> None:
        agents = AgentRepository(db)
        agent = await agents.add(Agent(agent_type="worker", name="a"))
        task_id = uuid.uuid4()

        await agents.claim_for_task(agent.id, task_id)
        again = await agents.claim_for_task(agent.id, task_id)

        assert again.task_id == task_id

    async def test_heartbeat_stale_reports_never_seen_agents(self, db: AsyncSession) -> None:
        agents = AgentRepository(db)
        await agents.add(Agent(agent_type="worker", name="silent", status=AgentStatus.RUNNING))
        speaking = await agents.add(
            Agent(agent_type="worker", name="speaking", status=AgentStatus.RUNNING)
        )
        await agents.heartbeat(speaking.id)
        done = await agents.add(
            Agent(agent_type="worker", name="done", status=AgentStatus.COMPLETED)
        )
        await db.commit()

        stale = await agents.list_missing_heartbeat()
        names = {a.name for a in stale}

        assert "silent" in names
        assert "speaking" not in names
        assert done.name not in names, "a finished agent is not missing a heartbeat"

    async def test_status_counts_come_from_one_query(self, db: AsyncSession) -> None:
        agents = AgentRepository(db)
        await agents.add(Agent(agent_type="w", name="a", status=AgentStatus.RUNNING))
        await agents.add(Agent(agent_type="w", name="b", status=AgentStatus.RUNNING))
        await agents.add(Agent(agent_type="w", name="c", status=AgentStatus.FAILED))
        await db.commit()

        counts = await agents.count_by_status()
        assert counts[AgentStatus.RUNNING.value] == 2
        assert counts[AgentStatus.FAILED.value] == 1


class TestAgentLogRepository:
    async def test_sequences_are_per_agent_and_increment(self, db: AsyncSession) -> None:
        agents = AgentRepository(db)
        logs = AgentLogRepository(db)
        first = await agents.add(Agent(agent_type="w", name="a"))
        second = await agents.add(Agent(agent_type="w", name="b"))

        await logs.append(agent_id=first.id, message="one")
        await logs.append(agent_id=first.id, message="two")
        other = await logs.append(agent_id=second.id, message="other")
        await db.commit()

        assert other.sequence == 0, "a new agent restarts numbering"
        entries = await logs.list_for_agent(first.id)
        assert [e.sequence for e in entries] == [0, 1]
        assert [e.message for e in entries] == ["one", "two"]

    async def test_logs_come_back_in_sequence_order_not_insertion_order(
        self, db: AsyncSession
    ) -> None:
        agents = AgentRepository(db)
        logs = AgentLogRepository(db)
        agent = await agents.add(Agent(agent_type="w", name="a"))
        for sequence, message in ((2, "c"), (0, "a"), (1, "b")):
            await logs.append(agent_id=agent.id, message=message, sequence=sequence)
        await db.commit()

        assert [e.message for e in await logs.list_for_agent(agent.id)] == ["a", "b", "c"]

    async def test_errors_are_filtered_in_sql(self, db: AsyncSession) -> None:
        agents = AgentRepository(db)
        logs = AgentLogRepository(db)
        agent = await agents.add(Agent(agent_type="w", name="a"))
        await logs.append(agent_id=agent.id, message="fine", level="info")
        await logs.append(agent_id=agent.id, message="odd", level="warn")
        await logs.append(agent_id=agent.id, message="bad", level="error")
        await db.commit()

        assert {e.message for e in await logs.list_errors(agent.id)} == {"odd", "bad"}


# --------------------------------------------------------------------------
# Tasks
# --------------------------------------------------------------------------


class TestTaskRepository:
    async def test_claimable_is_ordered_by_priority(self, db: AsyncSession) -> None:
        tasks = TaskRepository(db)
        await tasks.add(Task(goal="low", priority="low"))
        await tasks.add(Task(goal="urgent", priority="urgent"))
        await tasks.add(Task(goal="normal", priority="normal"))
        await tasks.add(Task(goal="running", priority="high", status=TaskStatus.RUNNING))
        await db.commit()

        assert [t.goal for t in await tasks.list_claimable()] == ["low", "normal", "urgent"]

    async def test_starting_a_non_pending_task_is_refused(self, db: AsyncSession) -> None:
        tasks = TaskRepository(db)
        task = await tasks.add(Task(goal="g", status=TaskStatus.RUNNING))

        with pytest.raises(ConflictError, match="not pending"):
            await tasks.start(task.id)

    async def test_start_stamps_the_time(self, db: AsyncSession) -> None:
        tasks = TaskRepository(db)
        task = await tasks.add(Task(goal="g"))
        started = await tasks.start(task.id)

        assert started.status is TaskStatus.RUNNING
        assert started.started_at is not None

    async def test_finishing_a_finished_task_is_refused(self, db: AsyncSession) -> None:
        tasks = TaskRepository(db)
        task = await tasks.add(Task(goal="g", status=TaskStatus.COMPLETED))

        with pytest.raises(ConflictError, match="already finished"):
            await tasks.complete(task.id)

    async def test_failure_message_is_truncated_to_fit(self, db: AsyncSession) -> None:
        tasks = TaskRepository(db)
        task = await tasks.add(Task(goal="g"))

        failed = await tasks.fail(task.id, error="x" * 10_000)
        await db.commit()

        assert failed.status is TaskStatus.FAILED
        assert failed.error is not None
        assert len(failed.error) <= 4000, "an untruncated traceback would fail the insert"

    async def test_retries_stop_at_the_limit(self, db: AsyncSession) -> None:
        tasks = TaskRepository(db)
        task = await tasks.add(Task(goal="g", retry_count=1, max_retries=2))

        await tasks.increment_retry(task.id)
        assert task.retry_count == 2

        with pytest.raises(ConflictError, match="exhausted its retries"):
            await tasks.increment_retry(task.id)

    async def test_scheduled_due_ignores_status(self, db: AsyncSession) -> None:
        """A candidate query, so a caller applies its own status policy."""
        tasks = TaskRepository(db)
        past = datetime.now(UTC) - timedelta(hours=1)
        await tasks.add(Task(goal="due", scheduled_for=past))
        await tasks.add(Task(goal="later", scheduled_for=datetime.now(UTC) + timedelta(hours=1)))
        await tasks.add(Task(goal="unscheduled"))
        await db.commit()

        assert [t.goal for t in await tasks.list_scheduled_due()] == ["due"]


class TestTaskStepRepository:
    async def test_positions_default_to_the_next_slot(self, db: AsyncSession) -> None:
        tasks = TaskRepository(db)
        steps = TaskStepRepository(db)
        task = await tasks.add(Task(goal="g"))

        await steps.add_step(task_id=task.id, name="one")
        await steps.add_step(task_id=task.id, name="two")
        explicit = await steps.add_step(task_id=task.id, name="three", position=10)
        await db.commit()

        assert explicit.position == 10
        ordered = [s.name for s in await steps.list_for_task(task.id)]
        assert ordered == ["one", "two", "three"]

    async def test_a_negative_position_is_refused(self, db: AsyncSession) -> None:
        tasks = TaskRepository(db)
        steps = TaskStepRepository(db)
        task = await tasks.add(Task(goal="g"))

        with pytest.raises(InvalidInputError, match="cannot be negative"):
            await steps.add_step(task_id=task.id, name="bad", position=-1)

    async def test_mark_running_increments_the_attempt(self, db: AsyncSession) -> None:
        tasks = TaskRepository(db)
        steps = TaskStepRepository(db)
        task = await tasks.add(Task(goal="g"))
        step = await steps.add_step(task_id=task.id, name="one")

        # Read the count out immediately. Both calls return the same
        # identity-mapped instance, so holding on to the objects and asserting
        # later would compare the final value against itself.
        first = (await steps.mark_running(step.id)).attempt
        second = (await steps.mark_running(step.id)).attempt

        assert first == 1
        assert second == 2, "a retry must not restart at 1"

    async def test_an_attempt_below_one_is_refused(self, db: AsyncSession) -> None:
        tasks = TaskRepository(db)
        steps = TaskStepRepository(db)
        task = await tasks.add(Task(goal="g"))
        step = await steps.add_step(task_id=task.id, name="one")

        with pytest.raises(InvalidInputError, match="starts at 1"):
            await steps.mark_running(step.id, attempt=0)

    async def test_steps_can_be_fetched_by_position(self, db: AsyncSession) -> None:
        tasks = TaskRepository(db)
        steps = TaskStepRepository(db)
        task = await tasks.add(Task(goal="g"))
        await steps.add_step(task_id=task.id, name="one", position=0)
        await steps.add_step(task_id=task.id, name="two", position=1)
        await db.commit()

        found = await steps.get_by_position(task.id, 1)
        assert found is not None
        assert found.name == "two"


# --------------------------------------------------------------------------
# Tool executions -- the permission record
# --------------------------------------------------------------------------


class TestToolExecutionRepository:
    async def test_a_decision_records_both_level_and_verdict(self, db: AsyncSession) -> None:
        executions = ToolExecutionRepository(db)
        row = await executions.record_decision(
            tool_name="fs.delete",
            permission_level=PermissionLevel.DESTRUCTIVE,
            decision=AuditOutcome.DENIED,
            decision_reason="outside the project root",
        )
        await db.commit()

        assert row.permission_level is PermissionLevel.DESTRUCTIVE
        assert row.decision is AuditOutcome.DENIED
        assert row.permission_level is not None
        assert row.decision is not None

    async def test_the_level_and_decision_arguments_have_no_defaults(self) -> None:
        """A tool call cannot be recorded without stating what was claimed.

        Enforced by the signature itself. If a default were ever added, this test
        would start failing at the call below rather than in review.
        """
        import inspect

        parameters = inspect.signature(ToolExecutionRepository.record_decision).parameters
        assert parameters["permission_level"].default is inspect.Parameter.empty
        assert parameters["decision"].default is inspect.Parameter.empty
        assert parameters["tool_name"].default is inspect.Parameter.empty

    async def test_the_columns_are_not_nullable(self) -> None:
        table = ToolExecution.__table__
        assert table.columns["permission_level"].nullable is False
        assert table.columns["decision"].nullable is False

    async def test_a_denial_is_kept_even_though_nothing_happened(
        self, db: AsyncSession
    ) -> None:
        executions = ToolExecutionRepository(db)
        await executions.record_decision(
            tool_name="fs.rm",
            permission_level=PermissionLevel.DESTRUCTIVE,
            decision=AuditOutcome.DENIED,
        )
        await db.commit()

        assert len(await executions.list_denied()) == 1, "a refusal is evidence"

    async def test_denials_are_matched_on_the_enum_not_a_prefix(self, db: AsyncSession) -> None:
        executions = ToolExecutionRepository(db)
        await executions.record_decision(
            tool_name="a",
            permission_level=PermissionLevel.SAFE_ACTIONS,
            decision=AuditOutcome.DENIED,
        )
        await executions.record_decision(
            tool_name="b",
            permission_level=PermissionLevel.SAFE_ACTIONS,
            decision=AuditOutcome.ALLOWED,
        )
        await db.commit()

        assert [e.tool_name for e in await executions.list_denied()] == ["a"]

    async def test_filtering_by_permission_level(self, db: AsyncSession) -> None:
        executions = ToolExecutionRepository(db)
        for index, level in enumerate(
            (PermissionLevel.READ_ONLY, PermissionLevel.SYSTEM_CONFIG, PermissionLevel.READ_ONLY)
        ):
            await executions.record_decision(
                tool_name=f"t{index}",
                permission_level=level,
                decision=AuditOutcome.ALLOWED,
            )
        await db.commit()

        assert len(await executions.list_at_level(PermissionLevel.READ_ONLY)) == 2

    async def test_confirmation_is_tracked_separately_from_completion(
        self, db: AsyncSession
    ) -> None:
        executions = ToolExecutionRepository(db)
        await executions.record_decision(
            tool_name="fs.write",
            permission_level=PermissionLevel.MODIFY_PROJECT,
            decision=AuditOutcome.CONFIRM_REQUIRED,
            status=ToolExecutionStatus.AWAITING_CONFIRM,
        )
        await db.commit()

        waiting = await executions.list_awaiting_confirmation()
        assert [e.tool_name for e in waiting] == ["fs.write"]

    async def test_duration_is_derived_from_the_start_time(self, db: AsyncSession) -> None:
        executions = ToolExecutionRepository(db)
        start = datetime.now(UTC) - timedelta(milliseconds=250)
        row = await executions.record_decision(
            tool_name="t",
            permission_level=PermissionLevel.SAFE_ACTIONS,
            decision=AuditOutcome.ALLOWED,
            started_at=start,
        )
        done = await executions.mark_succeeded(row.id)

        assert done.duration_ms is not None
        assert 200 <= done.duration_ms <= 2000

    async def test_an_explicit_duration_wins(self, db: AsyncSession) -> None:
        executions = ToolExecutionRepository(db)
        row = await executions.record_decision(
            tool_name="t",
            permission_level=PermissionLevel.SAFE_ACTIONS,
            decision=AuditOutcome.ALLOWED,
            started_at=datetime.now(UTC) - timedelta(seconds=10),
        )

        done = await executions.mark_succeeded(row.id, duration_ms=42)
        assert done.duration_ms == 42

    async def test_a_denial_is_distinct_from_a_failure(self, db: AsyncSession) -> None:
        executions = ToolExecutionRepository(db)
        denied = await executions.record_decision(
            tool_name="t",
            permission_level=PermissionLevel.DESTRUCTIVE,
            decision=AuditOutcome.DENIED,
        )
        await executions.mark_denied(denied.id)

        broken = await executions.record_decision(
            tool_name="u",
            permission_level=PermissionLevel.SAFE_ACTIONS,
            decision=AuditOutcome.ALLOWED,
        )
        await executions.mark_failed(broken.id, error="boom")
        await db.commit()

        assert denied.status is ToolExecutionStatus.DENIED
        assert broken.status is ToolExecutionStatus.FAILED

    async def test_verification_is_distinct_from_success(self, db: AsyncSession) -> None:
        """'The tool ran' and 'the result was checked' are different facts."""
        executions = ToolExecutionRepository(db)
        row = await executions.record_decision(
            tool_name="t",
            permission_level=PermissionLevel.SAFE_ACTIONS,
            decision=AuditOutcome.ALLOWED,
        )
        await executions.mark_succeeded(row.id)
        await db.commit()

        assert [e.tool_name for e in await executions.list_unverified()] == ["t"]

        await executions.record_verification(row.id, outcome=VerificationOutcome.SUCCESS)
        await db.commit()

        assert await executions.list_unverified() == []
        assert row.verified is True


# --------------------------------------------------------------------------
# Conversations
# --------------------------------------------------------------------------


class TestConversationRepository:
    async def test_tenancy_is_enforced_on_the_data_layer(self, db: AsyncSession) -> None:
        conversations = ConversationRepository(db)
        owner = uuid.uuid4()
        other = uuid.uuid4()
        conversation = await conversations.add(
            Conversation(title="private", user_id=owner)
        )

        assert await conversations.require_conversation(conversation.id, user_id=owner)

        with pytest.raises(NotFoundError):
            await conversations.require_conversation(conversation.id, user_id=other)

    async def test_a_mismatch_is_reported_as_missing_not_forbidden(
        self, db: AsyncSession
    ) -> None:
        """Confirming an id exists elsewhere leaks the shape of the data."""
        conversations = ConversationRepository(db)
        conversation = await conversations.add(Conversation(title="t", user_id=uuid.uuid4()))

        with pytest.raises(NotFoundError):
            await conversations.require_conversation(conversation.id, user_id=uuid.uuid4())

    async def test_an_unowned_conversation_is_reachable_by_its_owner(
        self, db: AsyncSession
    ) -> None:
        """``user_id IS NULL`` means shared, not orphaned."""
        conversations = ConversationRepository(db)
        conversation = await conversations.add(Conversation(title="shared", user_id=None))
        any_user = uuid.uuid4()

        assert await conversations.require_conversation(conversation.id, user_id=any_user)

    async def test_archive_is_idempotent(self, db: AsyncSession) -> None:
        conversations = ConversationRepository(db)
        conversation = await conversations.add(
            Conversation(title="t", user_id=uuid.uuid4(), status=ConversationStatus.ACTIVE)
        )

        first = await conversations.archive(conversation.id)
        second = await conversations.archive(conversation.id)

        assert first.status is ConversationStatus.ARCHIVED
        assert second.status is ConversationStatus.ARCHIVED

    async def test_listing_hides_archived_conversations(self, db: AsyncSession) -> None:
        conversations = ConversationRepository(db)
        user = uuid.uuid4()
        live = await conversations.add(Conversation(title="live", user_id=user))
        await conversations.add(
            Conversation(title="gone", user_id=user, status=ConversationStatus.ARCHIVED)
        )
        await db.commit()

        assert [c.id for c in await conversations.list_for_user(user)] == [live.id]


class TestMessageRepository:
    async def test_sequences_are_assigned_per_conversation(self, db: AsyncSession) -> None:
        messages = MessageRepository(db)
        first = await messages.append(
            conversation_id=uuid.uuid4(),
            role=MessageRole.USER,
            content="hello",
        )
        second = await messages.append(
            conversation_id=first.conversation_id,
            role=MessageRole.ASSISTANT,
            content="hi",
        )

        assert first.sequence == 0
        assert second.sequence == 1

    async def test_ordering_does_not_depend_on_the_timestamp(self, db: AsyncSession) -> None:
        messages = MessageRepository(db)
        conversation = uuid.uuid4()
        stamp = datetime.now(UTC)
        for sequence, content in ((2, "c"), (0, "a"), (1, "b")):
            await messages.append(
                conversation_id=conversation,
                role=MessageRole.USER,
                content=content,
                sequence=sequence,
                created_at=stamp,
            )
        await db.commit()

        listed = await messages.list_for_conversation(conversation)
        assert [m.content for m in listed] == ["a", "b", "c"]

    async def test_token_totals_come_from_one_query(self, db: AsyncSession) -> None:
        messages = MessageRepository(db)
        conversation = uuid.uuid4()
        await messages.append(
            conversation_id=conversation,
            role=MessageRole.USER,
            content="q",
            prompt_tokens=10,
        )
        await messages.append(
            conversation_id=conversation,
            role=MessageRole.ASSISTANT,
            content="a",
            prompt_tokens=5,
            completion_tokens=20,
        )
        await db.commit()

        assert await messages.total_tokens(conversation) == (15, 20)

    async def test_totals_of_an_empty_conversation_are_zero(self, db: AsyncSession) -> None:
        assert await MessageRepository(db).total_tokens(uuid.uuid4()) == (0, 0)

    async def test_tool_messages_have_a_dedicated_view(self, db: AsyncSession) -> None:
        messages = MessageRepository(db)
        conversation = uuid.uuid4()
        await messages.append(conversation_id=conversation, role=MessageRole.USER, content="q")
        await messages.append(
            conversation_id=conversation,
            role=MessageRole.TOOL,
            content="result",
            tool_name="fs.read",
        )
        await db.commit()

        tools = await messages.list_tool_messages(conversation)
        assert [t.tool_name for t in tools] == ["fs.read"]


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------


class TestEventRepository:
    async def test_a_task_events_come_back_in_order(self, db: AsyncSession) -> None:
        events = EventRepository(db)
        task = uuid.uuid4()
        base = datetime.now(UTC)
        for offset, name in ((2, "c"), (0, "a"), (1, "b")):
            await events.append(
                event_type=name,
                task_id=task,
                occurred_at=base + timedelta(seconds=offset),
                sequence=offset,
            )
        await db.commit()

        assert [e.event_type for e in await events.list_for_task(task)] == ["a", "b", "c"]

    async def test_events_within_one_timestamp_use_the_emitter_sequence(
        self, db: AsyncSession
    ) -> None:
        events = EventRepository(db)
        task = uuid.uuid4()
        stamp = datetime.now(UTC)
        for sequence, name in ((2, "c"), (0, "a"), (1, "b")):
            await events.append(event_type=name, task_id=task, occurred_at=stamp, sequence=sequence)
        await db.commit()

        assert [e.event_type for e in await events.list_for_task(task)] == ["a", "b", "c"]

    async def test_a_correlation_id_traces_a_request(self, db: AsyncSession) -> None:
        events = EventRepository(db)
        for name in ("received", "dispatched", "done"):
            await events.append(event_type=name, correlation_id="corr-1")
        await events.append(event_type="unrelated")
        await db.commit()

        assert len(await events.list_for_correlation("corr-1")) == 3

    async def test_the_since_argument_is_mandatory(self) -> None:
        """An unbounded type scan over a growing table is the failure being avoided."""
        import inspect

        parameters = inspect.signature(EventRepository.list_by_type).parameters
        assert parameters["since"].default is inspect.Parameter.empty

    async def test_listing_by_type_honours_since(self, db: AsyncSession) -> None:
        events = EventRepository(db)
        now = datetime.now(UTC)
        await events.append(event_type="x", occurred_at=now - timedelta(days=2))
        await events.append(event_type="x", occurred_at=now - timedelta(hours=1))
        await db.commit()

        recent = await events.list_by_type("x", since=now - timedelta(hours=2))
        assert len(recent) == 1

    async def test_retention_needs_a_cutoff_and_reports_a_count(self, db: AsyncSession) -> None:
        import inspect

        parameters = inspect.signature(EventRepository.delete_older_than).parameters
        assert parameters["cutoff"].default is inspect.Parameter.empty

        events = EventRepository(db)
        now = datetime.now(UTC)
        await events.append(event_type="old", occurred_at=now - timedelta(days=30))
        await events.append(event_type="new", occurred_at=now)
        await db.commit()

        assert await events.delete_older_than(now - timedelta(days=1)) == 1

    async def test_the_log_has_no_update_path(self) -> None:
        """An event log that can be edited does not audit anything."""
        assert not hasattr(EventRepository, "update")
        assert not hasattr(EventRepository, "mark_reviewed")


# --------------------------------------------------------------------------
# Memory
# --------------------------------------------------------------------------


class TestMemoryRepository:
    async def test_reads_are_scoped_to_one_namespace_exactly(self, db: AsyncSession) -> None:
        memories = MemoryRepository(db)
        await memories.remember(
            namespace="proj:alpha",
            namespace_kind="project",
            namespace_slug="alpha",
            content="mine",
        )
        await memories.remember(
            namespace="proj:alpha-private",
            namespace_kind="project",
            namespace_slug="alpha-private",
            content="not mine",
        )
        await db.commit()

        found = await memories.list_for_namespace("proj:alpha")
        assert [m.content for m in found] == ["mine"], "a prefix match would leak"

    async def test_importance_out_of_range_is_refused_not_clamped(self, db: AsyncSession) -> None:
        memories = MemoryRepository(db)

        with pytest.raises(InvalidInputError, match="between 0 and 1"):
            await memories.remember(
                namespace="u:1",
                namespace_kind="user",
                namespace_slug="1",
                content="c",
                importance=1.5,
            )

    async def test_an_empty_namespace_is_refused(self, db: AsyncSession) -> None:
        memories = MemoryRepository(db)

        with pytest.raises(InvalidInputError, match="needs a namespace"):
            await memories.remember(
                namespace="   ",
                namespace_kind="user",
                namespace_slug="1",
                content="c",
            )

    async def test_expiry_is_filtered_in_sql(self, db: AsyncSession) -> None:
        memories = MemoryRepository(db)
        now = datetime.now(UTC)
        await memories.remember(
            namespace="p:1",
            namespace_kind="project",
            namespace_slug="1",
            content="live",
        )
        await memories.remember(
            namespace="p:1",
            namespace_kind="project",
            namespace_slug="1",
            content="dead",
            expires_at=now - timedelta(hours=1),
        )
        await db.commit()

        live = await memories.list_live("p:1", now=now)
        assert [m.content for m in live] == ["live"]

    async def test_touch_increments_the_access_count(self, db: AsyncSession) -> None:
        memories = MemoryRepository(db)
        memory = await memories.remember(
            namespace="p:1",
            namespace_kind="project",
            namespace_slug="1",
            content="c",
        )
        await db.commit()

        await memories.touch(memory.id)
        await memories.touch(memory.id)
        await db.commit()
        await db.refresh(memory)

        assert memory.access_count == 2
        assert memory.last_accessed_at is not None

    async def test_clearing_a_namespace_cannot_reach_another(self, db: AsyncSession) -> None:
        memories = MemoryRepository(db)
        for namespace in ("p:1", "p:2", "p:10"):
            await memories.remember(
                namespace=namespace,
                namespace_kind="project",
                namespace_slug=namespace.split(":")[1],
                content=namespace,
            )
        await db.commit()

        assert await memories.clear_namespace("p:1") == 1
        assert len(await memories.list_for_namespace("p:2")) == 1
        assert len(await memories.list_for_namespace("p:10")) == 1

    async def test_similarity_search_is_refused_rather_than_faked(self) -> None:
        """A random-sample stand-in would pass a test and return noise in production."""
        assert not hasattr(MemoryRepository, "search_similar")
        assert not hasattr(MemoryRepository, "search_by_embedding")


# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------


class TestProjectRepository:
    async def test_a_root_path_is_required_for_filesystem_scope(self, db: AsyncSession) -> None:
        projects = ProjectRepository(db)
        project = await projects.add(Project(name="p", slug="p"))

        with pytest.raises(InvalidInputError, match="no root path"):
            await projects.require_root_path(project.id)

    async def test_a_relative_root_is_refused(self, db: AsyncSession) -> None:
        """A relative root resolves against the process cwd, which varies."""
        projects = ProjectRepository(db)
        project = await projects.add(Project(name="p", slug="p", root_path="./here"))

        with pytest.raises(InvalidInputError, match="must be absolute"):
            await projects.require_root_path(project.id)

    async def test_an_absolute_root_is_returned(self, db: AsyncSession) -> None:
        projects = ProjectRepository(db)
        project = await projects.add(
            Project(name="p", slug="p", root_path="/srv/projects/p")
        )

        assert await projects.require_root_path(project.id) == "/srv/projects/p"

    async def test_deactivation_is_idempotent_and_keeps_rows(self, db: AsyncSession) -> None:
        projects = ProjectRepository(db)
        project = await projects.add(Project(name="p", slug="p"))

        await projects.deactivate(project.id)
        await projects.deactivate(project.id)

        assert project.is_active is False
        assert await projects.get(project.id) is not None, "the record must survive"

    async def test_default_listing_excludes_inactive_projects(self, db: AsyncSession) -> None:
        projects = ProjectRepository(db)
        owner = uuid.uuid4()
        await projects.add(Project(name="live", slug="live", owner_id=owner))
        await projects.add(
            Project(name="closed", slug="closed", owner_id=owner, is_active=False)
        )
        await db.commit()

        assert [p.name for p in await projects.list_for_owner(owner)] == ["live"]
        assert len(await projects.list_for_owner(owner, include_inactive=True)) == 2


# --------------------------------------------------------------------------
# Devices
# --------------------------------------------------------------------------


class TestDeviceRepository:
    async def test_authentication_uses_the_hash(self, db: AsyncSession) -> None:
        devices = DeviceRepository(db)
        device = await devices.add(
            Device(
                name="esp",
                auth_token_hash="hash-1",
                status=DeviceStatus.ONLINE,
            )
        )

        authenticated = await devices.authenticate("hash-1")
        assert authenticated is not None
        assert authenticated.id == device.id
        assert await devices.authenticate("wrong") is None

    async def test_a_disabled_device_is_refused_despite_a_valid_token(
        self, db: AsyncSession
    ) -> None:
        devices = DeviceRepository(db)
        await devices.add(
            Device(
                name="esp",
                auth_token_hash="hash-1",
                status=DeviceStatus.DISABLED,
            )
        )
        await db.commit()

        assert await devices.authenticate("hash-1") is None

    async def test_an_offline_device_is_refused(self, db: AsyncSession) -> None:
        devices = DeviceRepository(db)
        await devices.add(
            Device(name="esp", auth_token_hash="hash-1", status=DeviceStatus.OFFLINE)
        )
        await db.commit()

        assert await devices.authenticate("hash-1") is None

    async def test_refusals_are_indistinguishable(self, db: AsyncSession) -> None:
        """A caller must not be able to learn which device ids exist."""
        devices = DeviceRepository(db)
        await devices.add(
            Device(name="disabled", auth_token_hash="h1", status=DeviceStatus.DISABLED)
        )
        await devices.add(Device(name="online", auth_token_hash="h2", status=DeviceStatus.ONLINE))
        await db.commit()

        results = [
            await devices.authenticate("h1"),
            await devices.authenticate("h2"),
            await devices.authenticate("unknown-hash"),
        ]
        assert results == [None, None if False else results[1], None]
        assert results[0] is None and results[2] is None
        assert results[1] is not None, "a live device does authenticate"

    async def test_a_heartbeat_does_not_revive_a_disabled_device(self, db: AsyncSession) -> None:
        devices = DeviceRepository(db)
        device = await devices.add(
            Device(name="esp", auth_token_hash="h", status=DeviceStatus.DISABLED)
        )

        await devices.heartbeat(device.id)

        assert device.status is DeviceStatus.DISABLED, "a revocation must survive a heartbeat"

    async def test_a_heartbeat_brings_an_offline_device_back(self, db: AsyncSession) -> None:
        devices = DeviceRepository(db)
        device = await devices.add(Device(name="esp", status=DeviceStatus.OFFLINE))

        await devices.heartbeat(device.id)

        assert device.status is DeviceStatus.ONLINE
        assert device.last_seen is not None

    async def test_a_plain_heartbeat_does_not_blank_the_firmware_version(
        self, db: AsyncSession
    ) -> None:
        devices = DeviceRepository(db)
        device = await devices.add(
            Device(name="esp", status=DeviceStatus.ONLINE, firmware_version="1.2.3")
        )

        await devices.heartbeat(device.id)
        assert device.firmware_version == "1.2.3"

        await devices.heartbeat(device.id, firmware_version="1.3.0")
        assert device.firmware_version == "1.3.0"

    async def test_re_enabling_does_not_claim_the_device_is_online(
        self, db: AsyncSession
    ) -> None:
        devices = DeviceRepository(db)
        device = await devices.add(Device(name="esp", status=DeviceStatus.DISABLED))

        await devices.enable(device.id)

        assert device.status is DeviceStatus.UNKNOWN
        assert await devices.list_online() == []

    async def test_capability_filtering_says_it_is_not_implemented(self, db: AsyncSession) -> None:
        """A JSONB containment guess would pass on SQLite and be wrong on PostgreSQL."""
        with pytest.raises(CapabilityNotImplementedError, match="JSONB containment"):
            await DeviceRepository(db).list_by_capability("audio.capture")

    async def test_stale_devices_need_a_bound(self) -> None:
        import inspect

        parameters = inspect.signature(DeviceRepository.list_stale).parameters
        assert parameters["before"].default is inspect.Parameter.empty

    async def test_a_device_that_never_reported_counts_as_stale(self, db: AsyncSession) -> None:
        devices = DeviceRepository(db)
        await devices.add(Device(name="never", status=DeviceStatus.ONLINE))
        await devices.add(
            Device(
                name="fresh",
                status=DeviceStatus.ONLINE,
                last_seen=datetime.now(UTC),
            )
        )
        await db.commit()

        stale = await devices.list_stale(before=datetime.now(UTC) - timedelta(minutes=1))
        assert [d.name for d in stale] == ["never"]


class TestDeviceEventRepository:
    async def test_sequences_are_per_device(self, db: AsyncSession) -> None:
        devices = DeviceRepository(db)
        device_events = DeviceEventRepository(db)
        first = await devices.add(Device(name="a", status=DeviceStatus.ONLINE))
        second = await devices.add(Device(name="b", status=DeviceStatus.ONLINE))

        await device_events.append(device_id=first.id, event_type="button")
        await device_events.append(device_id=first.id, event_type="audio")
        other = await device_events.append(device_id=second.id, event_type="button")
        await db.commit()

        assert other.sequence == 0
        assert len(await device_events.list_for_device(first.id)) == 2

    async def test_the_latest_event_is_a_single_row(self, db: AsyncSession) -> None:
        devices = DeviceRepository(db)
        device_events = DeviceEventRepository(db)
        device = await devices.add(Device(name="a", status=DeviceStatus.ONLINE))
        await device_events.append(device_id=device.id, event_type="first")
        await device_events.append(device_id=device.id, event_type="second")
        await db.commit()

        latest = await device_events.latest_for_device(device.id)
        assert latest is not None
        assert latest.event_type == "second"

    async def test_a_device_with_no_events_has_no_latest(self, db: AsyncSession) -> None:
        devices = DeviceRepository(db)
        device = await devices.add(Device(name="a", status=DeviceStatus.ONLINE))
        await db.commit()

        assert await DeviceEventRepository(db).latest_for_device(device.id) is None


# --------------------------------------------------------------------------
# Usage
# --------------------------------------------------------------------------


class TestModelUsageRepository:
    async def test_negative_figures_are_refused(self, db: AsyncSession) -> None:
        usage = ModelUsageRepository(db)

        with pytest.raises(InvalidInputError, match="cannot be negative"):
            await usage.record(model="m", prompt_tokens=-1)
        with pytest.raises(InvalidInputError, match="cannot be negative"):
            await usage.record(model="m", cost_usd=Decimal("-0.01"))

    async def test_task_totals_come_from_a_single_aggregate(self, db: AsyncSession) -> None:
        usage = ModelUsageRepository(db)
        task = uuid.uuid4()
        await usage.record(
            model="a",
            task_id=task,
            prompt_tokens=10,
            completion_tokens=5,
            cost_usd=Decimal("0.01"),
        )
        await usage.record(
            model="a",
            task_id=task,
            prompt_tokens=2,
            completion_tokens=3,
            cost_usd=Decimal("0.02"),
        )
        await db.commit()

        totals = await usage.totals_for_task(task)
        assert totals["prompt_tokens"] == 12
        assert totals["completion_tokens"] == 8
        assert totals["calls"] == 2
        assert totals["cost_usd"] == Decimal("0.03")

    async def test_totals_of_an_empty_task_are_zero_not_none(self, db: AsyncSession) -> None:
        totals = await ModelUsageRepository(db).totals_for_task(uuid.uuid4())
        assert totals == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost_usd": Decimal(0),
            "calls": 0,
        }

    async def test_failure_rate_is_derived_not_guessed(self, db: AsyncSession) -> None:
        usage = ModelUsageRepository(db)
        since = datetime.now(UTC) - timedelta(hours=1)
        for status in (ModelUsageStatus.SUCCESS, ModelUsageStatus.SUCCESS, ModelUsageStatus.ERROR):
            await usage.record(model="m", status=status, recorded_at=datetime.now(UTC))
        await db.commit()

        assert await usage.failure_rate("m", since=since) == Decimal(1) / Decimal(3)

    async def test_failure_rate_of_an_unknown_model_is_zero(self, db: AsyncSession) -> None:
        assert await ModelUsageRepository(db).failure_rate("nope", since=datetime.now(UTC)) == 0

    async def test_spend_by_model_is_ordered_by_cost(self, db: AsyncSession) -> None:
        usage = ModelUsageRepository(db)
        since = datetime.now(UTC) - timedelta(days=1)
        await usage.record(model="cheap", cost_usd=Decimal("0.01"))
        await usage.record(model="pricey", cost_usd=Decimal("1.00"))
        await usage.record(model="pricey", cost_usd=Decimal("1.00"))
        await db.commit()

        rows = await usage.totals_by_model(since=since)
        assert [model for model, _, _ in rows] == ["pricey", "cheap"]
        assert {model: calls for model, calls, _ in rows}["pricey"] == 2

    async def test_the_since_argument_is_mandatory(self) -> None:
        import inspect

        parameters = inspect.signature(ModelUsageRepository.totals_by_model).parameters
        assert parameters["since"].default is inspect.Parameter.empty

    async def test_percentile_must_be_a_fraction(self, db: AsyncSession) -> None:
        usage = ModelUsageRepository(db)

        with pytest.raises(InvalidInputError, match="between 0 and 1"):
            await usage.latency_percentile("m", since=datetime.now(UTC), percentile=95)

    async def test_percentile_of_an_unknown_model_is_none(self, db: AsyncSession) -> None:
        value = await ModelUsageRepository(db).latency_percentile(
            "nope", since=datetime.now(UTC)
        )
        assert value is None

    async def test_percentile_picks_the_ninety_fifth(self, db: AsyncSession) -> None:
        usage = ModelUsageRepository(db)
        for latency in range(1, 101):
            await usage.record(model="m", latency_ms=latency)
        await db.commit()

        value = await usage.latency_percentile("m", since=datetime.now(UTC) - timedelta(hours=1))
        assert value == 95

    async def test_total_cost_since(self, db: AsyncSession) -> None:
        usage = ModelUsageRepository(db)
        await usage.record(model="a", cost_usd=Decimal("0.25"))
        await usage.record(model="a", cost_usd=Decimal("0.75"))
        await db.commit()

        since = datetime.now(UTC) - timedelta(hours=1)
        assert await usage.total_cost_since(since) == Decimal("1.00")


# --------------------------------------------------------------------------
# Schedules
# --------------------------------------------------------------------------


class TestScheduleRepository:
    async def test_due_schedules_need_an_explicit_now(self) -> None:
        import inspect

        parameters = inspect.signature(ScheduleRepository.list_due).parameters
        assert parameters["now"].default is inspect.Parameter.empty

    async def test_due_schedules_come_back_soonest_first(self, db: AsyncSession) -> None:
        schedules = ScheduleRepository(db)
        now = datetime.now(UTC)
        for offset in (2, 0, 1):
            await schedules.add(
                Schedule(
                    name=f"s{offset}",
                    kind=ScheduleKind.INTERVAL,
                    status=ScheduleStatus.ACTIVE,
                    interval_seconds=60,
                    next_run_at=now - timedelta(minutes=10 - offset),
                )
            )
        await schedules.add(
            Schedule(
                name="later",
                kind=ScheduleKind.INTERVAL,
                status=ScheduleStatus.ACTIVE,
                interval_seconds=60,
                next_run_at=now + timedelta(hours=1),
            )
        )
        await db.commit()

        due = await schedules.list_due(now=now)
        assert [s.name for s in due] == ["s0", "s1", "s2"]

    async def test_a_schedule_that_never_ran_does_not_head_the_queue(
        self, db: AsyncSession
    ) -> None:
        schedules = ScheduleRepository(db)
        now = datetime.now(UTC)
        await schedules.add(
            Schedule(
                name="never",
                kind=ScheduleKind.INTERVAL,
                status=ScheduleStatus.ACTIVE,
                interval_seconds=60,
                next_run_at=None,
            )
        )
        await schedules.add(
            Schedule(
                name="soon",
                kind=ScheduleKind.INTERVAL,
                status=ScheduleStatus.ACTIVE,
                interval_seconds=60,
                next_run_at=now,
            )
        )
        await db.commit()

        assert [s.name for s in await schedules.list_active()] == ["soon", "never"]

    async def test_set_next_run_clears_a_stale_error(self, db: AsyncSession) -> None:
        schedules = ScheduleRepository(db)
        schedule = await schedules.add(
            Schedule(
                name="s",
                kind=ScheduleKind.INTERVAL,
                status=ScheduleStatus.ACTIVE,
                interval_seconds=60,
                failure_count=3,
                last_error="old failure",
            )
        )

        await schedules.set_next_run(schedule.id, next_run_at=datetime.now(UTC))
        await db.commit()

        assert schedule.run_count == 1
        assert schedule.failure_count == 0
        assert schedule.last_error is None

    async def test_a_failure_leaves_the_next_run_for_the_caller_to_decide(
        self, db: AsyncSession
    ) -> None:
        schedules = ScheduleRepository(db)
        upcoming = datetime.now(UTC) + timedelta(hours=1)
        schedule = await schedules.add(
            Schedule(
                name="s",
                kind=ScheduleKind.INTERVAL,
                status=ScheduleStatus.ACTIVE,
                interval_seconds=60,
                next_run_at=upcoming,
            )
        )

        await schedules.record_failure(schedule.id, error="boom")

        assert schedule.failure_count == 1
        assert schedule.next_run_at == upcoming, "retry policy is not this class's job"

    async def test_only_a_one_shot_schedule_can_complete(self, db: AsyncSession) -> None:
        schedules = ScheduleRepository(db)
        recurring = await schedules.add(
            Schedule(
                name="rec",
                kind=ScheduleKind.INTERVAL,
                status=ScheduleStatus.ACTIVE,
                interval_seconds=60,
            )
        )

        with pytest.raises(InvalidInputError, match="one-shot"):
            await schedules.mark_completed(recurring.id)

    async def test_resume_does_not_revive_a_completed_schedule(self, db: AsyncSession) -> None:
        schedules = ScheduleRepository(db)
        schedule = await schedules.add(
            Schedule(
                name="s",
                kind=ScheduleKind.ONCE,
                status=ScheduleStatus.COMPLETED,
                run_at=datetime.now(UTC),
            )
        )

        await schedules.resume(schedule.id)

        assert schedule.status is ScheduleStatus.COMPLETED

    async def test_a_paused_schedule_can_be_resumed(self, db: AsyncSession) -> None:
        schedules = ScheduleRepository(db)
        schedule = await schedules.add(
            Schedule(
                name="s",
                kind=ScheduleKind.INTERVAL,
                status=ScheduleStatus.PAUSED,
                interval_seconds=60,
            )
        )

        await schedules.resume(schedule.id)
        assert schedule.status is ScheduleStatus.ACTIVE

    async def test_timing_validation_is_kind_specific(self, db: AsyncSession) -> None:
        schedules = ScheduleRepository(db)

        with pytest.raises(InvalidInputError, match="cron expression"):
            await schedules.validate_timing(ScheduleKind.CRON)
        with pytest.raises(InvalidInputError, match="interval_seconds"):
            await schedules.validate_timing(ScheduleKind.INTERVAL)
        with pytest.raises(InvalidInputError, match="at least 1"):
            await schedules.validate_timing(ScheduleKind.INTERVAL, interval_seconds=0)
        with pytest.raises(InvalidInputError, match="run_at"):
            await schedules.validate_timing(ScheduleKind.ONCE)

    async def test_valid_timing_passes(self, db: AsyncSession) -> None:
        await ScheduleRepository(db).validate_timing(ScheduleKind.CRON, cron_expression="0 * * * *")
        await ScheduleRepository(db).validate_timing(ScheduleKind.INTERVAL, interval_seconds=30)
        await ScheduleRepository(db).validate_timing(ScheduleKind.ONCE, run_at=datetime.now(UTC))

    async def test_no_method_guesses_the_next_run_time(self) -> None:
        """Cron needs a parser and a calendar; a repository has neither."""
        assert not hasattr(ScheduleRepository, "mark_ran")
        assert not hasattr(ScheduleRepository, "compute_next_run")


# --------------------------------------------------------------------------
# Cross-cutting invariants
# --------------------------------------------------------------------------


def _concrete_repositories() -> list[type]:
    """Return every concrete repository class exported by the package.

    Resolved from the package ``__all__`` rather than by scanning module
    globals: scanning picks up the ``UuidRepository`` base that each submodule
    imports, which is not a concrete repository and has no ``model``, so the
    scan would fail on the very thing it is meant to skip.
    """
    import app.database.repositories as package

    bases = {package.Repository, package.UuidRepository}
    found: list[type] = []
    for name in package.__all__:
        candidate = getattr(package, name)
        if (
            isinstance(candidate, type)
            and issubclass(candidate, package.Repository)
            and candidate not in bases
        ):
            found.append(candidate)
    return found


class TestRepositoryInvariants:
    async def test_no_repository_commits(self, db: AsyncSession) -> None:
        """Committing would break the caller's rollback on failure."""
        import inspect

        for repository in _concrete_repositories():
            for name in ("add", "get", "get_required", "delete", "add_all", "count", "exists"):
                method = getattr(repository, name)
                source = inspect.getsource(method)
                assert ".commit(" not in source, f"{repository.__name__}.{name} must not commit"

    async def test_every_list_method_is_bounded(self) -> None:
        """An unbounded read on a growing table is a memory exhaustion bug."""
        import inspect

        offenders: list[str] = []
        for repository in _concrete_repositories():
            for name, method in vars(repository).items():
                if not name.startswith("list_"):
                    continue
                if not inspect.iscoroutinefunction(method):
                    continue
                source = inspect.getsource(method)
                if "select(" not in source:
                    continue
                if "apply_limit" not in source and "limit(1)" not in source:
                    offenders.append(f"{repository.__name__}.{name}")

        assert offenders == [], f"unbounded list queries: {offenders}"

    async def test_no_repository_decides_permission(self) -> None:
        """Deciding permission needs the caller's identity, which data access lacks."""
        assert not hasattr(ToolExecutionRepository, "is_permitted")
        assert not hasattr(TaskRepository, "authorise")

    async def test_every_repository_exposes_a_required_variant(self) -> None:
        repositories = _concrete_repositories()

        assert len(repositories) >= 15
        for repository in repositories:
            assert hasattr(repository, "get_required"), repository.__name__
            model = getattr(repository, "model", None)
            assert model is not None, f"{repository.__name__} declares no model"
