"""Event-triggered automation (T171, §44's fourth kind): bus event → task.

A schedule of kind ``EVENT`` has no clock. §44 lists it beside one-time,
recurring and delayed jobs, and the amendment (§66.11) is that "if X then run Y" is
just another scheduled job: the trigger is an event on the bus (§19) and the
action is a task, exactly as a timer's action is. `app.scheduler.triggers` gives an
event schedule no trigger and leaves its ``next_run_at`` NULL, so the poll loop
never sees it; *this* module is what makes it fire.

**The filter lives in the payload.** A schedule's ``payload`` already carries the
workflow (:mod:`app.scheduler.workflows`); the event filter is two more keys beside
it:

*   ``event`` — the event type to match, verbatim (e.g. ``DEVICE_CONNECTED``).
*   ``match`` — optional ``{field: value}`` pairs; every pair must equal the
    event's payload for the schedule to fire. This is §44's "event filter" and
    §64.18's example workflows ("when the front-door sensor reports motion after
    dark, ..."): a match on ``device_id`` plus one field.

**A lagged stream is not an event.** The bus hands a slow subscriber a synthetic
``stream.lagged`` envelope instead of dropping silently (the same contract
`PersistEvents` relies on). That envelope is a delivery *status*, not something a
user configured a workflow to react to, so it is ignored here.

**Matching is pure; firing is the scheduler's.** :func:`matches` decides from the
schedule's filter and an envelope alone, so the rule is testable with no bus and no
database. When it matches, this module calls
:meth:`~app.scheduler.manager.ScheduleManager.trigger_event`, which does the locking,
the task creation and the ``SCHEDULE_TRIGGERED`` publish — the automation layer
never touches a task or a session itself.

**The index is refreshed, not queried per event.** Reading the event schedules from
the database on every bus event would put a query on the hot path of every event.
Instead the active ``EVENT`` schedules are indexed on start and re-read on an
interval (and on demand via :meth:`refresh`), so adding a workflow is picked up
within one refresh window. A stale index can only ever *miss* a new schedule for a
moment, never fire a deleted one at length, because ``trigger_event`` re-reads the
row under its lock and refuses a non-``ACTIVE`` one.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping

from app.core.errors import UltronError
from app.database.models import Schedule, ScheduleKind
from app.events.bus import LAG_EVENT_TYPE, EventBus, EventEnvelope, Subscription
from app.observability.logging import get_logger
from app.scheduler.manager import ScheduleManager

__all__ = ["EventAutomation", "event_filter", "matches"]

_LOGGER = get_logger(__name__)

#: How often the active-event index is re-read, seconds. Bounds how long a
#: newly-created workflow waits before its events are seen.
DEFAULT_REFRESH_INTERVAL = 30.0

#: Where the event name and match map live inside a schedule payload.
EVENT_KEY = "event"
MATCH_KEY = "match"


def event_filter(schedule: Schedule) -> tuple[str, dict[str, object]] | None:
    """Return ``(event_type, match)`` for an event schedule, or ``None``.

    ``None`` means the schedule is not event-triggered (or is missing the
    ``event`` key, which is a misconfiguration): it is skipped with a debug log
    rather than matched against everything.
    """
    payload = schedule.payload or {}
    name = payload.get(EVENT_KEY) or payload.get("event_type")
    if not isinstance(name, str) or not name.strip():
        return None
    match = payload.get(MATCH_KEY) or {}
    if not isinstance(match, Mapping):
        return None
    return name, dict(match)


def matches(schedule: Schedule, envelope: EventEnvelope) -> bool:
    """Decide whether ``envelope`` should fire ``schedule``.

    Pure: the event type must equal the schedule's ``event`` (case-insensitively,
    because wire casing is not a rule anyone should have to remember) and every
    ``match`` pair must equal the corresponding event payload value.
    """
    parsed = event_filter(schedule)
    if parsed is None:
        return False
    name, expected = parsed
    if envelope.event_type.lower() != name.lower():
        return False
    payload = envelope.payload or {}
    return all(payload.get(key) == value for key, value in expected.items())


class EventAutomation:
    """Subscribe to the bus and fire ``EVENT`` schedules whose filter matches."""

    def __init__(
        self,
        *,
        scheduler: ScheduleManager,
        bus: EventBus,
        name: str = "event-automation",
        refresh_interval: float = DEFAULT_REFRESH_INTERVAL,
    ) -> None:
        """Bind the automation to a scheduler, a bus and a refresh cadence."""
        self._scheduler = scheduler
        self._bus = bus
        self._name = name
        self._refresh_interval = refresh_interval
        self._subscription: Subscription | None = None
        self._worker: asyncio.Task[None] | None = None
        self._schedules: list[Schedule] = []
        self._fired = 0

    @property
    def name(self) -> str:
        """The worker's name, for diagnostics."""
        return self._name

    @property
    def fired(self) -> int:
        """How many schedules this automation has fired."""
        return self._fired

    def snapshot(self) -> dict[str, object]:
        """Counters for a health check or the observability phase."""
        return {"watched": len(self._schedules), "fired": self._fired}

    async def start(self) -> None:
        """Load the event schedules and begin consuming the bus."""
        if self._worker is not None:
            return
        await self.refresh()
        self._subscription = self._bus.subscribe()
        self._worker = asyncio.create_task(self._run(), name=f"{self._name}-worker")
        _LOGGER.info(
            "event automation started",
            extra={"event": "automation.started", **self.snapshot()},
        )

    async def stop(self) -> None:
        """Stop the worker and close the subscription."""
        subscription = self._subscription
        self._subscription = None
        if subscription is not None:
            with contextlib.suppress(Exception):
                subscription.close()
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        _LOGGER.info("event automation stopped", extra={"event": "automation.stopped"})

    async def refresh(self) -> None:
        """Re-read the active ``EVENT`` schedules."""
        try:
            schedules = await self._scheduler.list_schedules(active_only=True)
        except UltronError:
            _LOGGER.warning(
                "event automation could not load schedules",
                extra={"event": "automation.refresh_failed"},
                exc_info=True,
            )
            return
        self._schedules = [
            schedule for schedule in schedules if ScheduleKind(schedule.kind) is ScheduleKind.EVENT
        ]

    async def handle(self, envelope: EventEnvelope) -> int:
        """Fire every schedule matching ``envelope``; return how many fired."""
        if envelope.event_type == LAG_EVENT_TYPE:
            return 0
        fired = 0
        for schedule in self._schedules:
            if not matches(schedule, envelope):
                continue
            try:
                task = await self._scheduler.trigger_event(schedule.id)
            except UltronError:
                _LOGGER.warning(
                    "event automation could not fire a schedule",
                    extra={"event": "automation.trigger_failed", "schedule_id": str(schedule.id)},
                    exc_info=True,
                )
                continue
            if task is not None:
                fired += 1
                self._fired += 1
        return fired

    async def _run(self) -> None:
        subscription = self._subscription
        if subscription is None:
            return
        refresh = asyncio.create_task(self._refresh_loop(), name=f"{self._name}-refresh")
        try:
            async for envelope in subscription:
                await self.handle(envelope)
        finally:
            refresh.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await refresh

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self._refresh_interval)
            await self.refresh()
