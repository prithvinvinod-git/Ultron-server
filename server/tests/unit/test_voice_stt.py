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
from app.voice.fallback import FallbackSTTEngine
from app.voice.stt import (
    AudioChunk,
    FasterWhisperSTT,
    GroqWhisperSTT,
    NullSTTEngine,
    OverlapDeduper,
    STTEngine,
    TranscriptChunk,
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


async def test_transport_frames_are_buffered_not_transcribed_individually() -> None:
    # Regression: the microphone sounded deaf because each 20 ms transport frame
    # was handed to Whisper on its own, which transcribes to nothing. The engine
    # must accumulate the utterance and transcribe it as one clip.
    model = FakeModel(["hello there"])
    engine = FasterWhisperSTT(_settings(), model_factory=lambda: model)
    frames = [_speech(b"\x00\x01" * 320) for _ in range(10)]  # 20 ms each @ 16 kHz
    chunks = [*frames, AudioChunk(data=b"", is_speech=False, end_of_utterance=True)]
    out = [c async for c in engine.transcribe_stream(_aiter(chunks))]

    finals = [c for c in out if c.is_final]
    assert [f.text for f in finals] == ["hello there"]
    assert len(model.calls) == 1  # one clip, not ten frames
    assert len(model.calls[0][0]) == 6400  # every byte of the utterance


async def test_pause_does_not_retranscribe_the_utterance() -> None:
    # §59.17's defect: a mid-sentence pause must produce ONE utterance with no
    # duplicated text, not two overlapping ones.
    model = FakeModel(["play some", "play some music"])
    engine = FasterWhisperSTT(
        _settings(), model_factory=lambda: model, silence_chunks=3, partial_interval_seconds=0.0001
    )
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
    # Partials are deltas of the growing buffer, so the transcript reads once.
    assert [p.text for p in partials] == ["play some", "music"]
    assert len(finals) == 1
    assert finals[0].text == "play some music"
    assert finals[0].utterance_id == 0
    # The final reuses the last partial instead of paying for the same audio twice.
    assert len(model.calls) == 2


async def test_partials_only_start_after_enough_audio() -> None:
    model = FakeModel(["a whole sentence"])
    engine = FasterWhisperSTT(_settings(), model_factory=lambda: model)
    frames = [_speech(b"\x00\x01" * 320) for _ in range(10)]  # 0.2 s total
    out = [c async for c in engine.transcribe_stream(_aiter(frames))]
    assert [c for c in out if not c.is_final] == []
    assert [c.text for c in out if c.is_final] == ["a whole sentence"]


async def test_faster_whisper_end_of_utterance_closes_immediately() -> None:
    model = FakeModel(["hello world"])
    engine = FasterWhisperSTT(_settings(), model_factory=lambda: model, silence_chunks=99)
    chunks = [
        _speech(b"pcm1"),
        _speech(b"pcm2"),
        AudioChunk(data=b"", is_speech=False, end_of_utterance=True),
    ]
    out = [c async for c in engine.transcribe_stream(_aiter(chunks))]
    finals = [c for c in out if c.is_final]
    assert [f.text for f in finals] == ["hello world"]
    assert finals[0].utterance_id == 0


async def test_two_utterances_get_distinct_ids() -> None:
    model = FakeModel(["first phrase", "second phrase"])
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


def test_factory_returns_groq() -> None:
    engine = create_stt_engine(_settings(stt_engine="groq", groq_api_key="gsk_test"))
    assert isinstance(engine, GroqWhisperSTT)
    assert engine.available is True


def test_factory_chains_a_configured_fallback() -> None:
    engine = create_stt_engine(
        _settings(stt_engine="groq", stt_fallback_engine="faster-whisper")
    )
    assert isinstance(engine, FallbackSTTEngine)
    assert engine.name == "groq>faster-whisper"


# --------------------------------------------------------------------------- #
# Groq cloud adapter (§59.17), against an injected transcriber.
# --------------------------------------------------------------------------- #
class RecordingTranscriber:
    """Pops one scripted text per call and records what it was handed."""

    def __init__(self, texts: Iterable[str]) -> None:
        self._texts = list(texts)
        self.calls: list[tuple[bytes, str | None]] = []

    async def __call__(self, audio: bytes, language: str | None) -> str:
        self.calls.append((audio, language))
        return self._texts.pop(0) if self._texts else ""


def test_groq_availability_is_the_key_presence() -> None:
    missing = GroqWhisperSTT(_settings(stt_engine="groq", groq_api_key=""))
    assert missing.available is False
    assert "GROQ_API_KEY" in (missing.unavailable_reason or "")

    configured = GroqWhisperSTT(
        _settings(stt_engine="groq", groq_api_key="gsk_secret")
    )
    assert configured.available is True
    assert configured.unavailable_reason is None


async def test_groq_one_shot_transcribe() -> None:
    transcriber = RecordingTranscriber(["hello there"])
    engine = GroqWhisperSTT(_settings(stt_engine="groq"), transcriber=transcriber)
    assert await engine.transcribe(b"pcm") == "hello there"
    assert transcriber.calls == [(b"pcm", "en")]


async def test_groq_stream_emits_incremental_partials_then_full_final() -> None:
    # The buffer grows; only the new suffix is streamed, the final is whole.
    transcriber = RecordingTranscriber(["play some", "play some music"])
    engine = GroqWhisperSTT(
        _settings(stt_engine="groq"), transcriber=transcriber, silence_chunks=3
    )
    chunks = [_speech(b"pcm1"), _speech(b"pcm2"), _silence(), _silence(), _silence()]
    out = [c async for c in engine.transcribe_stream(_aiter(chunks))]

    partials = [c.text for c in out if not c.is_final]
    finals = [c for c in out if c.is_final]
    assert partials == ["play some", "music"]
    assert len(finals) == 1
    assert finals[0].text == "play some music"
    assert finals[0].utterance_id == 0


async def test_groq_stream_without_partials_only_recognises_at_the_boundary() -> None:
    transcriber = RecordingTranscriber(["first and second"])
    engine = GroqWhisperSTT(
        _settings(stt_engine="groq", stt_partial_results=False),
        transcriber=transcriber,
        silence_chunks=2,
    )
    chunks = [_speech(b"pcm1"), _speech(b"pcm2"), _silence(), _silence()]
    out = [c async for c in engine.transcribe_stream(_aiter(chunks))]

    # One request, at the boundary, and it carried both speech chunks.
    assert len(transcriber.calls) == 1
    assert transcriber.calls[0][0] == b"pcm1pcm2"
    assert [c.text for c in out if c.is_final] == ["first and second"]
    assert all(c.is_final for c in out)


async def test_groq_stream_flushes_an_open_utterance_at_end_of_stream() -> None:
    transcriber = RecordingTranscriber(["unfinished thought"])
    engine = GroqWhisperSTT(_settings(stt_engine="groq"), transcriber=transcriber)
    out = [c async for c in engine.transcribe_stream(_aiter([_speech(b"pcm")]))]
    assert [c.text for c in out if c.is_final] == ["unfinished thought"]


async def test_groq_unavailable_transcribe_raises() -> None:
    engine = GroqWhisperSTT(_settings(stt_engine="groq", groq_api_key=""))
    with pytest.raises(DependencyUnavailableError):
        await engine.transcribe(b"pcm")


async def test_groq_end_of_utterance_closes_immediately() -> None:
    # A transport-level end-of-turn (client VAD) closes the turn without silence.
    transcriber = RecordingTranscriber(["hello world"])
    engine = GroqWhisperSTT(
        _settings(stt_engine="groq", stt_partial_results=False),
        transcriber=transcriber,
        silence_chunks=99,
    )
    chunks = [
        _speech(b"pcm1"),
        _speech(b"pcm2"),
        AudioChunk(data=b"", is_speech=False, end_of_utterance=True),
    ]
    out = [c async for c in engine.transcribe_stream(_aiter(chunks))]
    finals = [c for c in out if c.is_final]
    assert [f.text for f in finals] == ["hello world"]
    assert finals[0].utterance_id == 0


def test_groq_wraps_pcm_in_a_wav_container() -> None:
    from app.voice.stt.groq import _wrap_pcm

    pcm = b"\x01\x00\x02\x00"
    wav = _wrap_pcm(pcm, "wav", 16000)
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert wav[12:16] == b"fmt "
    assert wav[36:40] == b"data"
    assert wav[40:44] == len(pcm).to_bytes(4, "little")
    assert wav[44:] == pcm
    # A non-wav target has no encoder here, so the bytes pass through unchanged.
    assert _wrap_pcm(pcm, "mp3", 16000) == pcm


# --------------------------------------------------------------------------- #
# Fallback chaining (§59.17): cloud first, local behind it.
# --------------------------------------------------------------------------- #
class FakeSTT(STTEngine):
    def __init__(
        self,
        name: str,
        *,
        text: str = "",
        available: bool = True,
        reason: str | None = None,
        fail: bool = False,
    ) -> None:
        self.name = name
        self._text = text
        self._available = available
        self._reason = reason
        self._fail = fail

    @property
    def available(self) -> bool:
        return self._available

    @property
    def unavailable_reason(self) -> str | None:
        return self._reason

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        if self._fail:
            raise DependencyUnavailableError("stt", reason="primary broke")
        return self._text

    async def transcribe_stream(
        self, chunks: AsyncIterator[AudioChunk], *, language: str | None = None
    ) -> AsyncIterator[TranscriptChunk]:
        if not self._available:
            raise DependencyUnavailableError("stt", reason=self._reason or "unavailable")
        yield TranscriptChunk(text=self._text, is_final=True)


async def test_fallback_uses_primary_when_available() -> None:
    engine = FallbackSTTEngine(
        FakeSTT("cloud", text="from cloud"), FakeSTT("local", text="from local")
    )
    assert engine.name == "cloud>local"
    assert await engine.transcribe(b"pcm") == "from cloud"


async def test_fallback_uses_the_local_engine_when_the_cloud_is_unavailable() -> None:
    engine = FallbackSTTEngine(
        FakeSTT("cloud", available=False, reason="no key"),
        FakeSTT("local", text="from local"),
    )
    assert engine.available is True
    assert await engine.transcribe(b"pcm") == "from local"


async def test_fallback_retries_the_secondary_when_the_primary_raises() -> None:
    engine = FallbackSTTEngine(
        FakeSTT("cloud", fail=True), FakeSTT("local", text="local picked up")
    )
    assert await engine.transcribe(b"pcm") == "local picked up"


def test_fallback_reports_unavailable_only_when_both_are() -> None:
    engine = FallbackSTTEngine(
        FakeSTT("cloud", available=False, reason="no key"),
        FakeSTT("local", available=False, reason="not installed"),
    )
    assert engine.available is False
    assert "no key" in (engine.unavailable_reason or "")
    assert "not installed" in (engine.unavailable_reason or "")


async def test_fallback_stream_selects_the_available_engine() -> None:
    engine = FallbackSTTEngine(
        FakeSTT("cloud", available=False, reason="no key"),
        FakeSTT("local", text="local text"),
    )
    out = [c async for c in engine.transcribe_stream(_aiter([]))]
    assert [c.text for c in out] == ["local text"]
