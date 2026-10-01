"""Event persistence: `events`.

The spec names the table (line 1126), lists event type names (spec section 19,
lines 959-999) but specifies no columns, and requires that "everything important
generates events" (line 2941).

`event_type` is a plain string rather than an enum, and that is a deliberate
deviation from the rest of this package. Two reasons:

1.  The event bus must accept events the current build does not know about --
    the spec's own lists disagree with each other (`DEVICE_OFFLINE` at line 2957
    versus `DEVICE_DISCONNECTED` at line 985), and a client or a later phase may
    add types. A database enum would reject them.
2.  The set is not stable yet. Phase 2 task T030 defines the canonical types;
    hard-coding them into the schema now would make every addition a migration.

`payload` is JSONB and non-null with a `{}` default, so a subscriber can always
index it without a None check.

Retention: `.env.example` sets `events_persist=true` and
`event_retention_days=30` (settings lines 524-525). The pruning itself is
Phase 3+ work; `occurred_at` is indexed here so that query is possible without
another migration later.

This table is the *durable* record. The in-memory bus in `app/events/bus.py` is
the live path, and a handler that must not be missed writes through to here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, UUIDPrimaryKeyMixin, json_type


class Event(Base, UUIDPrimaryKeyMixin):
    """One persisted event."""

    __tablename__ = "events"

    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(
        json_type(), nullable=False, default=dict, server_default="{}"
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    sequence: Mapped[int | None] = mapped_column(
        BigInteger(),
        nullable=True,
        doc="Monotonic per-type counter where the emitter can supply one, so "
        "subscribers can detect gaps or reordering. NULL when unknown.",
    )
    source: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(
        String(64),
        index=True,
        doc="Ties related events together across a request (spec section 32 "
        "lists request_id as a structured log field).",
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    device_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_events_type_time", "event_type", "occurred_at"),
        Index("ix_events_correlation", "correlation_id", "occurred_at"),
        Index("ix_events_task_time", "task_id", "occurred_at"),
    )

    @property
    def is_error(self) -> bool:
        """True when the event carries an error payload."""
        return self.error is not None

    def __repr__(self) -> str:
        return f"<Event {self.event_type} at {self.occurred_at.isoformat()}>"
