"""Core events → notifications (T173, §45).

§45 asks for a ``NotificationProvider`` abstraction and says not to couple
notification *logic* to one provider. The T096 layer delivered the abstraction and
the facade; this module is the missing half — the wiring that turns something the
Core already announces into a notification, so a subscriber sees "the nightly
backup task failed" without every producer learning about the notification
service.

**It is a bus subscriber, like the scheduler's automation.** A producer publishes
``TASK_FAILED`` (§19) and moves on; whether that becomes a push, a desktop toast or
a log line is decided here, once, by the level map below. Adding a channel later is
a new provider, not a change to the task executor or an agent.

**Only the events worth waking someone for.** §45 does not enumerate them, so the
map is deliberately a curated set — failures and resource alerts — rather than
"every event". A notification per ``TASK_CREATED`` is noise, and noise is how a
real alert gets ignored. ``SCHEDULE_TRIGGERED`` is *not* in the map for the same
reason: a job firing on time is expected, and the scheduler's own failure hook
(below) covers the case that is not.

**Delivery never breaks a producer.** The service facade already returns a failed
result instead of raising (§45's loose-coupling rule); this subscriber goes one
step further and swallows even an unexpected error, because it runs on the bus
worker and must not stall the stream it is reading.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from app.database.models import Schedule
from app.events.bus import EventBus, EventEnvelope
from app.events.types import EventType
from app.notifications.base import NotificationLevel
from app.notifications.service import NotificationService
from app.observability.logging import get_logger
from app.scheduler.manager import Notifier

if TYPE_CHECKING:
    from app.events.bus import Subscription

__all__ = ["NOTIFY_LEVELS", "NotificationDispatch", "build_schedule_notifier"]

_LOGGER = get_logger(__name__)

#: The events that become notifications, and how urgent each is. Curated, not
#: exhaustive (§45), so the channel stays worth reading.
NOTIFY_LEVELS: dict[EventType, NotificationLevel] = {
    EventType.TASK_FAILED: NotificationLevel.WARNING,
    EventType.AGENT_FAILED: NotificationLevel.WARNING,
    EventType.TOOL_FAILED: NotificationLevel.WARNING,
    EventType.BUILD_FAILED: NotificationLevel.WARNING,
    EventType.TEST_FAILED: NotificationLevel.WARNING,
    EventType.MODEL_FAILED: NotificationLevel.WARNING,
    EventType.VOICE_CALL_FAILED: NotificationLevel.WARNING,
    EventType.PERMISSION_DENIED: NotificationLevel.WARNING,
    EventType.CPU_HIGH: NotificationLevel.WARNING,
    EventType.RAM_HIGH: NotificationLevel.WARNING,
    EventType.DISK_LOW: NotificationLevel.WARNING,
    EventType.GPU_HIGH: NotificationLevel.WARNING,
}


class NotificationDispatch:
    """Forward selected bus events to the notification service."""

    def __init__(
        self,
        *,
        service: NotificationService,
        bus: EventBus,
        name: str = "notification-dispatch",
    ) -> None:
        """Bind the dispatcher to a service and a bus."""
        self._service = service
        self._bus = bus
        self._name = name
        self._subscription: Subscription | None = None
        self._worker: asyncio.Task[None] | None = None
        self._sent = 0

    @property
    def sent(self) -> int:
        """How many notifications this dispatcher has handed to the service."""
        return self._sent

    def snapshot(self) -> dict[str, int]:
        """Counters for a health check or the observability phase."""
        return {"sent": self._sent}

    async def start(self) -> None:
        """Begin consuming the bus."""
        if self._worker is not None:
            return
        self._subscription = self._bus.subscribe()
        self._worker = asyncio.create_task(self._run(), name=self._name)
        _LOGGER.info("notification dispatch started", extra={"event": "notifications.started"})

    async def stop(self) -> None:
        """Stop the worker and close the subscription."""
        if self._subscription is not None:
            with contextlib.suppress(Exception):
                self._subscription.close()
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        self._subscription = None
        _LOGGER.info("notification dispatch stopped", extra={"event": "notifications.stopped"})

    async def handle(self, envelope: EventEnvelope) -> bool:
        """Notify for ``envelope`` if it is one of the mapped events."""
        try:
            event_type = EventType(envelope.event_type)
        except ValueError:
            return False
        level = NOTIFY_LEVELS.get(event_type)
        if level is None:
            return False
        title, message = _render(event_type, envelope)
        try:
            await self._service.notify(
                title,
                message,
                level=level,
                source="ultron.core",
                metadata=dict(envelope.payload or {}),
            )
        except Exception:
            _LOGGER.warning(
                "notification dispatch failed",
                extra={"event": "notifications.dispatch_failed", "event_type": envelope.event_type},
                exc_info=True,
            )
            return False
        self._sent += 1
        return True

    async def _run(self) -> None:
        subscription = self._subscription
        if subscription is None:
            return
        async for envelope in subscription:
            await self.handle(envelope)


def build_schedule_notifier(service: NotificationService) -> Notifier:
    """Return the scheduler's failure hook (§44/§45).

    The scheduler takes a ``notify`` callable so it never imports §45; this is the
    production one. A job that fails to produce a task is exactly the kind of
    thing §45 exists for, and it is reported at ``WARNING`` with the schedule's
    name so the notification is actionable without opening the database.
    """

    async def notify(schedule: Schedule, exc: BaseException) -> None:
        await service.notify(
            f"Scheduled job failed: {schedule.name}",
            f"{type(exc).__name__}: {exc}",
            level=NotificationLevel.WARNING,
            source="ultron.scheduler",
            metadata={"schedule_id": str(schedule.id), "kind": str(schedule.kind)},
        )

    return notify


def _render(event_type: EventType, envelope: EventEnvelope) -> tuple[str, str]:
    """Build a human title and message from an event's payload."""
    payload = envelope.payload or {}
    title = event_type.value.replace("_", " ").title()
    detail = payload.get("error") or payload.get("message") or payload.get("reason")
    if isinstance(detail, str) and detail.strip():
        return title, detail
    summary = ", ".join(f"{key}={value}" for key, value in payload.items() if value is not None)
    return title, summary or title

