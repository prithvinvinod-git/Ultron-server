"""Primary/fallback engine chaining for STT and TTS (T150/T151, spec §59.17–59.18).

"Cloud + local" is not a compile-time choice: a deployment wants a cloud provider
*first* and an installed local engine as the path it degrades to when the cloud
is unreachable, unconfigured, or rate-limited. These two wrappers express that
without leaking into the pipeline, which still sees one
:class:`~app.voice.stt.base.STTEngine` / :class:`~app.voice.tts.base.TTSEngine`.

**One-shot calls fall back; a stream hands over only while it is still silent.**
A one-shot ``transcribe``/``synthesize`` retries the fallback when the primary
raises :class:`~app.core.errors.DependencyUnavailableError` -- the class a cloud
adapter uses for an unreachable service, a rejected key, a 429, or an exhausted
credit balance. A *stream* cannot be re-joined mid-flight without replaying audio
it has already emitted, so a stream selects whichever engine is
:attr:`~STTEngine.available` when it starts. If the primary then fails **before
anything has been emitted**, the fallback takes the whole stream over: no audio
or transcript was produced, so nothing is replayed and the user still gets an
answer. Once output has begun, the failure propagates: a transcript that has
started must not silently restart from the same audio (§59.17 -- no
retransmission). The next utterance picks the healthy engine again.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from app.core.errors import DependencyUnavailableError
from app.observability import get_logger
from app.voice.stt.base import AudioChunk, STTEngine, TranscriptChunk
from app.voice.tts.base import TTSEngine

__all__ = ["FallbackSTTEngine", "FallbackTTSEngine"]

_LOGGER = get_logger(__name__)


class FallbackSTTEngine(STTEngine):
    """A primary STT engine with an honest degraded path behind it."""

    def __init__(self, primary: STTEngine, fallback: STTEngine) -> None:
        self._primary = primary
        self._fallback = fallback
        #: The two engines, exposed so a caller can report which one is serving
        #: a session (the primary first, the fallback behind it).
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}>{fallback.name}"
        chosen = primary if primary.available else fallback
        self.sample_rate = chosen.sample_rate

    @property
    def available(self) -> bool:
        return self._primary.available or self._fallback.available

    @property
    def unavailable_reason(self) -> str | None:
        if self.available:
            return None
        primary = self._primary.unavailable_reason or self._primary.name
        fallback = self._fallback.unavailable_reason or self._fallback.name
        return f"no speech-to-text engine is available ({primary}; {fallback})"

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        if self._primary.available:
            try:
                return await self._primary.transcribe(audio, language=language)
            except DependencyUnavailableError:
                self._degraded()
        return await self._fallback.transcribe(audio, language=language)

    def transcribe_stream(
        self, chunks: AsyncIterator[AudioChunk], *, language: str | None = None
    ) -> AsyncIterator[TranscriptChunk]:
        return self._stream(chunks, language)

    async def _stream(
        self, chunks: AsyncIterator[AudioChunk], language: str | None
    ) -> AsyncIterator[TranscriptChunk]:
        engine = self._primary if self._primary.available else self._fallback
        emitted = False
        try:
            async for chunk in engine.transcribe_stream(chunks, language=language):
                emitted = True
                yield chunk
        except DependencyUnavailableError:
            if emitted:
                raise
            # Nothing was transcribed yet, so the fallback can still take the
            # utterance over without replaying anything.
            self._degraded()
            async for chunk in self._fallback.transcribe_stream(chunks, language=language):
                yield chunk

    def _degraded(self) -> None:
        _LOGGER.warning(
            "stt primary failed; using fallback",
            extra={
                "event": "stt.fallback",
                "primary": self._primary.name,
                "fallback": self._fallback.name,
            },
        )

    async def aclose(self) -> None:
        await self._primary.aclose()
        await self._fallback.aclose()


class FallbackTTSEngine(TTSEngine):
    """A primary TTS engine with an honest degraded path behind it."""

    def __init__(self, primary: TTSEngine, fallback: TTSEngine) -> None:
        self._primary = primary
        self._fallback = fallback
        #: The two engines, exposed so a caller can report which one is serving
        #: a session (the primary first, the fallback behind it).
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}>{fallback.name}"
        chosen = primary if primary.available else fallback
        self.sample_rate = chosen.sample_rate
        self.content_type = chosen.content_type

    @property
    def available(self) -> bool:
        return self._primary.available or self._fallback.available

    @property
    def unavailable_reason(self) -> str | None:
        if self.available:
            return None
        primary = self._primary.unavailable_reason or self._primary.name
        fallback = self._fallback.unavailable_reason or self._fallback.name
        return f"no text-to-speech engine is available ({primary}; {fallback})"

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> bytes:
        if self._primary.available:
            try:
                return await self._primary.synthesize(text, voice=voice, tags=tags)
            except DependencyUnavailableError:
                self._degraded()
        return await self._fallback.synthesize(text, voice=voice, tags=tags)

    def synthesize_stream(
        self,
        text: str,
        *,
        voice: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> AsyncIterator[bytes]:
        return self._stream(text, voice, tags)

    async def _stream(
        self, text: str, voice: str | None, tags: Sequence[str] | None
    ) -> AsyncIterator[bytes]:
        engine = self._primary if self._primary.available else self._fallback
        emitted = False
        try:
            async for chunk in engine.synthesize_stream(text, voice=voice, tags=tags):
                emitted = True
                yield chunk
        except DependencyUnavailableError:
            if emitted:
                raise
            # Nothing was played yet, so the fallback can still speak the turn.
            self._degraded()
            async for chunk in self._fallback.synthesize_stream(
                text, voice=voice, tags=tags
            ):
                yield chunk

    def _degraded(self) -> None:
        _LOGGER.warning(
            "tts primary failed; using fallback",
            extra={
                "event": "tts.fallback",
                "primary": self._primary.name,
                "fallback": self._fallback.name,
            },
        )

    async def aclose(self) -> None:
        await self._primary.aclose()
        await self._fallback.aclose()
