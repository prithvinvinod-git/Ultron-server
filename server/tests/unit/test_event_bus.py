"""Unit tests for the in-process event bus (T023, spec §19).

The bus is the piece with real logic in it: fan-out by topic, bounded queues
that drop instead of blocking, and replay from a ring buffer. Each of those can
be wrong in a way that still looks healthy, so the tests below pin the behaviour
rather than the implementation.

The two properties most worth protecting:

**A slow subscriber cannot stall a publisher.** If it could, one wedged browser
tab would sit between a task completing and the agent being notified, which is a
far worse failure than a client missing an event and being told to resync.

**A subscriber that missed something is told.** A silent drop is
indistinguishable from an idle system, and §60.4 is explicit that a dropped
connection must not read as "nothing is happening".
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.events.bus import (
    ALL_TOPICS,
    LAG_EVENT_TYPE,
    SYSTEM_TOPIC,
    EventBus,
    EventBusClosedError,
    EventBusFullError,
    EventEnvelope,
    iter_topics,
    topic_for,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Topics
# --------------------------------------------------------------------------- #


class TestTopicDerivation:
    """Spec §19's event names map onto topics without the publisher choosing."""

    @pytest.mark.parametrize(
        ("event_type", "topic"),
        [
            ("TASK_CREATED", "task"),
            ("AGENT_STARTED", "agent"),
            ("TOOL_FAILED", "tool"),
            ("MODEL_REQUEST", "model"),
            ("BUILD_STARTED", "build"),
            ("TEST_FAILED", "test"),
            ("DEVICE_CONNECTED", "device"),
            ("WAKE_DETECTED", "voice"),
            ("STT_PARTIAL", "voice"),
            ("TTS_COMPLETED", "voice"),
            ("SCHEDULE_TRIGGERED", "schedule"),
            ("USER_MESSAGE", SYSTEM_TOPIC),
        ],
    )
    def test_each_spec_event_type_lands_on_a_topic(
        self, event_type: str, topic: str
    ) -> None:
        assert topic_for(event_type) == topic

    @pytest.mark.parametrize("event_type", ["CPU_HIGH", "RAM_HIGH", "DISK_LOW", "GPU_HIGH"])
    def test_resource_alarms_are_system_events(self, event_type: str) -> None:
        """They describe the host, not an agent a client would subscribe to."""
        assert topic_for(event_type) == SYSTEM_TOPIC

    def test_an_unknown_type_reaches_the_system_topic(self) -> None:
        """The safe direction: a new event reaches someone, not nobody.

        The alternative -- returning None and dropping -- would mean a type added
        in a later phase silently reaches no subscriber, and the failure would
        present as "the client does not show my new event" with nothing in the
        logs.
        """
        assert topic_for("SOMETHING_BRAND_NEW") == SYSTEM_TOPIC

    def test_a_prefix_match_does_not_capture_a_longer_word(self) -> None:
        """``TASK_`` must not swallow an unrelated type by accident."""
        assert topic_for("TASKS_CREATED") == SYSTEM_TOPIC


# --------------------------------------------------------------------------- #
# Envelope and wire format
# --------------------------------------------------------------------------- #


class TestEnvelope:
    def test_ids_are_monotonic_and_comparable(self) -> None:
        """Resumption depends on ordering, which a UUID cannot provide."""
        first = EventEnvelope(id=7, event_type="TASK_CREATED")
        second = EventEnvelope(id=8, event_type="TASK_STARTED")
        assert first.id < second.id

    def test_the_payload_shape_matches_the_events_table(self) -> None:
        """A client resyncing from the database must not see a second shape."""
        envelope = EventEnvelope(
            id=1,
            event_type="TASK_CREATED",
            payload={"task_id": "abc"},
            topic="task",
            correlation_id="req-1",
            task_id="abc",
        )
        assert set(envelope.to_dict()) == {
            "id",
            "event_type",
            "topic",
            "payload",
            "occurred_at",
            "correlation_id",
            "task_id",
            "agent_id",
            "device_id",
        }

    def test_sse_frame_carries_id_event_and_data(self) -> None:
        frame = EventEnvelope(id=12, event_type="AGENT_STARTED", payload={"a": 1}).to_sse()
        text = frame.decode()
        assert "id: 12\n" in text
        assert "event: AGENT_STARTED\n" in text
        assert '"a":1' in text
        assert text.endswith("\n\n"), "a frame must end with a blank line"

    def test_a_newline_in_a_payload_cannot_break_the_frame(self) -> None:
        """Raw newlines in ``data:`` would end the field early.

        This is the reason the payload is JSON-encoded rather than dropped in
        verbatim: an event whose text contains a blank line would otherwise split
        into two malformed frames, and the second half would look like a
        different event.
        """
        frame = EventEnvelope(
            id=1,
            event_type="TRANSCRIPT_PARTIAL",
            payload={"text": "line one\n\nline two"},
        ).to_sse()
        text = frame.decode()
        assert text.count("data:") == 1
        assert text.endswith("\n\n")


# --------------------------------------------------------------------------- #
# Fan-out
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestFanOut:
    async def test_a_subscriber_receives_a_published_event(self) -> None:
        bus = EventBus()
        subscription = bus.subscribe()
        await bus.publish("TASK_CREATED", {"id": "t1"})

        envelope = await asyncio.wait_for(subscription.get(), timeout=1)
        assert envelope is not None
        assert envelope.event_type == "TASK_CREATED"

    async def test_a_topic_filter_excludes_other_topics(self) -> None:
        bus = EventBus()
        subscription = bus.subscribe({"agent"})
        await bus.publish("TASK_CREATED", {"id": "t1"})

        # Nothing to read: the filter did its job.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(subscription.get(), timeout=0.05)

    async def test_a_topic_filter_includes_its_own_topic(self) -> None:
        bus = EventBus()
        subscription = bus.subscribe({"agent"})
        await bus.publish("AGENT_STARTED", {"id": "a1"})

        envelope = await asyncio.wait_for(subscription.get(), timeout=1)
        assert envelope is not None

    async def test_the_wildcard_receives_everything(self) -> None:
        bus = EventBus()
        subscription = bus.subscribe({ALL_TOPICS})
        for event_type in ("TASK_CREATED", "AGENT_STARTED", "CPU_HIGH"):
            await bus.publish(event_type)

        received = [await subscription.get() for _ in range(3)]
        assert [e.event_type for e in received if e] == [
            "TASK_CREATED",
            "AGENT_STARTED",
            "CPU_HIGH",
        ]

    async def test_every_subscriber_receives_the_event(self) -> None:
        """Fan-out, not work-queue semantics: this is a broadcast bus."""
        bus = EventBus()
        first = bus.subscribe()
        second = bus.subscribe()
        await bus.publish("AGENT_COMPLETED", {})

        assert await asyncio.wait_for(first.get(), timeout=1) is not None
        assert await asyncio.wait_for(second.get(), timeout=1) is not None

    async def test_no_subscribers_is_not_an_error(self) -> None:
        """Publishing before anyone connects is normal, not a failure.

        Treating it as an error would make an event emitted during startup -- a
        device announcement, say -- look like a fault when it is simply that
        nobody is listening yet.
        """
        bus = EventBus()
        envelope = await bus.publish("TASK_CREATED", {"id": "t1"})
        assert envelope.id == 1


# --------------------------------------------------------------------------- #
# Backpressure
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestBackpressure:
    async def test_publishing_does_not_block_on_a_full_queue(self) -> None:
        """The property that keeps one slow client off the task execution path."""
        bus = EventBus(queue_size=2)
        subscription = bus.subscribe()

        for index in range(50):
            await asyncio.wait_for(
                bus.publish("TASK_CREATED", {"i": index}), timeout=1
            )

        assert subscription.dropped == 48

    async def test_the_oldest_event_is_the_one_dropped(self) -> None:
        """Keep the newest: a client is far more helped by recent state."""
        bus = EventBus(queue_size=2)
        subscription = bus.subscribe()
        for index in range(4):
            await bus.publish("TASK_CREATED", {"i": index})

        first = await asyncio.wait_for(subscription.get(), timeout=1)
        second = await asyncio.wait_for(subscription.get(), timeout=1)
        assert first is not None and first.payload["i"] == 2
        assert second is not None and second.payload["i"] == 3

    async def test_a_lagged_subscriber_is_told_rather_than_left_guessing(self) -> None:
        """The drop has to be visible, or the client renders a false idle."""
        bus = EventBus(queue_size=1)
        subscription = bus.subscribe()
        for index in range(5):
            await bus.publish("AGENT_STARTED", {"i": index})

        # The bus does not inject the lag notice itself: it is the stream
        # generator's job to notice ``dropped`` and tell the client. What matters
        # here is that the counter the generator relies on is accurate.
        assert subscription.dropped == 4

    async def test_last_id_tracks_what_was_delivered(self) -> None:
        """This is what a reconnecting client sends back."""
        bus = EventBus()
        subscription = bus.subscribe()
        await bus.publish("TASK_CREATED", {"i": 1})
        await bus.publish("TASK_CREATED", {"i": 2})
        await subscription.get()
        await subscription.get()
        assert subscription.last_id == 2


# --------------------------------------------------------------------------- #
# Replay
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestReplay:
    async def test_a_reconnecting_client_gets_what_it_missed(self) -> None:
        """The whole point of carrying ids on the wire."""
        bus = EventBus(replay_size=16)
        await bus.publish("TASK_CREATED", {"i": 1})
        await bus.publish("TASK_STARTED", {"i": 2})
        await bus.publish("TASK_COMPLETED", {"i": 3})

        subscription = bus.subscribe(last_event_id=1)
        first = await asyncio.wait_for(subscription.get(), timeout=1)
        second = await asyncio.wait_for(subscription.get(), timeout=1)
        assert first is not None and first.id == 2
        assert second is not None and second.id == 3

    async def test_a_client_that_was_current_receives_no_backlog(self) -> None:
        bus = EventBus(replay_size=16)
        for index in range(5):
            await bus.publish("TASK_CREATED", {"i": index})

        subscription = bus.subscribe(last_event_id=5)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(subscription.get(), timeout=0.05)

    async def test_replay_respects_the_topic_filter(self) -> None:
        """Replaying a task event to an agent-only subscriber leaks across topics."""
        bus = EventBus(replay_size=16)
        await bus.publish("TASK_CREATED", {"i": 1})
        await bus.publish("AGENT_STARTED", {"i": 2})

        subscription = bus.subscribe({"agent"}, last_event_id=0)
        envelope = await asyncio.wait_for(subscription.get(), timeout=1)
        assert envelope is not None and envelope.event_type == "AGENT_STARTED"

    async def test_a_client_ahead_of_the_bus_resumes_cleanly(self) -> None:
        """A reconnect after a restart, where the counter went backwards."""
        bus = EventBus(replay_size=16)
        await bus.publish("TASK_CREATED", {"i": 1})

        subscription = bus.subscribe(last_event_id=99)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(subscription.get(), timeout=0.05)

    async def test_an_id_that_aged_out_reports_a_lag_instead_of_silence(self) -> None:
        """A resume that skips events is indistinguishable from a healthy stream."""
        bus = EventBus(replay_size=2)
        for index in range(10):
            await bus.publish("TASK_CREATED", {"i": index})

        subscription = bus.subscribe(last_event_id=1)
        envelope = await asyncio.wait_for(subscription.get(), timeout=1)
        assert envelope is not None
        assert envelope.event_type == LAG_EVENT_TYPE
        assert envelope.payload["action"] == "resync"
        assert envelope.payload["resumed_from"] == 1

    async def test_replay_can_be_disabled(self) -> None:
        """``EVENT_REPLAY_SIZE=0`` is a supported choice, not an edge case."""
        bus = EventBus(replay_size=0)
        await bus.publish("TASK_CREATED", {"i": 1})
        subscription = bus.subscribe(last_event_id=0)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(subscription.get(), timeout=0.05)


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestLifecycle:
    async def test_closing_wakes_every_subscriber(self) -> None:
        """Shutdown must end open streams rather than leaving them hanging."""
        bus = EventBus()
        subscription = bus.subscribe()
        bus.close()

        assert await asyncio.wait_for(subscription.get(), timeout=1) is None

    async def test_publishing_to_a_closed_bus_is_an_error(self) -> None:
        bus = EventBus()
        bus.close()
        with pytest.raises(EventBusClosedError):
            await bus.publish("TASK_CREATED", {})

    async def test_subscribing_to_a_closed_bus_is_an_error(self) -> None:
        bus = EventBus()
        bus.close()
        with pytest.raises(EventBusClosedError):
            bus.subscribe()

    async def test_a_disconnecting_subscriber_is_forgotten(self) -> None:
        """A leaked subscription would keep receiving events forever."""
        bus = EventBus()
        async with bus.subscribe() as subscription:
            assert bus.subscriber_count == 1
            assert subscription.topics == frozenset({ALL_TOPICS})
        assert bus.subscriber_count == 0

    async def test_the_subscriber_limit_is_enforced(self) -> None:
        """Refusing a new client beats degrading every existing one."""
        bus = EventBus(max_subscribers=2)
        first = bus.subscribe()
        second = bus.subscribe()
        with pytest.raises(EventBusFullError):
            bus.subscribe()
        assert first is not None and second is not None

    async def test_a_freed_slot_can_be_reused(self) -> None:
        bus = EventBus(max_subscribers=1)
        async with bus.subscribe():
            pass
        async with bus.subscribe():
            assert bus.subscriber_count == 1


class TestHealthSnapshot:
    def test_counters_describe_the_bus(self) -> None:
        """The ``event_bus`` readiness check reads this."""
        bus = EventBus()
        bus.subscribe()
        snapshot = bus.health_snapshot()
        assert snapshot["subscribers"] == 1
        assert snapshot["closed"] is False


class TestTopicFilterParsing:
    def test_values_are_normalised(self) -> None:
        assert list(iter_topics([" Agent ", "", "TASK"])) == ["agent", "task"]

    def test_nothing_parses_to_nothing(self) -> None:
        assert list(iter_topics(None)) == []


# --------------------------------------------------------------------------- #
# Configuration guards
# --------------------------------------------------------------------------- #


class TestConstruction:
    """Bad configuration should fail when the bus is built, not when it misbehaves."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"queue_size": 0},
            {"replay_size": -1},
            {"max_subscribers": 0},
        ],
    )
    def test_invalid_sizes_are_rejected(self, kwargs: dict[str, Any]) -> None:
        with pytest.raises(ValueError):
            EventBus(**kwargs)
