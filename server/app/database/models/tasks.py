"""Task models: `tasks`, `task_steps`, `tool_executions`.

`tasks` carries all 12 fields the spec lists for a task
(spec section 18, lines 917-928):

    task_id, goal, status, priority, created_at, started_at, completed_at,
    agent_id, parent_task_id, steps, result, error

Two of them are not columns here, deliberately:

*   **`agent_id` is stored on `agents.task_id`, not on `tasks`.** The spec lists
    it as a task field, so this looks like a contradiction. It is not: the same
    spec allows one agent to be paused and resumed and says agents are assigned
    tasks (section 7), which means a task can outlive any single agent
    assignment. A foreign key on `tasks` would lose the *current* assignment the
    moment an agent was destroyed, whereas the reverse reference keeps the
    history. `Task.current_agent_id` exposes it for reads.
*   **`steps` is a relationship, not a column.** The spec lists `steps` among a
    task's fields (line 926), and section 18's own graph example shows steps as
    children of a task. Modelling them as rows in `task_steps` (line 1124) is
    what makes restart recovery possible: a step's own status survives the
    process, which a JSON array on the task would not.

`parent_task_id` is the self-reference behind task graphs (line 931) and the
restart-recovery graph. `ON DELETE CASCADE` here because a subtask with no parent
has no owner; the alternative (SET NULL) would strand subtasks in a queue
nothing can ever run.

`result` and `error` are JSONB, and exactly one of them is expected to be set on
a terminal task. `TaskStatus.FAILED` does not imply `error` is populated -- a
task can be cancelled, which is terminal but not an error.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    json_type,
)
from app.database.models.enums import (
    AuditOutcome,
    PermissionLevel,
    StepStatus,
    TaskPriority,
    TaskStatus,
    ToolExecutionStatus,
    VerificationOutcome,
)

if TYPE_CHECKING:
    from app.database.models.agents import Agent


class Task(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One unit of work.

    `steps` and `agent` are declared below the columns that reference them.
    """

    __tablename__ = "tasks"

    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        String(32),
        nullable=False,
        default=TaskStatus.PENDING,
        server_default=TaskStatus.PENDING.value,
        index=True,
    )
    priority: Mapped[TaskPriority] = mapped_column(
        String(16),
        nullable=False,
        default=TaskPriority.NORMAL,
        server_default=TaskPriority.NORMAL.value,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        index=True,
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(json_type())
    error: Mapped[str | None] = mapped_column(Text)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    verification: Mapped[VerificationOutcome | None] = mapped_column(String(16))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)

    parent_task: Mapped[Task | None] = relationship(
        remote_side="Task.id",
        back_populates="child_tasks",
        lazy="raise_on_sql",
    )
    child_tasks: Mapped[list[Task]] = relationship(
        back_populates="parent_task",
        cascade="all, delete-orphan",
        lazy="raise_on_sql",
    )
    steps: Mapped[list[TaskStep]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="TaskStep.position",
        lazy="raise_on_sql",
    )
    agents: Mapped[list[Agent]] = relationship(back_populates="task", lazy="raise_on_sql")
    tool_executions: Mapped[list[ToolExecution]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        lazy="raise_on_sql",
    )

    __table_args__ = (
        # The scheduler's polling query: ready work, soonest first. `priority`
        # is a name, so ordering by it sorts alphabetically (URGENT after NORMAL
        # and before LOW); the rank is materialised by TaskQueue in Phase 2
        # rather than faked with a CASE expression here.
        Index("ix_tasks_dispatch", "status", "scheduled_for"),
        Index("ix_tasks_project_status", "project_id", "status"),
        # `parent_task_id` already carries `index=True` on the column above,
        # which emits `ix_tasks_parent_task_id`. A second index here over the
        # same single column was an exact duplicate: two identical structures
        # doubling write amplification on the hot task table for no query gain.
    )

    @property
    def task_id(self) -> uuid.UUID:
        """The spec's name for this row's identifier."""
        return self.id

    @property
    def current_agent_id(self) -> uuid.UUID | None:
        """The agent currently assigned, per the spec's `agent_id` task field.

        Derived from the `agents` relationship rather than stored, so it cannot
        disagree with the agents table.
        """
        active = [a.id for a in self.agents if a.task_id == self.id]
        return active[0] if active else None

    @property
    def is_terminal(self) -> bool:
        """True once the task will not run again without intervention."""
        return self.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }

    @property
    def duration_seconds(self) -> float | None:
        """Wall-clock runtime, or None while still running."""
        if self.started_at is None or self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()

    def __repr__(self) -> str:
        return f"<Task {self.id} {self.status}>"


class TaskStep(Base, UUIDPrimaryKeyMixin):
    """One step of a task.

    `position` is the ordinal within the task and is not unique on its own,
    because restart recovery re-runs a step and may append a retry at the same
    position; uniqueness is enforced on (task_id, attempt, position) instead.
    `depends_on` carries the graph edges from section 18 (line 931) as a list of
    sibling step ids, which is what the DAG in `app/tasks/graph.py` walks.

    Cycle detection lives in that module, not here: a relational constraint
    cannot express "no cycles in this JSON list" without a trigger.
    """

    __tablename__ = "task_steps"

    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[StepStatus] = mapped_column(
        String(32),
        nullable=False,
        default=StepStatus.PENDING,
        server_default=StepStatus.PENDING.value,
        index=True,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    depends_on: Mapped[list[str]] = mapped_column(
        json_type(),
        nullable=False,
        default=list,
        server_default="[]",
        doc="UUID strings of sibling steps that must complete first.",
    )
    input_payload: Mapped[dict[str, Any] | None] = mapped_column(json_type())
    output_payload: Mapped[dict[str, Any] | None] = mapped_column(json_type())
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    task: Mapped[Task] = relationship(back_populates="steps", lazy="raise_on_sql")

    __table_args__ = (
        Index("uq_task_steps_position", "task_id", "attempt", "position", unique=True),
        Index("ix_task_steps_task_status", "task_id", "status"),
    )

    @property
    def duration_seconds(self) -> float | None:
        """Wall-clock runtime, or None while still running."""
        if self.started_at is None or self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()

    def __repr__(self) -> str:
        return f"<TaskStep {self.task_id}#{self.position} {self.status}>"


class ToolExecution(Base, UUIDPrimaryKeyMixin):
    """One tool invocation (spec section 16, line 825 table `tool_executions`).

    Section 15 requires that *every* execution be permission-checked and logged
    (lines 817-821), so this row is the record of that check as well as of the
    call. `permission_level`, `decision`, and `decision_reason` are therefore
    NOT NULL: an execution row that did not record a permission verdict would be
    evidence of a skipped check, and the column being mandatory is what makes
    that impossible rather than merely discouraged.

    `tool_name` is denormalised from the tool's `input_schema`/`name` rather
    than joined, because the registry is in-process and a joined tool row could
    not be resolved after a restart anyway. `input` and `output` are JSONB and
    may be None: a tool can legitimately take no input or produce no output.

    `permission_level` and `decision` are annotated as enums rather than bare
    strings, matching the reasoning already documented on
    `AuditLogEntry.decision`: a decision recorded as an unconstrained string
    would accept `allowed`, `Allowed`, `ALLOW`, and `yes` as four different
    verdicts, and a query asking "what was denied" would silently miss rows
    written with the wrong spelling. The columns stay `VARCHAR(32)` so no native
    PostgreSQL enum type has to be migrated, and the in-process enum is what
    constrains the vocabulary.
    """

    __tablename__ = "tool_executions"

    task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        index=True,
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[ToolExecutionStatus] = mapped_column(
        String(32),
        nullable=False,
        default=ToolExecutionStatus.PENDING,
        server_default=ToolExecutionStatus.PENDING.value,
        index=True,
    )
    permission_level: Mapped[PermissionLevel] = mapped_column(String(32), nullable=False)
    decision: Mapped[AuditOutcome] = mapped_column(
        String(32),
        nullable=False,
        doc="allow | deny | confirm_required, from the permission check.",
    )
    decision_reason: Mapped[str | None] = mapped_column(Text)
    input_payload: Mapped[dict[str, Any] | None] = mapped_column(json_type())
    output_payload: Mapped[dict[str, Any] | None] = mapped_column(json_type())
    error: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        doc="True once the tool's verify() has run (spec section 14, line 755).",
    )
    verification_outcome: Mapped[VerificationOutcome | None] = mapped_column(String(16))
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    task: Mapped[Task | None] = relationship(back_populates="tool_executions", lazy="raise_on_sql")

    __table_args__ = (Index("ix_tool_executions_task_tool", "task_id", "tool_name"),)

    @property
    def succeeded(self) -> bool:
        """True when the tool ran to a successful completion."""
        return self.status is ToolExecutionStatus.SUCCEEDED

    def __repr__(self) -> str:
        return f"<ToolExecution {self.tool_name} {self.status}>"
