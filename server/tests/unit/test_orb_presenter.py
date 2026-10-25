"""Unit tests for the orb state presenter (spec §27 / §59.13).

The presenter is the only producer of ``ORB_STATE_CHANGED`` / ``TRANSCRIPT_*``.
These tests drive it with the real bus and the real event vocabulary, so a
rename in the voice/agent/task emitters shows up here rather than as a silently
idle orb in the desktop shell.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.events.bus import EventBus, EventEnvelope, Subscription
from app.events.types import EventType
from app.orb import OrbPresenter, OrbState


def _payload(envelope: EventEnvelope) -> dict[str, Any]:
    return dict(envelope.payload)


async def _next(
    subscription: Subscription, *event_types: EventType, timeout_s: float = 1.0
) -> EventEnvelope:
    """Read until one of ``event_types`` arrives (ignoring the rest)."""
    wanted = {str(event_type) for event_type in event_types}
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise AssertionError(f"no {wanted} event within {timeout_s}s")
        envelope = await asyncio.wait_for(subscription.get(), timeout=remaining)
        assert envelope is not None
        if envelope.event_type in wanted:
            return envelope


# --------------------------------------------------------------------------- #
# State projection
# --------------------------------------------------------------------------- #
def test_starts_idle_and_visible() -> None:
    presenter = OrbPresenter(EventBus())
    snapshot = presenter.snapshot()

    assert snapshot["state"] == OrbState.IDLE.value
    assert snapshot["visible"] is True
    assert snapshot["schema_version"] == 1


async def test_voice_listening_projects_listening_and_captions() -> None:
    bus = EventBus()
    presenter = OrbPresenter(bus)
    probe = bus.subscribe()
    await presenter.start()
    try:
        await bus.publish(EventType.VOICE_LISTENING, {"session_id": "s1"})
        state = await _next(probe, EventType.ORB_STATE_CHANGED)
        assert _payload(state)["state"] == "listening"
        assert _payload(state)["previous_state"] == "idle"

        await bus.publish(EventType.STT_PARTIAL, {"session_id": "s1", "text": "hey"})
        partial = await _next(probe, EventType.TRANSCRIPT_PARTIAL)
        assert _payload(partial)["text"] == "hey"
        assert _payload(partial)["session_id"] == "s1"
        assert _payload(partial)["partial"] is True

        await bus.publish(EventType.STT_FINAL, {"session_id": "s1", "text": "hey ultron"})
        final = await _next(probe, EventType.TRANSCRIPT_FINAL)
        assert _payload(final)["text"] == "hey ultron"
        assert _payload(final)["partial"] is False
    finally:
        await presenter.stop()


async def test_tts_speaking_then_idle() -> None:
    bus = EventBus()
    presenter = OrbPresenter(bus)
    probe = bus.subscribe()
    await presenter.start()
    try:
        await bus.publish(EventType.TTS_STARTED, {"session_id": "s1", "text": "hello"})
        speaking = await _next(probe, EventType.ORB_STATE_CHANGED)
        assert _payload(speaking)["state"] == "speaking"

        await bus.publish(EventType.TTS_COMPLETED, {"session_id": "s1"})
        await bus.publish(EventType.VOICE_ENDED, {"session_id": "s1"})
        # TTS_COMPLETED may re-emit idle already; accept either as the idle frame.
        idle = await _next(probe, EventType.ORB_STATE_CHANGED)
        assert _payload(idle)["state"] == "idle"
    finally:
        await presenter.stop()


async def test_agent_started_projects_executing_and_completion_returns_idle() -> None:
    bus = EventBus()
    presenter = OrbPresenter(bus)
    probe = bus.subscribe()
    await presenter.start()
    try:
        await bus.publish(
            EventType.AGENT_STARTED,
            {"agent_id": "a1", "agent_type": "coding.agent", "status": "running"},
            agent_id="a1",
        )
        executing = await _next(probe, EventType.ORB_STATE_CHANGED)
        assert _payload(executing)["state"] == "executing"
        assert _payload(executing)["active"]["agents"] == 1

        await bus.publish(
            EventType.AGENT_COMPLETED,
            {"agent_id": "a1", "agent_type": "coding.agent", "status": "completed"},
            agent_id="a1",
        )
        idle = await _next(probe, EventType.ORB_STATE_CHANGED)
        assert _payload(idle)["state"] == "idle"
    finally:
        await presenter.stop()


async def test_task_failure_surfaces_error_until_work_resumes() -> None:
    bus = EventBus()
    presenter = OrbPresenter(bus)
    probe = bus.subscribe()
    await presenter.start()
    try:
        await bus.publish(
            EventType.TASK_FAILED,
            {"task_id": "t1", "error": "boom", "error_code": "TASK_FAILED"},
            task_id="t1",
        )
        failed = await _next(probe, EventType.ORB_STATE_CHANGED)
        assert _payload(failed)["state"] == "error"
        assert _payload(failed)["error"]["message"] == "boom"

        await bus.publish(EventType.WAKE_DETECTED, {"wake_word": "ultron", "source": "esp32"})
        resumed = await _next(probe, EventType.ORB_SHOW)
        assert _payload(resumed)["reason"] == "wake_detected"
        listening = await _next(probe, EventType.ORB_STATE_CHANGED)
        assert _payload(listening)["state"] == "listening"
        assert _payload(listening)["pending"] is True
        assert _payload(listening).get("error") is None
    finally:
        await presenter.stop()


async def test_hide_and_show_publish_visibility_states() -> None:
    bus = EventBus()
    presenter = OrbPresenter(bus)
    probe = bus.subscribe()
    await presenter.start()
    try:
        await presenter.hide(reason="test")
        hidden = await _next(probe, EventType.ORB_STATE_CHANGED)
        assert _payload(hidden)["state"] == "hidden"
        assert presenter.visible is False

        await presenter.show(reason="test")
        shown = await _next(probe, EventType.ORB_STATE_CHANGED)
        assert _payload(shown)["state"] == "idle"
        assert presenter.visible is True
    finally:
        await presenter.stop()


async def test_resync_republishes_even_without_a_change() -> None:
    bus = EventBus()
    presenter = OrbPresenter(bus)
    probe = bus.subscribe()
    await presenter.start()
    try:
        # Drain the initial frame if one was emitted; resync is forced.
        snapshot = await presenter.resync()
        assert snapshot["state"] == "idle"
        envelope = await _next(probe, EventType.ORB_STATE_CHANGED)
        assert _payload(envelope)["reason"] == "resync"
    finally:
        await presenter.stop()


async def test_duplicate_event_ids_do_not_move_state_backwards() -> None:
    bus = EventBus()
    presenter = OrbPresenter(bus)
    await presenter.start()
    try:
        await bus.publish(EventType.TTS_STARTED, {"session_id": "s1"})
        await asyncio.sleep(0.05)
        assert presenter.state == OrbState.SPEAKING
        # Simulate a replayed frame below the high-water mark.
        presenter._last_id = 10_000  # deliberate stale-frame probe
        await bus.publish(EventType.VOICE_ENDED, {"session_id": "s1"})
        await asyncio.sleep(0.05)
        assert presenter.state == OrbState.SPEAKING
    finally:
        await presenter.stop()
