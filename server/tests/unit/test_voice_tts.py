"""Unit tests for the text-to-speech subsystem (T155, spec §59.18).

Covers sentence-level segmentation, the null engine's honest degradation, the
Piper adapter against an injected runner (no binary, no model), and the factory --
none of which need Piper installed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from app.config import VoiceSettings
from app.core.errors import DependencyUnavailableError, UltronError
from app.voice.tts import (
    NullTTSEngine,
    PiperTTS,
    TTSEngine,
    create_tts_engine,
    split_sentences,
)


def _settings(**over: Any) -> VoiceSettings:
    base: dict[str, Any] = {
        "enabled": True,
        "tts_engine": "piper",
        "tts_model_path": "voice.onnx",
        "tts_binary_path": "piper",
        "tts_sample_rate": 22050,
    }
    base.update(over)
    return VoiceSettings(**base)


class RecordingRunner:
    """An injected Piper runner that returns fixed audio and records its argv."""

    def __init__(self, audio: bytes = b"pcm") -> None:
        self.audio = audio
        self.calls: list[tuple[list[str], str, float]] = []

    async def __call__(self, argv: Sequence[str], text: str, limit: float) -> bytes:
        self.calls.append((list(argv), text, limit))
        return self.audio


async def _collect(stream: Any) -> list[bytes]:
    return [chunk async for chunk in stream]


# --------------------------------------------------------------------------- #
# Sentence segmentation (§59.18).
# --------------------------------------------------------------------------- #
def test_split_sentences_splits_on_terminators() -> None:
    assert split_sentences("Hello there. How are you? Fine!") == [
        "Hello there.",
        "How are you?",
        "Fine!",
    ]


def test_split_sentences_handles_newlines_and_whitespace() -> None:
    assert split_sentences("  line one \n line two  ") == ["line one", "line two"]


def test_split_sentences_empty_input() -> None:
    assert split_sentences("") == []
    assert split_sentences("   \n  ") == []


# --------------------------------------------------------------------------- #
# Null engine degrades honestly (§59.18, §64.11).
# --------------------------------------------------------------------------- #
def test_null_engine_is_unavailable() -> None:
    engine = NullTTSEngine(reason="no tts")
    assert engine.available is False
    assert engine.unavailable_reason == "no tts"


async def test_null_engine_synthesize_raises() -> None:
    engine = NullTTSEngine(reason="no tts")
    with pytest.raises(DependencyUnavailableError):
        await engine.synthesize("hello")


# --------------------------------------------------------------------------- #
# Piper adapter, against an injected runner (§59.18).
# --------------------------------------------------------------------------- #
async def test_synthesize_returns_runner_audio() -> None:
    runner = RecordingRunner(b"\x01\x02")
    engine = PiperTTS(_settings(), runner=runner)
    audio = await engine.synthesize("hello")
    assert audio == b"\x01\x02"
    assert runner.calls[0][1] == "hello"


def test_piper_argv_is_a_literal_command() -> None:
    engine = PiperTTS(_settings())
    assert engine._argv(None) == ["piper", "--model", "voice.onnx", "--output_raw"]
    # A per-call voice override replaces the model, never the shell.
    assert engine._argv("other.onnx")[2] == "other.onnx"


async def test_synthesize_stream_is_sentence_level() -> None:
    runner = RecordingRunner()
    engine = PiperTTS(_settings(), runner=runner)
    chunks = await _collect(engine.synthesize_stream("One. Two three."))
    assert len(chunks) == 2
    assert [call[1] for call in runner.calls] == ["One.", "Two three."]


async def test_empty_text_synthesises_nothing() -> None:
    runner = RecordingRunner()
    engine = PiperTTS(_settings(), runner=runner)
    assert await engine.synthesize("   ") == b""
    assert runner.calls == []


async def test_empty_audio_from_runner_is_an_error() -> None:
    engine = PiperTTS(_settings(), runner=RecordingRunner(b""))
    with pytest.raises(UltronError):
        await engine.synthesize("hello")


def test_available_with_runner_is_true() -> None:
    engine = PiperTTS(_settings(), runner=RecordingRunner())
    assert engine.available is True
    assert engine.unavailable_reason is None


def test_unavailable_without_model_or_binary() -> None:
    # No model configured.
    no_model = PiperTTS(_settings(tts_model_path=""))
    assert no_model.available is False
    assert "TTS_MODEL_PATH" in (no_model.unavailable_reason or "")

    # Model present but the executable is not resolvable.
    no_binary = PiperTTS(_settings(tts_binary_path="piper-not-installed-xyz"))
    assert no_binary.available is False
    assert no_binary.unavailable_reason is not None


async def test_unavailable_engine_raises_on_synthesize() -> None:
    engine = PiperTTS(_settings(tts_model_path=""))
    with pytest.raises(DependencyUnavailableError):
        await engine.synthesize("hello")


# --------------------------------------------------------------------------- #
# Factory (§59.18).
# --------------------------------------------------------------------------- #
def test_factory_returns_null_when_disabled() -> None:
    assert isinstance(create_tts_engine(VoiceSettings(enabled=False)), NullTTSEngine)


def test_factory_returns_null_for_none_engine() -> None:
    assert isinstance(
        create_tts_engine(VoiceSettings(enabled=True, tts_engine="none")), NullTTSEngine
    )


def test_factory_returns_piper() -> None:
    engine = create_tts_engine(_settings())
    assert isinstance(engine, PiperTTS)
    assert isinstance(engine, TTSEngine)
