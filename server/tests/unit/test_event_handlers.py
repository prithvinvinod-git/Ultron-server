"""Unit tests for the built-in event subscribers (T032, spec §19).

``handlers.py`` delivers the durable write-through the ``events`` table
docstring promises. What matters about it, in order of how painful it would
be to discover the regression in production:

1. **Only flagged events are written** — everything else must stay in the
   live bus, or ``STT_PARTIAL``-class volumes would swamp the table.
2. **A failed write must not kill the worker.** The writer talks to a
   database that may be down; if that took the subscriber with it, every
   later event on every topic would be dropped silently.
3. **Stopping drains the queue.** A shutdown that abandons queued writes
   would lose the update history up to the moment of the shutdown itself.
4. **Backpressure is reported, not hidden** — via the same ``dropped``
   counter the live SSE subscribers report.

The tests are deterministic rather than polled: ``publish`` delivers
synchronously into each subscriber's bounded queue, so a worker only runs
when a test ``await`` hands the loop control back — which is what ``stop()``
does when it drains the queue and joins the worker.
"""

from __future__ import annotations

import uuid

import pytest

from app.config import Settings
from app.container import Container
from app.events.bus import EventBus, EventEnvelope
from app.events.handlers import PersistEvents, _as_uuid

pytestmark = pytest.mark.unit


class RecorderWriter:
    """Test writer: appends envelopes, optionally failing one event type."""

    def __init__(self, fail: list[str] | None = None) -> None:
        self._fail = set(fail or [])
        self.events: list[EventEnvelope] = []

    async def write(self, envelope: EventEnvelope) -> None:
        if envelope.event_type in self._fail:
            raise RuntimeError(f"simulated write failure for {envelope.event_type}")
        self.events.append(envelope)

    async def __call__(self, envelope: EventEnvelope) -> None:
        await self.write(envelope)


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


# --------------------------------------------------------------------------- #
# The persist flag
# --------------------------------------------------------------------------- #
async def test_publish_carries_the_persist_flag_and_defaults_off(bus: EventBus) -> None:
    flagged = await bus.publish("TASK_COMPLETED", persist=True)
    plain = await bus.publish("TASK_COMPLETED")

    assert flagged.persist is True
    assert plain.persist is False
    # Routing instruction for subscribers; never part of the wire payload.
    assert "persist" not in flagged.to_dict()


async def test_the_flag_survives_into_the_subscription_stream(bus: EventBus) -> None:
    subscription = bus.subscribe()
    try:
        await bus.publish("TASK_COMPLETED", persist=True)
        await bus.publish("AGENT_STARTED")
        first = await subscription.get()
        second = await subscription.get()
        assert first is not None and first.persist is True
        assert second is not None and second.persist is False
    finally:
        subscription.close()


# --------------------------------------------------------------------------- #
# PersistEvents behaviour
# --------------------------------------------------------------------------- #
async def test_only_flagged_events_reach_the_table(bus: EventBus) -> None:
    writer = RecorderWriter()
    handler = PersistEvents(bus, writer=writer)
    await handler.start()
    try:
        task_id = str(uuid.uuid4())
        await bus.publish("TASK_COMPLETED", task_id=task_id, persist=True)
        await bus.publish("AGENT_STARTED")
    finally:
        await handler.stop()
    written = writer.events
    assert [e.event_type for e in written] == ["TASK_COMPLETED"]
    assert written[0].task_id == task_id


async def test_a_failed_write_is_counted_and_the_worker_survives(bus: EventBus) -> None:
    writer = RecorderWriter(fail=["TASK_FAILED"])
    handler = PersistEvents(bus, writer=writer)
    await handler.start()
    try:
        await bus.publish("TASK_FAILED", persist=True)
        await bus.publish("TASK_COMPLETED", persist=True)
    finally:
        await handler.stop()
    assert handler.failed == 1
    assert handler.written == 1
    assert [e.event_type for e in writer.events] == ["TASK_COMPLETED"]


async def test_stop_drains_everything_queued(bus: EventBus) -> None:
    writer = RecorderWriter()
    handler = PersistEvents(bus, writer=writer)
    await handler.start()
    try:
        for _ in range(5):
            await bus.publish("TASK_COMPLETED", persist=True)
    finally:
        await handler.stop()
    assert handler.written == 5
    assert len(writer.events) == 5


async def test_backpressure_is_reported_not_hidden(bus: EventBus) -> None:
    # The worker cannot run during the synchronous publish loop below, so the
    # queue fills at its 256-default and every offer beyond it drops the
    # oldest: exactly ``400 - 256`` events missed.
    writer = RecorderWriter()
    handler = PersistEvents(bus, writer=writer)
    await handler.start()
    try:
        for _ in range(400):
            await bus.publish("TASK_COMPLETED", persist=True)
        assert handler.written == 0
        assert handler.dropped == 144
        snapshot = handler.snapshot()
        assert snapshot["dropped"] == handler.dropped
        assert snapshot["written"] == 0
        assert snapshot["failed"] == 0
    finally:
        await handler.stop()
    # Stop drains the 255 events left in the queue after the wake-up sentinel
    # made room by sacrificing the oldest of the full queue.
    assert handler.written == 255
    assert handler.dropped == 144


async def test_a_runner_without_a_writer_needs_a_session_scope() -> None:
    with pytest.raises(ValueError, match="session_scope"):
        PersistEvents(EventBus())


def test_as_uuid_coerces_table_references() -> None:
    value = uuid.uuid4()
    assert _as_uuid(str(value)) == value
    assert _as_uuid("") is None
    assert _as_uuid(None) is None
    assert _as_uuid("not-a-uuid") is None


# --------------------------------------------------------------------------- #
# Container wiring
# --------------------------------------------------------------------------- #
async def test_container_attaches_the_handler_when_enabled() -> None:
    container = Container()
    await container._start_persist_handler()
    try:
        assert container._persist is not None
        assert container.events.subscriber_count == 1
    finally:
        if container._persist is not None:
            await container._persist.stop()
        assert container.events.subscriber_count == 0


async def test_container_stays_idle_when_persistence_is_disabled() -> None:
    container = Container(Settings(events_persist=False))
    await container._start_persist_handler()
    assert container._persist is None
    assert container.events.subscriber_count == 0
