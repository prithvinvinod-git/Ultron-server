"""Episode recorder: memory's built-in bus subscriber (T128, spec §22 / §19).

§22's episodic layer is *"What ULTRON did previously"* (line 1142), and the
natural producer of that is task completion. T128's second half is exactly this
wire: when a task finishes, memory should learn it did. This class is the
subscriber that closes the loop, and it follows the pattern the other built-in
subscriber already established (:class:`app.events.handlers.PersistEvents`):
subscribe to the bus, keep what is yours, write behind a private worker so a
slow database never blocks a publisher, and close cleanly on shutdown.

**One event, one episode.** ``TASK_COMPLETED`` and ``TASK_FAILED`` are the two
terminal task events, so they are the two that become episodes. Everything else
is ignored -- a plan or a progress tick is not an accomplishment, and recording
them would bury the real history in noise.

**The episode carries the task's identity.** ``task_id`` and ``agent_id`` come
from the envelope and are stored on the memory row, so a later review can
correlate "what ULTRON did" back to the exact task and agent. A failed task is
recorded with a little more importance than a completed one, because a failure
is the thing a later retrieval is *more* likely to be asked about.

**Recording is best-effort, and says so.** A write that raises increments
``failed`` and is logged; the worker survives and keeps draining. Losing one
episode must never take down the event pipeline, and the counter makes the loss
visible rather than silent.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories.memories import MemoryRepository
from app.events.bus import EventBus, EventEnvelope, Subscription
from app.events.types import EventType
from app.memory import namespaces
from app.memory.episodic import EpisodicMemory
from app.observability import get_logger

_LOGGER = get_logger(__name__)

#: Something that yields a session when entered (``Container.session_scope``).
SessionScope = Callable[[], AbstractAsyncContextManager[AsyncSession]]

#: The two terminal task events that become episodes.
_TERMINAL_EVENTS: frozenset[str] = frozenset(
    {EventType.TASK_COMPLETED.value, EventType.TASK_FAILED.value}
)

#: Importance by outcome. A failure is slightly more important than a success,
#: because it is the more likely thing to be asked about later.
_IMPORTANCE = {"completed": 0.5, "failed": 0.7}


def _as_uuid(value: str | None) -> uuid.UUID | None:
    """Coerce an envelope reference to a UUID, or drop it."""
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _summarise(envelope: EventEnvelope) -> tuple[str, str]:
    """Return ``(summary, outcome)`` for a terminal task event."""
    outcome = "completed" if envelope.event_type == EventType.TASK_COMPLETED.value else "failed"
    payload = envelope.payload
    for key in ("summary", "objective", "description", "title"):
        candidate = payload.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip(), outcome
    reference = envelope.task_id or envelope.agent_id or "unknown"
    return f"{envelope.event_type} ({reference})", outcome


class MemoryRecorder:
    """Turn terminal task events into episodic memories."""

    name = "memory_recorder"

    def __init__(
        self,
        bus: EventBus,
        *,
        session_scope: SessionScope,
        default_namespace: str = namespaces.USER,
        retention_days: int = 180,
    ) -> None:
        self._bus = bus
        self._session_scope = session_scope
        self._default_namespace = default_namespace
        self._retention_days = retention_days
        self._subscription: Subscription | None = None
        self._task: asyncio.Task[None] | None = None
        self._dropped_snapshot = 0
        self._recorded = 0
        self._failed = 0

    @property
    def recorded(self) -> int:
        """Episodes successfully written."""
        return self._recorded

    @property
    def failed(self) -> int:
        """Writes that raised; the worker survives."""
        return self._failed

    @property
    def dropped(self) -> int:
        """Events discarded because the queue was full while the worker was busy."""
        if self._subscription is not None:
            return self._subscription.dropped
        return self._dropped_snapshot

    def snapshot(self) -> dict[str, int]:
        """Counters for a health or monitoring subscriber."""
        return {"recorded": self.recorded, "failed": self.failed, "dropped": self.dropped}

    async def start(self) -> None:
        """Subscribe to the bus and start recording."""
        if self._subscription is not None:
            return
        self._subscription = self._bus.subscribe()
        self._task = asyncio.create_task(self._run(), name=f"{self.name}-worker")

    async def stop(self) -> None:
        """Close the subscription, drain what is queued, and stop the worker."""
        if self._subscription is None:
            return
        self._dropped_snapshot = self._subscription.dropped
        self._subscription.close()
        if self._task is not None:
            await self._task
        self._task = None
        self._subscription = None
        _LOGGER.info(
            "memory recorder stopped",
            extra={"event": "memory.recorder_stopped", **self.snapshot()},
        )

    async def _run(self) -> None:
        subscription = self._subscription
        if subscription is None:
            return
        while True:
            envelope = await subscription.get()
            if envelope is None:
                return
            if envelope.event_type not in _TERMINAL_EVENTS:
                continue
            try:
                await self._record(envelope)
            except Exception:
                self._failed += 1
                _LOGGER.error(
                    "episode recording failed",
                    extra={
                        "event": "memory.recorder_failed",
                        "event_type": envelope.event_type,
                        "failed": self._failed,
                    },
                    exc_info=True,
                )
            else:
                self._recorded += 1

    async def _record(self, envelope: EventEnvelope) -> None:
        """Write one episode through a short-lived session."""
        summary, outcome = _summarise(envelope)
        async with self._session_scope() as session:
            episodic = EpisodicMemory(
                MemoryRepository(session),
                retention_days=self._retention_days,
                default_namespace=self._default_namespace,
            )
            await episodic.record(
                summary,
                outcome=outcome,
                namespace=self._default_namespace,
                importance=_IMPORTANCE.get(outcome, 0.5),
                task_id=_as_uuid(envelope.task_id),
                agent_id=_as_uuid(envelope.agent_id),
            )


__all__ = ["MemoryRecorder"]
