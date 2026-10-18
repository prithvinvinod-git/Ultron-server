"""The scheduler (T170, §44): one-time, recurring and delayed jobs.

§44 asks for a scheduler that fires one-time, recurring and delayed jobs and
persists important ones, and its amendment (§66.11, §65.18) is emphatic that this
and the task DAG are the **only** scheduling machinery — a scheduled phone call is
an ordinary job whose action is `telephony.initiate_call`, and a background agent
is an ordinary job that produces a task. So this module does one thing: it turns a
due :class:`~app.database.models.Schedule` row into a real
:class:`~app.database.models.Task` and records that it ran.

**The row is the job store.** §44's "persist important scheduled jobs" and the
existing T017/T018 schema settle the design: ``schedules.next_run_at`` is when a
job fires, ``ScheduleRepository.list_due`` finds the due ones, and
``ScheduleRepository.get_for_update`` is the claim that stops two workers firing
the same row (the repository documents this in full). APScheduler is used only for
its calendar (:mod:`app.scheduler.triggers`); running its own scheduler would add
a second, drifting source of truth for "when does this fire".

**The task is injected.** What a firing *does* — which goal, which steps, which
priority — is a `TaskFactory`, a
``Callable[[AsyncSession, Schedule], Awaitable[Task]]`` handed in at construction,
the same seam `TaskExecutor` gives its `run_step`. The container supplies the real
one (:mod:`app.scheduler.planning`); tests supply a fake. The scheduler therefore
never imports the task/agent runtime.

**A firing is one transaction.** :meth:`ScheduleManager.fire` claims the row with
``SELECT ... FOR UPDATE``, re-checks it is still due *and* active, builds the task,
and records the run in one unit of work. A schedule another worker already advanced
is skipped; a job whose factory raised is recorded on the row (`record_failure`)
and the loop continues, because §44's point is that recurring work must not be one
bad goal away from silence. The failure is also the hook T173 notifies through,
injected as an optional callable so §45's provider stays out of this module.

**The monitor is optional.** :meth:`start` runs one poll task calling
:meth:`run_due` on an interval; :meth:`stop` cancels and awaits it. Tests call
:meth:`run_due` directly so there is no sleeping — the deterministic style the
device manager's ``sweep(now=...)`` uses.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import SchedulerSettings
from app.core.errors import InvalidInputError, NotFoundError, UltronError
from app.database.models import (
    Schedule,
    ScheduleKind,
    ScheduleStatus,
    Task,
)
from app.database.repositories.schedules import ScheduleRepository
from app.events.bus import EventBus
from app.events.types import EventType
from app.observability.logging import get_logger
from app.scheduler.triggers import next_run_time, resolve_timezone, validate_schedule

__all__ = ["DEFAULT_POLL_INTERVAL", "Notifier", "ScheduleManager", "TaskFactory"]

_LOGGER = get_logger(__name__)

#: What a firing produces: a real task, built from the schedule and committed in
#: the firing transaction. Injected so the scheduler never imports the task or
#: agent runtime and tests never need one.
TaskFactory = Callable[[AsyncSession, Schedule], Awaitable[Task]]

#: How a failed firing is reported (T173 fills this with the notification
#: service). Injected so §45's provider stays out of the scheduler's imports.
Notifier = Callable[[Schedule, BaseException], Awaitable[None]]

#: Default poll cadence, seconds: a "call me at 8 PM" job is not noticeably late,
#: and the database is not hammered.
DEFAULT_POLL_INTERVAL = 5.0


class ScheduleManager:
    """The §44 scheduler: persist schedules, fire the due ones, never stop."""

    def __init__(
        self,
        *,
        settings: SchedulerSettings,
        session_scope: Callable[[], Any],
        create_task: TaskFactory,
        bus: EventBus | None = None,
        notify: Notifier | None = None,
        poll_interval: float | None = None,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        """Bind the scheduler to its session scope, task factory and bus.

        ``now`` and ``sleep`` are injectable so the poll loop and the fire path
        are deterministic in tests without freezing the clock or sleeping.
        """
        self._settings = settings
        self._session_scope = session_scope
        self._create_task = create_task
        self._bus = bus
        self._notify = notify
        self._interval = DEFAULT_POLL_INTERVAL if poll_interval is None else poll_interval
        self._now = now or (lambda: datetime.now(UTC))
        self._sleep = sleep or asyncio.sleep
        self._tz: ZoneInfo = resolve_timezone(settings.timezone)
        self._task: asyncio.Task[None] | None = None
        self._fired = 0
        self._failed = 0

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    @property
    def settings(self) -> SchedulerSettings:
        """The scheduler's settings."""
        return self._settings

    @property
    def timezone(self) -> ZoneInfo:
        """The resolved timezone every trigger is built in."""
        return self._tz

    @property
    def running(self) -> bool:
        """Whether the poll loop is live."""
        return self._task is not None and not self._task.done()

    @property
    def fired(self) -> int:
        """Firings that produced a task."""
        return self._fired

    @property
    def failed(self) -> int:
        """Firings that failed to produce a task."""
        return self._failed

    # ------------------------------------------------------------------ #
    # Schedule CRUD (persisted "important scheduled jobs")
    # ------------------------------------------------------------------ #

    async def create(
        self,
        *,
        name: str,
        kind: ScheduleKind | str,
        payload: Mapping[str, Any] | None = None,
        cron_expression: str | None = None,
        interval_seconds: int | None = None,
        run_at: datetime | None = None,
        project_id: str | uuid.UUID | None = None,
        created_by_id: str | uuid.UUID | None = None,
        next_run_at: datetime | None = None,
    ) -> Schedule:
        """Create and persist a schedule, arming its first ``next_run_at``.

        The kind's fields are validated (the repository's own rule) and the
        timing is parsed (:mod:`app.scheduler.triggers`) *before* the row is
        written, so an unparseable cron expression is refused here rather than
        discovered by the poll loop. A time-based schedule with no explicit
        ``next_run_at`` gets its first fire computed from the trigger; an EVENT
        schedule keeps ``NULL`` and is matched on the bus (T171).
        """
        stored_kind = _ensure_kind(kind)
        if not isinstance(name, str) or not name.strip():
            raise InvalidInputError(
                "a schedule needs a non-empty name",
                details={"name": str(name)[:100]},
            )
        async with self._session_scope() as session:
            repo = ScheduleRepository(session)
            await repo.validate_timing(
                stored_kind,
                cron_expression=cron_expression,
                interval_seconds=interval_seconds,
                run_at=run_at,
            )
            schedule = Schedule(
                name=name,
                kind=stored_kind,
                status=ScheduleStatus.ACTIVE,
                cron_expression=cron_expression,
                interval_seconds=interval_seconds,
                run_at=_aware(run_at) if run_at is not None else None,
                payload=dict(payload or {}),
                project_id=_uuid(project_id, field="project_id"),
                created_by_id=_uuid(created_by_id, field="created_by_id"),
            )
            validate_schedule(schedule, timezone=self._tz)
            first = (
                next_run_at
                if next_run_at is not None
                else next_run_time(schedule, timezone=self._tz, now=self._now())
            )
            schedule.next_run_at = _aware(first) if first is not None else None
            return await repo.add(schedule)

    async def get(self, schedule_id: str | uuid.UUID) -> Schedule:
        """Return one schedule, or ``NotFoundError``."""
        async with self._session_scope() as session:
            return await ScheduleRepository(session).get_required(schedule_id)

    async def list_schedules(
        self,
        *,
        active_only: bool = False,
        limit: int | None = None,
    ) -> list[Schedule]:
        """Return schedules, optionally only the active ones."""
        async with self._session_scope() as session:
            repo = ScheduleRepository(session)
            if active_only:
                return await repo.list_active(limit=limit)
            return await repo.list_all(limit=limit)

    async def pause(self, schedule_id: str | uuid.UUID) -> Schedule:
        """Pause a schedule; it stops being due but keeps its definition."""
        async with self._session_scope() as session:
            return await ScheduleRepository(session).pause(schedule_id)

    async def resume(self, schedule_id: str | uuid.UUID) -> Schedule:
        """Resume a paused schedule and recompute its next fire.

        A resumed schedule whose ``next_run_at`` is in the past would fire
        immediately every poll until it was re-armed, so the trigger's next run
        *after now* is written before the row goes active again.
        """
        async with self._session_scope() as session:
            repo = ScheduleRepository(session)
            schedule = await repo.resume(schedule_id)
            if ScheduleStatus(schedule.status) is ScheduleStatus.ACTIVE:
                schedule.next_run_at = next_run_time(schedule, timezone=self._tz, now=self._now())
                await session.flush()
            return schedule

    async def delete(self, schedule_id: str | uuid.UUID) -> bool:
        """Delete a schedule, returning whether a row was removed."""
        async with self._session_scope() as session:
            return await ScheduleRepository(session).delete(schedule_id)

    # ------------------------------------------------------------------ #
    # Firing
    # ------------------------------------------------------------------ #

    async def run_due(self, *, now: datetime | None = None) -> list[str]:
        """Fire every due schedule, returning the task ids that were created.

        The due list is read once; each id is then fired in its own transaction,
        which re-reads and re-checks the row under the claim. A schedule another
        worker advanced between the read and the fire is skipped there, not here.
        """
        moment = _aware(now) if now is not None else self._now()
        async with self._session_scope() as session:
            due = await ScheduleRepository(session).list_due(now=moment)
            ids = [schedule.id for schedule in due]
        fired: list[str] = []
        for schedule_id in ids:
            try:
                task = await self.fire(schedule_id, now=moment)
            except NotFoundError:
                continue  # deleted between the read and the fire
            if task is not None:
                fired.append(str(task.id))
        return fired

    async def fire(
        self,
        schedule_id: str | uuid.UUID,
        *,
        now: datetime | None = None,
    ) -> Task | None:
        """Claim and run one due schedule, returning the task it produced.

        Returns ``None`` when the schedule is not active, was already advanced by
        another worker, or firing failed (recorded on the row). Raises only
        ``NotFoundError`` for a row that no longer exists.
        """
        return await self._execute(
            schedule_id,
            _aware(now) if now is not None else self._now(),
            require_due=True,
        )

    async def trigger_event(
        self,
        schedule_id: str | uuid.UUID,
        *,
        now: datetime | None = None,
    ) -> Task | None:
        """Run an ``EVENT`` schedule now, because its event arrived (T171).

        Unlike :meth:`fire` this does **not** look at ``next_run_at``: an event
        schedule has no clock, so it has no due time to compare against. It still
        takes the row lock and still requires ``ACTIVE`` — so a paused schedule
        ignores its event, and two events arriving together produce two tasks
        (each is a distinct occurrence) but the row is never written concurrently.
        """
        return await self._execute(
            schedule_id,
            _aware(now) if now is not None else self._now(),
            require_due=False,
        )

    async def _execute(
        self,
        schedule_id: str | uuid.UUID,
        moment: datetime,
        *,
        require_due: bool,
    ) -> Task | None:
        """The shared firing body: claim, build, record, announce."""
        async with self._session_scope() as session:
            repo = ScheduleRepository(session)
            try:
                schedule = await repo.get_for_update(schedule_id)
            except NoResultFound as error:
                raise NotFoundError("schedules", str(schedule_id)) from error
            if ScheduleStatus(schedule.status) is not ScheduleStatus.ACTIVE:
                return None
            if require_due:
                due_at = schedule.next_run_at
                if due_at is None or _aware(due_at) > moment:
                    return None  # already fired/advanced by another worker
            try:
                task = await self._create_task(session, schedule)
            except Exception as exc:
                self._failed += 1
                await repo.record_failure(schedule.id, error=_describe(exc), now=moment)
                await self._report_failure(schedule, exc)
                return None
            await self._advance(repo, schedule, task, now=moment)
            self._fired += 1
            await self._publish_triggered(task, schedule)
            return task

    async def _advance(
        self,
        repo: ScheduleRepository,
        schedule: Schedule,
        task: Task,
        *,
        now: datetime,
    ) -> None:
        """Record the run and set (or clear) the next fire time."""
        kind = ScheduleKind(schedule.kind)
        if kind is ScheduleKind.ONCE:
            await repo.set_next_run(schedule.id, next_run_at=now, last_task_id=task.id, now=now)
            await repo.mark_completed(schedule.id)
            return
        if kind is ScheduleKind.EVENT:
            await repo.record_run(schedule.id, last_task_id=task.id, now=now)
            return
        upcoming = next_run_time(schedule, timezone=self._tz, now=now)
        if upcoming is None:
            await repo.record_failure(
                schedule.id,
                error="the schedule has no future fire time",
                now=now,
            )
            return
        await repo.set_next_run(schedule.id, next_run_at=upcoming, last_task_id=task.id, now=now)

    async def _publish_triggered(self, task: Task, schedule: Schedule) -> None:
        await self._publish(
            EventType.SCHEDULE_TRIGGERED,
            {
                "schedule_id": str(schedule.id),
                "schedule_name": schedule.name,
                "kind": str(ScheduleKind(schedule.kind)),
                "task_id": str(task.id),
                "goal": task.goal,
                "last_task_id": str(task.id),
            },
            task_id=str(task.id),
        )

    async def _report_failure(self, schedule: Schedule, exc: BaseException) -> None:
        _LOGGER.warning(
            "scheduled job failed",
            extra={
                "event": "scheduler.job_failed",
                "schedule_id": str(schedule.id),
                "schedule_name": schedule.name,
            },
        )
        if self._notify is not None:
            with contextlib.suppress(Exception):
                await self._notify(schedule, exc)

    async def _publish(
        self,
        event_type: EventType,
        payload: Mapping[str, Any],
        *,
        task_id: str,
    ) -> None:
        """Best-effort publish: a broken bus never changes a firing's outcome."""
        if self._bus is None:
            return
        try:
            await self._bus.publish(event_type, payload, task_id=task_id, persist=True)
        except Exception:
            _LOGGER.warning(
                "scheduled event not published",
                extra={"event_type": event_type.value},
                exc_info=True,
            )

    # ------------------------------------------------------------------ #
    # Monitor
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Start the poll loop, if enabled and not already running."""
        if not self._settings.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._run_loop(), name="schedule-manager")
        _LOGGER.info(
            "scheduler started",
            extra={
                "event": "scheduler.started",
                "poll_interval": self._interval,
                "timezone": self._tz.key,
            },
        )

    async def stop(self) -> None:
        """Cancel the poll loop and wait for it to finish."""
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        _LOGGER.info("scheduler stopped", extra={"event": "scheduler.stopped"})

    async def _run_loop(self) -> None:
        while True:
            try:
                await self.run_due()
            except Exception:
                _LOGGER.error(
                    "scheduler poll failed",
                    extra={"event": "scheduler.poll_failed"},
                    exc_info=True,
                )
            await self._sleep(self._interval)

    def snapshot(self) -> dict[str, Any]:
        """Counters for a health check or the observability phase."""
        return {
            "enabled": self._settings.enabled,
            "running": self._task is not None and not self._task.done(),
            "fired": self._fired,
            "failed": self._failed,
            "timezone": self._tz.key,
        }


# -------------------------------------------------------------------------- #
# Pure helpers
# -------------------------------------------------------------------------- #


def _aware(value: datetime) -> datetime:
    """Return ``value`` timezone-aware (UTC assumed when naive)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _ensure_kind(kind: ScheduleKind | str) -> ScheduleKind:
    """Coerce ``kind`` to a :class:`ScheduleKind`, refusing an unknown one."""
    if isinstance(kind, ScheduleKind):
        return kind
    try:
        return ScheduleKind(kind)
    except ValueError as error:
        raise InvalidInputError(
            "unknown schedule kind",
            details={"kind": str(kind)[:40], "expected": [m.value for m in ScheduleKind]},
        ) from error


def _uuid(value: str | uuid.UUID | None, *, field: str) -> uuid.UUID | None:
    """Parse an optional id, refusing a malformed one (422, never a 404)."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as error:
        raise InvalidInputError(
            f"{field} must be a UUID",
            details={field: str(value)[:100]},
        ) from error


def _describe(exc: BaseException) -> str:
    """Render a firing failure as the one line the schedule row records."""
    if isinstance(exc, UltronError):
        return f"{type(exc).__name__}: {exc.message}"
    return f"{type(exc).__name__}: {exc}"
