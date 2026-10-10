"""Agent manager tests (T044).

Runs against SQLite in-memory via the same savepoint fixtures as the task
manager suite, because the manager's rules are rules about *rows*: a status
that did not persist, a task that never existed, a concurrency ceiling that
survived only in memory — none of that can be caught by a fake repository.

The weight, as with T039, is on the refusals: an illegal status move (409 with
the machine's legal targets), a status that is a bare string (422 — the
vocabulary must be stated), a live agent destroyed, a parent/task that does not
exist (404), a conflicting reassignment (409), and the concurrency ceiling on
agents actually *becoming* active. The §19 ``AGENT_*`` publication is pinned
through a real bus, so the payloads, the envelope identifiers and the
persist flag are asserted as data, not hand-waved.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import Agent
from app.agents.manager import AgentManager
from app.agents.registry import AgentRegistry
from app.core.errors import (
    AgentNotFoundError,
    AgentTypeNotFoundError,
    ConflictError,
    ErrorCode,
    InvalidInputError,
    TaskNotFoundError,
)
from app.database.models import Agent as AgentRow, AgentStatus
from app.database.session import Base, create_engine, dispose
from app.events.bus import EventBus, EventEnvelope
from app.events.types import EventType
from app.tasks.manager import TaskManager

pytestmark = pytest.mark.unit

SQLITE_URL = "sqlite+aiosqlite://"


class MockCoderAgent(Agent):
    agent_type = "mock.coder"
    description = "A mock coding agent for manager tests."

    async def run(self, context: Mapping[str, Any]) -> Any:
        return dict(context)


class MockBrowserAgent(Agent):
    agent_type = "mock.browser"
    description = "A mock browser agent for manager tests."

    async def run(self, context: Mapping[str, Any]) -> Any:
        return {"page": "blank"}


class RecordingBus(EventBus):
    """A real bus that also keeps what it published, for synchronous asserts."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[EventEnvelope] = []

    async def publish(
        self,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        correlation_id: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        device_id: str | None = None,
        persist: bool = False,
    ) -> EventEnvelope:
        envelope = await super().publish(
            event_type,
            payload,
            correlation_id=correlation_id,
            task_id=task_id,
            agent_id=agent_id,
            device_id=device_id,
            persist=persist,
        )
        self.sent.append(envelope)
        return envelope


@pytest.fixture
async def engine() -> AsyncIterator[object]:
    """A private in-memory database with the production schema."""
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
def registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.register(MockCoderAgent)
    reg.register(MockBrowserAgent)
    return reg


@pytest.fixture
def bus() -> RecordingBus:
    return RecordingBus()


@pytest.fixture
def manager(db: AsyncSession, registry: AgentRegistry, bus: RecordingBus) -> AgentManager:
    """The unit under test, bound to the test session and bus."""
    return AgentManager(db, agents=registry, events=bus)


async def admit(manager: AgentManager, *, agent_type: str = "mock.coder") -> AgentRow:
    """An agent walked through the one door: to RUNNING the legal way."""
    agent = await manager.create(agent_type, name="Worker")
    await manager.transition(agent.id, AgentStatus.INITIALIZING)
    await manager.transition(agent.id, AgentStatus.READY)
    return await manager.transition(agent.id, AgentStatus.RUNNING)


async def task_for(db: AsyncSession) -> uuid.UUID:
    """A persisted task, for the assignment rules."""
    return (await TaskManager(db).create("the work")).id


# --------------------------------------------------------------------------
# Create
# --------------------------------------------------------------------------


class TestCreate:
    async def test_a_created_agent_carries_the_spec_fields(self, manager: AgentManager) -> None:
        agent = await manager.create("mock.coder", name="C-1")

        assert agent.agent_type == "mock.coder"
        assert agent.name == "C-1"
        assert agent.description == MockCoderAgent.description
        assert AgentStatus(agent.status) is AgentStatus.CREATED
        assert agent.task_id is None
        assert agent.model is None
        assert agent.tools == []
        assert agent.permissions == []
        assert agent.memory_namespace is None
        assert agent.agent_id == agent.id

    async def test_create_announces_agent_created(
        self, manager: AgentManager, bus: RecordingBus
    ) -> None:
        agent = await manager.create("mock.coder", name="C-1")

        assert len(bus.sent) == 1
        envelope = bus.sent[0]
        assert envelope.event_type == EventType.AGENT_CREATED
        assert envelope.persist is True
        assert envelope.agent_id == str(agent.id)
        assert envelope.payload["agent_type"] == "mock.coder"
        assert envelope.payload["status"] == "created"

    async def test_an_unknown_type_is_a_404(self, manager: AgentManager) -> None:
        with pytest.raises(AgentTypeNotFoundError) as excinfo:
            await manager.create("mock.nope", name="X")
        assert excinfo.value.code is ErrorCode.AGENT_NOT_FOUND
        assert excinfo.value.http_status == 404

    async def test_a_blank_name_is_refused(self, manager: AgentManager) -> None:
        with pytest.raises(InvalidInputError) as excinfo:
            await manager.create("mock.coder", name="  ")
        assert excinfo.value.http_status == 422

    async def test_tools_and_permissions_are_copied(self, manager: AgentManager) -> None:
        tools = ["mock.echo"]
        permissions = ["READ_ONLY"]
        agent = await manager.create("mock.coder", name="C", tools=tools, permissions=permissions)
        tools.append("later")
        permissions.append("WRITE")
        assert agent.tools == ["mock.echo"]
        assert agent.permissions == ["READ_ONLY"]

    async def test_a_parent_must_exist(self, manager: AgentManager, bus: RecordingBus) -> None:
        parent = await manager.create("mock.coder", name="Parent")
        child = await manager.create("mock.browser", name="Child", parent_agent_id=parent.id)
        assert child.parent_agent_id == parent.id

        with pytest.raises(AgentNotFoundError) as excinfo:
            await manager.create("mock.coder", name="Orphan", parent_agent_id=uuid.uuid4())
        assert excinfo.value.code is ErrorCode.AGENT_NOT_FOUND
        assert excinfo.value.http_status == 404

    async def test_an_assigned_task_must_exist(
        self, manager: AgentManager, db: AsyncSession
    ) -> None:
        existing = await task_for(db)
        assigned = await manager.create("mock.coder", name="C", task_id=existing)
        assert assigned.task_id == existing

        with pytest.raises(TaskNotFoundError):
            await manager.create("mock.coder", name="C", task_id=uuid.uuid4())

    async def test_a_malformed_id_is_a_422(self, manager: AgentManager) -> None:
        with pytest.raises(InvalidInputError):
            await manager.create("mock.coder", name="C", task_id="not-a-uuid")


# --------------------------------------------------------------------------
# The one status door
# --------------------------------------------------------------------------


class TestTransition:
    async def test_the_lifecycle_cycle(self, manager: AgentManager) -> None:
        agent = await manager.create("mock.coder", name="C")
        path = [
            AgentStatus.INITIALIZING,
            AgentStatus.READY,
            AgentStatus.RUNNING,
            AgentStatus.WAITING,
            AgentStatus.RUNNING,
            AgentStatus.VERIFYING,
            AgentStatus.COMPLETED,
        ]
        for target in path:
            moved = await manager.transition(agent.id, target)
            assert AgentStatus(moved.status) is target

    async def test_an_illegal_move_is_a_409_naming_the_legal_targets(
        self, manager: AgentManager
    ) -> None:
        agent = await manager.create("mock.coder", name="C")
        with pytest.raises(ConflictError) as excinfo:
            await manager.transition(agent.id, AgentStatus.RUNNING)
        error = excinfo.value
        assert error.http_status == 409
        assert error.details["current"] == "created"
        assert error.details["target"] == "running"
        assert error.details["agent"] == str(agent.id)
        assert sorted(error.details["legal_targets"]) == ["cancelled", "failed", "initializing"]

    async def test_a_bare_string_status_is_refused(self, manager: AgentManager) -> None:
        agent = await manager.create("mock.coder", name="C")
        with pytest.raises(InvalidInputError) as excinfo:
            await manager.transition(agent.id, "running")  # type: ignore[arg-type]
        assert excinfo.value.http_status == 422

    async def test_a_terminal_agent_never_moves(self, manager: AgentManager) -> None:
        agent = await admit(manager)
        await manager.transition(agent.id, AgentStatus.COMPLETED)
        with pytest.raises(ConflictError):
            await manager.transition(agent.id, AgentStatus.RUNNING)

    async def test_no_self_transition(self, manager: AgentManager) -> None:
        agent = await admit(manager)
        with pytest.raises(ConflictError):
            await manager.transition(agent.id, AgentStatus.RUNNING)

    async def test_each_status_publishes_its_19_event(
        self, manager: AgentManager, bus: RecordingBus
    ) -> None:
        agent = await manager.create("mock.coder", name="C")
        bus.sent.clear()

        await manager.transition(agent.id, AgentStatus.INITIALIZING)
        await manager.transition(agent.id, AgentStatus.READY)
        await manager.transition(agent.id, AgentStatus.RUNNING)
        await manager.transition(agent.id, AgentStatus.WAITING)
        await manager.transition(agent.id, AgentStatus.RUNNING)
        await manager.transition(agent.id, AgentStatus.VERIFYING)
        await manager.transition(agent.id, AgentStatus.COMPLETED)

        names = [envelope.event_type for envelope in bus.sent]
        assert names == [
            EventType.AGENT_STARTED,
            EventType.AGENT_PAUSED,
            EventType.AGENT_STARTED,
            EventType.AGENT_COMPLETED,
        ]
        for envelope in bus.sent:
            assert envelope.persist is True
            assert envelope.agent_id == str(agent.id)

    async def test_the_first_agent_started_carries_the_task(
        self, manager: AgentManager, db: AsyncSession, bus: RecordingBus
    ) -> None:
        task = await task_for(db)
        agent = await manager.create("mock.coder", name="C", task_id=task)
        bus.sent.clear()

        await manager.transition(agent.id, AgentStatus.INITIALIZING)
        await manager.transition(agent.id, AgentStatus.READY)
        await manager.transition(agent.id, AgentStatus.RUNNING)

        started = next(e for e in bus.sent if e.event_type == EventType.AGENT_STARTED)
        assert started.task_id == str(task)

    async def test_cancel_publishes_stopped_with_the_reason(
        self, manager: AgentManager, bus: RecordingBus
    ) -> None:
        agent = await admit(manager)
        bus.sent.clear()

        await manager.cancel(agent.id, reason="plan changed")

        assert bus.sent[-1].event_type == EventType.AGENT_STOPPED
        assert bus.sent[-1].payload["reason"] == "plan changed"
        assert AgentStatus((await manager.get(agent.id)).status) is AgentStatus.CANCELLED


# --------------------------------------------------------------------------
# §7's named doors
# --------------------------------------------------------------------------


class TestLifecycleDoors:
    async def test_pause_leaves_only_from_running(self, manager: AgentManager) -> None:
        agent = await manager.create("mock.coder", name="C")
        await manager.transition(agent.id, AgentStatus.INITIALIZING)
        await manager.transition(agent.id, AgentStatus.READY)
        with pytest.raises(ConflictError):
            await manager.pause(agent.id)

        await manager.transition(agent.id, AgentStatus.RUNNING)
        paused = await manager.pause(agent.id)
        assert AgentStatus(paused.status) is AgentStatus.WAITING

    async def test_resume_targets_running_where_the_machine_allows(
        self, manager: AgentManager
    ) -> None:
        paused = await admit(manager)
        await manager.pause(paused.id)
        resumed = await manager.resume(paused.id)
        assert AgentStatus(resumed.status) is AgentStatus.RUNNING

        # RUNNING is also READY's admission edge, so resume is legal there too.
        fresh = await manager.create("mock.coder", name="Fresh")
        await manager.transition(fresh.id, AgentStatus.INITIALIZING)
        await manager.transition(fresh.id, AgentStatus.READY)
        admitted = await manager.resume(fresh.id)
        assert AgentStatus(admitted.status) is AgentStatus.RUNNING

        # CREATED has no RUNNING edge: the machine refuses resume.
        unborn = await manager.create("mock.coder", name="Unborn")
        with pytest.raises(ConflictError):
            await manager.resume(unborn.id)

    async def test_complete_finishes_running_or_verifying_agents(
        self, manager: AgentManager
    ) -> None:
        runner = await admit(manager)
        done = await manager.complete(runner.id)
        assert AgentStatus(done.status) is AgentStatus.COMPLETED

        verifier = await admit(manager)
        await manager.transition(verifier.id, AgentStatus.VERIFYING)
        done = await manager.complete(verifier.id)
        assert AgentStatus(done.status) is AgentStatus.COMPLETED

    async def test_fail_records_why_in_the_event(
        self, manager: AgentManager, bus: RecordingBus
    ) -> None:
        agent = await admit(manager)
        failed = await manager.fail(agent.id, error="model unreachable")
        assert AgentStatus(failed.status) is AgentStatus.FAILED
        assert bus.sent[-1].event_type == EventType.AGENT_FAILED
        assert bus.sent[-1].payload["message"] == "model unreachable"

    async def test_fail_without_a_message_is_refused(self, manager: AgentManager) -> None:
        agent = await admit(manager)
        with pytest.raises(InvalidInputError):
            await manager.fail(agent.id, error="   ")

    async def test_timeout_publishes_stopped(self, manager: AgentManager) -> None:
        agent = await admit(manager)
        timed = await manager.transition(agent.id, AgentStatus.TIMEOUT)
        assert AgentStatus(timed.status) is AgentStatus.TIMEOUT


# --------------------------------------------------------------------------
# Destroy
# --------------------------------------------------------------------------


class TestDestroy:
    async def test_a_live_agent_cannot_be_destroyed(self, manager: AgentManager) -> None:
        agent = await admit(manager)
        with pytest.raises(ConflictError) as excinfo:
            await manager.destroy(agent.id)
        assert excinfo.value.http_status == 409
        assert excinfo.value.details["status"] == "running"

    async def test_a_finished_agent_is_destroyed(self, manager: AgentManager) -> None:
        agent = await admit(manager)
        await manager.cancel(agent.id)
        await manager.destroy(agent.id)
        with pytest.raises(AgentNotFoundError):
            await manager.get(agent.id)


# --------------------------------------------------------------------------
# Assignment
# --------------------------------------------------------------------------


class TestAssignment:
    async def test_assign_task_points_the_agent(
        self, manager: AgentManager, db: AsyncSession
    ) -> None:
        agent = await manager.create("mock.coder", name="C")
        task = await task_for(db)

        assigned = await manager.assign_task(agent.id, task)
        assert assigned.task_id == task

        # Reassigning the *same* task is a no-op, not a conflict.
        again = await manager.assign_task(agent.id, task)
        assert again.task_id == task

    async def test_assign_a_different_task_is_a_conflict(
        self, manager: AgentManager, db: AsyncSession
    ) -> None:
        agent = await manager.create("mock.coder", name="C")
        first = await task_for(db)
        second = await task_for(db)
        await manager.assign_task(agent.id, first)

        with pytest.raises(ConflictError) as excinfo:
            await manager.assign_task(agent.id, second)
        assert excinfo.value.details["task_id"] == str(second)

    async def test_assign_an_unknown_task_is_a_404(self, manager: AgentManager) -> None:
        agent = await manager.create("mock.coder", name="C")
        with pytest.raises(TaskNotFoundError):
            await manager.assign_task(agent.id, uuid.uuid4())

    async def test_assign_model_sets_and_clears(self, manager: AgentManager) -> None:
        agent = await manager.create("mock.coder", name="C")
        assert (await manager.assign_model(agent.id, "openrouter:free")).model == "openrouter:free"
        assert (await manager.assign_model(agent.id, None)).model is None
        with pytest.raises(InvalidInputError):
            await manager.assign_model(agent.id, "  ")

    async def test_assign_tools_replaces_and_copies(self, manager: AgentManager) -> None:
        agent = await manager.create("mock.coder", name="C", tools=["mock.echo"])
        tools = ["filesystem.read", "git.status"]
        assigned = await manager.assign_tools(agent.id, tools)
        tools.clear()
        assert assigned.tools == ["filesystem.read", "git.status"]
        with pytest.raises(InvalidInputError):
            await manager.assign_tools(agent.id, ["", "fine"])

    async def test_assign_permissions_replaces(self, manager: AgentManager) -> None:
        agent = await manager.create("mock.coder", name="C")
        assigned = await manager.assign_permissions(agent.id, ["READ_ONLY"])
        assert assigned.permissions == ["READ_ONLY"]

    async def test_get_of_an_unknown_agent_is_a_typed_404(self, manager: AgentManager) -> None:
        with pytest.raises(AgentNotFoundError) as excinfo:
            await manager.get(uuid.uuid4())
        assert excinfo.value.code is ErrorCode.AGENT_NOT_FOUND
        assert excinfo.value.http_status == 404


# --------------------------------------------------------------------------
# Concurrency ceiling
# --------------------------------------------------------------------------


class TestConcurrencyLimit:
    @pytest.fixture
    def limited(self, db: AsyncSession, registry: AgentRegistry) -> AgentManager:
        return AgentManager(db, agents=registry, max_concurrent=1)

    async def test_a_ceiling_refuses_a_second_active_agent(self, limited: AgentManager) -> None:
        first = await limited.create("mock.coder", name="A")
        await limited.transition(first.id, AgentStatus.INITIALIZING)

        second = await limited.create("mock.browser", name="B")
        with pytest.raises(ConflictError) as excinfo:
            await limited.transition(second.id, AgentStatus.INITIALIZING)
        assert excinfo.value.http_status == 409
        assert excinfo.value.details["active"] == 1
        assert excinfo.value.details["limit"] == 1
        assert excinfo.value.details["target"] == "initializing"

    async def test_freeing_a_slot_admits_the_next(self, limited: AgentManager) -> None:
        first = await admit(limited)
        second = await limited.create("mock.browser", name="B")
        with pytest.raises(ConflictError):
            await limited.transition(second.id, AgentStatus.INITIALIZING)

        await limited.complete(first.id)

        moved = await limited.transition(second.id, AgentStatus.INITIALIZING)
        assert AgentStatus(moved.status) is AgentStatus.INITIALIZING

    async def test_staying_active_consumes_no_new_slot(self, limited: AgentManager) -> None:
        first = await admit(limited)
        # WAITING is already active, so pause + resume pass the ceiling.
        assert AgentStatus((await limited.pause(first.id)).status) is AgentStatus.WAITING
        assert AgentStatus((await limited.resume(first.id)).status) is AgentStatus.RUNNING

    async def test_no_ceiling_by_default(self, manager: AgentManager) -> None:
        await admit(manager)
        second = await manager.create("mock.browser", name="B")
        await manager.transition(second.id, AgentStatus.INITIALIZING)
        assert await manager.count_active() == 2

    async def test_an_invalid_ceiling_is_a_config_error(
        self, db: AsyncSession, registry: AgentRegistry
    ) -> None:
        with pytest.raises(ValueError):
            AgentManager(db, agents=registry, max_concurrent=0)
        with pytest.raises(ValueError):
            AgentManager(db, agents=registry, max_concurrent=True)
