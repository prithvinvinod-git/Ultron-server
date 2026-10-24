"""faster-whisper STT adapter (T150, spec §59.17).

The first local speech-to-text engine, sized for the §59.17 hardware target:
an Intel i5 with **no GPU**. Model size is the dominant latency term on such a
CPU, so the deployment picks the smallest viable model (``STT_MODEL``, default
``base``) and runs it ``int8`` on ``cpu`` -- no CUDA path exists and none is
required for correctness.

**Nothing is imported or loaded until first use.** ``faster_whisper`` and the
model weights are both heavyweight; the adapter probes availability at the seam,
builds the model lazily inside a worker thread, and reports an honest
:class:`~app.core.errors.DependencyUnavailableError` if the package is missing or
the load fails -- rather than crashing at import (§59.17, §64.11).

**Streaming applies the §59.17 policy.** :meth:`transcribe_stream` buffers the
audio of the current utterance, because Whisper transcribes *complete clips*: a
20 ms transport frame carries no words on its own (transcribing each frame would
return nothing, which made the microphone look deaf). While the utterance is
open a partial is re-transcribed every
:attr:`_PARTIAL_INTERVAL_SECONDS` of audio and only the new suffix is emitted
(the manager appends partials), and the boundary closes the turn by
transcribing the whole buffered utterance once. :class:`UtteranceBoundary`
still decides when a turn ends, so a mid-sentence pause does not start a second
utterance.

The transcriber itself is *injectable* (:attr:`model_factory`), so the streaming
policy and the adapter wiring are unit-testable with no whisper install and no
model download -- the same discipline the browser tools used (T111).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

from app.config import VoiceSettings
from app.core.errors import DependencyUnavailableError, UltronError
from app.observability import get_logger
from app.voice.stt.availability import (
    faster_whisper_available,
    faster_whisper_unavailable_reason,
)
from app.voice.stt.base import AudioChunk, STTEngine, TranscriptChunk
from app.voice.stt.utterance import UtteranceBoundary, incremental_suffix

__all__ = ["FasterWhisperSTT"]

_LOGGER = get_logger(__name__)

#: How many consecutive silent chunks close a turn by default. Callers that know
#: their chunk duration can convert a wall-clock silence to this count.
_DEFAULT_SILENCE_CHUNKS = 3

#: How much new audio (in seconds) to accumulate before re-transcribing the
#: utterance for a partial. Whisper needs roughly a second of speech to produce
#: anything useful, and re-running it per transport frame would be pointless.
_PARTIAL_INTERVAL_SECONDS = 1.0

#: Bytes per second of the engine's audio contract: mono, 16-bit PCM.
_BYTES_PER_SECOND = 2


class FasterWhisperSTT(STTEngine):
    """Local STT over faster-whisper, CPU-only (see the module docstring)."""

    name = "faster-whisper"

    def __init__(
        self,
        settings: VoiceSettings,
        *,
        model_factory: Callable[[], Any] | None = None,
        silence_chunks: int = _DEFAULT_SILENCE_CHUNKS,
        partial_interval_seconds: float = _PARTIAL_INTERVAL_SECONDS,
    ) -> None:
        self.settings = settings
        self.sample_rate = 16000  # faster-whisper's native rate.
        self._model_factory = model_factory
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()
        self._silence_chunks = silence_chunks
        self._partial_interval_bytes = max(
            1, int(partial_interval_seconds * self.sample_rate * _BYTES_PER_SECOND)
        )

    # -- availability ----------------------------------------------------- #
    @property
    def available(self) -> bool:
        # An injected factory is always usable; the real one needs the package.
        return self._model_factory is not None or faster_whisper_available()

    @property
    def unavailable_reason(self) -> str | None:
        if self.available:
            return None
        return faster_whisper_unavailable_reason()

    # -- one-shot --------------------------------------------------------- #
    async def transcribe(
        self, audio: bytes, *, language: str | None = None
    ) -> str:
        """Transcribe one complete utterance to its final text."""
        self._require_available()
        model = await self._get_model()
        payload = self._decode_audio(audio)

        def _run() -> str:
            segments, _info = model.transcribe(
                payload,
                language=language or self.settings.stt_language,
                beam_size=1,
            )
            return "".join(segment.text for segment in segments).strip()

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_run), timeout=self.settings.stt_timeout
            )
        except TimeoutError as exc:  # pragma: no cover - timing dependent
            raise UltronError(
                f"speech-to-text timed out after {self.settings.stt_timeout}s"
            ) from exc

    # -- streaming -------------------------------------------------------- #
    async def transcribe_stream(
        self, chunks: AsyncIterator[AudioChunk], *, language: str | None = None
    ) -> AsyncIterator[TranscriptChunk]:
        """Stream partial then final transcripts, honouring §59.17 boundaries."""
        self._require_available()
        model = await self._get_model()
        lang = language or self.settings.stt_language
        boundary = UtteranceBoundary(silence_threshold=self._silence_chunks)
        partials = self.settings.stt_partial_results
        utterance_id = 0
        buffer = bytearray()
        emitted = ""
        last_full: str | None = None
        last_full_bytes = 0
        next_partial_at = self._partial_interval_bytes

        async def _final() -> TranscriptChunk:
            nonlocal last_full, last_full_bytes
            text = await self._final_text(model, buffer, last_full, last_full_bytes, lang)
            last_full = None
            last_full_bytes = 0
            return TranscriptChunk(
                text=text, is_final=True, utterance_id=utterance_id, language=lang
            )

        async for chunk in chunks:
            if chunk.end_of_utterance:
                # The transport saw the end of a turn (client VAD / push-to-talk):
                # close it now instead of waiting for a run of silent chunks.
                if boundary.open or buffer:
                    yield await _final()
                    utterance_id += 1
                    buffer.clear()
                    emitted = ""
                    next_partial_at = self._partial_interval_bytes
                boundary.reset()
                continue

            if not chunk.is_speech or not chunk.data:
                if boundary.feed(is_speech=False):
                    yield await _final()
                    utterance_id += 1
                    buffer.clear()
                    emitted = ""
                    next_partial_at = self._partial_interval_bytes
                continue

            buffer.extend(chunk.data)
            boundary.feed(is_speech=True)
            if not partials or len(buffer) < next_partial_at:
                continue
            next_partial_at = len(buffer) + self._partial_interval_bytes
            text = await self._transcribe_bytes(model, bytes(buffer), language=lang)
            last_full = text
            last_full_bytes = len(buffer)
            delta = incremental_suffix(emitted, text)
            if delta:
                emitted += delta
                yield TranscriptChunk(
                    text=delta, is_final=False, utterance_id=utterance_id, language=lang
                )

        # Flush an utterance the stream ended before silencing.
        if boundary.open or buffer:
            yield await _final()

    # -- internals -------------------------------------------------------- #
    async def _final_text(
        self,
        model: Any,
        buffer: bytearray,
        cached: str | None,
        cached_bytes: int,
        language: str | None,
    ) -> str:
        """Transcribe the whole buffered utterance, reusing an exact partial."""
        if not buffer:
            return cached or ""
        if cached is not None and cached_bytes == len(buffer):
            return cached
        return await self._transcribe_bytes(model, bytes(buffer), language=language)
    async def _transcribe_bytes(
        self, model: Any, audio: bytes, *, language: str | None
    ) -> str:
        payload = self._decode_audio(audio)

        def _run() -> str:
            segments, _info = model.transcribe(
                payload, language=language, beam_size=1
            )
            return "".join(segment.text for segment in segments).strip()

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_run), timeout=self.settings.stt_timeout
            )
        except TimeoutError:
            _LOGGER.warning(
                "stt chunk timed out",
                extra={"event": "stt.chunk_timeout", "timeout": self.settings.stt_timeout},
            )
            return ""

    def _decode_audio(self, audio: bytes) -> Any:
        """Turn headerless 16-bit PCM into what faster-whisper expects.

        The engine contract (:class:`~app.voice.stt.base.AudioChunk`) is raw
        little-endian PCM, but ``WhisperModel.transcribe`` wants a float32 array
        in ``[-1, 1]``. An injected ``model_factory`` (the test seam) receives the
        bytes untouched, so the streaming policy is testable with no numpy.
        """
        if self._model_factory is not None:
            return audio
        try:
            import numpy as np
        except ImportError as exc:  # pragma: no cover - numpy ships with faster-whisper
            raise DependencyUnavailableError(
                "stt",
                reason="numpy is required to decode audio for faster-whisper",
                cause=exc,
            ) from exc
        return np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0

    async def _get_model(self) -> Any:
        """Build the model once, off the event loop, and cache it."""
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is not None:
                return self._model
            factory = self._model_factory or self._default_factory
            try:
                self._model = await asyncio.to_thread(factory)
            except UltronError:
                raise
            except Exception as exc:  # reboxed as a typed 503
                raise DependencyUnavailableError(
                    "stt", reason=f"failed to load faster-whisper: {exc}", cause=exc
                ) from exc
            _LOGGER.info(
                "stt model loaded",
                extra={"event": "stt.model_loaded", "engine": self.name},
            )
            return self._model

    def _default_factory(self) -> Any:
        if not faster_whisper_available():  # pragma: no cover - guarded by probe
            raise DependencyUnavailableError("stt", reason=self.unavailable_reason or "")
        from faster_whisper import WhisperModel  # lazy: optional heavy dependency

        return WhisperModel(
            self.settings.stt_model,
            device=self.settings.stt_device,
            compute_type=self.settings.stt_compute_type,
        )

    def _require_available(self) -> None:
        if not self.available:
            raise DependencyUnavailableError(
                "stt", reason=self.unavailable_reason or "speech-to-text is unavailable"
            )

    async def aclose(self) -> None:
        self._model = None
