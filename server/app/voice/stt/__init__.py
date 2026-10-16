"""Speech-to-text adapters (T150, spec §25 / §59.17).

The subsystem is provider-independent: :class:`~app.voice.stt.base.STTEngine` is
the seam and :class:`~app.voice.stt.faster_whisper.FasterWhisperSTT` is the first
local adapter. :func:`create_stt_engine` turns :class:`~app.config.VoiceSettings`
into an engine, returning the honest :class:`~app.voice.stt.base.NullSTTEngine`
when STT is disabled or unconfigured -- never a fake engine that returns invented
text (§59.17, §64.11).
"""

from __future__ import annotations

from app.config import VoiceSettings
from app.observability import get_logger
from app.voice.stt.availability import (
    faster_whisper_available,
    faster_whisper_unavailable_reason,
)
from app.voice.stt.base import AudioChunk, NullSTTEngine, STTEngine, TranscriptChunk
from app.voice.stt.faster_whisper import FasterWhisperSTT
from app.voice.stt.utterance import OverlapDeduper, UtteranceBoundary

__all__ = [
    "AudioChunk",
    "FasterWhisperSTT",
    "NullSTTEngine",
    "OverlapDeduper",
    "STTEngine",
    "TranscriptChunk",
    "UtteranceBoundary",
    "create_stt_engine",
    "faster_whisper_available",
    "faster_whisper_unavailable_reason",
]

_LOGGER = get_logger(__name__)


def create_stt_engine(settings: VoiceSettings) -> STTEngine:
    """Build the configured STT engine, or an honest null engine (§59.17).

    A selected engine that cannot actually run (faster-whisper not installed)
    still returns a real engine object whose :attr:`~STTEngine.available` is
    ``False`` and which raises :class:`~app.core.errors.DependencyUnavailableError`
    on use -- so the caller learns *why* at the seam instead of the server
    refusing to boot over an optional feature.
    """
    if not settings.enabled or settings.stt_engine == "none":
        reason = "voice is disabled" if not settings.enabled else "no STT engine is configured"
        _LOGGER.info("stt disabled", extra={"event": "stt.disabled", "reason": reason})
        return NullSTTEngine(reason=reason)

    if settings.stt_engine == "faster-whisper":
        engine = FasterWhisperSTT(settings)
        if not engine.available:
            _LOGGER.warning(
                "stt engine unavailable",
                extra={
                    "event": "stt.engine_unavailable",
                    "engine": settings.stt_engine,
                    "reason": engine.unavailable_reason,
                },
            )
        return engine

    return NullSTTEngine(reason=f"unknown STT engine '{settings.stt_engine}'")
