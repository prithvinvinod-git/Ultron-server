"""Agent models: `agents` and `agent_logs`.

`agents` carries all 12 attributes the spec lists for every agent
(spec section 6, lines 443-454), in that order:

    agent_id, agent_type, name, description, status, task_id, model,
    tools, permissions, memory_namespace, created_at, updated_at

Three of those need a judgement call:

*   **`tools` and `permissions`** are JSONB lists rather than join tables. The
    spec treats them as attributes of an agent (lines 450-451) and never as
    entities with ids, and the tool registry in Phase 2 is in-process
    (`app/tools/registry.py`), so a row per tool would have nothing to point at.
    A foreign key to a non-existent table is worse than a name.
*   **`agent_type`** is a free string, not an enum. The spec never fixes a value
    set; section 40 forbids hard-coding agent types into the core, and the
    appendix list at lines 2687-2699 is labelled "Additional agents" and is
    plainly not closed.
*   **`status`** is `AgentStatus`, the ten values the spec does enumerate
    (lines 460-481).

`agent_logs` exists because section 6 requires agents to emit events during
execution (line 483) and section 32 requires structured logs. It is a separate
table rather than a JSON column on `agents` because agent output is unbounded and
variable-rate: a log that grows inside the agent row would rewrite the row on
every line and grow `agents` past any sane `SELECT *`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    json_type,
)
from app.database.models.enums import AgentStatus

if TYPE_CHECKING:
    from app.database.models.tasks import Task


class Agent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A single agent instance.

    `agent_id` is aliased onto the `id` primary key. The spec names the
    attribute `agent_id` (line 443) and the API exposes `/agents/{id}`
    (line 1314), so the column has to be readable under that name even though
    the column itself is `id`.
    """

    __tablename__ = "agents"

    agent_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[AgentStatus] = mapped_column(
        String(32),
        nullable=False,
        default=AgentStatus.CREATED,
        server_default=AgentStatus.CREATED.value,
        index=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        index=True,
        doc="The task this agent is currently assigned. SET NULL rather than "
        "CASCADE: deleting a task must not delete the agent, which may be "
        "reassignable and whose logs are the record of what it did.",
    )
    model: Mapped[str | None] = mapped_column(String(255))
    tools: Mapped[list[str]] = mapped_column(
        json_type(),
        nullable=False,
        default=list,
        server_default="[]",
    )
    permissions: Mapped[list[str]] = mapped_column(
        json_type(),
        nullable=False,
        default=list,
        server_default="[]",
        doc="Permission level names the agent holds (e.g. READ_ONLY). "
        "NAME strings, not digits: the level is meaningful and a bare 3 in a "
        "payload is unreadable.",
    )
    memory_namespace: Mapped[str | None] = mapped_column(String(128), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    parent_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        doc="Set when this agent was spawned by another (spec section 41).",
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    task: Mapped[Task | None] = relationship(back_populates="agents", lazy="selectin")
    logs: Mapped[list[AgentLog]] = relationship(
        back_populates="agent",
        cascade="all, delete-orphan",
        lazy="raise_on_sql",
    )

    __table_args__ = (
        Index("ix_agents_status_type", "status", "agent_type"),
        Index("ix_agents_project_status", "project_id", "status"),
    )

    @property
    def agent_id(self) -> uuid.UUID:
        """The spec's name for this row's identifier."""
        return self.id

    @property
    def is_active(self) -> bool:
        """True while the agent is mid-lifecycle rather than finished or failed."""
        return self.status in {
            AgentStatus.INITIALIZING,
            AgentStatus.READY,
            AgentStatus.RUNNING,
            AgentStatus.WAITING,
            AgentStatus.VERIFYING,
        }

    @property
    def is_terminal(self) -> bool:
        """True once the agent can no longer change state on its own."""
        return self.status in {
            AgentStatus.COMPLETED,
            AgentStatus.FAILED,
            AgentStatus.CANCELLED,
            AgentStatus.TIMEOUT,
        }

    def __repr__(self) -> str:
        return f"<Agent {self.name} ({self.agent_type}) {self.status}>"


class AgentLog(Base, UUIDPrimaryKeyMixin):
    """One log line from an agent.

    `emitted_at` is written by the caller rather than defaulted by the database,
    because a log line describes when the thing happened, not when it was
    persisted; the two can differ by a long queue delay.
    """

    __tablename__ = "agent_logs"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    level: Mapped[str] = mapped_column(
        String(16), nullable=False, default="INFO", server_default="INFO"
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(
        json_type(), nullable=False, default=dict, server_default="{}"
    )
    request_id: Mapped[str | None] = mapped_column(String(64))
    emitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    agent: Mapped[Agent] = relationship(back_populates="logs", lazy="raise_on_sql")

    __table_args__ = (Index("ix_agent_logs_agent_sequence", "agent_id", "sequence", "emitted_at"),)

    def __repr__(self) -> str:
        return f"<AgentLog {self.agent_id} {self.level}>"
