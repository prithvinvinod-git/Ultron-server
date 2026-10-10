"""Core-level execution helpers (T050): the retry/replan policy over a task.

Spec §66.4 lists "retries, replanning, verification, human confirmation" as
*graph operations on the existing task DAG (T040/T041)* — not as an isolated
runtime. Each was deliberately left out of the modules that came before: the
executor (T041) **fails fast** and runs a task's steps once, and the orchestrator
(T049) **runs one pass end to end**. Both say so in their docstrings, and both
defer the *policy* — when to try again, when to build a different plan, when to
give up — to this module.

What it is, and what it is not:

*   **A policy, then a loop.** :class:`ExecutionPolicy` is a pure value object
    that answers exactly one question — for a task that failed, *retry, replan,
    or stop?* :class:`CoreExecutor` is the effectful runner that asks it after
    each attempt and drives the task back through the machine's own doors. No
    stage is re-implemented: retrying a task is `TaskManager.retry` (FAILED →
    QUEUED, §66.11's bound), replanning one is `TaskManager.transition` (FAILED →
    PENDING, §66.4's replan), and running one is T041's `TaskExecutor`. The one
    write this module adds beyond those doors is resetting a task's **FAILED
    steps to PENDING** before it re-enters: T041 leaves a failed step marked
    FAILED (its fail-fast), and the graph only ever starts PENDING steps — so
    without the reset a re-entered task would have nothing runnable and the
    executor would complete it with its failure still on the board. This module
    knows the order, the stopping rule and that one repair.
*   **It operates on an already-materialised task.** The plan is built, the task
    row written and its steps added by the orchestrator (T049); a replan is the
    one place the graph changes after that, and even then the *change* is the
    caller's — this module supplies the seam (:data:`ReplanCallback`), not the
    new steps. This keeps it a *runner*, not a second orchestrator.

**Retries are bounded by the row; replans by the policy.** A straight retry
re-runs the *same* plan, so its budget already lives on the task
(``max_retries``, set at creation and enforced by `TaskManager.retry` —
§66.11); :meth:`ExecutionPolicy.decide` only *reads* it and never overrides it. A
replan leaves that plan behind for a new one, so it needs its own bound or a
background agent could replan forever — the explicit loop guard §66.11 calls
for. A retry is therefore always preferred while budget remains (cheap and
predictable), and a replan is considered only once the retry budget is spent and
the caller actually supplied a :data:`ReplanCallback`.

**Events: reuse, do not invent (§64.15).** A straight retry needs no event of its
own — re-admission re-emits ``TASK_STARTED`` through the executor. A replan *does*
change the plan, and the §66.8 catalog already names that fact: ``PLAN_UPDATED``
is published (best-effort, like every event in this pipeline) when the callback
returns the new plan; a callback that returns ``None`` simply changes the graph
without an announcement. No ``TASK_RETRIED`` is added.

**Effects and discipline.** Like the orchestrator, this runner is session-bound
(constructed per unit of work) and flushes but never commits or closes — the
caller owns the ``session_scope``. ``run`` returns only when the task is
finished (COMPLETED, FAILED after the budgets are spent, or another outcome the
executor produced); a task that cannot be re-entered (already terminal when
admitted) raises the machine's 409 untouched, exactly as the executor would.

**Deliberately absent.** Verification and human confirmation: both act *on the
completion door* — a task is only decided once, and `TaskExecutor` completes it
the moment its steps succeed — so wiring a verifier requires the executor's
completion seam, not a loop around it. They remain §66.4 graph operations for a
later task; this module is the retry/replan half of the same policy, and it
narrates that boundary rather than pretending a post-completion verifier could
resurrect a finished row (the machine gives COMPLETED no exit, §66.16).
"""

from __future__ import annotations

import enum
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidInputError
from app.core.planner import Plan
from app.database.models import StepStatus, Task, TaskStatus
from app.database.repositories import TaskStepRepository
from app.events.bus import EventBus
from app.events.types import EventType
from app.observability.logging import get_logger
from app.tasks.executor import StepRunner, TaskExecutor
from app.tasks.manager import TaskManager

__all__ = [
    "CoreExecutor",
    "ExecutionOutcome",
    "ExecutionPolicy",
    "ReplanCallback",
    "RetryAction",
]

_LOGGER = get_logger(__name__)


class RetryAction(enum.StrEnum):
    """What the policy advises for a task that just failed (§66.4, §66.11)."""

    #: Give up: the retry and replan budgets are spent, the row keeps FAILED.
    STOP = "stop"
    #: Re-run the same plan through the queue (FAILED → QUEUED).
    RETRY = "retry"
    #: Hand the goal back for a new plan (FAILED → PENDING).
    REPLAN = "replan"


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    """When to stop retrying and replanning a task that keeps failing (§66.11).

    Only the *replan* budget is declared here: straight retries are bounded by
    the task's own ``max_retries`` (the row's field, which `TaskManager.retry`
    enforces), and this policy never mints a second, disagreeing bound. A
    replan budget of ``0`` — the default — means a failed task is simply left
    failed once its retries are spent.
    """

    max_replans: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.max_replans, bool) or not isinstance(self.max_replans, int):
            raise InvalidInputError(
                "max_replans must be a whole number",
                details={"max_replans": str(self.max_replans)[:100]},
            )
        if self.max_replans < 0:
            raise InvalidInputError(
                "max_replans must not be negative",
                details={"max_replans": self.max_replans},
            )

    def decide(self, task: Task, *, replans: int) -> RetryAction:
        """Choose the next move for a FAILED ``task`` after ``replans`` replans.

        The order encodes the preference: a straight retry is the cheapest,
        most predictable recovery and is tried while the row has retry budget
        left (``retry_count < max_retries``); the more expensive replan is
        considered only once that budget is gone, and only while the separate
        ``max_replans`` budget lasts. Both comparisons read the *row*, so the
        decision is a pure function of state the caller can already see.
        """
        if task.retry_count < task.max_retries:
            return RetryAction.RETRY
        if replans < self.max_replans:
            return RetryAction.REPLAN
        return RetryAction.STOP


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """What one :meth:`CoreExecutor.run` produced across its attempts.

    ``task`` is always the finished row — the record is the source of truth, and
    the counters describe how the run got there: ``attempts`` executions of the
    plan, ``retries`` straight retries and ``replans`` fresh plans, so
    ``attempts == retries + replans + 1``.
    """

    task: Task
    attempts: int
    retries: int
    replans: int

    @property
    def status(self) -> TaskStatus:
        """The finished task's status."""
        return TaskStatus(self.task.status)

    @property
    def succeeded(self) -> bool:
        """Whether the last attempt completed the task."""
        return self.status is TaskStatus.COMPLETED

    @property
    def failed(self) -> bool:
        """Whether the task is left failed (budgets spent, or no policy)."""
        return self.status is TaskStatus.FAILED


#: Rebuilds a failed task's graph (the caller's work) and returns the new plan
#: so the replan can be announced, or ``None`` to change it without an event.
ReplanCallback = Callable[[Task], Awaitable[Plan | None]]


class CoreExecutor:
    """Runs a materialised task, applying the retry/replan policy across attempts.

    Constructed from the same session the orchestrator owns, the shared bus, the
    step runner T041 executes with, and the policy; a :data:`ReplanCallback` is
    required only when the policy actually asks for a replan. It is the loop
    *around* T041's `TaskExecutor`: each iteration runs the task, and on failure
    the policy decides whether to requeue it, replan it, or stop.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        events: EventBus,
        run_step: StepRunner,
        policy: ExecutionPolicy | None = None,
        replanner: ReplanCallback | None = None,
    ) -> None:
        """Bind the run to ``session``, the bus, the runner and the policy.

        ``replanner`` is optional until the policy asks for a replan: a run that
        cannot replan raises rather than silently re-running a plan that already
        failed (see :meth:`_replan`). A non-callable ``replanner`` is a wiring
        mistake and refused immediately, the same way the orchestrator refuses a
        non-callable selector.
        """
        if replanner is not None and not callable(replanner):
            raise TypeError("replanner must be callable")
        self._session = session
        self._events = events
        self._policy = policy if policy is not None else ExecutionPolicy()
        self._replanner = replanner
        self._manager = TaskManager(session)
        self._steps = TaskStepRepository(session)
        self._executor = TaskExecutor(session, events=events, run_step=run_step)

    async def run(
        self,
        task_id: str | uuid.UUID,
        *,
        now: datetime | None = None,
    ) -> ExecutionOutcome:
        """Execute ``task_id``, retrying and replanning per the policy.

        Each pass runs the task through T041 and reads its outcome. A COMPLETED
        task ends the run; a FAILED one is requeued (its failed steps reset to
        PENDING, then `TaskManager.retry`), sent back for a new plan (the
        callback, then FAILED → PENDING), or left as it is, by the policy's
        verdict. The loop is bounded by the row's ``max_retries`` and the
        policy's ``max_replans``, so it always terminates. ``now`` pins
        timestamps for tests.

        A task the executor cannot admit (already terminal) raises its 409
        untouched; the policy only ever speaks about a *failure*, never about a
        task that never ran.
        """
        attempts = 0
        retries = 0
        replans = 0
        while True:
            task = await self._executor.execute(task_id, now=now)
            attempts += 1
            if TaskStatus(task.status) is TaskStatus.COMPLETED:
                return _outcome(task, attempts, retries, replans)
            action = self._policy.decide(task, replans=replans)
            if action is RetryAction.RETRY:
                await self._reset_failed_steps(task.id)
                await self._manager.retry(task.id, now=now)
                retries += 1
            elif action is RetryAction.REPLAN:
                await self._replan(task, replans=replans, now=now)
                replans += 1
            else:
                return _outcome(task, attempts, retries, replans)

    def __repr__(self) -> str:
        return (
            f"CoreExecutor(max_replans={self._policy.max_replans}, "
            f"replanner={self._replanner is not None})"
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    async def _reset_failed_steps(self, task_id: uuid.UUID) -> None:
        """Return a task's FAILED steps to PENDING so a retry can re-run them.

        A straight retry re-runs the *same* plan, but the executor leaves the
        failed step marked FAILED (T041's fail-fast) and the graph only ever
        starts PENDING steps — so without this reset a retried task would have
        nothing runnable and the executor would complete it with its failure
        still on the board. The reset keeps each step's ``attempt`` count (the
        try that failed is history) and touches only FAILED steps; COMPLETED
        ones stay completed, exactly as a resume skips finished work.
        """
        steps = await self._steps.list_for_task(task_id)
        for step in steps:
            if StepStatus(step.status) is StepStatus.FAILED:
                await self._steps.mark_pending(step.id)

    async def _replan(self, task: Task, *, replans: int, now: datetime | None) -> None:
        """Rebuild the graph through the caller's callback, then re-enter PENDING.

        The callback owns the graph change and is invoked while the task is still
        FAILED, so a callback that raises leaves the row failed and recoverable
        rather than half-replanned. Any step it leaves FAILED is then reset to
        PENDING — a replan must not re-enter the lifecycle with an unrunnable
        graph, or the executor would complete the task around its failure — and
        only then is the machine's replan edge walked (FAILED → PENDING, §66.4)
        and the new plan announced.
        """
        if self._replanner is None:
            raise InvalidInputError(
                "the execution policy requested a replan but no replanner is configured",
                details={"task_id": str(task.id), "max_replans": self._policy.max_replans},
            )
        plan = await self._replanner(task)
        await self._reset_failed_steps(task.id)
        moved = await self._manager.transition(task.id, TaskStatus.PENDING, now=now)
        await self._publish_replan(moved, plan, replans=replans + 1)

    async def _publish_replan(self, task: Task, plan: Plan | None, *, replans: int) -> None:
        """Announce a replan as §66.8's ``PLAN_UPDATED`` when a new plan was given.

        A callback that returned no plan changed the graph anonymously; there is
        nothing honest to put in the payload, so nothing is published. The event
        is best-effort like every other in the pipeline: a broken bus must not
        undo a replan that already happened.
        """
        if plan is None:
            return
        payload: dict[str, Any] = {
            "task_id": str(task.id),
            "goal": plan.goal,
            "replan": replans,
            "steps": [step.key for step in plan.steps],
        }
        try:
            await self._events.publish(
                EventType.PLAN_UPDATED,
                payload,
                task_id=str(task.id),
                persist=True,
            )
        except Exception:
            _LOGGER.warning(
                "plan update not published",
                extra={"task_id": str(task.id)},
                exc_info=True,
            )


def _outcome(task: Task, attempts: int, retries: int, replans: int) -> ExecutionOutcome:
    """Build the run's record; pure, so the loop's exits read the same."""
    return ExecutionOutcome(task=task, attempts=attempts, retries=retries, replans=replans)
