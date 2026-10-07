"""Built-in subscribers on the event bus (T032, spec §19).

Spec §19 asks for asynchronous subscribers; the bus provides the mechanism
(``EventBus.subscribe`` + ``Subscription`` iteration) and this module provides
the subscribers the system itself installs.

**Delivered: :class:`PersistEvents`** — the durable write-through the
``events`` table docstring promises ("a handler that must not be missed
writes through to here"). It subscribes to every topic, keeps the events
flagged ``persist=True``, and appends them through ``EventRepository``, gated
on ``settings.observability.events_persist``. Writes run on a private worker
task behind the bus's bounded queue, so a slow or dead database can never
block a publisher — the same drop-don't-block rule the bus applies to browser
tabs, answered with counters rather than a ``stream.lagged`` signal because no
client is waiting on these.

**Why the other built-ins the task names are not classes here:**

- *Logger* — ``EventBus.publish`` already logs every event (type, topic,
  subscriber count) at the transport level. A subscriber logging the same
  event again would double every line while adding nothing; domain-specific
  logging belongs next to the domain code that emits.
- *WS fan-out* — superseded by T023: each SSE client subscribes to the bus
  itself, so there is no shared socket to fan out to. The original ``/ws``
  was replaced by receive-only SSE.
- *Memory* — ``app/memory/`` arrives with Phase 7 (T120+). It, the scheduler
  (Phase 11) and the monitoring subscribers §64.15 names all attach by the
  same pattern used here: subscribe, consume, close on shutdown. Writing them
  before their sources exist would be scaffolding with no producer.

Retention stays Phase 3+ work: ``event_retention_days`` is read by settings
but nothing prunes yet — the model docstring says so plainly, and the index
that pruning needs already exists.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories.events import EventRepository
from app.events.bus import EventBus, EventEnvelope, Subscription
from app.observability.logging import get_logger

_LOGGER = get_logger(__name__)

#: Writes one envelope to its durable home. Injectable so unit tests can
#: substitute a recorder without a database.
Writer = Callable[[EventEnvelope], Awaitable[None]]

#: Something that yields a session when entered (``Container.session_scope``).
SessionScope = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def _as_uuid(value: str | None) -> uuid.UUID | None:
    """Coerce an envelope id to the UUID the table stores, else drop it.

    A dangling id is worse than a missing one: ``task_id`` is the join key
    that ties an event to its task, and NULL at least cannot point at the
    wrong row.
    """
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        _LOGGER.warning(
            "event persistence dropped a non-UUID reference",
            extra={"event": "events.persist_bad_reference", "reference": value},
        )
        return None


class PersistEvents:
    """Write ``persist=True`` events to the ``events`` table.

    Attached by the container at startup when persistence is enabled, and
    stopped at shutdown. The worker is the bus's own subscription, so it is
    bounded and drop-oldest like every subscriber; ``written`` / ``failed``
    / ``dropped`` make its health answerable after the fact.
    """

    name = "persist_events"

    def __init__(
        self,
        bus: EventBus,
        *,
        session_scope: SessionScope | None = None,
        writer: Writer | None = None,
    ) -> None:
        if writer is None:
            if session_scope is None:
                raise ValueError("PersistEvents needs a session_scope unless a writer is given")
            self._writer = self._append_via_repository(session_scope)
        else:
            self._writer = writer
        self._bus = bus
        self._subscription: Subscription | None = None
        self._task: asyncio.Task[None] | None = None
        self._dropped_snapshot = 0
        self._written = 0
        self._failed = 0

    @property
    def written(self) -> int:
        """Events successfully appended to the table."""
        return self._written

    @property
    def failed(self) -> int:
        """Writes that raised (database down, schema drift) — worker survives."""
        return self._failed

    @property
    def dropped(self) -> int:
        """Events discarded because the queue was full while the worker was busy."""
        if self._subscription is not None:
            return self._subscription.dropped
        return self._dropped_snapshot

    def snapshot(self) -> dict[str, int]:
        """Counters for a health or monitoring subscriber."""
        return {
            "written": self.written,
            "failed": self.failed,
            "dropped": self.dropped,
        }

    def _append_via_repository(self, session_scope: SessionScope) -> Writer:
        async def append(envelope: EventEnvelope) -> None:
            async with session_scope() as session:
                await EventRepository(session).append(
                    event_type=envelope.event_type,
                    payload=dict(envelope.payload),
                    occurred_at=envelope.occurred_at,
                    sequence=envelope.id,
                    correlation_id=envelope.correlation_id,
                    task_id=_as_uuid(envelope.task_id),
                    agent_id=_as_uuid(envelope.agent_id),
                    device_id=_as_uuid(envelope.device_id),
                    error=(
                        envelope.payload["error"]
                        if isinstance(envelope.payload.get("error"), str)
                        else None
                    ),
                )

        return append

    async def start(self) -> None:
        """Subscribe to every topic and start draining to the table."""
        if self._subscription is not None:
            return
        self._subscription = self._bus.subscribe()
        self._task = asyncio.create_task(self._run(), name=f"{self.name}-worker")

    async def stop(self) -> None:
        """Close the subscription, drain what is queued, and stop the worker.

        ``Subscription.close`` wakes the waiter with ``None``, and the
        async-iterator contract delivers that only after everything already in
        the queue, so a stopped handler has flushed rather than abandoned its
        backlog.
        """
        if self._subscription is None:
            return
        self._dropped_snapshot = self._subscription.dropped
        self._subscription.close()
        if self._task is not None:
            await self._task
        self._task = None
        self._subscription = None
        _LOGGER.info(
            "event persistence stopped",
            extra={
                "event": "events.persist_stopped",
                **self.snapshot(),
            },
        )

    async def _run(self) -> None:
        subscription = self._subscription
        if subscription is None:
            _LOGGER.error(
                "event persistence worker started without a subscription",
                extra={"event": "events.persist_misstarted"},
            )
            return
        while True:
            envelope = await subscription.get()
            if envelope is None:
                return
            if not envelope.persist:
                continue
            try:
                await self._writer(envelope)
            except Exception:
                self._failed += 1
                _LOGGER.error(
                    "event persistence write failed",
                    extra={
                        "event": "events.persist_failed",
                        "event_type": envelope.event_type,
                        "failed": self._failed,
                    },
                    exc_info=True,
                )
            else:
                self._written += 1


__all__ = ["PersistEvents", "Writer"]
