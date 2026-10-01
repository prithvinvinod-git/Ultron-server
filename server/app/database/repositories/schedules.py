"""Schedule repository (T015).

**This repository does not compute when a schedule should next fire.** It records
what a caller has already decided. "Next fire time" for a cron expression needs
a parser, a timezone, and a policy for what happens when a run overruns its
interval -- three things that belong to the scheduler (T017/T018), not to a
data-access class with no clock and no calendar dependency.

The visible consequence is that :func:`ScheduleRepository.set_next_run` requires
``next_run_at`` as an argument. There is no ``mark_ran`` that guesses. A
repository that computed it would be untestable without freezing time, and its
guess would be wrong for exactly the cases that matter: DST transitions, catch-up
after downtime, and intervals shorter than the run itself.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.errors import InvalidInputError
from app.database.models import Schedule, ScheduleKind, ScheduleStatus
from app.database.repositories.base import UuidRepository, apply_limit


class ScheduleRepository(UuidRepository[Schedule]):
    """Schedules."""

    model = Schedule

    async def list_active(self, *, limit: int | None = None) -> list[Schedule]:
        """Return active schedules, soonest first.

        ``nulls_last`` keeps a schedule that has never run -- and therefore has no
        next run -- from sorting to the top of a due-work queue.
        """
        statement = (
            select(Schedule)
            .where(Schedule.status == ScheduleStatus.ACTIVE)
            .order_by(Schedule.next_run_at.asc().nulls_last(), Schedule.created_at)
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_due(self, *, now: datetime, limit: int | None = None) -> list[Schedule]:
        """Return active schedules whose ``next_run_at`` has passed.

        ``now`` is a required argument. A default would be evaluated at call time
        and look harmless, but it hides that the query is time-dependent and
        leaves it untestable without a clock injection.
        """
        statement = (
            select(Schedule)
            .where(
                Schedule.status == ScheduleStatus.ACTIVE,
                Schedule.next_run_at.is_not(None),
                Schedule.next_run_at <= now,
            )
            .order_by(Schedule.next_run_at)
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def get_for_update(self, identifier: uuid.UUID | str) -> Schedule:
        """Return one schedule with ``SELECT ... FOR UPDATE``.

        This is the claim. Two workers reading the same due schedule would each
        create a task for it; the row lock makes one wait and then see the
        already-updated ``next_run_at``.

        Callers must invoke this inside the transaction that performs the claiming
        work. A lock released at the end of a read-only transaction protects
        nothing, and the usual way to get that wrong is to call this from a
        helper that is itself wrapped in its own ``session_scope``.

        Requires PostgreSQL. SQLite ignores the clause, so the concurrency
        guarantee here can only be tested as an integration test -- which is
        exactly the gap T025/T026 closes.
        """
        statement = (
            select(Schedule).where(Schedule.id == self._coerce(identifier)).with_for_update()
        )
        return (await self._session.execute(statement)).scalar_one()

    async def set_next_run(
        self,
        identifier: uuid.UUID | str,
        *,
        next_run_at: datetime,
        last_task_id: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> Schedule:
        """Record a run and the next fire time, incrementing the run count.

        ``last_error`` is cleared and the failure count reset. A schedule that has
        recovered should not keep reporting a failure that has since stopped
        happening, and a stale error here is indistinguishable from a current one
        to anyone reading the row.
        """
        schedule = await self.get_required(identifier)
        schedule.next_run_at = next_run_at
        schedule.last_run_at = now or datetime.now(UTC)
        schedule.last_task_id = last_task_id
        schedule.run_count = schedule.run_count + 1
        schedule.failure_count = 0
        schedule.last_error = None
        await self._session.flush()
        return schedule

    async def record_failure(
        self,
        identifier: uuid.UUID | str,
        *,
        error: str,
        now: datetime | None = None,
    ) -> Schedule:
        """Record a failed run, incrementing the failure count.

        The error is truncated to the column's size and ``next_run_at`` is left
        alone. Whether to retry, back off, or give up is a policy decision, and
        this repository has no basis for making it.
        """
        schedule = await self.get_required(identifier)
        schedule.failure_count = schedule.failure_count + 1
        schedule.last_error = error[:4000]
        schedule.last_run_at = now or datetime.now(UTC)
        await self._session.flush()
        return schedule

    async def mark_completed(self, identifier: uuid.UUID | str) -> Schedule:
        """Mark a one-shot schedule finished.

        Refuses a recurring kind. A ``CRON`` or ``INTERVAL`` schedule that reached
        ``COMPLETED`` would stop firing, and the likelier cause of that call is a
        caller confusing the two -- so it is rejected rather than honoured.
        """
        schedule = await self.get_required(identifier)
        if schedule.kind is not ScheduleKind.ONCE:
            raise InvalidInputError(
                "only a one-shot schedule can complete",
                details={"kind": str(schedule.kind)},
            )
        schedule.status = ScheduleStatus.COMPLETED
        schedule.next_run_at = None
        await self._session.flush()
        return schedule

    async def pause(self, identifier: uuid.UUID | str) -> Schedule:
        """Pause a schedule, keeping its configuration for a later resume."""
        schedule = await self.get_required(identifier)
        schedule.status = ScheduleStatus.PAUSED
        await self._session.flush()
        return schedule

    async def resume(self, identifier: uuid.UUID | str) -> Schedule:
        """Resume a paused schedule, or revive a failed one.

        A completed schedule stays completed: :meth:`mark_completed` is terminal.
        """
        schedule = await self.get_required(identifier)
        if schedule.status in {ScheduleStatus.PAUSED, ScheduleStatus.FAILED}:
            schedule.status = ScheduleStatus.ACTIVE
        await self._session.flush()
        return schedule

    async def validate_timing(
        self,
        kind: ScheduleKind,
        *,
        cron_expression: str | None = None,
        interval_seconds: int | None = None,
        run_at: datetime | None = None,
    ) -> None:
        """Check that the fields a schedule kind needs are present.

        Called before persisting rather than enforced by the database, because a
        missing field on a ``CRON`` schedule is a caller mistake with an obvious
        message, and a ``CHECK`` constraint could only report "constraint
        violated".

        Whether a cron expression *parses* is not decided here. That needs a
        parser, which arrives with the scheduler in T017/T018; this confirms only
        that something was supplied. An unparseable expression is caught by the
        scheduler, where a real calendar is available to say why.
        """
        if kind is ScheduleKind.CRON and not cron_expression:
            raise InvalidInputError("a cron schedule needs a cron expression")
        if kind is ScheduleKind.INTERVAL:
            if interval_seconds is None:
                raise InvalidInputError("an interval schedule needs interval_seconds")
            if interval_seconds < 1:
                raise InvalidInputError(
                    "interval_seconds must be at least 1",
                    details={"interval_seconds": interval_seconds},
                )
        if kind is ScheduleKind.ONCE and run_at is None:
            raise InvalidInputError("a one-shot schedule needs run_at")


__all__ = ["ScheduleRepository"]
