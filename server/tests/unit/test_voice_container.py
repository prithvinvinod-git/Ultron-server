"""Container wiring for voice (T154, spec §25 / §59.21).

The router and the engines are covered elsewhere; what these tests pin is the
*composition*: that ``Container.voice`` builds a manager over the STT/TTS
factories, that it is memoised, and -- the easy one to get wrong -- that the
manager publishes to the same event bus the WebSocket route subscribes to. Two
different buses would look wired but relay nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.config import Settings
from app.container import Container
from app.voice.manager import VoiceManager

pytestmark = pytest.mark.unit


def _settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "VOICE_ENABLED": True,
        "STT_ENGINE": "groq",
        "STT_FALLBACK_ENGINE": "faster-whisper",
        "GROQ_API_KEY": "gsk_test",
        "TTS_ENGINE": "elevenlabs",
        "TTS_FALLBACK_ENGINE": "edge-tts",
        "ELEVENLABS_API_KEY": "sk_test",
    }
    base.update(over)
    return Settings(_env_file=None, **base)


def test_container_builds_a_wired_voice_manager() -> None:
    container = Container(_settings())
    manager = container.voice

    assert isinstance(manager, VoiceManager)
    # Both engines are the cloud-primary -> local-fallback chains, not bare.
    assert manager.stt.name == "groq>faster-whisper"
    assert manager.tts.name == "elevenlabs>edge-tts"
    # The manager must share the container's bus: the WS route subscribes to
    # ``container.events`` and would otherwise never see the manager's events.
    assert manager._bus is container.events
    # The agent turn is wired, so voice speaks through the same agents/tools.
    assert manager._responder is not None


def test_container_voice_is_memoised() -> None:
    container = Container(_settings())
    assert container.voice is container.voice


def test_voice_disabled_yields_null_engines() -> None:
    container = Container(_settings(VOICE_ENABLED=False))
    manager = container.voice
    assert manager.stt.name == "none"
    assert manager.tts.name == "none"
    assert manager.stt.available is False
    assert manager.tts.available is False
