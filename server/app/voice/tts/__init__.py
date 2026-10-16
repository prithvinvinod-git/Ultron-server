"""Text-to-speech adapters (T151, spec §25 / §59.18).

Provider-independent: :class:`~app.voice.tts.base.TTSEngine` is the seam and
:class:`~app.voice.tts.piper.PiperTTS` is the first local adapter.
:func:`create_tts_engine` turns :class:`~app.config.VoiceSettings` into an
engine, returning the honest :class:`~app.voice.tts.base.NullTTSEngine` when TTS
is disabled or unconfigured -- never a fake engine that emits silence and calls
it speech (§59.18, §64.11).
"""

from __future__ import annotations

from app.config import VoiceSettings
from app.observability import get_logger
from app.voice.tts.availability import piper_available, piper_unavailable_reason
from app.voice.tts.base import NullTTSEngine, TTSEngine
from app.voice.tts.piper import PiperTTS
from app.voice.tts.segments import split_sentences

__all__ = [
    "NullTTSEngine",
    "PiperTTS",
    "TTSEngine",
    "create_tts_engine",
    "piper_available",
    "piper_unavailable_reason",
    "split_sentences",
]

_LOGGER = get_logger(__name__)


def create_tts_engine(settings: VoiceSettings) -> TTSEngine:
    """Build the configured TTS engine, or an honest null engine (§59.18).

    A selected engine that cannot actually run (Piper missing, or no voice model
    configured) still returns a real engine object whose
    :attr:`~TTSEngine.available` is ``False`` and which raises
    :class:`~app.core.errors.DependencyUnavailableError` on use -- so the caller
    learns *why* at the seam instead of the server refusing to boot over an
    optional feature.
    """
    if not settings.enabled or settings.tts_engine == "none":
        reason = "voice is disabled" if not settings.enabled else "no TTS engine is configured"
        _LOGGER.info("tts disabled", extra={"event": "tts.disabled", "reason": reason})
        return NullTTSEngine(reason=reason)

    if settings.tts_engine == "piper":
        engine = PiperTTS(settings)
        if not engine.available:
            _LOGGER.warning(
                "tts engine unavailable",
                extra={
                    "event": "tts.engine_unavailable",
                    "engine": settings.tts_engine,
                    "reason": engine.unavailable_reason,
                },
            )
        return engine

    return NullTTSEngine(reason=f"unknown TTS engine '{settings.tts_engine}'")
