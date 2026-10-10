"""Task manager tests (T039).

Runs against SQLite in-memory via the same savepoint fixtures T015's
repository tests use (see ``test_database_repositories.py``), because the
manager's rules are rules about *rows*: a status that did not persist, a
timestamp the database never stamped, a bound that survived only in memory —
none of those can be caught by a fake repository.

The weight is on the refusals. The illegal ``PENDING → RUNNING`` shortcut
(the machine's rule 1 seen from the caller's side), a payload meant for a
different outcome, a terminal task being reprioritised, a live task being
deleted, an exhausted retry bound: each of these failing silently would mean
the state machine and the validation boundary are decorative.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ConflictError,
    ErrorCode,
    InvalidInputError,
    TaskNotFoundError,
)
from app.database.models import (
    Task,
    TaskPriority,
    TaskStatus,
    VerificationOutcome,
)
from app.database.session import Base, create_engine, dispose
from app.tasks.manager import TaskManager

pytestmark = pytest.mark.unit

SQLITE_URL = "sqlite+aiosqlite://"

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 1, 1, 12, 5, 0, tzinfo=UTC)
T2 = datetime(2026, 1, 1, 12, 10, 0, tzinfo=UTC)


@pytest.fixture
async def engine() -> AsyncIterator[object]:
    """A private in-memory database with the production schema.

    Copied from T015's fixture pair rather than shared: the two test modules
    must stay independently runnable, and ``Base.metadata`` is the real one
    either way — not a hand-built table list that can drift from the
    migrations.
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
    """A session rolled back after each test (savepoint per commit)."""
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


@pytest.fixture
def manager(db: AsyncSession) -> TaskManager:
    """The unit under test, bound to the test session."""
    return TaskManager(db)


async def admitted(manager: TaskManager, *, goal: str = "the work") -> Task:
    """A task walked through the one door: PENDING → QUEUED → RUNNING."""
    task = await manager.create(goal)
    await manager.transition(task.id, TaskStatus.QUEUED, now=T0)
    return await manager.transition(task.id, TaskStatus.RUNNING, now=T1)


# --------------------------------------------------------------------------
# Create
# --------------------------------------------------------------------------


class TestCreate:
    async def test_a_created_task_carries_the_spec_fields(self, manager: TaskManager) -> None:
        task = await manager.create("ship it")

        assert task.goal == "ship it"
        assert task.status is TaskStatus.PENDING
        assert task.priority is TaskPriority.NORMAL
        assert task.created_at is not None
        assert task.started_at is None
        assert task.completed_at is None
        assert task.result is None
        assert task.error is None
        assert task.parent_task_id is None
        assert task.task_id == task.id
        assert task.retry_count == 0
        assert task.max_retries == 3
        loaded = await manager.get_with_children(task.id)
        assert loaded.steps == []

    async def test_create_accepts_every_optional_field(self, manager: TaskManager) -> None:
        parent = await manager.create("parent")
        project = uuid.uuid4()
        conversation = uuid.uuid4()

        task = await manager.create(
            "child work",
            priority=TaskPriority.URGENT,
            parent_task_id=parent.id,
            project_id=project,
            conversation_id=conversation,
            scheduled_for=T1,
            max_retries=1,
        )

        assert task.priority is TaskPriority.URGENT
        assert task.parent_task_id == parent.id
        assert task.project_id == project
        assert task.conversation_id == conversation
        assert task.scheduled_for == T1
        assert task.max_retries == 1

    @pytest.mark.parametrize("goal", ["", "   ", "\t\n"])
    async def test_a_task_needs_a_goal(self, manager: TaskManager, goal: str) -> None:
        with pytest.raises(InvalidInputError):
            await manager.create(goal)

    async def test_priority_must_be_an_enum_member(self, manager: TaskManager) -> None:
        with pytest.raises(InvalidInputError) as exc:
            await manager.create("x", priority="urgent")  # type: ignore[arg-type]
        assert "expected" in exc.value.details

    async def test_negative_max_retries_is_refused(self, manager: TaskManager) -> None:
        with pytest.raises(InvalidInputError):
            await manager.create("x", max_retries=-1)

    async def test_max_retries_must_be_a_whole_number(self, manager: TaskManager) -> None:
        with pytest.raises(InvalidInputError):
            await manager.create("x", max_retries=True)

    async def test_a_naive_schedule_is_refused(self, manager: TaskManager) -> None:
        with pytest.raises(InvalidInputError):
            await manager.create("x", scheduled_for=T1.replace(tzinfo=None))

    async def test_a_missing_parent_is_404(self, manager: TaskManager) -> None:
        with pytest.raises(TaskNotFoundError):
            await manager.create("x", parent_task_id=uuid.uuid4())

    async def test_a_parent_accepts_the_uuid_as_a_string(self, manager: TaskManager) -> None:
        parent = await manager.create("parent")
        child = await manager.create("child", parent_task_id=str(parent.id))
        assert child.parent_task_id == parent.id


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


class TestReads:
    async def test_an_unknown_task_is_404(self, manager: TaskManager) -> None:
        with pytest.raises(TaskNotFoundError) as exc:
            await manager.get(uuid.uuid4())
        assert exc.value.code is ErrorCode.TASK_NOT_FOUND

    async def test_a_malformed_id_is_a_422_not_a_404(self, manager: TaskManager) -> None:
        # Not "not found": a row was never asked for — the input is wrong,
        # and a 404 would send the caller looking for data instead of at
        # their own typo.
        with pytest.raises(InvalidInputError):
            await manager.get("not-a-uuid")

    async def test_children_read_back_and_an_unknown_parent_404s(
        self, manager: TaskManager
    ) -> None:
        parent = await manager.create("parent")
        first = await manager.create("first", parent_task_id=parent.id)
        second = await manager.create("second", parent_task_id=parent.id)

        children = await manager.list_children(parent.id)
        assert {child.id for child in children} == {first.id, second.id}

        with pytest.raises(TaskNotFoundError):
            await manager.list_children(uuid.uuid4())

    async def test_list_by_status_filters(self, manager: TaskManager) -> None:
        queued = await manager.create("queued one")
        await manager.transition(queued.id, TaskStatus.QUEUED)
        pending_a = await manager.create("pending a")
        pending_b = await manager.create("pending b")

        assert {t.id for t in await manager.list_by_status(TaskStatus.PENDING)} == {
            pending_a.id,
            pending_b.id,
        }
        assert [t.id for t in await manager.list_by_status(TaskStatus.QUEUED)] == [queued.id]

    async def test_list_by_status_rejects_a_bare_string(self, manager: TaskManager) -> None:
        with pytest.raises(InvalidInputError):
            await manager.list_by_status("pending")  # type: ignore[arg-type]

    async def test_list_for_project_scopes_to_that_project(self, manager: TaskManager) -> None:
        mine = uuid.uuid4()
        other = uuid.uuid4()
        a = await manager.create("a", project_id=mine)
        await manager.create("b", project_id=other)

        assert [t.id for t in await manager.list_for_project(mine)] == [a.id]

    async def test_list_ready_sees_only_queued_work(self, manager: TaskManager) -> None:
        # The claim view under the machine: PENDING has not been admitted
        # and RUNNING already has, so neither belongs to a worker choosing
        # its next task.
        waiting = await manager.create("waiting")
        await manager.transition(waiting.id, TaskStatus.QUEUED)
        await manager.create("not yet queued")
        running = await admitted(manager, goal="already running")

        ready = await manager.list_ready()
        assert [t.id for t in ready] == [waiting.id]
        assert running.status is TaskStatus.RUNNING

    async def test_list_steps_of_an_unknown_task_is_404(self, manager: TaskManager) -> None:
        with pytest.raises(TaskNotFoundError):
            await manager.list_steps(uuid.uuid4())


# --------------------------------------------------------------------------
# Steps (the §18 `steps` field)
# --------------------------------------------------------------------------


class TestSteps:
    async def test_steps_append_positions_and_read_back_in_order(
        self, manager: TaskManager
    ) -> None:
        task = await manager.create("graph me")
        await manager.add_step(task.id, name="research")
        await manager.add_step(task.id, name="implement")

        steps = await manager.list_steps(task.id)
        assert [s.name for s in steps] == ["research", "implement"]
        assert [s.position for s in steps] == [0, 1]

        loaded = await manager.get_with_children(task.id)
        assert [s.name for s in loaded.steps] == ["research", "implement"]

    async def test_a_step_can_claim_a_later_position(self, manager: TaskManager) -> None:
        task = await manager.create("graph me")
        step = await manager.add_step(task.id, name="last", position=5)
        assert step.position == 5

    async def test_a_blank_step_name_is_refused(self, manager: TaskManager) -> None:
        task = await manager.create("graph me")
        with pytest.raises(InvalidInputError):
            await manager.add_step(task.id, name="   ")

    async def test_a_negative_step_position_is_refused(self, manager: TaskManager) -> None:
        task = await manager.create("graph me")
        with pytest.raises(InvalidInputError):
            await manager.add_step(task.id, name="s", position=-1)

    async def test_a_non_integer_step_position_is_refused(self, manager: TaskManager) -> None:
        task = await manager.create("graph me")
        with pytest.raises(InvalidInputError):
            await manager.add_step(task.id, name="s", position="1")  # type: ignore[arg-type]

    async def test_dependencies_must_be_uuids(self, manager: TaskManager) -> None:
        task = await manager.create("graph me")
        with pytest.raises(InvalidInputError):
            await manager.add_step(task.id, name="s", depends_on=["not-a-uuid"])

    async def test_dependencies_are_stored_canonically(self, manager: TaskManager) -> None:
        # The graph executor compares these against str(step.id), so a
        # mixed-case spelling must not become a dependency on "another" step.
        task = await manager.create("graph me")
        sibling = uuid.uuid4()
        step = await manager.add_step(task.id, name="s", depends_on=[str(sibling).upper()])
        assert step.depends_on == [str(sibling)]

    async def test_add_step_to_an_unknown_task_is_404(self, manager: TaskManager) -> None:
        with pytest.raises(TaskNotFoundError):
            await manager.add_step(uuid.uuid4(), name="s")

    async def test_an_unserialisable_input_payload_is_refused(self, manager: TaskManager) -> None:
        task = await manager.create("graph me")
        with pytest.raises(InvalidInputError):
            await manager.add_step(task.id, name="s", input_payload={"when": T0})


# --------------------------------------------------------------------------
# Transition
# --------------------------------------------------------------------------


class TestTransition:
    async def test_pending_cannot_jump_to_running(self, manager: TaskManager) -> None:
        task = await manager.create("the work")

        with pytest.raises(ConflictError) as exc:
            await manager.transition(task.id, TaskStatus.RUNNING)
        assert exc.value.details["current"] == "pending"
        assert exc.value.details["legal_targets"] == [
            "blocked",
            "cancelled",
            "failed",
            "paused",
            "queued",
        ]

        await manager.transition(task.id, TaskStatus.QUEUED)
        started = await manager.transition(task.id, TaskStatus.RUNNING)
        assert started.status is TaskStatus.RUNNING

    async def test_started_at_is_stamped_once(self, manager: TaskManager) -> None:
        task = await manager.create("the work")
        await manager.transition(task.id, TaskStatus.QUEUED, now=T0)
        running = await manager.transition(task.id, TaskStatus.RUNNING, now=T1)
        assert running.started_at == T1

        # Crash and re-admit: the second run must not erase when work began.
        await manager.transition(task.id, TaskStatus.QUEUED, now=T2)
        readmitted = await manager.transition(task.id, TaskStatus.RUNNING, now=T2)
        assert readmitted.started_at == T1

    async def test_completion_records_result_and_verification(self, manager: TaskManager) -> None:
        task = await admitted(manager)

        done = await manager.transition(
            task.id,
            TaskStatus.COMPLETED,
            result={"answer": 42},
            verification=VerificationOutcome.SUCCESS,
            now=T2,
        )

        assert done.status is TaskStatus.COMPLETED
        assert done.completed_at == T2
        assert done.result == {"answer": 42}
        assert done.verification is VerificationOutcome.SUCCESS

    async def test_the_result_is_copied_not_aliased(self, manager: TaskManager) -> None:
        task = await admitted(manager)
        payload = {"items": [1]}

        await manager.transition(task.id, TaskStatus.COMPLETED, result=payload)
        payload["items"].append(2)

        stored = await manager.get(task.id)
        assert stored.result == {"items": [1]}

    async def test_a_failure_must_say_why(self, manager: TaskManager) -> None:
        task = await admitted(manager)
        with pytest.raises(InvalidInputError):
            await manager.transition(task.id, TaskStatus.FAILED)

    async def test_a_failure_records_a_bounded_error(self, manager: TaskManager) -> None:
        task = await admitted(manager)
        failed = await manager.transition(task.id, TaskStatus.FAILED, error="x" * 5000, now=T2)
        assert len(failed.error or "") == 4000
        assert failed.completed_at == T2

    async def test_cancellation_stamps_completed_at_without_an_error(
        self, manager: TaskManager
    ) -> None:
        task = await admitted(manager)
        cancelled = await manager.transition(task.id, TaskStatus.CANCELLED, now=T2)
        assert cancelled.completed_at == T2
        assert cancelled.error is None
        assert cancelled.result is None

    @pytest.mark.parametrize(
        ("target", "kwargs"),
        [
            (TaskStatus.COMPLETED, {"error": "boom"}),
            (TaskStatus.FAILED, {"result": {"ok": True}}),
            (TaskStatus.FAILED, {"verification": VerificationOutcome.SUCCESS}),
            (TaskStatus.CANCELLED, {"result": {}}),
            (TaskStatus.QUEUED, {"error": "boom"}),
        ],
    )
    async def test_a_payload_for_the_wrong_outcome_is_refused(
        self,
        manager: TaskManager,
        target: TaskStatus,
        kwargs: dict[str, object],
    ) -> None:
        task = await admitted(manager)
        with pytest.raises(InvalidInputError):
            await manager.transition(task.id, target, **kwargs)  # type: ignore[arg-type]

    async def test_a_finished_task_does_not_move(self, manager: TaskManager) -> None:
        task = await admitted(manager)
        await manager.transition(task.id, TaskStatus.COMPLETED, now=T2)

        with pytest.raises(ConflictError) as exc:
            await manager.transition(task.id, TaskStatus.QUEUED)
        assert exc.value.details["legal_targets"] == []

    async def test_a_self_transition_is_refused(self, manager: TaskManager) -> None:
        task = await manager.create("the work")
        await manager.transition(task.id, TaskStatus.QUEUED)
        with pytest.raises(ConflictError):
            await manager.transition(task.id, TaskStatus.QUEUED)

    async def test_leaving_a_finished_attempt_clears_it(self, manager: TaskManager) -> None:
        task = await admitted(manager)
        await manager.transition(task.id, TaskStatus.FAILED, error="boom", now=T2)

        replanned = await manager.transition(task.id, TaskStatus.PENDING, now=T2)

        assert replanned.error is None
        assert replanned.result is None
        assert replanned.verification is None
        assert replanned.completed_at is None
        assert replanned.started_at == T1
        assert replanned.retry_count == 0

    async def test_transition_of_an_unknown_task_is_404(self, manager: TaskManager) -> None:
        with pytest.raises(TaskNotFoundError):
            await manager.transition(uuid.uuid4(), TaskStatus.QUEUED)

    async def test_status_must_be_an_enum_member(self, manager: TaskManager) -> None:
        task = await manager.create("the work")
        with pytest.raises(InvalidInputError):
            await manager.transition(task.id, "running")  # type: ignore[arg-type]

    async def test_a_naive_now_is_refused(self, manager: TaskManager) -> None:
        task = await manager.create("the work")
        await manager.transition(task.id, TaskStatus.QUEUED)
        with pytest.raises(InvalidInputError):
            await manager.transition(task.id, TaskStatus.RUNNING, now=T0.replace(tzinfo=None))

    async def test_verification_must_be_an_enum_member(self, manager: TaskManager) -> None:
        task = await admitted(manager)
        with pytest.raises(InvalidInputError):
            await manager.transition(
                task.id,
                TaskStatus.COMPLETED,
                verification="success",  # type: ignore[arg-type]
            )


# --------------------------------------------------------------------------
# Reprioritise
# --------------------------------------------------------------------------


class TestReprioritise:
    async def test_a_live_task_may_be_reprioritised_both_ways(self, manager: TaskManager) -> None:
        task = await manager.create("the work")
        # Both calls return the one session object (identity map), so each
        # assertion has to happen before the next write mutates it.
        boosted = await manager.reprioritise(task.id, TaskPriority.URGENT)
        assert boosted.priority is TaskPriority.URGENT
        demoted = await manager.reprioritise(task.id, TaskPriority.LOW)
        assert demoted.priority is TaskPriority.LOW
        assert demoted is boosted

    async def test_a_finished_task_is_not_reprioritisable(self, manager: TaskManager) -> None:
        task = await admitted(manager)
        await manager.transition(task.id, TaskStatus.COMPLETED, now=T2)

        with pytest.raises(ConflictError) as exc:
            await manager.reprioritise(task.id, TaskPriority.HIGH)
        assert exc.value.details["current_status"] == "completed"

    async def test_reprioritise_rejects_a_bare_string(self, manager: TaskManager) -> None:
        task = await manager.create("the work")
        with pytest.raises(InvalidInputError):
            await manager.reprioritise(task.id, "high")  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Retry
# --------------------------------------------------------------------------


class TestRetry:
    async def test_retry_requeues_and_counts_the_attempt(self, manager: TaskManager) -> None:
        task = await admitted(manager)
        await manager.transition(task.id, TaskStatus.FAILED, error="boom", now=T2)

        retried = await manager.retry(task.id, now=T2)

        assert retried.status is TaskStatus.QUEUED
        assert retried.retry_count == 1
        assert retried.error is None
        assert retried.completed_at is None
        assert retried.started_at == T1

    async def test_retry_stops_at_the_bound(self, manager: TaskManager) -> None:
        task = await manager.create("flaky", max_retries=1)
        await manager.transition(task.id, TaskStatus.QUEUED)
        await manager.transition(task.id, TaskStatus.RUNNING)
        await manager.transition(task.id, TaskStatus.FAILED, error="first")

        assert (await manager.retry(task.id)).retry_count == 1

        await manager.transition(task.id, TaskStatus.RUNNING)
        await manager.transition(task.id, TaskStatus.FAILED, error="second")

        with pytest.raises(ConflictError) as exc:
            await manager.retry(task.id)
        assert exc.value.details["retry_count"] == 1
        assert exc.value.details["max_retries"] == 1

    async def test_retry_of_a_finished_task_is_refused_before_the_bound(
        self, manager: TaskManager
    ) -> None:
        # retry_count is 0 of 3, so the refusal proves legality is checked
        # before the bound — a COMPLETED task does not retry, full stop.
        task = await admitted(manager)
        await manager.transition(task.id, TaskStatus.COMPLETED, now=T2)

        with pytest.raises(ConflictError) as exc:
            await manager.retry(task.id)
        assert exc.value.details["current"] == "completed"

    async def test_retry_of_an_unknown_task_is_404(self, manager: TaskManager) -> None:
        with pytest.raises(TaskNotFoundError):
            await manager.retry(uuid.uuid4())


# --------------------------------------------------------------------------
# Delete
# --------------------------------------------------------------------------


class TestDelete:
    async def test_a_finished_task_can_be_deleted(self, manager: TaskManager) -> None:
        task = await admitted(manager)
        await manager.transition(task.id, TaskStatus.COMPLETED, now=T2)

        await manager.delete(task.id)

        with pytest.raises(TaskNotFoundError):
            await manager.get(task.id)

    async def test_a_live_task_cannot_be_deleted(self, manager: TaskManager) -> None:
        task = await manager.create("the work")

        with pytest.raises(ConflictError) as exc:
            await manager.delete(task.id)
        assert exc.value.details["status"] == "pending"

        still_there = await manager.get(task.id)
        assert still_there.status is TaskStatus.PENDING

    async def test_deleting_an_unknown_task_is_404(self, manager: TaskManager) -> None:
        with pytest.raises(TaskNotFoundError):
            await manager.delete(uuid.uuid4())
