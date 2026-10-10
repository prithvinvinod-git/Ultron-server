"""Task executor tests (T041).

The executor is pure orchestration over three already-tested pieces — the
manager (T039), the state machine (T038) and the step DAG (T040) — so the
weight here is on the seams: steps run in dependency order, status writes go
only through the manager and the step repository, `TASK_*` events fire at the
right moments with the right payload, a failing step fails the task and names
it, and a crash is recoverable without losing completed work.

Runs against SQLite in-memory via the same savepoint fixtures T015/T039 use,
because the executor's rules are rules about rows: a status that did not
persist, an attempt that did not survive, a step reset that only changed the
in-memory object. One test (`test_a_reload_coerces_enum_statuses`) pins the
subtlety that makes the graph work at all after a real restart: a freshly
loaded status is a plain string, and the executor coerces it at the boundary.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, InvalidInputError
from app.database.models import StepStatus, Task, TaskPriority, TaskStatus, TaskStep
from app.database.repositories import TaskStepRepository
from app.database.session import Base, create_engine, dispose
from app.events.bus import EventBus, EventEnvelope
from app.events.types import EventType
from app.tasks.executor import TaskExecutor
from app.tasks.manager import TaskManager

pytestmark = pytest.mark.unit

SQLITE_URL = "sqlite+aiosqlite://"

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 1, 1, 12, 5, 0, tzinfo=UTC)
T2 = datetime(2026, 1, 1, 12, 10, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #
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


class FakeRunner:
    """A step runner that records its calls and can fail one step."""

    def __init__(
        self,
        *,
        fail_on: str | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.calls: list[str] = []
        self._fail_on = fail_on
        self._error = error or RuntimeError("boom")

    async def __call__(self, step: TaskStep) -> Mapping[str, Any] | None:
        self.calls.append(step.name)
        if step.name == self._fail_on:
            raise self._error
        return {"echo": step.name}


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
def manager(db: AsyncSession) -> TaskManager:
    return TaskManager(db)


@pytest.fixture
def bus() -> RecordingBus:
    return RecordingBus()


def types_sent(bus: RecordingBus) -> list[EventType]:
    """The event types published, in order."""
    return [EventType(envelope.event_type) for envelope in bus.sent]


async def make_task(
    manager: TaskManager,
    steps: list[tuple[str, list[str]]],
    *,
    goal: str = "the work",
) -> tuple[Task, dict[str, str]]:
    """Create a task and its steps; return it with a name → id map.

    Each ``(name, dependencies)`` pair's dependencies must already have been
    added, so ids can be referenced as they are produced.
    """
    task = await manager.create(goal)
    ids: dict[str, str] = {}
    for name, dependencies in steps:
        step = await manager.add_step(task.id, name=name, depends_on=dependencies)
        ids[name] = str(step.id)
    return task, ids


async def admit(manager: TaskManager, task_id: Any, *, now: datetime = T1) -> Task:
    """Walk a task through the queue to RUNNING, as a worker would."""
    await manager.transition(task_id, TaskStatus.QUEUED, now=T0)
    return await manager.transition(task_id, TaskStatus.RUNNING, now=now)


# --------------------------------------------------------------------------- #
# Create
# --------------------------------------------------------------------------- #
class TestCreate:
    async def test_create_publishes_task_created(self, db: AsyncSession, bus: RecordingBus) -> None:
        executor = TaskExecutor(db, events=bus, run_step=FakeRunner())

        task = await executor.create("ship it", priority=TaskPriority.HIGH)

        assert types_sent(bus) == [EventType.TASK_CREATED]
        envelope = bus.sent[0]
        assert envelope.persist is True
        assert envelope.task_id == str(task.id)
        assert envelope.payload["goal"] == "ship it"
        assert envelope.payload["status"] == "pending"
        assert envelope.payload["priority"] == "high"
        assert envelope.payload["parent_task_id"] is None

    async def test_create_refuses_an_empty_goal(self, db: AsyncSession, bus: RecordingBus) -> None:
        executor = TaskExecutor(db, events=bus, run_step=FakeRunner())

        with pytest.raises(InvalidInputError):
            await executor.create("   ")


# --------------------------------------------------------------------------- #
# Execute
# --------------------------------------------------------------------------- #
class TestExecute:
    async def test_runs_every_step_in_order(
        self, db: AsyncSession, bus: RecordingBus, manager: TaskManager
    ) -> None:
        task, _ = await make_task(manager, [("a", []), ("b", []), ("c", [])])
        runner = FakeRunner()
        executor = TaskExecutor(db, events=bus, run_step=runner)

        finished = await executor.execute(task.id, now=T1)

        assert finished.status is TaskStatus.COMPLETED
        assert runner.calls == ["a", "b", "c"]
        assert finished.result == {
            "steps": {
                "0": {"echo": "a"},
                "1": {"echo": "b"},
                "2": {"echo": "c"},
            }
        }
        stored = await TaskStepRepository(db).list_for_task(task.id)
        assert all(StepStatus(step.status) is StepStatus.COMPLETED for step in stored)
        assert all(step.attempt == 1 for step in stored)

    async def test_steps_wait_for_their_dependencies(
        self, db: AsyncSession, bus: RecordingBus, manager: TaskManager
    ) -> None:
        task = await manager.create("diamond")
        a = await manager.add_step(task.id, name="a")
        b = await manager.add_step(task.id, name="b", depends_on=[str(a.id)])
        c = await manager.add_step(task.id, name="c", depends_on=[str(a.id)])
        await manager.add_step(task.id, name="d", depends_on=[str(b.id), str(c.id)])
        runner = FakeRunner()
        executor = TaskExecutor(db, events=bus, run_step=runner)

        finished = await executor.execute(task.id, now=T1)

        assert finished.status is TaskStatus.COMPLETED
        assert runner.calls[0] == "a"
        assert runner.calls[-1] == "d"
        assert set(runner.calls[1:3]) == {"b", "c"}

    async def test_publishes_the_lifecycle_events(
        self, db: AsyncSession, bus: RecordingBus, manager: TaskManager
    ) -> None:
        task, _ = await make_task(manager, [("a", [])])
        executor = TaskExecutor(db, events=bus, run_step=FakeRunner())

        await executor.execute(task.id, now=T1)

        sent = types_sent(bus)
        assert sent[0] is EventType.TASK_STARTED
        assert sent[-1] is EventType.TASK_COMPLETED
        assert sent.count(EventType.TASK_PROGRESS) == 2  # running, completed

        started = bus.sent[0]
        assert started.persist is True
        assert started.payload["status"] == "running"
        assert started.payload["goal"] == "the work"

        progress = [e for e in bus.sent if e.event_type == EventType.TASK_PROGRESS]
        assert progress[0].payload["status"] == "running"
        assert progress[0].payload["attempt"] == 1
        assert progress[1].payload["status"] == "completed"
        assert all(e.persist is False for e in progress)

    async def test_a_task_with_no_steps_completes(
        self, db: AsyncSession, bus: RecordingBus, manager: TaskManager
    ) -> None:
        task = await manager.create("nothing to do")
        runner = FakeRunner()
        executor = TaskExecutor(db, events=bus, run_step=runner)

        finished = await executor.execute(task.id, now=T1)

        assert finished.status is TaskStatus.COMPLETED
        assert runner.calls == []
        assert finished.result == {"steps": {}}

    async def test_a_reload_coerces_enum_statuses(
        self, db: AsyncSession, bus: RecordingBus, manager: TaskManager
    ) -> None:
        task, _ = await make_task(manager, [("a", []), ("b", [])])
        task_id = task.id
        # A restart loads rows fresh, where status columns come back as plain
        # strings; the executor must coerce them or the graph sees nothing
        # runnable and "completes" without running anything.
        db.expire_all()
        runner = FakeRunner()
        executor = TaskExecutor(db, events=bus, run_step=runner)

        finished = await executor.execute(task_id, now=T1)

        assert runner.calls == ["a", "b"]
        assert finished.status is TaskStatus.COMPLETED

    async def test_an_already_completed_step_is_skipped_on_resume(
        self, db: AsyncSession, bus: RecordingBus, manager: TaskManager
    ) -> None:
        task, _ = await make_task(manager, [("a", []), ("b", [])])
        task_id = task.id
        steps = {step.name: step for step in await manager.list_steps(task_id)}
        await TaskStepRepository(db).mark_completed(
            steps["a"].id, output_payload={"echo": "a"}, now=T0
        )
        db.expire_all()
        runner = FakeRunner()
        executor = TaskExecutor(db, events=bus, run_step=runner)

        finished = await executor.execute(task_id, now=T1)

        assert runner.calls == ["b"]
        assert finished.result == {"steps": {"0": {"echo": "a"}, "1": {"echo": "b"}}}

    async def test_refuses_a_task_outside_the_lifecycle(
        self, db: AsyncSession, bus: RecordingBus, manager: TaskManager
    ) -> None:
        task = await manager.create("blocked work")
        await manager.transition(task.id, TaskStatus.BLOCKED, now=T0)
        executor = TaskExecutor(db, events=bus, run_step=FakeRunner())

        with pytest.raises(ConflictError):
            await executor.execute(task.id, now=T1)

    async def test_a_closed_bus_does_not_fail_execution(
        self, db: AsyncSession, bus: RecordingBus, manager: TaskManager
    ) -> None:
        task, _ = await make_task(manager, [("a", [])])
        runner = FakeRunner()
        executor = TaskExecutor(db, events=bus, run_step=runner)
        bus.close()

        finished = await executor.execute(task.id, now=T1)

        assert finished.status is TaskStatus.COMPLETED
        assert runner.calls == ["a"]
        assert bus.sent == []


# --------------------------------------------------------------------------- #
# Failure
# --------------------------------------------------------------------------- #
class TestFailure:
    async def test_a_failing_step_fails_the_task_and_names_it(
        self, db: AsyncSession, bus: RecordingBus, manager: TaskManager
    ) -> None:
        task, ids = await make_task(manager, [("a", []), ("c", [])])
        # b depends on a; c is independent. A failure of `a` is fail-fast, so
        # neither b (blocked) nor c (independent) runs.
        await manager.add_step(task.id, name="b", depends_on=[ids["a"]])
        runner = FakeRunner(fail_on="a")
        executor = TaskExecutor(db, events=bus, run_step=runner)

        finished = await executor.execute(task.id, now=T1)

        assert finished.status is TaskStatus.FAILED
        assert "step 'a'" in (finished.error or "")
        assert "boom" in (finished.error or "")
        assert runner.calls == ["a"]

        last = bus.sent[-1]
        assert EventType(last.event_type) is EventType.TASK_FAILED
        assert last.persist is True
        assert last.payload["failed_step"]["name"] == "a"
        assert last.payload["error_code"] == "TASK_FAILED"

        stored = {s.name: s for s in await manager.list_steps(task.id)}
        assert StepStatus(stored["a"].status) is StepStatus.FAILED
        assert StepStatus(stored["b"].status) is StepStatus.PENDING
        assert StepStatus(stored["c"].status) is StepStatus.PENDING

    async def test_a_typed_error_carries_its_own_code(
        self, db: AsyncSession, bus: RecordingBus, manager: TaskManager
    ) -> None:
        task, _ = await make_task(manager, [("a", [])])
        runner = FakeRunner(fail_on="a", error=ConflictError("not allowed"))
        executor = TaskExecutor(db, events=bus, run_step=runner)

        finished = await executor.execute(task.id, now=T1)

        assert finished.status is TaskStatus.FAILED
        assert "ConflictError: not allowed" in (finished.error or "")
        assert bus.sent[-1].payload["error_code"] == "CONFLICT"


# --------------------------------------------------------------------------- #
# Recovery
# --------------------------------------------------------------------------- #
class TestRecover:
    async def test_requeues_running_tasks_and_resets_in_flight_steps(
        self, db: AsyncSession, bus: RecordingBus, manager: TaskManager
    ) -> None:
        task, _ = await make_task(manager, [("a", []), ("b", [])])
        task_id = task.id
        await admit(manager, task_id)
        steps = {s.name: s for s in await manager.list_steps(task_id)}
        await TaskStepRepository(db).mark_running(steps["a"].id, now=T1)
        db.expire_all()
        executor = TaskExecutor(db, events=bus, run_step=FakeRunner())

        recovered = await executor.recover(now=T2)

        assert [t.id for t in recovered] == [task_id]
        assert TaskStatus(recovered[0].status) is TaskStatus.QUEUED
        assert recovered[0].started_at is not None  # start time preserved

        stored = {s.name: s for s in await manager.list_steps(task_id)}
        assert StepStatus(stored["a"].status) is StepStatus.PENDING
        assert stored["a"].attempt == 1  # the interrupted try is kept
        assert StepStatus(stored["b"].status) is StepStatus.PENDING
        assert bus.sent == []  # no requeue event exists in §19

    async def test_leaves_completed_steps_completed(
        self, db: AsyncSession, bus: RecordingBus, manager: TaskManager
    ) -> None:
        task, _ = await make_task(manager, [("a", []), ("b", [])])
        task_id = task.id
        await admit(manager, task_id)
        repo = TaskStepRepository(db)
        steps = {s.name: s for s in await manager.list_steps(task_id)}
        await repo.mark_completed(steps["a"].id, now=T1)
        await repo.mark_running(steps["b"].id, now=T1)
        db.expire_all()
        executor = TaskExecutor(db, events=bus, run_step=FakeRunner())

        await executor.recover(now=T2)

        stored = {s.name: s for s in await manager.list_steps(task_id)}
        assert StepStatus(stored["a"].status) is StepStatus.COMPLETED
        assert StepStatus(stored["b"].status) is StepStatus.PENDING

    async def test_recovery_then_resume_reruns_the_interrupted_step(
        self, db: AsyncSession, bus: RecordingBus, manager: TaskManager
    ) -> None:
        task, _ = await make_task(manager, [("a", [])])
        task_id = task.id
        await admit(manager, task_id)
        steps = {s.name: s for s in await manager.list_steps(task_id)}
        await TaskStepRepository(db).mark_running(steps["a"].id, now=T1)
        db.expire_all()
        runner = FakeRunner()
        executor = TaskExecutor(db, events=bus, run_step=runner)

        await executor.recover(now=T2)
        finished = await executor.execute(task_id, now=T2)

        assert runner.calls == ["a"]
        assert finished.status is TaskStatus.COMPLETED
        stored = {s.name: s for s in await manager.list_steps(task_id)}
        assert stored["a"].attempt == 2  # the retried attempt


# --------------------------------------------------------------------------- #
# Schedules
# --------------------------------------------------------------------------- #
class TestTriggerDue:
    async def test_queues_due_pending_tasks_and_announces_them(
        self, db: AsyncSession, bus: RecordingBus, manager: TaskManager
    ) -> None:
        due = await manager.create("due", scheduled_for=T0)
        await manager.create("later", scheduled_for=T2)
        already = await manager.create("queued", scheduled_for=T0)
        await manager.transition(already.id, TaskStatus.QUEUED, now=T0)
        executor = TaskExecutor(db, events=bus, run_step=FakeRunner())

        triggered = await executor.trigger_due(now=T1)

        assert [t.id for t in triggered] == [due.id]
        assert TaskStatus(triggered[0].status) is TaskStatus.QUEUED
        assert types_sent(bus) == [EventType.SCHEDULE_TRIGGERED]
        assert bus.sent[0].payload["scheduled_for"] == T0.isoformat()
        assert bus.sent[0].persist is True
