"""Schedule timing: turn a ``schedules`` row into a real next-fire time (T170).

The spec (§44) asks for one-time, recurring, delayed and event-triggered jobs
and says to "use APScheduler initially or an equivalent lightweight scheduler".
This module uses APScheduler for exactly one thing — its **trigger engine**, a
correct, timezone- and DST-aware calendar — and nothing else. The durable job
store is the ``schedules`` table (`app/database/models/schedules.py`, T015) and
the runner is the poll loop in :mod:`app.scheduler.manager`; APScheduler's own
scheduler and job stores are deliberately not started.

**Why not APScheduler's ``AsyncIOScheduler``.** The schedule table already
exists, carries `next_run_at`, and its repository already documents the claim
(``ScheduleRepository.get_for_update``: "Two workers reading the same due
schedule would each create a task for it; the row lock makes one wait"). Running
APScheduler's own scheduler would create a *second* source of truth for when a
job fires — one in ``schedules.next_run_at`` and one inside APScheduler's job
store — and the two would drift the moment a row was edited outside APScheduler.
The spec's own words allow "APScheduler ... **or an equivalent** lightweight
scheduler"; the equivalent here is the repository plus a poll loop, and
APScheduler is used for the one part it does better than hand-rolled code: the
calendar. This mirrors the repository's own docstring, which says next-fire
computation "belongs to the scheduler (T017/T018)" and needs "a parser, a
timezone, and a policy" — that is exactly what this module supplies.

**The four kinds** map one-to-one:

*   ``INTERVAL`` — ``IntervalTrigger(seconds=n)``; a fixed period after each run.
*   ``CRON`` — ``CronTrigger.from_crontab``; §44's "at 8 AM" example. A cron
    expression that does not parse is a :class:`~app.core.errors.ScheduleError`
    (503), not a 422, because the *request* was well-formed (a non-empty
    expression) and it is the scheduler's calendar that refuses it — the same
    split ``ScheduleRepository.validate_timing`` documents when it checks only
    that something was supplied.
*   ``ONCE`` — ``DateTrigger(run_date=...)``; a delayed or one-shot run. Its
    next fire is the run time if that is still ahead, and ``None`` once it has
    passed, which is how the manager knows to complete it.
*   ``EVENT`` — **no trigger**. An event schedule has no clock; it is matched
    against the bus by :mod:`app.scheduler.automation` (T171). ``build_trigger``
    returns ``None`` so the manager never arms a timer for one, and
    ``next_run_time`` returns ``None`` so its ``next_run_at`` stays NULL — the
    repository's ``list_due`` already excludes NULL, so an event schedule can
    never be mistaken for a due timer.

Timezones are the scheduler's configured one (``SCHEDULER_TIMEZONE``), resolved
to a :class:`~zoneinfo.ZoneInfo`. An unknown zone name is a
:class:`~app.core.errors.ConfigError` at resolution time rather than a silent
fall-back to UTC, because a schedule firing in the wrong zone is exactly the bug
nobody notices until it is months late.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.errors import ConfigError, InvalidInputError, ScheduleError
from app.database.models import Schedule, ScheduleKind

__all__ = [
    "build_trigger",
    "next_run_time",
    "resolve_timezone",
    "validate_schedule",
]


def resolve_timezone(name: str) -> ZoneInfo:
    """Return ``name`` as a :class:`ZoneInfo`, or raise a ``ConfigError``.

    ``UTC`` is accepted as an alias for the zoneinfo spelling so the default
    setting does not depend on whether the platform ships a bare ``UTC`` file.
    """
    if name in {"UTC", "utc"}:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ConfigError(
            f"unknown scheduler timezone {name!r}",
            details={"timezone": name},
            cause=error,
        ) from error


def build_trigger(schedule: Schedule, *, timezone: ZoneInfo) -> BaseTrigger | None:
    """Return the APScheduler trigger for ``schedule``, or ``None`` for EVENT.

    Raises:
        InvalidInputError: the schedule is missing a field its kind needs
            (the same check `ScheduleRepository.validate_timing` makes).
        ScheduleError: the fields are present but unparseable — a cron
            expression the calendar rejects, or a one-shot with no run time.
    """
    kind = ScheduleKind(schedule.kind)
    if kind is ScheduleKind.EVENT:
        return None
    if not schedule.is_valid:
        raise InvalidInputError(
            "the schedule is missing a field its kind requires",
            details={"schedule_id": str(schedule.id), "kind": kind.value},
        )
    interval = schedule.interval_seconds
    run_at = schedule.run_at
    try:
        if kind is ScheduleKind.INTERVAL:
            if interval is None:  # guarded by is_valid; keeps the type honest
                raise InvalidInputError(
                    "an interval schedule needs interval_seconds",
                    details={"schedule_id": str(schedule.id)},
                )
            return IntervalTrigger(seconds=interval, timezone=timezone)
        if kind is ScheduleKind.CRON:
            return CronTrigger.from_crontab(schedule.cron_expression or "", timezone=timezone)
        if kind is ScheduleKind.ONCE:
            if run_at is None:  # guarded by is_valid; keeps the type honest
                raise InvalidInputError(
                    "a one-shot schedule needs a run time",
                    details={"schedule_id": str(schedule.id)},
                )
            return DateTrigger(run_date=_aware(run_at), timezone=timezone)
    except (ValueError, TypeError) as error:
        raise ScheduleError(
            "the schedule's timing fields are not valid",
            details={"schedule_id": str(schedule.id), "kind": kind.value},
            cause=error,
        ) from error
    return None


def next_run_time(
    schedule: Schedule,
    *,
    timezone: ZoneInfo,
    now: datetime | None = None,
) -> datetime | None:
    """Return ``schedule``'s next fire time at or after ``now``, or ``None``.

    Computed explicitly per kind rather than through
    ``BaseTrigger.get_next_fire_time``, because that engine's notion of a "first
    run" does not take the caller's clock: an
    :class:`~apscheduler.triggers.interval.IntervalTrigger` counts from its own
    construction instant and a ``DateTrigger`` returns its date even after it has
    passed. Both would make the poll loop's ``now`` — the value every other
    comparison uses, and the one tests inject — mean nothing here.

    * ``INTERVAL`` — a fixed cadence from the last run (or from ``now`` the first
      time), advanced in whole periods so a scheduler that was down for a while
      resumes without a burst of catch-up firings.
    * ``CRON`` — the calendar's next matching instant, which *does* honour ``now``.
    * ``ONCE`` — its run time while that is not behind ``now``, else ``None``.
    * ``EVENT`` — always ``None``; it has no clock.
    """
    moment = _aware(now) if now is not None else datetime.now(UTC)
    kind = ScheduleKind(schedule.kind)
    if kind is ScheduleKind.EVENT:
        return None
    if not schedule.is_valid:
        raise InvalidInputError(
            "the schedule is missing a field its kind requires",
            details={"schedule_id": str(schedule.id), "kind": kind.value},
        )
    if kind is ScheduleKind.ONCE:
        if schedule.run_at is None:  # guarded by is_valid
            raise InvalidInputError(
                "a one-shot schedule needs a run time",
                details={"schedule_id": str(schedule.id)},
            )
        run_at = _aware(schedule.run_at)
        return run_at if run_at >= moment else None
    if kind is ScheduleKind.INTERVAL:
        if schedule.interval_seconds is None:  # guarded by is_valid
            raise InvalidInputError(
                "an interval schedule needs interval_seconds",
                details={"schedule_id": str(schedule.id)},
            )
        period = timedelta(seconds=schedule.interval_seconds)
        base = _aware(schedule.last_run_at) if schedule.last_run_at is not None else moment
        upcoming = base + period
        while upcoming <= moment:
            upcoming += period
        return upcoming
    try:
        cron = CronTrigger.from_crontab(schedule.cron_expression or "", timezone=timezone)
    except ValueError as error:
        raise ScheduleError(
            "the cron expression is not valid",
            details={"schedule_id": str(schedule.id), "expression": schedule.cron_expression},
            cause=error,
        ) from error
    upcoming = cron.get_next_fire_time(None, moment)
    return _aware(upcoming) if upcoming is not None else None


def validate_schedule(schedule: Schedule, *, timezone: ZoneInfo) -> None:
    """Raise if ``schedule`` cannot be armed; a no-op if it can.

    The single gate the manager calls before persisting a schedule, so an
    unparseable cron expression is refused at *creation* time (a 4xx/5xx the
    caller can act on) rather than discovered by the poll loop at 3 a.m.
    """
    build_trigger(schedule, timezone=timezone)


def _aware(value: datetime) -> datetime:
    """Return ``value`` with a timezone, assuming UTC when it has none.

    ``DateTrigger`` and SQLite round-trips both drop tzinfo, and a naive
    datetime compared against an aware one raises ``TypeError`` deep in the
    poll loop. Normalising here keeps every downstream comparison aware.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
