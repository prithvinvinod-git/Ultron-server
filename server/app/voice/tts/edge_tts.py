"""edge-tts local TTS adapter (T151, spec §59.18).

The second local text-to-speech engine, alongside Piper. It is a *local* process
in the sense that matters to §59.18 -- no API key and no account -- but it
reaches Microsoft's Edge read-aloud service over the network, so it fills the gap
between a fully offline Piper voice and a paid cloud provider.

**Nothing is imported until first use.** ``edge_tts`` is an optional dependency;
the adapter probes availability at the seam and imports the package inside the
synthesis call, reporting an honest
:class:`~app.core.errors.DependencyUnavailableError` when it is missing rather
than crashing at import (§59.18, §64.11).

**Sentence-level playback comes from the interface.** :meth:`synthesize` handles
one sentence and the inherited
:meth:`~app.voice.tts.base.TTSEngine.synthesize_stream` drives it sentence by
sentence, so playback begins before the whole response exists (§59.19).

**The container is MP3, and that is declared.** edge-tts streams MP3 at 24 kHz;
the adapter reports :attr:`content_type` as ``audio/mpeg`` and
:attr:`sample_rate` as 24000 so a client picks the right decoder instead of
assuming the raw PCM the other adapters emit.

**The synthesis function is injectable** (:attr:`synthesizer`), so the adapter is
unit-testable with no package installed and no network.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.config import VoiceSettings
from app.core.errors import DependencyUnavailableError, UltronError
from app.observability import get_logger
from app.voice.tts.availability import edge_tts_available, edge_tts_unavailable_reason
from app.voice.tts.base import TTSEngine

__all__ = ["EdgeTTSTTS"]

_LOGGER = get_logger(__name__)

#: Synthesises ``text`` in ``voice`` and returns the audio bytes.
Synthesizer = Callable[[str, str | None], Awaitable[bytes]]


class EdgeTTSTTS(TTSEngine):
    """Local TTS over the ``edge-tts`` package (see the module docstring)."""

    name = "edge-tts"

    def __init__(
        self,
        settings: VoiceSettings,
        *,
        synthesizer: Synthesizer | None = None,
    ) -> None:
        self.settings = settings
        self.sample_rate = 24000  # edge-tts streams 24 kHz MP3.
        self.content_type = "audio/mpeg"
        self._synthesizer = synthesizer

    # -- availability ----------------------------------------------------- #
    @property
    def available(self) -> bool:
        return self._synthesizer is not None or edge_tts_available()

    @property
    def unavailable_reason(self) -> str | None:
        if self.available:
            return None
        return edge_tts_unavailable_reason()

    # -- synthesis -------------------------------------------------------- #
    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        """Synthesise one sentence and return MP3 audio."""
        self._require_available()
        if not text.strip():
            return b""
        synth = self._synthesizer or self._synthesize_package
        audio = await synth(text, voice)
        if not audio:
            raise UltronError("edge-tts produced no audio")
        return audio

    async def _synthesize_package(self, text: str, voice: str | None) -> bytes:
        import edge_tts  # lazy: optional dependency

        selected = voice or self.settings.edge_tts_voice
        communicate = edge_tts.Communicate(text, selected)
        collected = bytearray()
        try:
            async for chunk in communicate.stream():
                if _is_audio(chunk):
                    collected.extend(chunk["data"])
        except Exception as exc:  # reboxed as a typed 503
            raise DependencyUnavailableError(
                "tts", reason=f"edge-tts synthesis failed: {exc}", cause=exc
            ) from exc
        if not collected:
            raise DependencyUnavailableError(
                "tts", reason="edge-tts returned no audio"
            )
        _LOGGER.debug("edge-tts synthesised %d bytes", len(collected))
        return bytes(collected)

    def _require_available(self) -> None:
        if not self.available:
            raise DependencyUnavailableError(
                "tts", reason=self.unavailable_reason or "text-to-speech is unavailable"
            )


def _is_audio(chunk: dict[str, Any]) -> bool:
    return chunk.get("type") == "audio" and isinstance(chunk.get("data"), bytes)
