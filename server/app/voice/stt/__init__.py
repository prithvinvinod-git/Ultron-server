"""Speech-to-text adapters (T150, spec §25 / §59.17).

The subsystem is provider-independent: :class:`~app.voice.stt.base.STTEngine` is
the seam, and the adapters are a cloud engine
(:class:`~app.voice.stt.groq.GroqWhisperSTT`) and a local one
(:class:`~app.voice.stt.faster_whisper.FasterWhisperSTT`).
:func:`create_stt_engine` turns :class:`~app.config.VoiceSettings` into an
engine -- a primary, or a primary chained to a fallback
(:class:`~app.voice.fallback.FallbackSTTEngine`) -- returning the honest
:class:`~app.voice.stt.base.NullSTTEngine` when STT is disabled or unconfigured,
never a fake engine that returns invented text (§59.17, §64.11).
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
from app.voice.stt.groq import GroqWhisperSTT
from app.voice.stt.utterance import OverlapDeduper, UtteranceBoundary

__all__ = [
    "AudioChunk",
    "FasterWhisperSTT",
    "GroqWhisperSTT",
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


def _build_stt_engine(settings: VoiceSettings, name: str) -> STTEngine:
    """Build one concrete STT engine by name (``none``/unknown -> null)."""
    if name == "faster-whisper":
        return FasterWhisperSTT(settings)
    if name == "groq":
        return GroqWhisperSTT(settings)
    if name == "none":
        return NullSTTEngine(reason="no STT engine is configured")
    return NullSTTEngine(reason=f"unknown STT engine '{name}'")


def create_stt_engine(settings: VoiceSettings) -> STTEngine:
    """Build the configured STT engine, or an honest null engine (§59.17).

    A selected engine that cannot actually run (faster-whisper not installed, a
    cloud key unset) still returns a real engine object whose
    :attr:`~STTEngine.available` is ``False`` and which raises
    :class:`~app.core.errors.DependencyUnavailableError` on use -- so the caller
    learns *why* at the seam instead of the server refusing to boot over an
    optional feature. When ``STT_FALLBACK_ENGINE`` names a second engine, the two
    are chained so a cloud primary degrades to a local one.
    """
    if not settings.enabled:
        _LOGGER.info(
            "stt disabled",
            extra={"event": "stt.disabled", "reason": "voice is disabled"},
        )
        return NullSTTEngine(reason="voice is disabled")

    if settings.stt_engine == "none" and settings.stt_fallback_engine == "none":
        _LOGGER.info(
            "stt disabled",
            extra={"event": "stt.disabled", "reason": "no STT engine is configured"},
        )
        return NullSTTEngine(reason="no STT engine is configured")

    primary = _build_stt_engine(settings, settings.stt_engine)
    fallback_name = settings.stt_fallback_engine
    if fallback_name in ("none", settings.stt_engine):
        engine = primary
    else:
        # Imported here: ``app.voice.fallback`` imports both engine bases, and a
        # module-level import would cycle through the sibling ``tts`` package.
        from app.voice.fallback import FallbackSTTEngine

        engine = FallbackSTTEngine(primary, _build_stt_engine(settings, fallback_name))

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
