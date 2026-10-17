"""Groq cloud STT adapter (T150, spec §59.17).

§59.17 makes a cloud speech-to-text provider the *first* choice and local
Whisper the optional, never-assumed fallback. This is that cloud adapter: it
speaks Groq's OpenAI-compatible transcription endpoint
(``POST {base}/audio/transcriptions``) with ``whisper-large-v3-turbo`` by
default, so a CPU-only box gets high-quality recognition without a downloaded
model or a CTranslate2 install.

**Availability is a key check, not a probe.** Unlike ``faster-whisper`` there is
no heavyweight package here -- ``httpx`` is already a core dependency -- so the
engine is available exactly when ``GROQ_API_KEY`` is set. A blank key reports an
honest :class:`~app.core.errors.DependencyUnavailableError` at the seam rather
than a call that fails later (§59.17, §64.11).

**Streaming keeps the §59.17 utterance rules.** :class:`UtteranceBoundary` still
decides when a turn ends, so a mid-sentence pause does not start a second
utterance. Because the cloud endpoint transcribes a *complete* clip, the engine
accumulates the audio of one utterance and sends it whole -- one request per
partial, against the growing buffer -- then emits only the *new* suffix as an
incremental partial (the manager appends partials, §59.19). The final chunk
carries the whole utterance, which is what the pipeline treats as authoritative;
when a partial already transcribed the final buffer its text is reused rather
than paid for twice.

**The transcriber is injectable** (:attr:`transcriber`), so the streaming policy
and the adapter wiring are unit-testable with no network and no key -- the same
discipline the local adapter used.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx

from app.config import VoiceSettings
from app.core.errors import DependencyUnavailableError, OperationTimeoutError
from app.observability import get_logger
from app.voice.stt.base import AudioChunk, STTEngine, TranscriptChunk
from app.voice.stt.utterance import UtteranceBoundary

__all__ = ["GroqWhisperSTT"]

_LOGGER = get_logger(__name__)

#: How many consecutive silent chunks close a turn by default (see the local
#: adapter; the same knob exists so the two engines agree on turn boundaries).
_DEFAULT_SILENCE_CHUNKS = 3

#: Media type declared for each uploaded container.
_CONTENT_TYPES: dict[str, str] = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "webm": "audio/webm",
    "ogg": "audio/ogg",
    "flac": "audio/flac",
    "m4a": "audio/mp4",
}

#: Transcribes one complete clip and returns its text.
Transcriber = Callable[[bytes, str | None], Awaitable[str]]


class GroqWhisperSTT(STTEngine):
    """Cloud STT over Groq's Whisper endpoint (see the module docstring)."""

    name = "groq"

    def __init__(
        self,
        settings: VoiceSettings,
        *,
        client: httpx.AsyncClient | None = None,
        transcriber: Transcriber | None = None,
        silence_chunks: int = _DEFAULT_SILENCE_CHUNKS,
    ) -> None:
        self.settings = settings
        self.sample_rate = 16000  # Whisper's native rate.
        self._client = client
        self._owns_client = client is None
        self._transcriber = transcriber
        self._silence_chunks = silence_chunks

    # -- availability ----------------------------------------------------- #
    @property
    def available(self) -> bool:
        if self._transcriber is not None:
            return True
        return bool(self.settings.groq_api_key.get_secret_value().strip())

    @property
    def unavailable_reason(self) -> str | None:
        if self.available:
            return None
        return (
            "GROQ_API_KEY is not configured; set it to enable cloud "
            "speech-to-text, or use STT_ENGINE=faster-whisper for local"
        )

    # -- one-shot --------------------------------------------------------- #
    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        """Transcribe one complete utterance to its final text."""
        self._require_available()
        if not audio:
            return ""
        return await self._recognise(audio, language or self.settings.stt_language)

    # -- streaming -------------------------------------------------------- #
    async def transcribe_stream(
        self, chunks: AsyncIterator[AudioChunk], *, language: str | None = None
    ) -> AsyncIterator[TranscriptChunk]:
        """Stream incremental partials then the final utterance (§59.17)."""
        self._require_available()
        lang = language or self.settings.stt_language
        boundary = UtteranceBoundary(silence_threshold=self._silence_chunks)
        partials = self.settings.stt_partial_results
        buffer = bytearray()
        emitted = ""
        last_full: str | None = None
        utterance_id = 0

        async for chunk in chunks:
            if chunk.end_of_utterance:
                # The transport saw the end of a turn (client VAD / push-to-talk):
                # close it now instead of waiting for a run of silent chunks.
                if boundary.open or buffer or last_full is not None:
                    text = last_full if last_full is not None else await self._recognise(
                        bytes(buffer), lang
                    )
                    yield TranscriptChunk(
                        text=text, is_final=True, utterance_id=utterance_id, language=lang
                    )
                    utterance_id += 1
                    buffer.clear()
                    emitted = ""
                    last_full = None
                boundary.reset()
                continue

            if not chunk.is_speech or not chunk.data:
                if boundary.feed(is_speech=False):
                    text = last_full if last_full is not None else await self._recognise(
                        bytes(buffer), lang
                    )
                    yield TranscriptChunk(
                        text=text, is_final=True, utterance_id=utterance_id, language=lang
                    )
                    utterance_id += 1
                    buffer.clear()
                    emitted = ""
                    last_full = None
                continue

            buffer.extend(chunk.data)
            boundary.feed(is_speech=True)
            if not partials:
                continue
            text = await self._recognise(bytes(buffer), lang)
            last_full = text
            delta = _incremental(emitted, text)
            if delta:
                emitted += delta
                yield TranscriptChunk(
                    text=delta, is_final=False, utterance_id=utterance_id, language=lang
                )

        # Flush an utterance the stream ended before silencing.
        if boundary.open:
            text = last_full if last_full is not None else await self._recognise(
                bytes(buffer), lang
            )
            yield TranscriptChunk(
                text=text, is_final=True, utterance_id=utterance_id, language=lang
            )

    # -- internals -------------------------------------------------------- #
    async def _recognise(self, audio: bytes, language: str | None) -> str:
        if not audio:
            return ""
        if self._transcriber is not None:
            return await self._with_timeout(self._transcriber(audio, language))
        return await self._recognise_http(audio, language)

    async def _with_timeout(self, awaitable: Awaitable[str]) -> str:
        try:
            return await asyncio.wait_for(awaitable, timeout=self.settings.stt_timeout)
        except TimeoutError as exc:
            raise OperationTimeoutError(
                f"cloud speech-to-text timed out after {self.settings.stt_timeout}s"
            ) from exc

    async def _recognise_http(self, audio: bytes, language: str | None) -> str:
        fmt = self.settings.stt_audio_format
        payload = _wrap_pcm(audio, fmt, self.sample_rate)
        files = {"file": (f"audio.{fmt}", payload, _CONTENT_TYPES.get(fmt, "audio/wav"))}
        data: dict[str, str] = {"model": self.settings.groq_stt_model}
        if language:
            data["language"] = language
        headers = {
            "Authorization": f"Bearer {self.settings.groq_api_key.get_secret_value()}",
        }
        timeout = httpx.Timeout(self.settings.stt_timeout)
        try:
            response = await self._http().post(
                self._endpoint(), headers=headers, files=files, data=data, timeout=timeout
            )
        except httpx.TimeoutException as exc:
            raise OperationTimeoutError(
                f"cloud speech-to-text timed out after {self.settings.stt_timeout}s"
            ) from exc
        except httpx.TransportError as exc:
            raise DependencyUnavailableError(
                "stt", reason=f"Groq is unreachable: {exc}", cause=exc
            ) from exc
        if response.status_code >= 400:
            reason = _error_reason(response.status_code)
            _LOGGER.warning(
                "groq speech-to-text failed",
                extra={
                    "event": "stt.groq_failed",
                    "status": response.status_code,
                    "engine": self.name,
                },
            )
            raise DependencyUnavailableError("stt", reason=reason)
        try:
            payload = response.json()
        except ValueError as exc:
            raise DependencyUnavailableError(
                "stt", reason="Groq returned a non-JSON response", cause=exc
            ) from exc
        text = payload.get("text") if isinstance(payload, dict) else None
        return text.strip() if isinstance(text, str) else ""

    def _endpoint(self) -> str:
        return f"{self.settings.groq_base_url.rstrip('/')}/audio/transcriptions"

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
            self._owns_client = True
        return self._client

    def _require_available(self) -> None:
        if not self.available:
            raise DependencyUnavailableError(
                "stt", reason=self.unavailable_reason or "speech-to-text is unavailable"
            )

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None


def _wrap_pcm(pcm: bytes, fmt: str, sample_rate: int) -> bytes:
    """Wrap raw 16-bit mono PCM in a container the upload endpoint expects.

    :class:`~app.voice.stt.base.AudioChunk` carries headerless little-endian PCM
    (its documented contract), but the transcription endpoint needs a real
    container. ``wav`` is wrapped with a 44-byte RIFF header; for any other
    format there is no encoder here, so the bytes are passed through unchanged and
    the caller must already have supplied that container.
    """
    if fmt != "wav":
        return pcm
    block_align = 2  # mono, 16-bit
    byte_rate = sample_rate * block_align
    header = bytearray()
    header += b"RIFF"
    header += (36 + len(pcm)).to_bytes(4, "little")
    header += b"WAVEfmt "
    header += (16).to_bytes(4, "little")  # fmt chunk size
    header += (1).to_bytes(2, "little")  # PCM
    header += (1).to_bytes(2, "little")  # channels
    header += sample_rate.to_bytes(4, "little")
    header += byte_rate.to_bytes(4, "little")
    header += block_align.to_bytes(2, "little")
    header += (16).to_bytes(2, "little")  # bits per sample
    header += b"data"
    header += len(pcm).to_bytes(4, "little")
    header += pcm
    return bytes(header)


def _incremental(emitted: str, full: str) -> str:
    """Return the new suffix of ``full`` beyond what was already emitted.

    A growing-buffer transcriber usually returns a stable prefix plus new text,
    so the delta is a plain substring. When the transcript is *corrected*
    instead of extended the prefix no longer matches and nothing is streamed --
    the final chunk still carries the authoritative text.
    """
    if full.startswith(emitted):
        return full[len(emitted) :].strip()
    return ""


def _error_reason(status: int) -> str:
    if status in (401, 403):
        return f"Groq rejected the API key (HTTP {status})"
    if status == 429:
        return "Groq is rate limiting requests (HTTP 429)"
    return f"Groq transcription request failed (HTTP {status})"
