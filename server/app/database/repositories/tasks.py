"""Task repositories (T015).

This is where the "an AI may not act beyond what it was granted" requirement
becomes structural rather than advisory, so it is worth being explicit about what
enforces it and what does not.

:class:`ToolExecutionRepository` will not invent a permission level. Every write
goes through :func:`ToolExecutionRepository.record_decision`, whose
``permission_level`` and ``decision`` arguments are keyword-only with no
defaults, and the row's own columns are ``NOT NULL``. A caller therefore cannot
create a record of having executed a tool without also stating, in the same
statement, what level it claimed and whether that claim was allowed. There is no
"execute first, classify later" path.

What this repository does **not** do is decide whether a call is permitted. It
records the decisions it is given and enforces the schema around them. The
decision itself belongs to the permission layer (T033/T034), because that layer
knows the caller's identity, the project's policy, and what confirmation was
obtained. A repository that decided permission would be trusting whoever wrote
the calling code -- which is precisely the arrangement being avoided.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, InvalidInputError
from app.database.models import (
    AuditOutcome,
    PermissionLevel,
    StepStatus,
    Task,
    TaskStatus,
    TaskStep,
    ToolExecution,
    ToolExecutionStatus,
    VerificationOutcome,
)
from app.database.repositories.base import UuidRepository, apply_limit, apply_offset


class TaskRepository(UuidRepository[Task]):
    """Tasks. The queue view and the claim view are the two things that matter."""

    model = Task

    async def get_with_children(self, identifier: uuid.UUID | str) -> Task | None:
        """Return one task with steps, agents, and tool executions loaded.

        Loaded together because a caller inspecting a task almost always needs
        all three, and three round trips on a hot path is the sort of latency
        that later gets "fixed" by turning off ``lazy="raise_on_sql"``.
        """
        statement = (
            select(Task)
            .options(
                selectinload(Task.steps),
                selectinload(Task.agents),
                selectinload(Task.tool_executions),
            )
            .where(Task.id == self._coerce(identifier))
        )
        return await self._fetch_one(statement)

    async def list_claimable(self, *, limit: int | None = None) -> list[Task]:
        """Return tasks that can be handed to an agent now.

        Ordered by priority then creation time, so the most urgent pending work is
        the first row a bounded page returns. A missing ``ORDER BY`` here would
        make selection order arbitrary and starve old work under load.
        """
        statement = (
            select(Task)
            .where(Task.status == TaskStatus.PENDING)
            .order_by(Task.priority, Task.created_at)
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_by_status(
        self,
        status: TaskStatus,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Task]:
        """Return tasks in one status, oldest first."""
        statement = select(Task).where(Task.status == status).order_by(Task.created_at)
        return await self._fetch_all(apply_limit(apply_offset(statement, offset), limit))

    async def list_scheduled_due(
        self,
        *,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> list[Task]:
        """Return scheduled tasks whose time has come.

        A candidate query, not a claim: no status filter is applied, so the
        caller pairs it with whatever status check it controls. Filtering here
        would hard-code a policy -- whether a cancelled task whose schedule fired
        should still run is not a data-access question.
        """
        statement = (
            select(Task)
            .where(
                Task.scheduled_for.is_not(None),
                Task.scheduled_for <= (now or datetime.now(UTC)),
            )
            .order_by(Task.scheduled_for)
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        limit: int | None = None,
    ) -> list[Task]:
        """Return a project's tasks, newest first."""
        statement = (
            select(Task).where(Task.project_id == project_id).order_by(Task.created_at.desc())
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_children(
        self,
        identifier: uuid.UUID | str,
        *,
        limit: int | None = None,
    ) -> list[Task]:
        """Return a task's direct children, oldest first."""
        statement = (
            select(Task)
            .where(Task.parent_task_id == self._coerce(identifier))
            .order_by(Task.created_at)
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_root(
        self,
        *,
        limit: int | None = None,
    ) -> list[Task]:
        """Return tasks with no parent, oldest first."""
        statement = select(Task).where(Task.parent_task_id.is_(None)).order_by(Task.created_at)
        return await self._fetch_all(apply_limit(statement, limit))

    async def start(self, identifier: uuid.UUID | str, *, now: datetime | None = None) -> Task:
        """Move a pending task to running, stamping ``started_at``.

        Refuses a task that is not pending. A start that silently overwrote
        ``started_at`` on an already-running task would lose the real start time
        and make duration metrics wrong in a way nobody notices until billing.
        """
        task = await self.get_required(identifier)
        if task.status != TaskStatus.PENDING:
            raise ConflictError(
                "the task is not pending",
                details={"task_id": str(task.id), "status": str(task.status)},
            )
        task.status = TaskStatus.RUNNING
        task.started_at = now or datetime.now(UTC)
        await self._session.flush()
        return task

    async def complete(
        self,
        identifier: uuid.UUID | str,
        *,
        result_payload: dict[str, object] | None = None,
        verification: VerificationOutcome | None = None,
        now: datetime | None = None,
    ) -> Task:
        """Mark a task completed, recording its result and verification.

        Refuses an already-finished task. ``completed_at`` is set from ``now``
        rather than derived from ``started_at``, because a task can sit queued
        for a long time before it runs and the elapsed time since creation is not
        how long the work took.
        """
        task = await self.get_required(identifier)
        if task.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
            raise ConflictError(
                "the task has already finished",
                details={"task_id": str(task.id), "status": str(task.status)},
            )
        task.status = TaskStatus.COMPLETED
        task.completed_at = now or datetime.now(UTC)
        task.result = result_payload
        if verification is not None:
            task.verification = verification
        await self._session.flush()
        return task

    async def fail(
        self,
        identifier: uuid.UUID | str,
        *,
        error: str,
        now: datetime | None = None,
    ) -> Task:
        """Mark a task failed, recording the error.

        A failure is recorded, not raised: the caller is usually a worker that
        has already caught the real exception and needs to persist why. The
        message is truncated to what the column can hold, because an untruncated
        traceback would fail the insert -- turning a recorded failure into a lost
        one, which is the opposite of the point.
        """
        task = await self.get_required(identifier)
        if task.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
            raise ConflictError(
                "the task has already finished",
                details={"task_id": str(task.id), "status": str(task.status)},
            )
        task.status = TaskStatus.FAILED
        task.completed_at = now or datetime.now(UTC)
        task.error = error[:4000]
        await self._session.flush()
        return task

    async def increment_retry(self, identifier: uuid.UUID | str) -> Task:
        """Count one more attempt, refusing past ``max_retries``.

        The bound is enforced here rather than by the caller because a retry loop
        that checks its own limit is a retry loop that can be off by one -- and
        an unbounded retry against a flaky dependency is how one bad task
        saturates a worker pool.
        """
        task = await self.get_required(identifier)
        if task.retry_count >= task.max_retries:
            raise ConflictError(
                "the task has exhausted its retries",
                details={
                    "task_id": str(task.id),
                    "retry_count": task.retry_count,
                    "max_retries": task.max_retries,
                },
            )
        task.retry_count = task.retry_count + 1
        task.status = TaskStatus.PENDING
        await self._session.flush()
        return task


class TaskStepRepository(UuidRepository[TaskStep]):
    """Task steps. Ordered by ``position``, not by id."""

    model = TaskStep

    async def list_for_task(
        self,
        task_id: uuid.UUID,
        *,
        limit: int | None = None,
    ) -> list[TaskStep]:
        """Return a task's steps in execution order."""
        statement = select(TaskStep).where(TaskStep.task_id == task_id).order_by(TaskStep.position)
        return await self._fetch_all(apply_limit(statement, limit))

    async def get_by_position(self, task_id: uuid.UUID, position: int) -> TaskStep | None:
        """Return one step by its position within the task."""
        statement = select(TaskStep).where(
            TaskStep.task_id == task_id,
            TaskStep.position == position,
        )
        return await self._fetch_one(statement)

    async def add_step(
        self,
        *,
        task_id: uuid.UUID,
        name: str,
        description: str | None = None,
        position: int | None = None,
        depends_on: list[str] | None = None,
        input_payload: dict[str, object] | None = None,
    ) -> TaskStep:
        """Append a step, defaulting ``position`` to the next free slot.

        A negative position is rejected rather than normalised. Positions order
        execution, and quietly turning ``-1`` into a valid slot would run a step
        in a place the caller did not ask for.
        """
        if position is not None and position < 0:
            raise InvalidInputError(
                "step position cannot be negative",
                details={"position": position},
            )
        if position is None:
            statement = select(TaskStep.position).where(TaskStep.task_id == task_id)
            result = await self._session.execute(statement)
            existing = [value for value in result.scalars().all() if value is not None]
            position = (max(existing) + 1) if existing else 0

        step = TaskStep(
            task_id=task_id,
            position=position,
            name=name,
            description=description,
            status=StepStatus.PENDING,
            depends_on=depends_on or [],
            input_payload=input_payload,
        )
        return await self.add(step)

    async def mark_running(
        self,
        step_id: uuid.UUID | str,
        *,
        attempt: int | None = None,
        now: datetime | None = None,
    ) -> TaskStep:
        """Move a step to running.

        With no ``attempt`` supplied the current value is incremented, because
        ``attempt`` counts *tries* and a retried step must not restart at one.
        An explicit value below 1 is refused: attempts start at 1, and a stored 0
        reads as "never tried" to anything computing retry cost.
        """
        step = await self.get_required(step_id)
        if attempt is not None:
            if attempt < 1:
                raise InvalidInputError(
                    "attempt numbering starts at 1",
                    details={"attempt": attempt},
                )
            step.attempt = attempt
        else:
            step.attempt = step.attempt + 1
        step.status = StepStatus.RUNNING
        step.started_at = now or datetime.now(UTC)
        await self._session.flush()
        return step

    async def mark_completed(
        self,
        step_id: uuid.UUID | str,
        *,
        output_payload: dict[str, object] | None = None,
        now: datetime | None = None,
    ) -> TaskStep:
        """Mark a step completed, recording its output."""
        step = await self.get_required(step_id)
        step.status = StepStatus.COMPLETED
        step.completed_at = now or datetime.now(UTC)
        if output_payload is not None:
            step.output_payload = output_payload
        await self._session.flush()
        return step

    async def mark_failed(
        self,
        step_id: uuid.UUID | str,
        *,
        error: str,
        now: datetime | None = None,
    ) -> TaskStep:
        """Mark a step failed, truncating the message to the column's size."""
        step = await self.get_required(step_id)
        step.status = StepStatus.FAILED
        step.completed_at = now or datetime.now(UTC)
        step.error = error[:4000]
        await self._session.flush()
        return step

    async def list_blocked(self, task_id: uuid.UUID, *, limit: int | None = None) -> list[TaskStep]:
        """Return steps still pending, in position order.

        A pending step whose ``depends_on`` references a step that already failed
        is included: deciding it is unreachable is a policy call for the
        orchestrator, not a filter a data class should apply.
        """
        statement = (
            select(TaskStep)
            .where(TaskStep.task_id == task_id, TaskStep.status == StepStatus.PENDING)
            .order_by(TaskStep.position)
        )
        return await self._fetch_all(apply_limit(statement, limit))


class ToolExecutionRepository(UuidRepository[ToolExecution]):
    """Records of tool calls and the permission decision that preceded each."""

    model = ToolExecution

    async def record_decision(
        self,
        *,
        tool_name: str,
        permission_level: PermissionLevel,
        decision: AuditOutcome,
        status: ToolExecutionStatus = ToolExecutionStatus.PENDING,
        task_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        decision_reason: str | None = None,
        input_payload: dict[str, object] | None = None,
        request_id: str | None = None,
        started_at: datetime | None = None,
    ) -> ToolExecution:
        """Record a permission decision for a tool call.

        ``tool_name``, ``permission_level`` and ``decision`` are keyword-only with
        no defaults, so this cannot be called without stating what was claimed and
        what was decided. All three are ``NOT NULL`` on the row as well, which
        means a decision cannot be deferred to after execution either.

        The two enums are the point of the signature. A caller passing a raw
        string is forced to make the vocabulary explicit, and the ORM stores the
        canonical member, so a denial cannot be written three different ways and
        then missed by a reviewer's query.

        A denied decision is still written, with ``status`` as the caller supplied
        it. A refusal is evidence: "the model wanted to delete a file and was not
        permitted" is the record an operator needs, and suppressing it because
        nothing happened would erase exactly the interesting case.
        """
        execution = ToolExecution(
            task_id=task_id,
            agent_id=agent_id,
            tool_name=tool_name,
            status=status,
            permission_level=permission_level,
            decision=decision,
            decision_reason=decision_reason,
            input_payload=input_payload,
            request_id=request_id,
            started_at=started_at,
        )
        return await self.add(execution)

    async def mark_succeeded(
        self,
        identifier: uuid.UUID | str,
        *,
        output_payload: dict[str, object] | None = None,
        duration_ms: int | None = None,
        now: datetime | None = None,
    ) -> ToolExecution:
        """Record a tool call that ran to completion.

        ``duration_ms`` is derived from ``started_at`` when not supplied, because
        the two are recorded in different places and letting them drift would make
        latency reporting quietly wrong.
        """
        execution = await self.get_required(identifier)
        stamp = now or datetime.now(UTC)
        execution.status = ToolExecutionStatus.SUCCEEDED
        execution.completed_at = stamp
        if output_payload is not None:
            execution.output_payload = output_payload
        execution.duration_ms = self._duration_ms(execution, stamp, duration_ms)
        await self._session.flush()
        return execution

    async def mark_failed(
        self,
        identifier: uuid.UUID | str,
        *,
        error: str,
        duration_ms: int | None = None,
        now: datetime | None = None,
    ) -> ToolExecution:
        """Record a tool failure, truncating the message to the column's size."""
        execution = await self.get_required(identifier)
        stamp = now or datetime.now(UTC)
        execution.status = ToolExecutionStatus.FAILED
        execution.completed_at = stamp
        execution.error = error[:4000]
        execution.duration_ms = self._duration_ms(execution, stamp, duration_ms)
        await self._session.flush()
        return execution

    async def mark_denied(self, identifier: uuid.UUID | str) -> ToolExecution:
        """Mark a recorded call as refused.

        Kept distinct from :meth:`mark_failed`: a denial is the system working,
        and merging the two would make "how often are we correctly refusing" the
        same question as "how often does this tool break".
        """
        execution = await self.get_required(identifier)
        execution.status = ToolExecutionStatus.DENIED
        execution.completed_at = datetime.now(UTC)
        await self._session.flush()
        return execution

    @staticmethod
    def _duration_ms(
        execution: ToolExecution,
        completed_at: datetime,
        duration_ms: int | None,
    ) -> int | None:
        """Return the caller's duration, else derive it from ``started_at``.

        Clamped at zero because the two timestamps can cross in an ordering that
        yields a small negative value, and a negative duration is a nonsense
        figure that would flow straight into latency reporting.
        """
        if duration_ms is not None:
            return duration_ms
        if execution.started_at is None:
            return None
        return max(0, int((completed_at - execution.started_at).total_seconds() * 1000))

    async def record_verification(
        self,
        identifier: uuid.UUID | str,
        *,
        outcome: VerificationOutcome,
        verified: bool = True,
    ) -> ToolExecution:
        """Record whether the verification step passed.

        Kept separate from completion because verification happens after the tool
        returns, and collapsing the two would lose the distinction between "the
        tool ran" and "the result was checked" -- the distinction that decides
        whether a result may be used.
        """
        execution = await self.get_required(identifier)
        execution.verified = verified
        execution.verification_outcome = outcome
        await self._session.flush()
        return execution

    async def list_for_task(
        self,
        task_id: uuid.UUID,
        *,
        limit: int | None = None,
    ) -> list[ToolExecution]:
        """Return a task's tool calls, oldest first."""
        statement = (
            select(ToolExecution)
            .where(ToolExecution.task_id == task_id)
            .order_by(ToolExecution.started_at)
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_denied(self, *, limit: int | None = None) -> list[ToolExecution]:
        """Return refused tool calls, newest first.

        The reviewer's view. A run of denials is either a misconfigured policy or
        an agent probing its boundaries, and both look like a spike in this
        query. Matched on the enum rather than a ``LIKE`` prefix, so a row written
        with a non-canonical spelling cannot slip past.
        """
        statement = (
            select(ToolExecution)
            .where(ToolExecution.decision == AuditOutcome.DENIED)
            .order_by(ToolExecution.started_at.desc())
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_at_level(
        self,
        permission_level: PermissionLevel,
        *,
        limit: int | None = None,
    ) -> list[ToolExecution]:
        """Return every call that claimed one permission level."""
        statement = (
            select(ToolExecution)
            .where(ToolExecution.permission_level == permission_level)
            .order_by(ToolExecution.started_at.desc())
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_awaiting_confirmation(
        self,
        *,
        limit: int | None = None,
    ) -> list[ToolExecution]:
        """Return calls parked awaiting a human decision.

        A call needing confirmation that has not been resolved is a
        half-finished operation. Surfacing it as its own query means a stuck
        confirmation is visible rather than sitting in the general listing.
        """
        statement = (
            select(ToolExecution)
            .where(ToolExecution.status == ToolExecutionStatus.AWAITING_CONFIRM)
            .order_by(ToolExecution.started_at)
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_unverified(
        self,
        *,
        limit: int | None = None,
    ) -> list[ToolExecution]:
        """Return succeeded calls whose results were never verified.

        The standing "what did we act on without checking" query. Every other read
        in this layer is a normal listing; this one is an audit question, and it
        is bounded like the rest.
        """
        statement = (
            select(ToolExecution)
            .where(
                ToolExecution.status == ToolExecutionStatus.SUCCEEDED,
                ToolExecution.verified.is_(False),
            )
            .order_by(ToolExecution.completed_at.desc())
        )
        return await self._fetch_all(apply_limit(statement, limit))


__all__ = ["TaskRepository", "TaskStepRepository", "ToolExecutionRepository"]
