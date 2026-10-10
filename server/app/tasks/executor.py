"""The task executor (T041): §18's step graph, run, and resumed after a crash.

Section 18 fixes what a task *is* (`app/tasks/manager.py` writes the record),
T038 fixes where it may move, and T040 fixes the shape of its steps. This
module is the runtime that ties the three together: it admits a queued task,
walks its step DAG to completion, publishes the `TASK_*` lifecycle events §19
names, and — after a restart — picks the work back up without losing it.

**One authority per write.** Task status is written only through
`TaskManager.transition` (never `TaskRepository.start/complete/fail`, which
the machine's rule 1 predates — see the manager's docstring); step status is
written only through `TaskStepRepository`. The executor owns neither table.

**The runner is injected.** What a *step* actually does — call a model, run a
tool, delegate to an agent — is not this module's concern. It is a
`StepRunner`, a `Callable[[TaskStep], Awaitable[Mapping | None]]` handed in at
construction, the same seam `ToolExecutor`'s optional `PolicyCheck` uses. The
agent runtime (T042+) supplies the real one; tests supply a fake. A runner
that raises fails its step; returning ``None`` is a completed step with no
output.

**Sequential, by design.** §66.4's "parallel-where-safe steps" is *data* —
`StepGraph.waves()` groups steps that share no edges — but executing a wave
concurrently would race on the single `AsyncSession` this executor is bound
to (SQLAlchemy sessions are not safe for concurrent use). Steps therefore run
one at a time in dependency order. Real parallelism needs one session per
step, which is a worker-pool concern (T049/T050), not this unit-of-work.

**Events (§19).** `TASK_CREATED` (from `create`), `TASK_STARTED` (admission),
`TASK_COMPLETED`/`TASK_FAILED` (outcome) are durable (`persist=True`, low
volume); `TASK_PROGRESS` (one per step change) is not (`persist=False`, high
volume — §64.9 feeds it to the ESP32 display). `SCHEDULE_TRIGGERED` fires from
`trigger_due` when a `scheduled_for` task's time has come. Publishing is
best-effort, exactly as the tool pipeline's is: a closed or full bus logs a
warning instead of failing work that ran — observability degrades, the task
record does not.

**A step's failure is fail-fast.** The step is marked FAILED and the task
FAILED with a message naming it; steps downstream of the failure are left
PENDING (a truthful "never ran"), not SKIPPED. Independent branches that had
not started are left PENDING too: §66.4's retry/replan/escalate policy is
T050's, so this executor does not pretend a partial plan "completed" and does
not silently burn a retry.

**Restart recovery (§18: "tasks must survive server restarts").** A RUNNING
task whose worker died is ambiguous — a step may have half-happened — so
`recover` requeues the *task* (RUNNING → QUEUED, the machine's restart edge,
which keeps `started_at`) and resets each of its RUNNING *steps* to PENDING so
the graph re-runs them. No event is emitted: the §19 catalog has no requeue
name and §64.15 forbids inventing one.

**Deliberately absent.** Retry/replan (T050), agent assignment (T044),
concurrency and per-step sessions (T049), and the recurring/delayed/event
schedules of §44 (T170) — `trigger_due` fires the one-shot case §19's
`SCHEDULE_TRIGGERED` describes and leaves recurrence to the scheduler.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorCode, UltronError
from app.database.models import (
    StepStatus,
    Task,
    TaskPriority,
    TaskStatus,
    TaskStep,
)
from app.database.repositories import TaskRepository, TaskStepRepository
from app.events.bus import EventBus
from app.events.types import EventType
from app.observability.logging import get_logger
from app.tasks.graph import StepGraph
from app.tasks.manager import TaskManager

__all__ = ["StepRunner", "TaskExecutor"]

_LOGGER = get_logger(__name__)

#: What a step *does*. Injected so the executor stays pure orchestration — the
#: agent runtime (T042+) provides the real one. Returning ``None`` completes
#: the step with no output; raising fails it.
StepRunner = Callable[[TaskStep], Awaitable[Mapping[str, Any] | None]]


@dataclass(frozen=True, slots=True)
class _StepOutcome:
    """The result of running one step: its new status, and why if it failed."""

    status: StepStatus
    error: str | None = None
    code: ErrorCode | None = None


class TaskExecutor:
    """§18's step graph, driven to completion and recovered across restarts."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        events: EventBus,
        run_step: StepRunner,
    ) -> None:
        """Bind to ``session`` (the unit of work), a bus, and a step runner.

        All four services below share the one session, so a status change and
        the step write beside it land in the same transaction. Like the
        manager and repositories, this class flushes and never commits — the
        caller owns the unit of work (`session_scope`).
        """
        self._session = session
        self._tasks = TaskRepository(session)
        self._steps = TaskStepRepository(session)
        self._manager = TaskManager(session)
        self._events = events
        self._run_step = run_step

    # ------------------------------------------------------------------ #
    # Create
    # ------------------------------------------------------------------ #

    async def create(
        self,
        goal: str,
        *,
        priority: TaskPriority = TaskPriority.NORMAL,
        parent_task_id: str | uuid.UUID | None = None,
        project_id: str | uuid.UUID | None = None,
        conversation_id: str | uuid.UUID | None = None,
        scheduled_for: datetime | None = None,
        max_retries: int = 3,
    ) -> Task:
        """Create a task and announce it (`TASK_CREATED`).

        A thin wrapper over `TaskManager.create`: every field rule lives
        there, and the manager has no bus by design (§19's `TASK_CREATED`
        belongs to this executor, per the manager's docstring). The event
        carries identifiers and the goal, never the whole row.
        """
        task = await self._manager.create(
            goal,
            priority=priority,
            parent_task_id=parent_task_id,
            project_id=project_id,
            conversation_id=conversation_id,
            scheduled_for=scheduled_for,
            max_retries=max_retries,
        )
        await self._publish(
            EventType.TASK_CREATED,
            {
                "task_id": str(task.id),
                "goal": task.goal,
                "status": TaskStatus(task.status).value,
                "priority": TaskPriority(task.priority).value,
                "parent_task_id": (
                    None if task.parent_task_id is None else str(task.parent_task_id)
                ),
            },
            task_id=str(task.id),
            persist=True,
        )
        return task

    # ------------------------------------------------------------------ #
    # Execute
    # ------------------------------------------------------------------ #

    async def execute(
        self,
        task_id: str | uuid.UUID,
        *,
        now: datetime | None = None,
    ) -> Task:
        """Admit a task and run its step graph, returning the finished row.

        The task is walked through the one door the machine allows — PENDING →
        QUEUED → RUNNING on a first run, and straight to RUNNING on a resume
        the executor already admitted — then its steps run in dependency order
        until none is runnable. A `TaskStatus` the machine refuses (BLOCKED,
        PAUSED, terminal) raises the machine's 409 before any step runs.

        ``now`` pins timestamps for tests; production leaves it ``None``.
        """
        task = await self._manager.get(task_id)
        task = await self._admit(task, now=now)

        steps = await self._steps.list_for_task(task.id)
        graph = StepGraph.from_task_steps(steps)
        nodes = {step.id: step for step in steps}

        # Statuses live on the rows, but a freshly loaded row's status is a
        # plain string (the column is `String`), and the graph compares with
        # `is` — so they are coerced to StepStatus here, at the load boundary,
        # and kept as members thereafter.
        statuses: dict[uuid.UUID, StepStatus] = {step.id: StepStatus(step.status) for step in steps}
        outputs: dict[str, Any] = {
            str(step.position): step.output_payload
            for step in steps
            if statuses[step.id] is StepStatus.COMPLETED
        }

        while True:
            runnable = graph.runnable(statuses)
            if not runnable:
                break
            for step_id in runnable:
                step = nodes[step_id]
                outcome = await self._run_one(step, task, outputs, now=now)
                statuses[step_id] = outcome.status
                if outcome.status is StepStatus.FAILED:
                    return await self._fail(
                        task, step, outcome.error or "step failed", code=outcome.code, now=now
                    )

        blocked = graph.blocked(statuses)
        if blocked:
            victim = nodes[blocked[0]]
            return await self._fail(
                task,
                victim,
                "a prerequisite failed, so this step cannot run",
                now=now,
            )
        return await self._complete(task, outputs, now=now)

    async def _admit(self, task: Task, *, now: datetime | None) -> Task:
        """Move a task into RUNNING through the queue, emitting `TASK_STARTED`."""
        current = TaskStatus(task.status)
        if current is TaskStatus.PENDING:
            task = await self._manager.transition(task.id, TaskStatus.QUEUED, now=now)
            current = TaskStatus(task.status)
        if current is TaskStatus.QUEUED:
            task = await self._manager.transition(task.id, TaskStatus.RUNNING, now=now)
            await self._publish(
                EventType.TASK_STARTED,
                {
                    "task_id": str(task.id),
                    "goal": task.goal,
                    "status": TaskStatus(task.status).value,
                    "priority": TaskPriority(task.priority).value,
                },
                task_id=str(task.id),
                persist=True,
            )
        elif current is not TaskStatus.RUNNING:
            # BLOCKED/PAUSED/terminal: let the machine produce the 409 that
            # names every legal target, rather than invent a refusal here.
            task = await self._manager.transition(task.id, TaskStatus.RUNNING, now=now)
        return task

    async def _run_one(
        self,
        step: TaskStep,
        task: Task,
        outputs: dict[str, Any],
        *,
        now: datetime | None,
    ) -> _StepOutcome:
        """Run one step, bracketed by status writes and `TASK_PROGRESS` events."""
        await self._steps.mark_running(step.id, now=now)
        await self._progress(task, step, StepStatus.RUNNING)
        try:
            output = await self._run_step(step)
        except asyncio.CancelledError:
            # The caller's cancellation. The step is left RUNNING and the task
            # RUNNING; `recover` resets both. Re-raised untouched so asyncio's
            # structured cancellation is preserved.
            raise
        except Exception as exc:
            error = _describe(exc)
            await self._steps.mark_failed(step.id, error=error, now=now)
            await self._progress(task, step, StepStatus.FAILED, error=error)
            return _StepOutcome(status=StepStatus.FAILED, error=error, code=_failure_code(exc))

        payload = None if output is None else dict(output)
        await self._steps.mark_completed(step.id, output_payload=payload, now=now)
        outputs[str(step.position)] = payload
        await self._progress(task, step, StepStatus.COMPLETED)
        return _StepOutcome(status=StepStatus.COMPLETED)

    async def _complete(
        self,
        task: Task,
        outputs: Mapping[str, Any],
        *,
        now: datetime | None,
    ) -> Task:
        """Finish a task whose every step completed, recording their outputs."""
        result: dict[str, Any] = {"steps": dict(outputs)}
        moved = await self._manager.transition(
            task.id, TaskStatus.COMPLETED, result=result, now=now
        )
        await self._publish(
            EventType.TASK_COMPLETED,
            {
                "task_id": str(moved.id),
                "goal": moved.goal,
                "status": TaskStatus(moved.status).value,
                "result": result,
            },
            task_id=str(moved.id),
            persist=True,
        )
        return moved

    async def _fail(
        self,
        task: Task,
        step: TaskStep,
        error: str,
        *,
        code: ErrorCode | None = None,
        now: datetime | None,
    ) -> Task:
        """Fail a task, naming the step whose outcome stopped the plan."""
        message = f"step '{step.name}' (position {step.position}) failed: {error}"
        moved = await self._manager.transition(task.id, TaskStatus.FAILED, error=message, now=now)
        await self._publish(
            EventType.TASK_FAILED,
            {
                "task_id": str(moved.id),
                "goal": moved.goal,
                "status": TaskStatus(moved.status).value,
                "error": message,
                "error_code": (code or ErrorCode.TASK_FAILED).value,
                "failed_step": {
                    "id": str(step.id),
                    "position": step.position,
                    "name": step.name,
                },
            },
            task_id=str(moved.id),
            persist=True,
        )
        return moved

    # ------------------------------------------------------------------ #
    # Recovery and schedules
    # ------------------------------------------------------------------ #

    async def recover(self, *, now: datetime | None = None) -> list[Task]:
        """Requeue tasks a crash left RUNNING and reset their in-flight steps.

        The machine permits RUNNING → QUEUED precisely for this recovery (§18:
        "tasks must survive server restarts"), and it keeps `started_at`, so
        the requeue does not fake a fresh start. Each step left RUNNING is
        returned to PENDING by `TaskStepRepository.mark_pending` so the graph
        re-runs it; COMPLETED steps are untouched and are skipped on the
        re-run. Returns the requeued tasks — the caller decides whether to
        execute them now.
        """
        running = await self._manager.list_by_status(TaskStatus.RUNNING)
        requeued: list[Task] = []
        for task in running:
            steps = await self._steps.list_for_task(task.id)
            for step in steps:
                if StepStatus(step.status) is StepStatus.RUNNING:
                    await self._steps.mark_pending(step.id)
            requeued.append(await self._manager.transition(task.id, TaskStatus.QUEUED, now=now))
        if requeued:
            _LOGGER.info("requeued tasks after restart", extra={"count": len(requeued)})
        return requeued

    async def trigger_due(
        self,
        *,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> list[Task]:
        """Queue every PENDING task whose ``scheduled_for`` has arrived (§19).

        The one-shot half of §44: candidates come from
        `TaskRepository.list_scheduled_due` (which applies no status filter by
        design), the PENDING check decides which are actually due for work,
        and each emits `SCHEDULE_TRIGGERED` before it enters the queue. The
        recurring/delayed/event-driven schedules are the scheduler's (T170);
        this method is what a poll loop calls when a timer fires.
        """
        due = await self._tasks.list_scheduled_due(now=now, limit=limit)
        triggered: list[Task] = []
        for task in due:
            if TaskStatus(task.status) is not TaskStatus.PENDING:
                continue
            moved = await self._manager.transition(task.id, TaskStatus.QUEUED, now=now)
            await self._publish(
                EventType.SCHEDULE_TRIGGERED,
                {
                    "task_id": str(moved.id),
                    "goal": moved.goal,
                    "scheduled_for": (
                        None if moved.scheduled_for is None else moved.scheduled_for.isoformat()
                    ),
                },
                task_id=str(moved.id),
                persist=True,
            )
            triggered.append(moved)
        return triggered

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    async def _progress(
        self,
        task: Task,
        step: TaskStep,
        status: StepStatus,
        *,
        error: str | None = None,
    ) -> None:
        """Publish one step transition on `TASK_PROGRESS` (ephemeral, §64.9)."""
        payload: dict[str, Any] = {
            "task_id": str(task.id),
            "goal": task.goal,
            "step_id": str(step.id),
            "position": step.position,
            "name": step.name,
            "status": status.value,
            "attempt": step.attempt,
        }
        if error is not None:
            payload["error"] = error[:500]
        await self._publish(
            EventType.TASK_PROGRESS,
            payload,
            task_id=str(task.id),
            persist=False,
        )

    async def _publish(
        self,
        event_type: EventType,
        payload: Mapping[str, Any],
        *,
        task_id: str,
        persist: bool,
    ) -> None:
        """Best-effort publish: a broken bus must not change the outcome."""
        try:
            await self._events.publish(
                event_type,
                payload,
                task_id=task_id,
                persist=persist,
            )
        except Exception:
            _LOGGER.warning(
                "task event not published",
                extra={"event_type": event_type.value},
                exc_info=True,
            )


# -------------------------------------------------------------------------- #
# Pure helpers (stateless, so they stay module-level like manager.py's)
# -------------------------------------------------------------------------- #


def _describe(exc: BaseException) -> str:
    """Render a runner's exception as the one line a step and task record.

    A ULTRON error contributes its typed message (its `details` are documented
    never to carry secrets); anything else contributes its type and `str`, so
    an unexpected `KeyError` is still legible without a traceback.
    """
    if isinstance(exc, UltronError):
        return f"{type(exc).__name__}: {exc.message}"
    return f"{type(exc).__name__}: {exc}"


def _failure_code(exc: BaseException) -> ErrorCode:
    """The event's `error_code`: a ULTRON error's own, else `TASK_FAILED`."""
    return exc.code if isinstance(exc, UltronError) else ErrorCode.TASK_FAILED
