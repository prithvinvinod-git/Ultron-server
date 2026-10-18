"""Turn a fired schedule into a real task (T170/T172).

The scheduler (:mod:`app.scheduler.manager`) deliberately does not know what a
firing *means* — it hands the work to a `TaskFactory`. This module is the
production factory: it reads a schedule's payload as a workflow
(:mod:`app.scheduler.workflows`) and creates the task through the §18 executor, so
`TASK_CREATED` fires from the one place that owns it.

It is separate from the manager for the same reason the manager takes the factory
as a seam: the scheduler's poll loop must be testable without the task graph, and
this module must be testable without a scheduler.

**Why the executor, and not the manager, creates the task.** `TaskExecutor.create`
is the only writer that also publishes `TASK_CREATED` (§19). Creating the row
through `TaskManager` and emitting the event by hand here would be a second copy of
a rule the executor already states — and the first to drift.

**The step runner is a placeholder on purpose.** `TaskExecutor` demands a
`StepRunner` at construction, but `create` never calls it: running a task's steps
is the job of the task-execution layer, which is not part of Phase 11 (the phase
creates the task; the DAG worker is separate). Rather than hand in a runner that
silently "completes" a step with no output, this factory hands in one that raises —
if a scheduled workflow is ever executed before that layer exists, it fails loudly
instead of pretending to have run.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UltronError
from app.database.models import Schedule, Task, TaskStep
from app.database.repositories import TaskStepRepository
from app.events.bus import EventBus
from app.scheduler.manager import TaskFactory
from app.scheduler.workflows import Workflow
from app.tasks.executor import TaskExecutor

__all__ = ["build_task_factory", "workflow_from_schedule"]


async def workflow_from_schedule(schedule: Schedule) -> Workflow:
    """Read ``schedule.payload`` as a workflow, or raise ``InvalidInputError``.

    Exposed so the API layer can validate a payload at creation time rather than
    leaving a malformed one to fail on its first fire.
    """
    return Workflow.from_payload(schedule.payload or {})


def build_task_factory(events: EventBus) -> TaskFactory:
    """Return the production :data:`~app.scheduler.manager.TaskFactory`.

    The factory builds the task and its steps in the firing transaction; the
    manager commits. ``events`` is the executor's bus, so `TASK_CREATED` travels
    the same path as any other task.
    """

    async def plan(session: AsyncSession, schedule: Schedule) -> Task:
        workflow = await workflow_from_schedule(schedule)
        executor = TaskExecutor(session, events=events, run_step=_no_step_runner)
        task = await executor.create(
            workflow.goal,
            priority=workflow.task_priority,
            project_id=schedule.project_id,
            scheduled_for=schedule.next_run_at,
        )
        steps = TaskStepRepository(session)
        for step in workflow.steps:
            await steps.add_step(
                task_id=task.id,
                name=step.name,
                description=step.description,
                input_payload=dict(step.input) or None,
            )
        return task

    return plan


async def _no_step_runner(step: TaskStep) -> Mapping[str, Any] | None:
    """Fail loudly: executing a scheduled task's steps is not this phase's job."""
    raise UltronError(
        "scheduled workflows cannot be executed yet: the task-execution layer "
        "is not part of Phase 11",
        details={"step_id": str(step.id), "step_name": step.name},
    )
