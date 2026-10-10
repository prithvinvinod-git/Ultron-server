"""The Orchestrator (T049): §50's pipeline, composed once per request.

§5 lists the Core's responsibilities and draws the lifecycle it names T049 for:

    REQUEST → UNDERSTAND → PLAN → TASK → AGENT → MODEL → EXECUTE → OBSERVE
      → VERIFY → RETRY/REPLAN → COMPLETE → RESPOND

and §50 draws the same line through the whole system. Every *stage* of that
pipeline is already its own module and each is deliberately pure or
single-purpose: :class:`~app.core.router.IntentRouter` (T047) classifies,
:class:`~app.core.planner.Planner` (T048) decomposes, `TaskManager` (T039)
writes the record, `AgentRegistry` (T043) holds the spawnable types,
`AgentManager` (T044) owns agent lifecycles, and `TaskExecutor` (T041) runs a
task's step DAG. **This module is the joint between them** — the one place that
knows the order the stages run in and owns their effects (the ``AsyncSession``
and the ``EventBus``). It is the composition root §66.4 names when it says the
lifecycle is *"not a new runtime"*: it adds no stage and no policy, only the
sequence.

What one :meth:`Orchestrator.run` does, in order:

1. **UNDERSTAND** — route the goal through T047's router, producing a
   :class:`~app.core.router.Routing` (intent + handler key).
2. **PLAN** — decompose the goal through T048's planner, producing a validated
   :class:`~app.core.planner.Plan`; announce it with §19's ``PLAN_CREATED``.
3. **AGENT SELECTION** (§66.4 puts it *after* the task graph) — ask the
   declared :data:`AgentSelector` (or the configured default ``agent_type``)
   which kind of agent should do the work. A selector that returns ``None``
   means *no agent*: there is nothing to execute, so the run stops here and
   reports the routing and plan alone (a conversation or a question is answered
   without a task). The orchestrator holds no agent kinds itself — §40 keeps
   them declared on the agents, exactly as T047/T048 keep routing/planning on
   the caller's declarations.
4. **TASK** — materialise the plan: T041's executor ``create`` writes one
   ``tasks`` row through `TaskManager` and announces §19's ``TASK_CREATED``,
   then one ``task_steps`` row per plan step is walked in
   :meth:`~app.core.planner.Plan.topological_order` so every sibling a step
   ``depends_on`` is already a row whose id can be referenced (T048's plan keys
   are plan-local; the rows get real ids, and this is the one place the two are
   bridged).
5. **AGENT** — spawn one agent of the selected kind through the registry
   (T043) and the manager (T044), bound to the task, and walk it to RUNNING
   through the machine's own doors (``CREATED → INITIALIZING → READY →
   RUNNING``). The in-memory :class:`~app.agents.base.Agent` the registry spawns
   shares the row's id, so the worker that runs and the row that is recorded are
   the same agent.
6. **EXECUTE / OBSERVE** — run the task's steps through T041's executor, whose
   injected ``run_step`` hands each step's context to that agent's ``run``; the
   executor owns the ``TASK_*`` events and the step rows, the manager owns the
   ``AGENT_*`` events, and this module writes no event of its own but
   ``PLAN_CREATED``.
7. **VERIFY / COMPLETE** — read the finished task's outcome and bring the agent
   through its last doors: VERIFYING → COMPLETED on success, FAILED on failure
   (recording the task's own error in ``AGENT_FAILED``).

Deliberately **absent**, and each for the same reason — they are policy over a
live graph, not composition:

*   **Retry / replan / verification policy** is T050's (§66.11): the pipeline
    runs once and reports what happened. The executor already fails fast with
    the failing step named (§18), and a retried task is a fresh admission, not a
    loop here.
*   **Model selection** is §66.6's, the AI router (T066) — Phase 2 has no model
    in the pipeline, and an agent carries only a ``model`` *name* (§6), never a
    provider.
*   **Permission checks** (§66.4's PERMISSION stage) belong to T033's engine,
    which tools declare to and agents do not duplicate (§66.7); no
    orchestration-level decision is defined yet, so none is invented.
*   **Parallel step execution** needs one session per step (T041's note) and is
    a worker-pool concern; this orchestrator runs steps sequentially through the
    one session it owns, exactly as the executor does.

**Effects and discipline.** Like every effectful Core module, the orchestrator
flushes and never commits or closes — the caller owns the unit of work
(``session_scope``). It is session-bound (constructed per unit of work) while
the router, planner and registry are application-scoped and stateless.
``events`` is required, because the executor it builds requires a bus; the
manager receives the same one, so agent and task events land on one stream.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import Agent
from app.agents.manager import AgentManager
from app.agents.registry import AgentRegistry
from app.core.errors import InvalidInputError
from app.core.planner import Plan, Planner, PlanningRequest
from app.core.router import Intent, IntentRouter, Routing, RoutingRequest
from app.database.models import (
    Agent as AgentRow,
    AgentStatus,
    Task,
    TaskPriority,
    TaskStatus,
    TaskStep,
)
from app.events.bus import EventBus
from app.events.types import EventType
from app.observability.logging import get_logger
from app.tasks.executor import TaskExecutor
from app.tasks.manager import TaskManager

__all__ = [
    "AgentSelector",
    "OrchestrationRequest",
    "OrchestrationResult",
    "Orchestrator",
]

_LOGGER = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class OrchestrationRequest:
    """One request through the pipeline: the goal and where it belongs.

    ``context`` is an open mapping handed to both the router and the planner,
    exactly as their own requests keep one — the orchestrator takes no
    dependency on the context engine's shape. The task fields
    (``priority``/``project_id``/``conversation_id``) are validated by
    `TaskManager` when the plan is materialised, so the manager stays the one
    authority on what a task may be.
    """

    goal: str
    context: Mapping[str, Any] = field(default_factory=dict)
    priority: TaskPriority = TaskPriority.NORMAL
    project_id: str | uuid.UUID | None = None
    conversation_id: str | uuid.UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.goal, str) or not self.goal.strip():
            raise InvalidInputError(
                "an orchestration request needs a non-empty goal",
                details={"goal": str(self.goal)[:100]},
            )
        if not isinstance(self.context, Mapping):
            raise InvalidInputError(
                "an orchestration request's context must be a mapping",
                details={"context": type(self.context).__name__},
            )


#: Which kind of agent should do the work, asked *after* the plan is built
#: (§66.4). ``None`` means no agent: the run reports the routing and plan and
#: stops. A selector's answer must name a registered type (T043) or the manager
#: refuses it as a 404.
AgentSelector = Callable[[OrchestrationRequest, Routing, Plan], str | None]


@dataclass(frozen=True, slots=True)
class OrchestrationResult:
    """What one pipeline run produced: the routing, the plan, and the task.

    ``task`` is ``None`` when the run stopped before execution (the selector
    chose no agent) — then only the routing and plan are meaningful. When a task
    ran, ``task`` is the finished row (COMPLETED or FAILED) and the convenience
    properties read its outcome; the row itself is the record.
    """

    routing: Routing
    plan: Plan
    task: Task | None = None
    agent_id: uuid.UUID | None = None
    agent_type: str | None = None

    @property
    def executed(self) -> bool:
        """Whether a task was materialised and run (an agent was selected)."""
        return self.task is not None

    @property
    def intent(self) -> Intent:
        """The intent the router classified the request as."""
        return self.routing.intent

    @property
    def status(self) -> TaskStatus | None:
        """The finished task's status, or ``None`` if nothing ran."""
        return None if self.task is None else TaskStatus(self.task.status)

    @property
    def result(self) -> Mapping[str, Any] | None:
        """The finished task's result payload, or ``None``."""
        return None if self.task is None else self.task.result

    @property
    def error(self) -> str | None:
        """The finished task's error, or ``None``."""
        return None if self.task is None else self.task.error


class Orchestrator:
    """Runs §50's pipeline once, composing the pure stages with the effectful ones.

    Constructed per unit of work from a session, the three stateless
    collaborators (router, planner, type registry) and the shared bus; it is
    the effectful composition root, so it owns the `AgentManager` and the
    `TaskManager` it feeds, and builds a `TaskExecutor` per run around the agent
    that run spawns.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        router: IntentRouter,
        planner: Planner,
        agents: AgentRegistry,
        events: EventBus,
        agent_type: str | None = None,
        selector: AgentSelector | None = None,
        max_concurrent: int | None = None,
    ) -> None:
        """Bind the run to ``session``, the declared stages and the shared bus.

        ``agent_type`` is the default kind the run spawns; ``selector``, when
        given, decides per request instead (and may return ``None`` for "no
        agent"). ``max_concurrent`` is forwarded to the `AgentManager` as its
        ceiling on active agents (§66's load-shedding) — the orchestrator adds no
        policy of its own on top.
        """
        if selector is not None and not callable(selector):
            raise TypeError("selector must be callable")
        if agent_type is not None and (not isinstance(agent_type, str) or not agent_type.strip()):
            raise InvalidInputError(
                "agent_type must be a non-empty string when set",
                details={"agent_type": str(agent_type)[:100]},
            )
        self._session = session
        self._router = router
        self._planner = planner
        self._agents = agents
        self._events = events
        self._agent_type = agent_type
        self._selector = selector
        self._manager = AgentManager(
            session,
            agents=agents,
            events=events,
            max_concurrent=max_concurrent,
        )
        self._tasks = TaskManager(session)

    # ------------------------------------------------------------------ #
    # The pipeline
    # ------------------------------------------------------------------ #

    async def run(self, request: OrchestrationRequest) -> OrchestrationResult:
        """Run one request end to end and return what it produced.

        The stages run in §66.4's order. A selector that names no agent stops
        the run after PLANNING (§50's conversation/question paths have no task);
        otherwise the plan is materialised, one agent is spawned and walked to
        RUNNING, the task's steps run through it, and the agent is finished
        according to the task's outcome.

        Errors from the stages propagate untouched — a routing/planning refusal
        is the caller's 422, an unknown agent type is the registry's 404, an
        exhausted agent ceiling is the manager's 409. Cancellation is honoured:
        the agent is cancelled before the ``CancelledError`` continues, so no
        run is left half-recorded.
        """
        if not isinstance(request, OrchestrationRequest):
            raise TypeError("run expects an OrchestrationRequest")

        routing = self._router.route(
            RoutingRequest(text=request.goal, context=dict(request.context))
        )
        plan = self._planner.plan(PlanningRequest(goal=request.goal, context=dict(request.context)))
        await self._publish_plan(routing, plan)

        agent_type = self._select_agent(request, routing, plan)
        if agent_type is None:
            return OrchestrationResult(routing=routing, plan=plan)

        # The task and the worker are materialised before the steps run, but the
        # runner the executor holds is defined first so the executor can announce
        # the task's creation (`TASK_CREATED`, §19). The closure reads `task` and
        # `runnable`, both assigned below and invoked only during `execute`.
        task: Task
        runnable: Agent

        async def _run_step(step: TaskStep) -> Mapping[str, Any] | None:
            output = await runnable.run(self._step_context(task, step, request))
            return None if output is None else dict(output)

        executor = TaskExecutor(self._session, events=self._events, run_step=_run_step)
        task = await executor.create(
            plan.goal,
            priority=request.priority,
            project_id=request.project_id,
            conversation_id=request.conversation_id,
        )
        await self._add_steps(task, plan)

        row = await self._manager.create(agent_type, name=agent_type, task_id=task.id)
        runnable = self._spawn(agent_type, row)

        try:
            for target in (AgentStatus.INITIALIZING, AgentStatus.READY, AgentStatus.RUNNING):
                await self._advance(row, runnable, target)
        except Exception:
            await self._cancel_quietly(row, runnable)
            raise

        try:
            finished = await executor.execute(task.id)
        except asyncio.CancelledError:
            await self._cancel_quietly(row, runnable)
            raise

        await self._finish(row, runnable, finished)
        return OrchestrationResult(
            routing=routing,
            plan=plan,
            task=finished,
            agent_id=row.id,
            agent_type=agent_type,
        )

    def __repr__(self) -> str:
        return (
            f"Orchestrator(agent_type={self._agent_type!r}, selector={self._selector is not None})"
        )

    # ------------------------------------------------------------------ #
    # Stages
    # ------------------------------------------------------------------ #

    def _select_agent(
        self,
        request: OrchestrationRequest,
        routing: Routing,
        plan: Plan,
    ) -> str | None:
        """Ask the declared selector (or the default) which agent should work.

        A selector is authoritative when present — including its ``None``, which
        means "no agent" and stops the run. Without one, the configured
        ``agent_type`` is used (itself possibly ``None``).
        """
        if self._selector is None:
            return self._agent_type
        chosen = self._selector(request, routing, plan)
        if chosen is None:
            return None
        if not isinstance(chosen, str) or not chosen.strip():
            raise InvalidInputError(
                "the agent selector must return an agent type or None",
                details={"selected": str(chosen)[:100]},
            )
        return chosen

    async def _add_steps(self, task: Task, plan: Plan) -> None:
        """Write the plan's steps onto the already-created task, in order.

        Steps are added in dependency order so a step's ``depends_on`` ids are
        always already rows; the plan's local keys are mapped to the rows' real
        ids exactly once, here. All writes go through `TaskManager`, the one
        authority on the tasks and task_steps records.
        """
        step_ids: dict[str, uuid.UUID] = {}
        for key in plan.topological_order():
            step = plan.get(key)
            depends_on = [str(step_ids[dep]) for dep in step.depends_on]
            row = await self._tasks.add_step(
                task.id,
                name=step.name,
                description=step.description or None,
                depends_on=depends_on or None,
            )
            step_ids[key] = row.id

    def _spawn(self, agent_type: str, row: AgentRow) -> Agent:
        """Instantiate the registered type as the run's worker, sharing the row id.

        The registry (T043) is the only source of spawnable types; the manager
        already created the row, and the instance is built with the same id and
        the same §6 assignment so the worker that runs and the row that records
        it are one agent.
        """
        return self._agents.create(
            agent_type,
            agent_id=row.id,
            name=row.name,
            task_id=row.task_id,
            model=row.model,
            tools=list(row.tools),
            permissions=list(row.permissions),
            memory_namespace=row.memory_namespace,
        )

    def _step_context(
        self,
        task: Task,
        step: TaskStep,
        request: OrchestrationRequest,
    ) -> dict[str, Any]:
        """Assemble one step's context: the task, the step, and the request's own.

        The request's ``context`` is nested rather than spread, so a caller key
        named ``task_id`` cannot shadow the pipeline's own identifier.
        """
        return {
            "task_id": str(task.id),
            "goal": task.goal,
            "step": {
                "id": str(step.id),
                "position": step.position,
                "name": step.name,
                "description": step.description,
            },
            "input": step.input_payload,
            "context": dict(request.context),
        }

    # ------------------------------------------------------------------ #
    # Agent lifecycle (driven through the manager's own doors)
    # ------------------------------------------------------------------ #

    async def _advance(self, row: AgentRow, runnable: Agent, target: AgentStatus) -> None:
        """Move both the row and the worker to ``target`` through one door.

        The manager owns the row (and publishes the ``AGENT_*`` event); the
        in-memory worker mirrors the row's status so a `run` that reads its own
        ``status`` sees the truth. The machine is asked first by the manager, so
        an illegal move is its 409, never an orchestration guess.
        """
        moved = await self._manager.transition(row.id, target)
        runnable.status = AgentStatus(moved.status)

    async def _finish(self, row: AgentRow, runnable: Agent, task: Task) -> None:
        """Close the worker according to how the task ended.

        A completed task walks the last doors (VERIFYING → COMPLETED); a failed
        one goes straight to FAILED (legal from RUNNING), carrying the task's own
        error into ``AGENT_FAILED``. The executor only ever completes or fails a
        task, so no other outcome reaches here.
        """
        status = TaskStatus(task.status)
        if status is TaskStatus.COMPLETED:
            await self._advance(row, runnable, AgentStatus.VERIFYING)
            await self._advance(row, runnable, AgentStatus.COMPLETED)
        elif status is TaskStatus.FAILED:
            await self._manager.fail(row.id, error=task.error or "the task failed")
            runnable.status = AgentStatus.FAILED

    async def _cancel_quietly(self, row: AgentRow, runnable: Agent) -> None:
        """Cancel a run's agent, best-effort, after a failure or cancellation.

        Cancelling is legal from every live state, so this closes an agent that
        was admitted but whose pipeline then failed — a CREATED or RUNNING row is
        never left dangling. A cancel that itself fails is logged, not raised: it
        must not replace the original error the caller is about to see.
        """
        try:
            await self._manager.cancel(row.id)
            runnable.status = AgentStatus.CANCELLED
        except Exception:
            _LOGGER.warning(
                "could not cancel an agent after a pipeline failure",
                extra={"agent_id": str(row.id)},
                exc_info=True,
            )

    # ------------------------------------------------------------------ #
    # Events (§19: no invented names — PLAN_CREATED is the plan stage's)
    # ------------------------------------------------------------------ #

    async def _publish_plan(self, routing: Routing, plan: Plan) -> None:
        """Publish ``PLAN_CREATED`` best-effort, like the executor's events.

        A broken bus must not change the run: the plan is recorded as an event
        for observers, not as a precondition for work. No ``task_id`` yet — the
        plan precedes its materialisation.
        """
        try:
            await self._events.publish(
                EventType.PLAN_CREATED,
                {
                    "goal": plan.goal,
                    "intent": routing.intent.value,
                    "handler": routing.handler,
                    "steps": [
                        {"key": step.key, "name": step.name, "depends_on": list(step.depends_on)}
                        for step in plan.steps
                    ],
                },
                persist=True,
            )
        except Exception:
            _LOGGER.warning("plan event not published", exc_info=True)
