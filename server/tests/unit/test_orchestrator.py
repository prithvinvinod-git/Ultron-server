"""Orchestrator tests (T049).

The orchestrator is the *joint* between stages that already have their own
tested contracts (T047 router, T048 planner, T039/T041 tasks, T043/T044 agents),
so these tests do not re-prove the stages in isolation. They pin the things that
exist only *because* the stages are composed: that a request is routed then
planned then (maybe) materialised and executed in §66.4's order; that a selector
returning ``None`` stops the run with routing and plan but no task; that one
agent is spawned for the whole task, shares the task's row id, and is walked
through its own doors; that the agent's terminal status follows the task's
outcome; that a failure records a FAILED agent and a cancellation records a
CANCELLED one; and that the only event this module invents is ``PLAN_CREATED``
(everything else is the executor's ``TASK_*`` or the manager's ``AGENT_*``).

Runs against SQLite in-memory through the same savepoint fixtures as the task
and agent manager suites: an orchestrated run is a sequence of *rows* and
*events*, and a fake repository could not catch a status that never persisted or
an event published twice.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import FrozenInstanceError
from typing import Any, ClassVar

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import Agent
from app.agents.registry import AgentRegistry
from app.config import DatabaseSettings
from app.core.errors import AgentError, AgentTypeNotFoundError, InvalidInputError
from app.core.orchestrator import (
    OrchestrationRequest,
    OrchestrationResult,
    Orchestrator,
)
from app.core.planner import Plan, Planner, PlanStep, PlanStrategy
from app.core.router import (
    Intent,
    IntentRouter,
    RouteRule,
    Routing,
    keyword_matcher,
)
from app.database.models import Agent as AgentRow, AgentStatus, TaskPriority, TaskStatus
from app.database.session import Base, create_engine, dispose
from app.events.bus import EventBus, EventEnvelope
from app.events.types import EventType
from app.tasks.manager import TaskManager

SQLITE_URL = "sqlite+aiosqlite://"


class RecordingAgent(Agent):
    """A worker that records each step context it is handed, and echoes it back."""

    agent_type = "mock.recording"
    description = "Records every step context it is handed (orchestrator tests)."

    instances: ClassVar[list[RecordingAgent]] = []

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls: list[dict[str, Any]] = []
        RecordingAgent.instances.append(self)

    async def run(self, context: Mapping[str, Any]) -> Any:
        self.calls.append(dict(context))
        step = context.get("step", {})
        return {"echo": step.get("name")}


class FailingAgent(Agent):
    """A worker that always fails with a typed agent error."""

    agent_type = "mock.failing"
    description = "Always fails (orchestrator tests)."

    async def run(self, context: Mapping[str, Any]) -> Any:
        raise AgentError(str(self.agent_id), "deterministic failure")


class GatedAgent(Agent):
    """A worker that blocks until released, so a test can cancel it mid-run."""

    agent_type = "mock.gated"
    description = "Blocks until released (orchestrator tests)."

    started: ClassVar[asyncio.Event]
    released: ClassVar[asyncio.Event]

    async def run(self, context: Mapping[str, Any]) -> Any:
        GatedAgent.started.set()
        await GatedAgent.released.wait()
        return {"ok": True}


GatedAgent.started = asyncio.Event()
GatedAgent.released = asyncio.Event()


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


# --------------------------------------------------------------------------
# Fixtures and collaborators
# --------------------------------------------------------------------------


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
def registry() -> AgentRegistry:
    RecordingAgent.instances.clear()
    reg = AgentRegistry()
    reg.register(RecordingAgent)
    reg.register(FailingAgent)
    reg.register(GatedAgent)
    return reg


@pytest.fixture
def bus() -> RecordingBus:
    return RecordingBus()


def make_router() -> IntentRouter:
    """A task-or-conversation router: keywords route to TASK, else CONVERSATION."""
    router = IntentRouter()
    router.register(
        RouteRule(
            name="task",
            intent=Intent.TASK,
            handler="task.runner",
            matches=keyword_matcher("build", "research", "make"),
            priority=10,
        )
    )
    router.register(
        RouteRule(
            name="conversation",
            intent=Intent.CONVERSATION,
            handler="conversation.reply",
            priority=1000,
        )
    )
    return router


def make_planner() -> Planner:
    """A catch-all strategy: every goal becomes a gather → write chain."""
    planner = Planner()
    planner.register(
        PlanStrategy(
            name="two-step",
            decompose=lambda request: (
                PlanStep(key="gather", name="gather", description="collect the inputs"),
                PlanStep(key="write", name="write", depends_on=("gather",)),
            ),
        )
    )
    return planner


def make_orchestrator(
    db: AsyncSession,
    registry: AgentRegistry,
    bus: EventBus,
    **kwargs: Any,
) -> Orchestrator:
    """Build the unit under test with the shared collaborators."""
    return Orchestrator(
        db,
        router=make_router(),
        planner=make_planner(),
        agents=registry,
        events=bus,
        **kwargs,
    )


def types_of(bus: RecordingBus) -> list[str]:
    return [envelope.event_type for envelope in bus.sent]


async def fetch_agent(db: AsyncSession, agent_id: uuid.UUID) -> AgentRow | None:
    return await db.get(AgentRow, agent_id)


# --------------------------------------------------------------------------
# OrchestrationRequest — the input guard
# --------------------------------------------------------------------------


class TestRequest:
    def test_minimal_request_defaults(self) -> None:
        request = OrchestrationRequest(goal="build the thing")
        assert request.context == {}
        assert request.priority is TaskPriority.NORMAL
        assert request.project_id is None
        assert request.conversation_id is None

    def test_rejects_empty_goal(self) -> None:
        with pytest.raises(InvalidInputError):
            OrchestrationRequest(goal="")

    def test_rejects_blank_goal(self) -> None:
        with pytest.raises(InvalidInputError):
            OrchestrationRequest(goal="   ")

    def test_rejects_non_mapping_context(self) -> None:
        with pytest.raises(InvalidInputError):
            OrchestrationRequest(goal="build", context=["x"])  # type: ignore[arg-type]

    def test_frozen(self) -> None:
        request = OrchestrationRequest(goal="build")
        with pytest.raises(FrozenInstanceError):
            request.goal = "other"  # type: ignore[misc]


# --------------------------------------------------------------------------
# OrchestrationResult — the reported shape
# --------------------------------------------------------------------------


class TestResult:
    def _routing_and_plan(self) -> tuple[Routing, Plan]:
        routing = Routing(
            intent=Intent.TASK,
            handler="task.runner",
            rule="task",
            matched=True,
            reason="why",
        )
        plan = Plan(goal="build", steps=(PlanStep(key="s", name="do"),))
        return routing, plan

    def test_no_task_result_reports_routing_and_plan_only(self) -> None:
        routing, plan = self._routing_and_plan()
        result = OrchestrationResult(routing=routing, plan=plan)
        assert result.executed is False
        assert result.intent is Intent.TASK
        assert result.status is None
        assert result.result is None
        assert result.error is None
        assert result.agent_id is None
        assert result.agent_type is None

    async def test_result_reads_a_completed_task(self, db: AsyncSession) -> None:
        routing, plan = self._routing_and_plan()
        manager = TaskManager(db)
        task = await manager.create("build")
        await manager.transition(task.id, TaskStatus.QUEUED)
        await manager.transition(task.id, TaskStatus.RUNNING)
        task = await manager.transition(task.id, TaskStatus.COMPLETED, result={"ok": True})

        result = OrchestrationResult(
            routing=routing,
            plan=plan,
            task=task,
            agent_id=uuid.uuid4(),
            agent_type="mock.recording",
        )
        assert result.executed is True
        assert result.status is TaskStatus.COMPLETED
        assert result.result == {"ok": True}


# --------------------------------------------------------------------------
# Construction guards and selection
# --------------------------------------------------------------------------


class TestConstruction:
    def test_non_callable_selector_is_refused(
        self, db: AsyncSession, registry: AgentRegistry, bus: RecordingBus
    ) -> None:
        with pytest.raises(TypeError):
            make_orchestrator(db, registry, bus, selector=object())

    def test_blank_agent_type_is_refused(
        self, db: AsyncSession, registry: AgentRegistry, bus: RecordingBus
    ) -> None:
        with pytest.raises(InvalidInputError):
            make_orchestrator(db, registry, bus, agent_type="   ")

    def test_repr_names_the_active_choices(
        self, db: AsyncSession, registry: AgentRegistry, bus: RecordingBus
    ) -> None:
        orchestrator = make_orchestrator(db, registry, bus, agent_type="mock.recording")
        assert "agent_type='mock.recording'" in repr(orchestrator)
        assert "selector=False" in repr(orchestrator)


class TestSelection:
    async def test_default_agent_type_is_spawned(
        self, db: AsyncSession, registry: AgentRegistry, bus: RecordingBus
    ) -> None:
        orchestrator = make_orchestrator(db, registry, bus, agent_type="mock.recording")
        result = await orchestrator.run(OrchestrationRequest(goal="build the thing"))
        assert result.executed is True
        assert result.agent_type == "mock.recording"

    async def test_selector_overrides_the_default(
        self, db: AsyncSession, registry: AgentRegistry, bus: RecordingBus
    ) -> None:
        def selector(request: OrchestrationRequest, routing: Routing, plan: Plan) -> str | None:
            return "mock.recording"

        orchestrator = make_orchestrator(
            db, registry, bus, agent_type="mock.recording", selector=selector
        )
        result = await orchestrator.run(OrchestrationRequest(goal="build the thing"))
        assert result.agent_type == "mock.recording"

    async def test_selector_none_stops_before_any_task(
        self, db: AsyncSession, registry: AgentRegistry, bus: RecordingBus
    ) -> None:
        def selector(request: OrchestrationRequest, routing: Routing, plan: Plan) -> str | None:
            return None

        orchestrator = make_orchestrator(
            db, registry, bus, agent_type="mock.recording", selector=selector
        )
        result = await orchestrator.run(OrchestrationRequest(goal="build the thing"))

        assert result.executed is False
        assert result.task is None
        assert result.intent is Intent.TASK
        assert [step.key for step in result.plan.steps] == ["gather", "write"]
        # The plan stage still announced itself; no task exists to announce.
        assert EventType.PLAN_CREATED in types_of(bus)
        assert not [t for t in types_of(bus) if t.startswith("TASK_")]

    async def test_selector_sees_the_request_routing_and_plan(
        self, db: AsyncSession, registry: AgentRegistry, bus: RecordingBus
    ) -> None:
        seen: list[tuple[OrchestrationRequest, Routing, Plan]] = []

        def selector(request: OrchestrationRequest, routing: Routing, plan: Plan) -> str | None:
            seen.append((request, routing, plan))
            return "mock.recording"

        orchestrator = make_orchestrator(db, registry, bus, selector=selector)
        request = OrchestrationRequest(goal="build the thing")
        await orchestrator.run(request)

        assert len(seen) == 1
        got_request, got_routing, got_plan = seen[0]
        assert got_request is request
        assert got_routing.intent is Intent.TASK
        assert got_plan.goal == request.goal

    async def test_selector_returning_blank_is_refused(
        self, db: AsyncSession, registry: AgentRegistry, bus: RecordingBus
    ) -> None:
        def selector(request: OrchestrationRequest, routing: Routing, plan: Plan) -> str | None:
            return "   "

        orchestrator = make_orchestrator(db, registry, bus, selector=selector)
        with pytest.raises(InvalidInputError):
            await orchestrator.run(OrchestrationRequest(goal="build the thing"))

    async def test_selector_returning_a_non_string_is_refused(
        self, db: AsyncSession, registry: AgentRegistry, bus: RecordingBus
    ) -> None:
        def selector(request: OrchestrationRequest, routing: Routing, plan: Plan) -> str | None:
            return 123  # type: ignore[return-value]

        orchestrator = make_orchestrator(db, registry, bus, selector=selector)
        with pytest.raises(InvalidInputError):
            await orchestrator.run(OrchestrationRequest(goal="build the thing"))

    async def test_unknown_agent_type_is_a_404(
        self, db: AsyncSession, registry: AgentRegistry, bus: RecordingBus
    ) -> None:
        orchestrator = make_orchestrator(db, registry, bus, agent_type="ghost")
        with pytest.raises(AgentTypeNotFoundError):
            await orchestrator.run(OrchestrationRequest(goal="build the thing"))

    async def test_non_request_argument_is_a_type_error(
        self, db: AsyncSession, registry: AgentRegistry, bus: RecordingBus
    ) -> None:
        orchestrator = make_orchestrator(db, registry, bus, agent_type="mock.recording")
        with pytest.raises(TypeError):
            await orchestrator.run("build the thing")  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# The pipeline — the happy path
# --------------------------------------------------------------------------


class TestPipeline:
    async def test_plans_creates_a_task_and_executes_it(
        self, db: AsyncSession, registry: AgentRegistry, bus: RecordingBus
    ) -> None:
        orchestrator = make_orchestrator(db, registry, bus, agent_type="mock.recording")
        result = await orchestrator.run(OrchestrationRequest(goal="build the thing"))

        assert result.executed is True
        assert result.task is not None
        assert TaskStatus(result.task.status) is TaskStatus.COMPLETED
        assert result.task.goal == "build the thing"
        assert result.intent is Intent.TASK

    async def test_one_agent_runs_every_step_in_order(
        self, db: AsyncSession, registry: AgentRegistry, bus: RecordingBus
    ) -> None:
        orchestrator = make_orchestrator(db, registry, bus, agent_type="mock.recording")
        await orchestrator.run(OrchestrationRequest(goal="build the thing"))

        assert len(RecordingAgent.instances) == 1
        agent = RecordingAgent.instances[0]
        assert [call["step"]["name"] for call in agent.calls] == ["gather", "write"]

    async def test_step_context_carries_task_step_and_request_context(
        self, db: AsyncSession, registry: AgentRegistry, bus: RecordingBus
    ) -> None:
        orchestrator = make_orchestrator(db, registry, bus, agent_type="mock.recording")
        request = OrchestrationRequest(goal="build the thing", context={"tone": "terse"})
        result = await orchestrator.run(request)

        assert result.task is not None
        first = RecordingAgent.instances[0].calls[0]
        assert first["task_id"] == str(result.task.id)
        assert first["goal"] == "build the thing"
        assert first["step"]["name"] == "gather"
        assert first["input"] is None
        assert first["context"] == {"tone": "terse"}

    async def test_agent_shares_the_task_id_and_ends_completed(
        self, db: AsyncSession, registry: AgentRegistry, bus: RecordingBus
    ) -> None:
        orchestrator = make_orchestrator(db, registry, bus, agent_type="mock.recording")
        result = await orchestrator.run(OrchestrationRequest(goal="build the thing"))

        assert result.task is not None
        assert result.agent_id is not None
        row = await fetch_agent(db, result.agent_id)
        assert row is not None
        assert row.task_id == result.task.id
        assert AgentStatus(row.status) is AgentStatus.COMPLETED

    async def test_step_rows_bridge_plan_dependencies(
        self, db: AsyncSession, registry: AgentRegistry, bus: RecordingBus
    ) -> None:
        orchestrator = make_orchestrator(db, registry, bus, agent_type="mock.recording")
        result = await orchestrator.run(OrchestrationRequest(goal="build the thing"))

        assert result.task is not None
        steps = await TaskManager(db).list_steps(result.task.id)
        assert [step.name for step in steps] == ["gather", "write"]
        assert steps[1].depends_on == [str(steps[0].id)]

    async def test_publishes_the_task_and_agent_lifecycles(
        self, db: AsyncSession, registry: AgentRegistry, bus: RecordingBus
    ) -> None:
        orchestrator = make_orchestrator(db, registry, bus, agent_type="mock.recording")
        await orchestrator.run(OrchestrationRequest(goal="build the thing"))

        emitted = set(types_of(bus))
        assert EventType.PLAN_CREATED in emitted
        assert EventType.TASK_CREATED in emitted
        assert EventType.TASK_STARTED in emitted
        assert EventType.TASK_PROGRESS in emitted
        assert EventType.TASK_COMPLETED in emitted
        assert EventType.AGENT_CREATED in emitted
        assert EventType.AGENT_STARTED in emitted
        assert EventType.AGENT_COMPLETED in emitted

    async def test_plan_event_names_the_goal_and_steps(
        self, db: AsyncSession, registry: AgentRegistry, bus: RecordingBus
    ) -> None:
        orchestrator = make_orchestrator(db, registry, bus, agent_type="mock.recording")
        await orchestrator.run(OrchestrationRequest(goal="build the thing"))

        plan_event = next(
            envelope for envelope in bus.sent if envelope.event_type == EventType.PLAN_CREATED
        )
        assert plan_event.payload["goal"] == "build the thing"
        assert plan_event.payload["intent"] == Intent.TASK.value
        assert [step["key"] for step in plan_event.payload["steps"]] == ["gather", "write"]


# --------------------------------------------------------------------------
# Failure — a failed task fails the agent
# --------------------------------------------------------------------------


class TestFailure:
    async def test_failing_step_fails_the_task_and_the_agent(
        self, db: AsyncSession, registry: AgentRegistry, bus: RecordingBus
    ) -> None:
        orchestrator = make_orchestrator(db, registry, bus, agent_type="mock.failing")
        result = await orchestrator.run(OrchestrationRequest(goal="build the thing"))

        assert result.task is not None
        assert TaskStatus(result.task.status) is TaskStatus.FAILED
        assert result.error is not None
        assert "failed" in result.error

        assert result.agent_id is not None
        row = await fetch_agent(db, result.agent_id)
        assert row is not None
        assert AgentStatus(row.status) is AgentStatus.FAILED

        emitted = set(types_of(bus))
        assert EventType.TASK_FAILED in emitted
        assert EventType.AGENT_FAILED in emitted
        assert EventType.AGENT_COMPLETED not in emitted


# --------------------------------------------------------------------------
# Cancellation — a cancelled run cancels the agent
# --------------------------------------------------------------------------


class TestCancellation:
    async def test_cancelling_the_run_cancels_the_agent(
        self, db: AsyncSession, registry: AgentRegistry, bus: RecordingBus
    ) -> None:
        GatedAgent.started = asyncio.Event()
        GatedAgent.released = asyncio.Event()

        orchestrator = make_orchestrator(db, registry, bus, agent_type="mock.gated")
        run = asyncio.create_task(orchestrator.run(OrchestrationRequest(goal="build the thing")))

        await asyncio.wait_for(GatedAgent.started.wait(), timeout=5)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

        rows = (await db.execute(select(AgentRow))).scalars().all()
        assert len(rows) == 1
        assert AgentStatus(rows[0].status) is AgentStatus.CANCELLED
        assert EventType.AGENT_STOPPED in set(types_of(bus))
