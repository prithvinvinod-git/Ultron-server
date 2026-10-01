"""Schedule model: `schedules`.

The spec names the table only in the architecture diagram (line 3096) -- it is
absent from section 23's list (lines 1119-1136) -- and defines no columns. Section
44 requires it, names four kinds at lines 1778-1781, and says "persist important
scheduled jobs" (line 1795).

That last requirement is why this table exists as rows rather than in-memory
timers: a schedule the server forgets on restart is a schedule that silently
stops firing, and section 44's whole point is recurring work.

Kinds are `ScheduleKind`, the four the spec names. `cron_expression` and
`interval_seconds` are mutually exclusive by kind but are *not* enforced by a
database constraint: the parser belongs to the scheduler in Phase 2+, and a
CHECK written here would have to duplicate its rules. `JobDefinition.next_run_at`
reports when the row is next due, and returns None for a malformed schedule
rather than raising -- a broken schedule must not stop the scheduler loop from
running the others.

`last_run_at` plus `SCHEDULE_TRIGGERED` events (line 998) give the two things a
schedule needs to be safe to re-run: a record of what happened last time, and an
event every time it fires.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, json_type
from app.database.models.enums import ScheduleKind, ScheduleStatus


class Schedule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A recurring or one-shot trigger."""

    __tablename__ = "schedules"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[ScheduleKind] = mapped_column(
        String(16),
        nullable=False,
        default=ScheduleKind.INTERVAL,
        server_default=ScheduleKind.INTERVAL.value,
    )
    status: Mapped[ScheduleStatus] = mapped_column(
        String(16),
        nullable=False,
        default=ScheduleStatus.ACTIVE,
        server_default=ScheduleStatus.ACTIVE.value,
        index=True,
    )
    cron_expression: Mapped[str | None] = mapped_column(String(128))
    interval_seconds: Mapped[int | None] = mapped_column(Integer)
    run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        doc="For ONCE schedules. Kept as a column rather than folded into "
        "`interval_seconds` so a one-shot trigger is readable without decoding it.",
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        json_type(),
        nullable=False,
        default=dict,
        server_default="{}",
        doc="What to run: the goal text, tool name, or event filter.",
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        doc="The task the last firing produced. SET NULL so pruning a finished "
        "task does not delete the schedule that made it.",
    )
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_schedules_due", "status", "next_run_at"),)

    @property
    def is_due(self) -> bool:
        """True when the schedule is active and its next run has arrived.

        Returns False rather than raising when `next_run_at` is NULL, because a
        NULL means the scheduler has not computed a next time -- either the
        schedule is malformed or it is a one-shot that has already fired. Either
        way it is not due, and guessing a time here would double-fire it.
        """
        if self.status is not ScheduleStatus.ACTIVE or self.next_run_at is None:
            return False
        due = self.next_run_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        return due <= datetime.now(UTC)

    @property
    def is_valid(self) -> bool:
        """Report whether the schedule carries the fields its kind requires.

        A CHECK constraint could express this, but only by re-stating the rule
        for each kind and keeping it in sync with the scheduler's parser. This
        method is the single definition; the scheduler calls it before firing
        and records the reason in `last_error`.
        """
        match self.kind:
            case ScheduleKind.INTERVAL:
                return self.interval_seconds is not None and self.interval_seconds > 0
            case ScheduleKind.CRON:
                return bool(self.cron_expression)
            case ScheduleKind.ONCE:
                return self.run_at is not None
            case ScheduleKind.EVENT:
                return bool(self.payload)
            case _:
                return False

    def __repr__(self) -> str:
        return f"<Schedule {self.name} {self.kind} {self.status}>"
