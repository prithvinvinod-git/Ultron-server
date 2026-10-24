"""Text-to-speech engine interface (T151, spec §25 / §59.18).

The voice agent must not be coupled to Piper (§59.18): synthesis lives behind
this interface and the engine is selected by configuration. Piper is merely the
first local adapter; a future cloud engine drops in behind the same ABC.

**``TTS_ENGINE=none`` is a valid deployment and it degrades honestly.** Every
synthesis from :class:`NullTTSEngine` raises
:class:`~app.core.errors.DependencyUnavailableError`, so an accidental dependency
on spoken output surfaces as a clear 503 rather than as silence a caller might
mistake for a working (if quiet) speaker (§59.18, §64.11).

**Sentence-level streaming is the default, not an adapter detail.** §59.18
requires synthesis per sentence so playback can begin before the whole response
exists (§59.19). :meth:`TTSEngine.synthesize_stream` implements that once, on the
interface, by splitting the text and synthesising each sentence in turn -- so
every adapter inherits the streaming behaviour and a caller never waits for a
whole paragraph. An adapter that can stream mid-sentence overrides it.

**Engines are cheap to construct and open nothing until first use** (the same
discipline as the STT engines and the embedding providers). A process is spawned
on the first synthesis, where a missing binary or model can be reported.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence

from app.core.errors import DependencyUnavailableError
from app.observability import get_logger
from app.voice.tts.segments import split_sentences

__all__ = ["NullTTSEngine", "TTSEngine"]

_LOGGER = get_logger(__name__)


class TTSEngine(ABC):
    """Turns text into audio. Provider-independent (§25)."""

    #: Short name for logs and audit rows (``"piper"``, ``"none"``).
    name: str = "unnamed"

    #: Sample rate of the audio this engine emits, in Hz.
    sample_rate: int = 22050

    #: Media type of the audio this engine emits. The default is headerless
    #: little-endian 16-bit PCM (what Piper and ElevenLabs ``pcm_*`` produce);
    #: an adapter that emits a container (MP3) overrides it so a client can pick
    #: a decoder instead of assuming raw samples.
    content_type: str = "audio/L16"

    @property
    def available(self) -> bool:
        """Whether this engine can synthesise at all.

        ``False`` is a supported state, not an error. Callers that would raise a
        :class:`~app.core.errors.DependencyUnavailableError` can ask first and
        take the honest degraded path instead.
        """
        return True

    @property
    def unavailable_reason(self) -> str | None:
        """Why :attr:`available` is ``False``, or ``None`` when it is ``True``."""
        return None

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> bytes:
        """Synthesise ``text`` and return the complete audio for it.

        ``tags`` are delivery cues -- ``calm``, ``excited``, ``angry`` -- that a
        provider understands as inline markers. An engine that has no notion of
        them ignores the argument rather than failing: the caller asks for a
        tone, and a provider without tones still speaks.
        """

    async def synthesize_stream(
        self,
        text: str,
        *,
        voice: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> AsyncIterator[bytes]:
        """Yield audio one sentence at a time (§59.18, §59.19).

        Playback can start on the first yielded chunk, before the rest of the
        text is synthesised. Sentences that a streaming-aware adapter cannot
        merge are simply synthesised in order; an empty input yields nothing.

        The delivery cues are handed to every sentence, because a provider that
        reads them as a sentence-level marker (Fish Audio does) needs them at
        the start of each sentence, not only at the start of the text.
        """
        for sentence in split_sentences(text):
            yield await self.synthesize(sentence, voice=voice, tags=tags)

    async def aclose(self) -> None:
        """Release any held process or connection. Safe to call more than once."""
        return None


class NullTTSEngine(TTSEngine):
    """The honest "no text-to-speech here" engine.

    Chosen when ``TTS_ENGINE=none`` or when a selected engine is unconfigured.
    Every synthesis raises :class:`~app.core.errors.DependencyUnavailableError`,
    so a deployment that expects to be heard finds out with a clear 503.
    """

    name = "none"

    def __init__(self, *, reason: str = "") -> None:
        self._reason = reason or "no text-to-speech engine is configured"

    @property
    def available(self) -> bool:
        return False

    @property
    def unavailable_reason(self) -> str:
        return self._reason

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> bytes:
        raise DependencyUnavailableError("tts", reason=self._reason)
