"""Core executor tests (T050): the retry/replan policy over a live task.

The module under test is a *policy and a loop* around three already-tested
pieces — T041's `TaskExecutor`, T039's `TaskManager` and the T038 machine — so
these tests do not re-prove step execution. They pin the decisions that exist
only here: that a straight retry is preferred over a replan, that it resets the
failed steps so the *same* plan can actually re-run, that replanning walks the
machine's FAILED → PENDING door and announces the new plan, that both budgets
bound the loop, and that neither a missing replanner nor a raising one is
allowed to lose the failure.

Runs against SQLite in-memory through the same savepoint fixtures the task
executor and agent manager suites use: a retry is a sequence of *rows*, and a
fake repository could not catch a failed step that was never reset (the
executor would then falsely complete it) or a task left PENDING after a
replanner that raised.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DatabaseSettings
from app.core.errors import ConflictError, InvalidInputError
from app.core.executor import (
    CoreExecutor,
    ExecutionOutcome,
    ExecutionPolicy,
    RetryAction,
)
from app.core.planner import Plan, PlanStep
from app.database.models import StepStatus, Task, TaskStatus, TaskStep
from app.database.repositories import TaskStepRepository
from app.database.session import Base, create_engine, dispose
from app.events.bus import EventBus, EventEnvelope
from app.events.types import EventType
from app.tasks.manager import TaskManager

SQLITE_URL = "sqlite+aiosqlite://"


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


class ScriptedRunner:
    """A step runner that fails each named step a scripted number of times.

    ``fails`` maps a step name to how many attempts should raise before the step
    succeeds (a name absent from the map never fails); ``calls`` records every
    step name it ran, in order, so a test can prove exactly what a retry re-ran.
    """

    def __init__(
        self,
        fails: Mapping[str, int] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._fails = dict(fails or {})
        self._error = error or RuntimeError("boom")
        self.calls: list[str] = []

    async def __call__(self, step: TaskStep) -> Mapping[str, Any] | None:
        self.calls.append(step.name)
        remaining = self._fails.get(step.name, 0)
        if remaining > 0:
            self.fails[step.name] = remaining - 1
            raise self._error
        return {"echo": step.name}

    @property
    def fails(self) -> dict[str, int]:
        return self._fails


@pytest.fixture
async def engine() -> AsyncIterator[object]:
    """A private in-memory database with the production schema."""
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
def bus() -> RecordingBus:
    return RecordingBus()


def types_of(bus: RecordingBus) -> list[EventType]:
    return [EventType(envelope.event_type) for envelope in bus.sent]


async def make_task(
    db: AsyncSession,
    steps: list[tuple[str, list[str]]],
    *,
    goal: str = "the goal",
    max_retries: int = 3,
) -> tuple[Task, dict[str, uuid.UUID]]:
    """Create a task and its steps; return it with a name → step-id map."""
    manager = TaskManager(db)
    task = await manager.create(goal, max_retries=max_retries)
    ids: dict[str, uuid.UUID] = {}
    for name, dependencies in steps:
        step = await manager.add_step(task.id, name=name, depends_on=dependencies)
        assert step.id is not None
        ids[name] = step.id
    return task, ids


def make_executor(
    db: AsyncSession,
    bus: EventBus,
    run_step: Any,
    **kwargs: Any,
) -> CoreExecutor:
    """Build the unit under test with the shared collaborators."""
    return CoreExecutor(db, events=bus, run_step=run_step, **kwargs)


def _failed_task(*, retry_count: int, max_retries: int) -> Task:
    """An unpersisted FAILED row, for the pure policy tests."""
    return Task(
        goal="g",
        status=TaskStatus.FAILED,
        retry_count=retry_count,
        max_retries=max_retries,
    )


# --------------------------------------------------------------------------- #
# ExecutionPolicy — the pure decision
# --------------------------------------------------------------------------- #
class TestPolicy:
    def test_default_has_no_replan_budget(self) -> None:
        assert ExecutionPolicy().max_replans == 0

    def test_retry_is_preferred_while_the_row_has_budget(self) -> None:
        policy = ExecutionPolicy(max_replans=5)
        task = _failed_task(retry_count=0, max_retries=3)
        assert policy.decide(task, replans=0) is RetryAction.RETRY

    def test_replans_once_the_retry_budget_is_spent(self) -> None:
        policy = ExecutionPolicy(max_replans=1)
        task = _failed_task(retry_count=3, max_retries=3)
        assert policy.decide(task, replans=0) is RetryAction.REPLAN

    def test_stops_when_every_budget_is_spent(self) -> None:
        policy = ExecutionPolicy(max_replans=1)
        task = _failed_task(retry_count=3, max_retries=3)
        assert policy.decide(task, replans=1) is RetryAction.STOP

    def test_no_budget_stops_immediately(self) -> None:
        policy = ExecutionPolicy()
        task = _failed_task(retry_count=0, max_retries=0)
        assert policy.decide(task, replans=0) is RetryAction.STOP

    def test_negative_max_replans_is_refused(self) -> None:
        with pytest.raises(InvalidInputError):
            ExecutionPolicy(max_replans=-1)

    def test_bool_max_replans_is_refused(self) -> None:
        with pytest.raises(InvalidInputError):
            ExecutionPolicy(max_replans=True)

    def test_non_integer_max_replans_is_refused(self) -> None:
        with pytest.raises(InvalidInputError):
            ExecutionPolicy(max_replans=1.5)  # type: ignore[arg-type]

    def test_frozen(self) -> None:
        policy = ExecutionPolicy()
        with pytest.raises(FrozenInstanceError):
            policy.max_replans = 2  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# ExecutionOutcome — the reported shape
# --------------------------------------------------------------------------- #
class TestOutcome:
    def test_counts_describe_the_run(self) -> None:
        outcome = ExecutionOutcome(
            task=_failed_task(retry_count=2, max_retries=2),
            attempts=3,
            retries=1,
            replans=1,
        )
        assert outcome.attempts == outcome.retries + outcome.replans + 1
        assert outcome.status is TaskStatus.FAILED
        assert outcome.failed is True
        assert outcome.succeeded is False

    def test_frozen(self) -> None:
        outcome = ExecutionOutcome(
            task=_failed_task(retry_count=0, max_retries=0),
            attempts=1,
            retries=0,
            replans=0,
        )
        with pytest.raises(FrozenInstanceError):
            outcome.attempts = 2  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #
class TestConstruction:
    def test_non_callable_replanner_is_refused(self, db: AsyncSession, bus: RecordingBus) -> None:
        with pytest.raises(TypeError):
            make_executor(db, bus, ScriptedRunner(), replanner=object())

    def test_repr_names_the_policy_and_the_replanner(
        self, db: AsyncSession, bus: RecordingBus
    ) -> None:
        executor = make_executor(db, bus, ScriptedRunner(), policy=ExecutionPolicy(max_replans=2))
        assert "max_replans=2" in repr(executor)
        assert "replanner=False" in repr(executor)

    def test_default_policy_is_used_when_none_is_given(
        self, db: AsyncSession, bus: RecordingBus
    ) -> None:
        executor = make_executor(db, bus, ScriptedRunner())
        assert "max_replans=0" in repr(executor)


# --------------------------------------------------------------------------- #
# The run loop
# --------------------------------------------------------------------------- #
class TestRun:
    async def test_a_successful_run_needs_one_attempt(
        self, db: AsyncSession, bus: RecordingBus
    ) -> None:
        task, _ = await make_task(db, [("build", [])])
        runner = ScriptedRunner()
        outcome = await make_executor(db, bus, runner).run(task.id)

        assert outcome.succeeded is True
        assert outcome.status is TaskStatus.COMPLETED
        assert (outcome.attempts, outcome.retries, outcome.replans) == (1, 0, 0)
        assert outcome.task.result == {"steps": {"0": {"echo": "build"}}}
        assert runner.calls == ["build"]

    async def test_the_task_lifecycle_events_fire(
        self, db: AsyncSession, bus: RecordingBus
    ) -> None:
        task, _ = await make_task(db, [("build", [])])
        await make_executor(db, bus, ScriptedRunner()).run(task.id)

        sent = types_of(bus)
        # The runner operates on an already-materialised task, so it does not
        # create one: `TASK_CREATED` belongs to whoever materialised the plan.
        assert EventType.TASK_CREATED not in sent
        assert EventType.TASK_STARTED in sent
        assert EventType.TASK_COMPLETED in sent

    async def test_a_failure_without_budget_is_left_failed(
        self, db: AsyncSession, bus: RecordingBus
    ) -> None:
        task, _ = await make_task(db, [("build", [])], max_retries=0)
        runner = ScriptedRunner(fails={"build": 99})
        outcome = await make_executor(db, bus, runner).run(task.id)

        assert outcome.failed is True
        assert outcome.status is TaskStatus.FAILED
        assert (outcome.attempts, outcome.retries, outcome.replans) == (1, 0, 0)

    async def test_a_retry_reruns_the_same_plan_and_succeeds(
        self, db: AsyncSession, bus: RecordingBus
    ) -> None:
        task, _ = await make_task(db, [("build", [])], max_retries=3)
        runner = ScriptedRunner(fails={"build": 1})
        outcome = await make_executor(db, bus, runner).run(task.id)

        assert outcome.succeeded is True
        assert (outcome.attempts, outcome.retries, outcome.replans) == (2, 1, 0)
        assert runner.calls == ["build", "build"]
        # The failed step was reset (and its attempt counted), so it re-ran.
        steps = await TaskStepRepository(db).list_for_task(task.id)
        assert StepStatus(steps[0].status) is StepStatus.COMPLETED
        assert steps[0].attempt == 2

    async def test_a_retry_resets_only_the_failed_step(
        self, db: AsyncSession, bus: RecordingBus
    ) -> None:
        manager = TaskManager(db)
        task = await manager.create("chain", max_retries=2)
        first = await manager.add_step(task.id, name="first")
        await manager.add_step(task.id, name="second", depends_on=[str(first.id)])

        runner = ScriptedRunner(fails={"second": 1})
        outcome = await make_executor(db, bus, runner).run(task.id)

        assert outcome.succeeded is True
        assert (outcome.attempts, outcome.retries) == (2, 1)
        # `first` completed before the failure and was not reset/re-run.
        assert runner.calls == ["first", "second", "second"]
        by_name = {step.name: step for step in await TaskStepRepository(db).list_for_task(task.id)}
        assert by_name["first"].attempt == 1
        assert by_name["second"].attempt == 2

    async def test_the_retry_budget_is_bounded(self, db: AsyncSession, bus: RecordingBus) -> None:
        task, _ = await make_task(db, [("build", [])], max_retries=1)
        runner = ScriptedRunner(fails={"build": 99})
        outcome = await make_executor(db, bus, runner).run(task.id)

        assert outcome.failed is True
        assert (outcome.attempts, outcome.retries) == (2, 1)
        # One attempt plus exactly one retry — never more.
        assert runner.calls == ["build", "build"]

    async def test_a_replan_without_a_replanner_is_refused(
        self, db: AsyncSession, bus: RecordingBus
    ) -> None:
        task, _ = await make_task(db, [("build", [])], max_retries=0)
        runner = ScriptedRunner(fails={"build": 99})
        executor = make_executor(db, bus, runner, policy=ExecutionPolicy(max_replans=1))

        with pytest.raises(InvalidInputError):
            await executor.run(task.id)

        reloaded = await TaskManager(db).get(task.id)
        assert TaskStatus(reloaded.status) is TaskStatus.FAILED

    async def test_a_replan_reenters_pending_and_announces_a_new_plan(
        self, db: AsyncSession, bus: RecordingBus
    ) -> None:
        task, _ = await make_task(db, [("build", [])], max_retries=0)
        runner = ScriptedRunner(fails={"build": 99})
        seen: list[Task] = []

        async def replanner(failed: Task) -> Plan | None:
            seen.append(failed)
            return Plan(goal="the goal", steps=(PlanStep(key="retry", name="retry"),))

        executor = make_executor(
            db,
            bus,
            runner,
            policy=ExecutionPolicy(max_replans=1),
            replanner=replanner,
        )
        outcome = await executor.run(task.id)

        assert outcome.failed is True
        assert (outcome.attempts, outcome.retries, outcome.replans) == (2, 0, 1)
        # The callback saw the still-failed task, before the re-entry.
        assert len(seen) == 1
        assert TaskStatus(seen[0].status) is TaskStatus.FAILED
        assert EventType.PLAN_UPDATED in types_of(bus)

    async def test_a_replanner_returning_none_announces_nothing(
        self, db: AsyncSession, bus: RecordingBus
    ) -> None:
        task, _ = await make_task(db, [("build", [])], max_retries=0)
        runner = ScriptedRunner(fails={"build": 99})

        async def replanner(_: Task) -> Plan | None:
            return None

        executor = make_executor(
            db,
            bus,
            runner,
            policy=ExecutionPolicy(max_replans=1),
            replanner=replanner,
        )
        outcome = await executor.run(task.id)

        assert outcome.replans == 1
        assert EventType.PLAN_UPDATED not in types_of(bus)

    async def test_a_raising_replanner_leaves_the_task_failed(
        self, db: AsyncSession, bus: RecordingBus
    ) -> None:
        task, _ = await make_task(db, [("build", [])], max_retries=0)
        runner = ScriptedRunner(fails={"build": 99})

        async def exploding_replanner(failed: Task) -> Plan | None:
            raise RuntimeError("cannot replan")

        executor = make_executor(
            db,
            bus,
            runner,
            policy=ExecutionPolicy(max_replans=1),
            replanner=exploding_replanner,
        )
        with pytest.raises(RuntimeError):
            await executor.run(task.id)

        reloaded = await TaskManager(db).get(task.id)
        assert TaskStatus(reloaded.status) is TaskStatus.FAILED

    async def test_retrying_announces_a_second_start(
        self, db: AsyncSession, bus: RecordingBus
    ) -> None:
        task, _ = await make_task(db, [("build", [])], max_retries=3)
        runner = ScriptedRunner(fails={"build": 1})
        await make_executor(db, bus, runner).run(task.id)

        assert types_of(bus).count(EventType.TASK_STARTED) == 2

    async def test_an_already_terminal_task_raises_the_machines_conflict(
        self, db: AsyncSession, bus: RecordingBus
    ) -> None:
        task, _ = await make_task(db, [("build", [])])
        executor = make_executor(db, bus, ScriptedRunner())
        await executor.run(task.id)

        with pytest.raises(ConflictError):
            await executor.run(task.id)
