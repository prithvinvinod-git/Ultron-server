"""Unit tests for wake-word detection (T155, spec §25 / §26).

Covers word-boundary matching (not a substring match), the stripped command text,
and that both a transcript hit and a device-side signal raise the single
``WAKE_DETECTED`` event the voice manager reacts to.
"""

from __future__ import annotations

import asyncio

from app.config import VoiceSettings
from app.events.bus import EventBus
from app.events.types import EventType
from app.voice.wake import WakeWordDetector, build_wake_detector


def _detector(bus: EventBus | None = None) -> WakeWordDetector:
    return WakeWordDetector(["ultron"], bus=bus)


# --------------------------------------------------------------------------- #
# Matching (word boundary, case-insensitive).
# --------------------------------------------------------------------------- #
def test_match_finds_the_wake_word_and_strips_it() -> None:
    match = _detector().match("Ultron, play some music")
    assert match is not None
    assert match.word == "ultron"
    assert match.command == "play some music"


def test_match_is_case_insensitive() -> None:
    assert _detector().match("hey ULTRON what time is it") is not None


def test_match_rejects_a_substring() -> None:
    # "ultronix" contains "ultron" but is not the wake word.
    assert _detector().match("the ultronix device is offline") is None


def test_match_returns_none_without_a_wake_word() -> None:
    assert _detector().match("just talking to myself") is None


def test_match_returns_none_when_no_words_configured() -> None:
    assert WakeWordDetector([]).match("hello") is None


# --------------------------------------------------------------------------- #
# Events.
# --------------------------------------------------------------------------- #
async def test_detect_publishes_wake_detected() -> None:
    bus = EventBus()
    detector = _detector(bus)
    sub = bus.subscribe()
    try:
        match = await detector.detect("ultron, open the browser")
        assert match is not None
        envelope = await asyncio.wait_for(sub.get(), timeout=1.0)
    finally:
        sub.close()
        bus.close()
    assert envelope is not None
    assert envelope.event_type == EventType.WAKE_DETECTED.value
    assert envelope.payload["wake_word"] == "ultron"
    assert envelope.payload["command"] == "open the browser"
    assert envelope.payload["source"] == "stt"


async def test_detect_without_wake_word_publishes_nothing() -> None:
    bus = EventBus()
    detector = _detector(bus)
    assert await detector.detect("no wake here") is None
    bus.close()


async def test_device_signal_publishes_wake_detected() -> None:
    # §26: the ESP32 device decides locally and sends a wake signal.
    bus = EventBus()
    detector = _detector(bus)
    sub = bus.subscribe()
    try:
        await detector.signal(source="esp32", device_id="esp32-1")
        envelope = await asyncio.wait_for(sub.get(), timeout=1.0)
    finally:
        sub.close()
        bus.close()
    assert envelope is not None
    assert envelope.event_type == EventType.WAKE_DETECTED.value
    assert envelope.payload["source"] == "esp32"
    assert envelope.device_id == "esp32-1"


def test_build_wake_detector_uses_settings_words() -> None:
    bus = EventBus()
    detector = build_wake_detector(VoiceSettings(enabled=True, wake_words=["jarvis"]), bus)
    assert detector.wake_words == ("jarvis",)
    assert detector.match("jarvis, status") is not None
