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

**Streaming applies the §59.17 policy.** :meth:`transcribe_stream` composes the
two pure helpers from :mod:`app.voice.stt.utterance`: an
:class:`~app.voice.stt.utterance.OverlapDeduper` strips the duplicated head that
a sliding-window transcriber produces, and an
:class:`~app.voice.stt.utterance.UtteranceBoundary` keeps a mid-sentence pause
from closing the utterance. Only the boundary closing emits a final chunk.

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
from app.voice.stt.utterance import OverlapDeduper, UtteranceBoundary

__all__ = ["FasterWhisperSTT"]

_LOGGER = get_logger(__name__)

#: How many consecutive silent chunks close a turn by default. Callers that know
#: their chunk duration can convert a wall-clock silence to this count.
_DEFAULT_SILENCE_CHUNKS = 3


class FasterWhisperSTT(STTEngine):
    """Local STT over faster-whisper, CPU-only (see the module docstring)."""

    name = "faster-whisper"

    def __init__(
        self,
        settings: VoiceSettings,
        *,
        model_factory: Callable[[], Any] | None = None,
        silence_chunks: int = _DEFAULT_SILENCE_CHUNKS,
    ) -> None:
        self.settings = settings
        self.sample_rate = 16000  # faster-whisper's native rate.
        self._model_factory = model_factory
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()
        self._silence_chunks = silence_chunks

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

        def _run() -> str:
            segments, _info = model.transcribe(
                audio,
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
        deduper = OverlapDeduper()
        boundary = UtteranceBoundary(silence_threshold=self._silence_chunks)
        utterance_id = 0
        accumulated = ""

        async for chunk in chunks:
            if not chunk.is_speech or not chunk.data:
                if boundary.feed(is_speech=False):
                    yield TranscriptChunk(
                        text=accumulated, is_final=True, utterance_id=utterance_id, language=lang
                    )
                    utterance_id += 1
                    accumulated = ""
                    deduper.reset()
                continue

            raw = await self._transcribe_bytes(model, chunk.data, language=lang)
            new_text = deduper.feed(raw)
            boundary.feed(is_speech=True)
            if new_text:
                accumulated += new_text
                yield TranscriptChunk(
                    text=new_text, is_final=False, utterance_id=utterance_id, language=lang
                )

        # Flush an utterance the stream ended before silencing.
        if boundary.open:
            yield TranscriptChunk(
                text=accumulated, is_final=True, utterance_id=utterance_id, language=lang
            )

    # -- internals -------------------------------------------------------- #
    async def _transcribe_bytes(
        self, model: Any, audio: bytes, *, language: str | None
    ) -> str:
        def _run() -> str:
            segments, _info = model.transcribe(
                audio, language=language, beam_size=1
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
