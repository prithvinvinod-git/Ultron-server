"""Text-to-speech adapters (T151, spec §25 / §59.18).

Provider-independent: :class:`~app.voice.tts.base.TTSEngine` is the seam, and the
adapters are two cloud engines
(:class:`~app.voice.tts.elevenlabs.ElevenLabsTTS`,
:class:`~app.voice.tts.fish_audio.FishAudioTTS`) and local ones
(:class:`~app.voice.tts.piper.PiperTTS`,
:class:`~app.voice.tts.edge_tts.EdgeTTSTTS`). :func:`create_tts_engine` turns
:class:`~app.config.VoiceSettings` into an engine -- a primary, or a primary
chained to a fallback (:class:`~app.voice.fallback.FallbackTTSEngine`) --
returning the honest :class:`~app.voice.tts.base.NullTTSEngine` when TTS is
disabled or unconfigured, never a fake engine that emits silence and calls it
speech (§59.18, §64.11).
"""

from __future__ import annotations

from app.config import VoiceSettings
from app.observability import get_logger
from app.voice.tts.availability import (
    edge_tts_available,
    edge_tts_unavailable_reason,
    piper_available,
    piper_unavailable_reason,
)
from app.voice.tts.base import NullTTSEngine, TTSEngine
from app.voice.tts.edge_tts import EdgeTTSTTS
from app.voice.tts.elevenlabs import ElevenLabsTTS
from app.voice.tts.fish_audio import FishAudioTTS
from app.voice.tts.piper import PiperTTS
from app.voice.tts.segments import split_sentences

__all__ = [
    "EdgeTTSTTS",
    "ElevenLabsTTS",
    "FishAudioTTS",
    "NullTTSEngine",
    "PiperTTS",
    "TTSEngine",
    "build_tts_engine",
    "create_tts_engine",
    "edge_tts_available",
    "edge_tts_unavailable_reason",
    "piper_available",
    "piper_unavailable_reason",
    "split_sentences",
]

_LOGGER = get_logger(__name__)


def build_tts_engine(settings: VoiceSettings, name: str) -> TTSEngine:
    """Build one concrete TTS engine by name (``none``/unknown -> null).

    Unlike :func:`create_tts_engine` this never consults ``VOICE_ENABLED`` and
    never chains a fallback: it is the *named* engine the user asked for, so the
    voice session can honour a per-request provider choice (§25.1). An unknown
    name yields the honest null engine rather than a guess.
    """
    if name == "piper":
        return PiperTTS(settings)
    if name == "elevenlabs":
        return ElevenLabsTTS(settings)
    if name == "edge-tts":
        return EdgeTTSTTS(settings)
    if name == "fish-audio":
        return FishAudioTTS(settings)
    if name == "none":
        return NullTTSEngine(reason="no TTS engine is configured")
    return NullTTSEngine(reason=f"unknown TTS engine '{name}'")


#: Internal alias kept for the module's own call sites.
_build_tts_engine = build_tts_engine


def create_tts_engine(settings: VoiceSettings) -> TTSEngine:
    """Build the configured TTS engine, or an honest null engine (§59.18).

    A selected engine that cannot actually run (Piper missing, no voice model,
    a cloud key unset) still returns a real engine object whose
    :attr:`~TTSEngine.available` is ``False`` and which raises
    :class:`~app.core.errors.DependencyUnavailableError` on use -- so the caller
    learns *why* at the seam instead of the server refusing to boot over an
    optional feature. When ``TTS_FALLBACK_ENGINE`` names a second engine, the two
    are chained so a cloud primary degrades to a local one.
    """
    if not settings.enabled:
        _LOGGER.info(
            "tts disabled",
            extra={"event": "tts.disabled", "reason": "voice is disabled"},
        )
        return NullTTSEngine(reason="voice is disabled")

    if settings.tts_engine == "none" and settings.tts_fallback_engine == "none":
        _LOGGER.info(
            "tts disabled",
            extra={"event": "tts.disabled", "reason": "no TTS engine is configured"},
        )
        return NullTTSEngine(reason="no TTS engine is configured")

    primary = build_tts_engine(settings, settings.tts_engine)
    fallback_name = settings.tts_fallback_engine
    if fallback_name in ("none", settings.tts_engine):
        engine = primary
    else:
        # Imported here: ``app.voice.fallback`` imports both engine bases, and a
        # module-level import would cycle through the sibling ``stt`` package.
        from app.voice.fallback import FallbackTTSEngine

        engine = FallbackTTSEngine(primary, build_tts_engine(settings, fallback_name))

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
