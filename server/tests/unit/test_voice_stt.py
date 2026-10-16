"""Unit tests for the speech-to-text subsystem (T155, spec §59.17).

Covers the pure utterance policy (the defect §59.17 names, testable in isolation),
the null engine's honest degradation, the faster-whisper streaming wiring against
an injected model, and the factory's configuration handling -- none of which need
the ``faster-whisper`` package or a downloaded model.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from types import SimpleNamespace
from typing import Any

import pytest

from app.config import VoiceSettings
from app.core.errors import DependencyUnavailableError
from app.voice.stt import (
    AudioChunk,
    FasterWhisperSTT,
    NullSTTEngine,
    OverlapDeduper,
    UtteranceBoundary,
    create_stt_engine,
    faster_whisper_available,
)


def _settings(**over: Any) -> VoiceSettings:
    base: dict[str, Any] = {
        "enabled": True,
        "stt_engine": "faster-whisper",
        "stt_model": "tiny",
        "stt_language": "en",
    }
    base.update(over)
    return VoiceSettings(**base)


class _Segment:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeModel:
    """Stands in for a faster-whisper model: pops one scripted text per call."""

    def __init__(self, texts: Iterable[str]) -> None:
        self._texts = list(texts)
        self.calls: list[tuple[bytes, str | None]] = []

    def transcribe(
        self, audio: bytes, *, language: str | None = None, beam_size: int | None = None, **kw: Any
    ) -> tuple[list[_Segment], SimpleNamespace]:
        self.calls.append((audio, language))
        text = self._texts.pop(0) if self._texts else ""
        return [_Segment(text)], SimpleNamespace(language=language)


async def _aiter(items: Iterable[AudioChunk]) -> AsyncIterator[AudioChunk]:
    for item in items:
        yield item


def _speech(data: bytes) -> AudioChunk:
    return AudioChunk(data=data, is_speech=True)


def _silence() -> AudioChunk:
    return AudioChunk(data=b"", is_speech=False)


# --------------------------------------------------------------------------- #
# Overlap policy (§59.17): a sliding-window transcriber re-reads the tail.
# --------------------------------------------------------------------------- #
def test_overlap_deduper_strips_duplicated_head() -> None:
    dedup = OverlapDeduper(max_overlap=64)
    assert dedup.feed(" play some") == " play some"
    assert dedup.feed(" play some music") == " music"


def test_overlap_deduper_is_empty_when_chunk_is_pure_overlap() -> None:
    dedup = OverlapDeduper(max_overlap=64)
    dedup.feed("play music")
    assert dedup.feed("play music") == ""


def test_overlap_deduper_bounds_the_overlap_so_a_shared_word_survives() -> None:
    # A one-word overlap inside the window is stripped; beyond it, it is real speech.
    dedup = OverlapDeduper(max_overlap=64)
    dedup.feed("turn the lights on now")
    assert dedup.feed("now please and then") == " please and then"


def test_overlap_deduper_reset_forgets_context() -> None:
    dedup = OverlapDeduper(max_overlap=64)
    dedup.feed("play music")
    dedup.reset()
    assert dedup.feed("play music") == "play music"


# --------------------------------------------------------------------------- #
# Utterance boundary (§59.17): a pause must not close the utterance.
# --------------------------------------------------------------------------- #
def test_utterance_stays_open_across_a_short_pause() -> None:
    boundary = UtteranceBoundary(silence_threshold=3)
    assert boundary.feed(is_speech=True) is False  # speech opens it
    assert boundary.feed(is_speech=False) is False  # pause 1 of 3
    assert boundary.feed(is_speech=False) is False  # pause 2 of 3
    assert boundary.feed(is_speech=True) is False  # recovered -- same utterance
    assert boundary.open is True


def test_utterance_closes_after_a_sustained_silence() -> None:
    boundary = UtteranceBoundary(silence_threshold=3)
    boundary.feed(is_speech=True)
    assert boundary.feed(is_speech=False) is False
    assert boundary.feed(is_speech=False) is False
    assert boundary.feed(is_speech=False) is True  # third silent chunk ends the turn
    assert boundary.open is False


def test_utterance_never_closes_without_speech_first() -> None:
    boundary = UtteranceBoundary(silence_threshold=2)
    # Silence before any speech never yields a "final" (nothing to finalise).
    assert boundary.feed(is_speech=False) is False
    assert boundary.feed(is_speech=False) is False
    assert boundary.open is False


# --------------------------------------------------------------------------- #
# Null engine degrades honestly (§59.17, §64.11).
# --------------------------------------------------------------------------- #
def test_null_engine_is_unavailable_with_a_reason() -> None:
    engine = NullSTTEngine(reason="no engine")
    assert engine.available is False
    assert engine.unavailable_reason == "no engine"


async def test_null_engine_transcribe_raises() -> None:
    engine = NullSTTEngine(reason="no engine")
    with pytest.raises(DependencyUnavailableError):
        await engine.transcribe(b"\x00\x00")


async def test_null_engine_stream_raises_on_first_chunk() -> None:
    engine = NullSTTEngine(reason="no engine")
    stream = engine.transcribe_stream(_aiter([]))
    with pytest.raises(DependencyUnavailableError):
        await stream.__anext__()


# --------------------------------------------------------------------------- #
# faster-whisper streaming (§59.17), against an injected model.
# --------------------------------------------------------------------------- #
def test_faster_whisper_reports_availability_from_the_package() -> None:
    engine = FasterWhisperSTT(_settings())
    assert engine.available is faster_whisper_available()
    if not engine.available:
        assert engine.unavailable_reason is not None


async def test_one_shot_transcribe_returns_final_text() -> None:
    model = FakeModel(["hello there"])
    engine = FasterWhisperSTT(_settings(), model_factory=lambda: model)
    text = await engine.transcribe(b"pcm")
    assert text == "hello there"


async def test_pause_does_not_retranscribe_the_utterance() -> None:
    # §59.17's defect: a mid-sentence pause must produce ONE utterance with no
    # duplicated text, not two overlapping ones.
    model = FakeModel([" play some", " play some music"])
    engine = FasterWhisperSTT(_settings(), model_factory=lambda: model, silence_chunks=3)
    chunks = [
        _speech(b"pcm1"),
        _speech(b"pcm2"),
        _silence(),
        _silence(),
        _silence(),
    ]
    out = [c async for c in engine.transcribe_stream(_aiter(chunks))]

    partials = [c for c in out if not c.is_final]
    finals = [c for c in out if c.is_final]
    # The adapter strips each chunk, so the first partial has no leading space.
    assert [p.text for p in partials] == ["play some", " music"]
    assert len(finals) == 1
    assert finals[0].text.strip() == "play some music"
    assert finals[0].utterance_id == 0


async def test_two_utterances_get_distinct_ids() -> None:
    model = FakeModel(["first phrase", " second phrase"])
    engine = FasterWhisperSTT(_settings(), model_factory=lambda: model, silence_chunks=3)
    chunks = [
        _speech(b"pcm1"),
        _silence(),
        _silence(),
        _silence(),
        _speech(b"pcm2"),
    ]
    out = [c async for c in engine.transcribe_stream(_aiter(chunks))]
    finals = [c for c in out if c.is_final]
    assert [f.text.strip() for f in finals] == ["first phrase", "second phrase"]
    assert [f.utterance_id for f in finals] == [0, 1]


# --------------------------------------------------------------------------- #
# Factory (§59.17).
# --------------------------------------------------------------------------- #
def test_factory_returns_null_when_disabled() -> None:
    engine = create_stt_engine(VoiceSettings(enabled=False))
    assert isinstance(engine, NullSTTEngine)
    assert engine.available is False


def test_factory_returns_null_for_none_engine() -> None:
    engine = create_stt_engine(VoiceSettings(enabled=True, stt_engine="none"))
    assert isinstance(engine, NullSTTEngine)


def test_factory_returns_faster_whisper() -> None:
    engine = create_stt_engine(_settings())
    assert isinstance(engine, FasterWhisperSTT)
