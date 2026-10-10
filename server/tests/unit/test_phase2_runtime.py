"""Phase 2 composition tests (T052).

The individual Phase 2 units already have their own suites: the event bus
(``test_event_bus.py``), the permission engine (``test_permissions.py``), the
tool and agent registries (``test_tool_registry.py`` / ``test_agent_registry.py``),
the step executor (``test_task_executor.py``), the task and agent lifecycles
(``test_task_manager.py`` / ``test_agent_manager.py``) and the orchestrator's own
contract (``test_orchestrator.py``). What none of those pin is the property the
Phase 2 gate actually names:

    multiple agents run concurrently; one agent failure does not affect others

That is a *composition* property — it only exists once several runs share one
process — so it is tested here, against the same real components (SQLite rows,
the real managers, the real executor, a recording bus) rather than doubles.
Each concurrent run gets its own in-memory database so the only thing the runs
share is the event loop: if two runs could not actually overlap, the rendezvous
barrier below would never release and the test would time out rather than pass.

A third test closes the loop the gate assumes: one request, end to end, from
routing through planning and a spawned agent to a persisted result.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, ClassVar

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.agents.base import Agent
from app.agents.registry import AgentRegistry
from app.config import DatabaseSettings
from app.core.errors import AgentError
from app.core.orchestrator import (
    OrchestrationRequest,
    OrchestrationResult,
    Orchestrator,
)
from app.core.planner import Planner, PlanStep, PlanStrategy
from app.core.router import Intent, IntentRouter, RouteRule
from app.database.models import Agent as AgentRow, AgentStatus, Task, TaskStatus, TaskStep
from app.database.session import (
    Base,
    create_engine,
    create_session_factory,
    dispose,
    read_session_scope,
    session_scope,
)
from app.events.bus import EventBus, EventEnvelope
from app.events.types import EventType

pytestmark = pytest.mark.unit

SQLITE_URL = "sqlite+aiosqlite://"


# --------------------------------------------------------------------------- #
# Workers
# --------------------------------------------------------------------------- #


class RendezvousAgent(Agent):
    """A worker that waits at a shared barrier before succeeding.

    The barrier is the concurrency probe: it only releases once *every* run's
    agent has arrived, so a sequential scheduler deadlocks and the test fails by
    timeout instead of passing for the wrong reason.
    """

    agent_type = "mock.rendezvous"
    description = "Waits at a shared barrier, then echoes its step (T052)."

    barrier: ClassVar[asyncio.Barrier | None] = None
    arrivals: ClassVar[list[str]] = []

    async def run(self, context: Mapping[str, Any]) -> Any:
        barrier = RendezvousAgent.barrier
        if barrier is not None:
            await barrier.wait()
        RendezvousAgent.arrivals.append(str(self.agent_id))
        step = context.get("step") or {}
        return {"agent": str(self.agent_id), "step": step.get("name")}


class RendezvousFailAgent(Agent):
    """Waits at the same barrier as the successes, then fails.

    Reaching the barrier proves the failure overlapped the other runs rather
    than running after them (which would make "did not affect others" trivial).
    """

    agent_type = "mock.rendezvous_fail"
    description = "Wait at the barrier, then fail (failure-isolation tests)."

    barrier: ClassVar[asyncio.Barrier | None] = None

    async def run(self, context: Mapping[str, Any]) -> Any:
        barrier = RendezvousFailAgent.barrier
        if barrier is not None:
            await barrier.wait()
        raise AgentError(str(self.agent_id), "deterministic failure")


class StepEchoAgent(Agent):
    """One agent that records every step context and echoes the step's name."""

    agent_type = "mock.step_echo"
    description = "Record each step context and echo the step name (E2E tests)."

    instances: ClassVar[list[StepEchoAgent]] = []

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls: list[dict[str, Any]] = []
        StepEchoAgent.instances.append(self)

    async def run(self, context: Mapping[str, Any]) -> Any:
        self.calls.append(dict(context))
        step = context.get("step") or {}
        return {"step": step.get("name")}


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


# --------------------------------------------------------------------------- #
# Collaborators and fixtures
# --------------------------------------------------------------------------- #


def _router() -> IntentRouter:
    """Every request is a task: the orchestrator only needs a classification."""
    router = IntentRouter()
    router.register(
        RouteRule(
            name="task",
            intent=Intent.TASK,
            handler="task.runner",
            matches=None,
        )
    )
    return router


def _two_step_planner() -> Planner:
    """A gather → write chain: the E2E test's dependency edge."""
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


def _single_step_planner() -> Planner:
    """One step per task, so a rendezvous barrier sees exactly one wait per agent."""
    planner = Planner()
    planner.register(
        PlanStrategy(
            name="one-step",
            decompose=lambda request: (PlanStep(key="only", name="only"),),
        )
    )
    return planner


@asynccontextmanager
async def _database() -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]]]:
    """A private in-memory database with the production schema, disposed after."""
    engine = create_engine(DatabaseSettings(url=SQLITE_URL))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield engine, create_session_factory(engine)
    finally:
        await dispose(engine)


def _planner_for(plan: str) -> Planner:
    return _two_step_planner() if plan == "two-step" else _single_step_planner()


async def _run_pipeline(
    factory: async_sessionmaker[AsyncSession],
    registry: AgentRegistry,
    request: OrchestrationRequest,
    *,
    agent_type: str,
    events: EventBus,
    plan: str = "two-step",
) -> OrchestrationResult:
    """One request through the real pipeline, committed by the caller's scope."""
    async with session_scope(factory) as session:
        orchestrator = Orchestrator(
            session,
            router=_router(),
            planner=_planner_for(plan),
            agents=registry,
            events=events,
            agent_type=agent_type,
        )
        return await orchestrator.run(request)


async def _isolated_run(
    registry: AgentRegistry,
    request: OrchestrationRequest,
    *,
    agent_type: str,
    plan: str = "two-step",
) -> OrchestrationResult:
    """A whole run against its own database, so concurrent runs do not contend."""
    async with _database() as (_, factory):
        return await _run_pipeline(
            factory,
            registry,
            request,
            agent_type=agent_type,
            events=RecordingBus(),
            plan=plan,
        )


def _types(bus: RecordingBus) -> list[str]:
    return [envelope.event_type for envelope in bus.sent]


# --------------------------------------------------------------------------- #
# The gate: concurrency and failure isolation
# --------------------------------------------------------------------------- #


async def test_multiple_agents_run_concurrently() -> None:
    """Three runs overlap: none can finish its step until all three have started.

    The barrier is the assertion. If the runs were serialised the first would
    wait forever for arrivals that cannot happen, and ``wait_for`` would fail
    the test on timeout instead of the test passing by accident.
    """
    total = 3
    RendezvousAgent.barrier = asyncio.Barrier(total)
    RendezvousAgent.arrivals = []

    registry = AgentRegistry()
    registry.register(RendezvousAgent)

    runs = [
        _isolated_run(
            registry,
            OrchestrationRequest(goal="build it"),
            agent_type="mock.rendezvous",
            plan="one-step",
        )
        for _ in range(total)
    ]
    results = await asyncio.wait_for(asyncio.gather(*runs), timeout=15)

    assert [result.status for result in results] == [TaskStatus.COMPLETED] * total
    assert len(RendezvousAgent.arrivals) == total


async def test_one_agent_failure_does_not_affect_others() -> None:
    """Three successes and one failure overlap; only the failing run is FAILED."""
    total = 4
    barrier = asyncio.Barrier(total)
    RendezvousAgent.barrier = barrier
    RendezvousFailAgent.barrier = barrier
    RendezvousAgent.arrivals = []

    registry = AgentRegistry()
    registry.register(RendezvousAgent)
    registry.register(RendezvousFailAgent)

    agent_types = ["mock.rendezvous", "mock.rendezvous", "mock.rendezvous", "mock.rendezvous_fail"]
    runs = [
        _isolated_run(
            registry,
            OrchestrationRequest(goal="build it"),
            agent_type=agent_type,
            plan="one-step",
        )
        for agent_type in agent_types
    ]
    results = await asyncio.wait_for(asyncio.gather(*runs), timeout=15)

    statuses = [result.status for result in results]
    assert statuses[:3] == [TaskStatus.COMPLETED] * 3
    assert statuses[3] is TaskStatus.FAILED
    assert results[3].error is not None

    # All four reached the barrier, so the failure was concurrent with the rest;
    # the three successes still recorded their arrival.
    assert len(RendezvousAgent.arrivals) == 3


# --------------------------------------------------------------------------- #
# End to end: request → result
# --------------------------------------------------------------------------- #


async def test_request_flows_to_a_persisted_result() -> None:
    """One request becomes rows and events: routing, plan, task, agent, result."""
    StepEchoAgent.instances.clear()
    registry = AgentRegistry()
    registry.register(StepEchoAgent)
    bus = RecordingBus()

    async with _database() as (_, factory):
        result = await _run_pipeline(
            factory,
            registry,
            OrchestrationRequest(goal="build the report", context={"tone": "terse"}),
            agent_type="mock.step_echo",
            events=bus,
        )
        assert result.task is not None and result.agent_id is not None
        task_id = result.task.id
        agent_id = result.agent_id
        # Read inside the read scope and keep plain values: a rolled-back
        # session expires its rows, so touching them afterwards would lazy-load
        # on a detached instance.
        async with read_session_scope(factory) as session:
            task = await session.get(Task, task_id)
            agent = await session.get(AgentRow, agent_id)
            steps = (
                (
                    await session.execute(
                        select(TaskStep)
                        .where(TaskStep.task_id == task_id)
                        .order_by(TaskStep.position)
                    )
                )
                .scalars()
                .all()
            )
            assert task is not None and agent is not None
            task_status = TaskStatus(task.status)
            agent_status = AgentStatus(agent.status)
            agent_task_id = agent.task_id
            step_names = [step.name for step in steps]
            second_depends_on = list(steps[1].depends_on)
            first_step_id = str(steps[0].id)

    assert result.executed is True
    assert result.intent is Intent.TASK
    assert result.status is TaskStatus.COMPLETED
    assert result.result == {"steps": {"0": {"step": "gather"}, "1": {"step": "write"}}}

    assert task_status is TaskStatus.COMPLETED
    assert agent_status is AgentStatus.COMPLETED
    assert agent_task_id == task_id
    assert step_names == ["gather", "write"]
    assert second_depends_on == [first_step_id]

    # The worker really saw the request: goal, nested context, and each step.
    calls = StepEchoAgent.instances[0].calls
    assert calls[0]["goal"] == "build the report"
    assert calls[0]["context"] == {"tone": "terse"}
    assert [call["step"]["name"] for call in calls] == ["gather", "write"]

    emitted = _types(bus)
    assert EventType.PLAN_CREATED in emitted
    for earlier, later in [
        (EventType.PLAN_CREATED, EventType.TASK_CREATED),
        (EventType.TASK_CREATED, EventType.TASK_STARTED),
        (EventType.TASK_STARTED, EventType.TASK_COMPLETED),
        (EventType.TASK_COMPLETED, EventType.AGENT_COMPLETED),
        (EventType.AGENT_CREATED, EventType.AGENT_STARTED),
    ]:
        assert emitted.index(earlier) < emitted.index(later)


async def test_concurrent_runs_persist_independent_rows() -> None:
    """The overlapping runs share no state: each has its own task and agent row."""
    total = 3
    RendezvousAgent.barrier = asyncio.Barrier(total)
    RendezvousAgent.arrivals = []

    registry = AgentRegistry()
    registry.register(RendezvousAgent)

    runs = [
        _isolated_run(
            registry,
            OrchestrationRequest(goal=f"build item {index}"),
            agent_type="mock.rendezvous",
            plan="one-step",
        )
        for index in range(total)
    ]
    results = await asyncio.wait_for(asyncio.gather(*runs), timeout=15)

    task_ids = {result.task.id for result in results if result.task is not None}
    agent_ids = {result.agent_id for result in results if result.agent_id is not None}
    assert len(task_ids) == total
    assert len(agent_ids) == total
