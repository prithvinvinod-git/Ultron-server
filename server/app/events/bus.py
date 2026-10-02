"""In-process event bus: typed events, topic subscription, bounded fan-out.

Spec §19 asks for an internal event bus with asynchronous subscribers; T023 needs
something to fan events out to the client stream. This is that bus. The durable
record lives in ``app/database/models/events.py`` — this is the live path, and a
handler that must not be missed writes through to the table.

**Why this is in-process and not Redis pub/sub.** Redis is deferred project-wide
(``todo.md`` C5) and this deliberately does not reintroduce the dependency for one
feature. The trade is honest and worth stating: with more than one worker
process, a subscriber only sees events published *by its own process*. That is
correct for Phase 1 and wrong for horizontal scale, and the fix is a Redis or
Postgres ``LISTEN/NOTIFY`` backend behind this same interface rather than a
rewrite. The bus therefore keeps a narrow surface — ``publish``, ``subscribe`` —
so that swap stays local.

**Every queue is bounded and drops rather than blocks.** A publisher must never
wait on a slow client: one wedged browser tab would otherwise stall task
execution behind it. When a subscriber's queue is full the oldest item is
discarded and the subscriber is handed a :data:`LAG_EVENT_TYPE` envelope so it
can resync. Silently dropping would be worse — the client would see a gap and
render a system that looks idle, which spec §60.4 calls out directly.

**Replay is bounded too.** ``Last-Event-ID`` resumes from a ring buffer of recent
events rather than the database, because the resumption path has to be fast
enough to run on every reconnect. If the requested id has already aged out, the
subscriber gets the same ``stream.lagged`` signal rather than a silent resume
from "now", because a resume that quietly skips events is indistinguishable from
a working stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections import deque
from collections.abc import AsyncIterator, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self

from app.observability.logging import get_logger

_LOGGER = get_logger(__name__)

#: Event type used to tell a subscriber it fell behind. Not in spec §19's list:
#: it describes the *transport* rather than the domain, and a client that cannot
#: tell "I missed events" from "nothing is happening" will show a false idle.
LAG_EVENT_TYPE = "stream.lagged"

#: Event type sent once when a stream opens, carrying what it resumed from.
CONNECTED_EVENT_TYPE = "stream.connected"

#: Topic for events that belong to no other topic (resource alarms, schedules).
SYSTEM_TOPIC = "system"

#: Wildcard topic: subscribe to everything.
ALL_TOPICS = "*"

_TOPIC_BY_PREFIX: tuple[tuple[str, str], ...] = (
    ("TASK_", "task"),
    ("AGENT_", "agent"),
    ("TOOL_", "tool"),
    ("MODEL_", "model"),
    ("BUILD_", "build"),
    ("TEST_", "test"),
    ("DEVICE_", "device"),
    ("WAKE_", "voice"),
    ("STT_", "voice"),
    ("TTS_", "voice"),
    ("SCHEDULE_", "schedule"),
)

#: Prefixes that are resource alarms rather than anything a client subscribes to
#: by name; they land on the system topic with everything else.
_SYSTEM_EVENT_TYPES = frozenset({"CPU_HIGH", "RAM_HIGH", "DISK_LOW", "GPU_HIGH"})


def topic_for(event_type: str) -> str:
    """Return the topic an event type belongs to.

    Derived from the spec §19 type name rather than passed in, so a publisher
    cannot file an event under a topic that contradicts its type. An unknown type
    becomes :data:`SYSTEM_TOPIC`, which is the safe direction: a new event type
    reaches subscribers instead of silently reaching nobody.
    """
    if event_type in _SYSTEM_EVENT_TYPES:
        return SYSTEM_TOPIC
    for prefix, topic in _TOPIC_BY_PREFIX:
        if event_type.startswith(prefix):
            return topic
    return SYSTEM_TOPIC


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """One event on the wire.

    ``id`` is the SSE ``Last-Event-ID`` and a bus-wide monotonic integer, which
    is what makes resumption possible. It is a counter rather than a UUID because
    ordering across publishers is the property being asked for, and a random id
    cannot be compared.
    """

    id: int
    event_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    topic: str = SYSTEM_TOPIC
    correlation_id: str | None = None
    task_id: str | None = None
    agent_id: str | None = None
    device_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON body of the event.

        Field names are the column names from the ``events`` table, so a client
        that resyncs from the database and a client reading the live stream see
        one shape rather than two.
        """
        return {
            "id": self.id,
            "event_type": self.event_type,
            "topic": self.topic,
            "payload": dict(self.payload),
            "occurred_at": self.occurred_at.isoformat(),
            "correlation_id": self.correlation_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "device_id": self.device_id,
        }

    def to_sse(self) -> bytes:
        """Encode as an SSE frame.

        ``json.dumps`` is what keeps a payload containing a newline from breaking
        the frame: raw newlines inside a ``data:`` field would be read as the end
        of that field, silently truncating the event into two malformed ones.
        """
        return (
            f"id: {self.id}\n"
            f"event: {self.event_type}\n"
            f"data: {json.dumps(self.to_dict(), separators=(',', ':'))}\n\n"
        ).encode()


class Subscription:
    """One client's view of the bus.

    Not constructed directly: use :meth:`EventBus.subscribe`, which guarantees
    the subscription is unregistered even if the caller abandons it.
    """

    __slots__ = ("_bus", "_closed", "_dropped", "_last_id", "_queue", "_topics")

    def __init__(self, bus: EventBus, topics: frozenset[str], maxsize: int) -> None:
        self._bus = bus
        self._topics = topics
        self._queue: asyncio.Queue[EventEnvelope | None] = asyncio.Queue(maxsize=maxsize)
        self._dropped = 0
        self._last_id = 0
        self._closed = False

    @property
    def topics(self) -> frozenset[str]:
        return self._topics

    @property
    def dropped(self) -> int:
        """How many events this subscriber missed."""
        return self._dropped

    @property
    def last_id(self) -> int:
        """Highest id delivered, for the next ``Last-Event-ID``."""
        return self._last_id

    def wants(self, envelope: EventEnvelope) -> bool:
        return ALL_TOPICS in self._topics or envelope.topic in self._topics

    def offer(self, envelope: EventEnvelope) -> None:
        """Enqueue an event, dropping the oldest if this subscriber is behind.

        Only called from the publishing task, so the queue operations do not need
        to interleave with a consumer's ``get``.
        """
        if self._closed:
            return
        if self._queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
            self._dropped += 1
        self._queue.put_nowait(envelope)

    def backlog(self, events: Sequence[EventEnvelope]) -> None:
        """Seed the queue with replayed events, preserving their ids."""
        for envelope in events:
            self.offer(envelope)

    async def get(self) -> EventEnvelope | None:
        """Await the next event, or ``None`` once the bus closes.

        ``last_id`` advances here rather than in the iterator, because the stream
        route consumes the queue through this method directly. Advancing it in
        ``_iterate`` instead meant a caller using ``get()`` recorded nothing, so
        every reconnect sent ``Last-Event-ID: 0`` and replayed the whole buffer.
        """
        envelope = await self._queue.get()
        if envelope is not None and envelope.event_type != LAG_EVENT_TYPE:
            self._last_id = envelope.id
        return envelope

    def close(self) -> None:
        """Wake any waiting consumer and unregister from the bus.

        This is the complete cleanup. The route's ``finally`` block calls this
        rather than using ``async with``, so unregistering here is what actually
        removes the subscription from the bus. Without it a disconnected client
        left its subscription behind forever, still receiving events into a queue
        nobody reads -- a slow memory leak that no single-connection test would
        catch.
        """
        self._closed = True
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)
        self._bus._forget(self)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # ``close`` already unregisters; calling ``_forget`` again would be a
        # no-op but would also log a second "unsubscribed" line per disconnect.
        self.close()

    def __aiter__(self) -> AsyncIterator[EventEnvelope]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[EventEnvelope]:
        while True:
            envelope = await self.get()
            if envelope is None:
                return
            yield envelope


class EventBus:
    """Fan events out to subscribed clients.

    ``publish`` is a coroutine because callers should be able to await
    persistence writes to the ``events`` table when an event must be durable.
    Delivery to subscribers is synchronous and never fails: a subscriber that
    cannot keep up drops, it does not block the publisher.
    """

    def __init__(
        self,
        *,
        queue_size: int = 256,
        replay_size: int = 128,
        max_subscribers: int = 512,
    ) -> None:
        if queue_size < 1:
            raise ValueError("queue_size must be at least 1")
        if replay_size < 0:
            raise ValueError("replay_size must not be negative")
        if max_subscribers < 1:
            raise ValueError("max_subscribers must be at least 1")
        self._queue_size = queue_size
        self._replay_size = replay_size
        self._max_subscribers = max_subscribers
        self._subscribers: dict[int, Subscription] = {}
        self._replay: deque[EventEnvelope] = deque(maxlen=replay_size or None)
        self._next_id = 0
        self._closed = False

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def next_id(self) -> int:
        """The id the next published event will carry."""
        return self._next_id

    async def publish(
        self,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        correlation_id: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        device_id: str | None = None,
        persist: bool = False,
    ) -> EventEnvelope:
        """Publish an event to every interested subscriber.

        ``persist=True`` records the event in the ``events`` table. That is a
        separate opt-in per call rather than a bus-wide setting, because spec
        §19's "everything important generates events" and the retention policy in
        ``observability`` disagree about how much is important, and high-volume
        types like ``STT_PARTIAL`` would otherwise dominate the table.
        """
        if self._closed:
            raise EventBusClosedError("the event bus is closed")

        self._next_id += 1
        envelope = EventEnvelope(
            id=self._next_id,
            event_type=event_type,
            payload=dict(payload or {}),
            topic=topic_for(event_type),
            correlation_id=correlation_id,
            task_id=task_id,
            agent_id=agent_id,
            device_id=device_id,
        )
        if self._replay_size:
            self._replay.append(envelope)
        self._deliver(envelope)
        _LOGGER.info(
            "event published",
            extra={
                "event": "event.published",
                "event_type": event_type,
                "topic": envelope.topic,
                "subscribers": self.subscriber_count,
            },
        )
        return envelope

    def _deliver(self, envelope: EventEnvelope) -> None:
        for subscription in list(self._subscribers.values()):
            if subscription.wants(envelope):
                subscription.offer(envelope)

    def subscribe(
        self,
        topics: Iterable[str] | None = None,
        *,
        last_event_id: int | None = None,
    ) -> Subscription:
        """Return a subscription, replaying anything missed since ``last_event_id``.

        Raises:
            EventBusFullError: too many live subscribers. Refusing a connection
                is the honest answer -- accepting one that cannot be served would
                mean dropping events for every existing client to accommodate a
                new one.
        """
        if self._closed:
            raise EventBusClosedError("the event bus is closed")
        if len(self._subscribers) >= self._max_subscribers:
            raise EventBusFullError(
                f"{self._max_subscribers} subscribers already connected"
            )

        wanted = frozenset(topics) if topics else frozenset({ALL_TOPICS})
        subscription = Subscription(self, wanted, self._queue_size)

        if last_event_id is not None:
            subscription.backlog(self._replay_since(last_event_id, wanted))

        self._subscribers[id(subscription)] = subscription
        _LOGGER.info(
            "stream subscribed",
            extra={
                "event": "stream.subscribed",
                "topics": sorted(wanted),
                "resumed_from": last_event_id,
                "subscribers": self.subscriber_count,
            },
        )
        return subscription

    def _replay_since(
        self, last_event_id: int, topics: frozenset[str]
    ) -> list[EventEnvelope]:
        """Events after ``last_event_id``, or a lag signal if that is impossible."""
        if last_event_id >= self._next_id:
            # Client claims to be ahead of the bus: a restarted process resets the
            # counter, so this is a normal reconnect after a deploy.
            return []
        retained = [e for e in self._replay if e.id > last_event_id]
        if self._replay and retained and retained[0].id > last_event_id + 1:
            # The events the client missed have aged out of the ring buffer.
            return [self._lag_envelope(last_event_id, reason="replay_evicted")]
        wanted = [
            e
            for e in retained
            if ALL_TOPICS in topics or e.topic in topics
        ]
        if last_event_id and not wanted and self._dropped_between(last_event_id):
            return [self._lag_envelope(last_event_id, reason="replay_evicted")]
        return wanted

    def _dropped_between(self, last_event_id: int) -> bool:
        """Whether ids between the client's position and the buffer exist."""
        if not self._replay:
            return self._next_id > last_event_id
        return self._replay[0].id > last_event_id + 1

    def _lag_envelope(self, last_event_id: int, *, reason: str) -> EventEnvelope:
        return EventEnvelope(
            id=self._next_id,
            event_type=LAG_EVENT_TYPE,
            topic=SYSTEM_TOPIC,
            payload={
                "reason": reason,
                "resumed_from": last_event_id,
                "latest_id": self._next_id,
                "action": "resync",
            },
        )

    def _forget(self, subscription: Subscription) -> None:
        if self._subscribers.pop(id(subscription), None) is not None:
            _LOGGER.info(
                "stream unsubscribed",
                extra={
                    "event": "stream.unsubscribed",
                    "topics": sorted(subscription.topics),
                    "dropped": subscription.dropped,
                    "subscribers": self.subscriber_count,
                },
            )

    def close(self) -> None:
        """Close the bus and wake every subscriber."""
        self._closed = True
        for subscription in list(self._subscribers.values()):
            subscription.close()
        self._subscribers.clear()

    @property
    def closed(self) -> bool:
        return self._closed

    def health_snapshot(self) -> dict[str, int | bool]:
        """Counters for the ``event_bus`` readiness check."""
        return {
            "subscribers": self.subscriber_count,
            "next_id": self._next_id,
            "retained": len(self._replay),
            "closed": self._closed,
        }


class EventBusError(Exception):
    """Base for bus control-flow errors."""


class EventBusClosedError(EventBusError):
    """Raised when publishing to or subscribing on a closed bus."""


class EventBusFullError(EventBusError):
    """Raised when the subscriber limit is reached."""


def new_stream_id() -> str:
    """A per-connection id, for correlating a stream in logs."""
    return uuid.uuid4().hex


def iter_topics(values: Sequence[str] | None) -> Iterator[str]:
    """Normalise a topic filter: trimmed, lowercased, blanks discarded."""
    if not values:
        return
    for value in values:
        cleaned = value.strip().lower()
        if cleaned:
            yield cleaned


__all__ = [
    "ALL_TOPICS",
    "CONNECTED_EVENT_TYPE",
    "LAG_EVENT_TYPE",
    "SYSTEM_TOPIC",
    "EventBus",
    "EventBusClosedError",
    "EventBusError",
    "EventBusFullError",
    "EventEnvelope",
    "Subscription",
    "iter_topics",
    "new_stream_id",
    "topic_for",
]
