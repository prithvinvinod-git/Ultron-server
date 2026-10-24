"""ElevenLabs cloud TTS adapter (T151, spec §59.18).

§59.18 makes a cloud text-to-speech provider the *first* choice and local Piper
the optional, never-assumed fallback. This is that cloud adapter: it calls
ElevenLabs' streaming synthesis endpoint
(``POST {base}/text-to-speech/{voice_id}``) and returns the audio bytes.

**Availability is a key check, not a probe** -- ``httpx`` is already a core
dependency, so the engine is available exactly when ``ELEVENLABS_API_KEY`` is
set. A blank key reports an honest
:class:`~app.core.errors.DependencyUnavailableError` at the seam (§59.18,
§64.11).

**Sentence-level playback comes from the interface.** :meth:`synthesize` handles
one sentence and the inherited
:meth:`~app.voice.tts.base.TTSEngine.synthesize_stream` drives it sentence by
sentence, so playback starts before a whole response exists (§59.19).

**Output format is explicit.** The default ``pcm_22050`` returns headerless
16-bit PCM, matching Piper, so one client-side decoder handles both engines; a
``mp3_*`` format switches :attr:`content_type` to ``audio/mpeg`` so the client
knows to use a container decoder instead.

**The HTTP call is injectable** (:attr:`synthesizer`), so the adapter is
unit-testable with no network and no key.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

import httpx

from app.config import VoiceSettings
from app.core.errors import DependencyUnavailableError, OperationTimeoutError
from app.observability import get_logger
from app.voice.tts.base import TTSEngine

__all__ = ["ElevenLabsTTS"]

_LOGGER = get_logger(__name__)

#: Synthesises ``text`` in ``voice`` and returns the audio bytes.
Synthesizer = Callable[[str, str | None], Awaitable[bytes]]


class ElevenLabsTTS(TTSEngine):
    """Cloud TTS over ElevenLabs (see the module docstring)."""

    name = "elevenlabs"

    def __init__(
        self,
        settings: VoiceSettings,
        *,
        client: httpx.AsyncClient | None = None,
        synthesizer: Synthesizer | None = None,
    ) -> None:
        self.settings = settings
        self._client = client
        self._owns_client = client is None
        self._synthesizer = synthesizer
        self.sample_rate = _sample_rate(settings)
        self.content_type = _content_type(settings.elevenlabs_output_format)

    # -- availability ----------------------------------------------------- #
    @property
    def available(self) -> bool:
        if self._synthesizer is not None:
            return True
        return bool(self.settings.elevenlabs_api_key.get_secret_value().strip())

    @property
    def unavailable_reason(self) -> str | None:
        if self.available:
            return None
        return (
            "ELEVENLABS_API_KEY is not configured; set it to enable cloud "
            "text-to-speech, or use TTS_ENGINE=edge-tts for local"
        )

    # -- synthesis -------------------------------------------------------- #
    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> bytes:
        """Synthesise one sentence and return audio (see :attr:`content_type`).

        ``tags`` are ignored: ElevenLabs expresses character through the voice
        and its own settings, not through inline markers, so a cue it cannot
        render is dropped rather than turned into a failure.
        """
        self._require_available()
        if not text.strip():
            return b""
        if self._synthesizer is not None:
            return await self._synthesizer(text, voice)
        return await self._synthesize_http(text, voice)

    async def _synthesize_http(self, text: str, voice: str | None) -> bytes:
        voice_id = voice or self.settings.elevenlabs_voice_id
        url = f"{self.settings.elevenlabs_base_url.rstrip('/')}/text-to-speech/{voice_id}"
        headers = {
            "xi-api-key": self.settings.elevenlabs_api_key.get_secret_value(),
            "content-type": "application/json",
            "accept": "audio/*",
        }
        params = {"output_format": self.settings.elevenlabs_output_format}
        payload = {"text": text, "model_id": self.settings.elevenlabs_model}
        timeout = httpx.Timeout(self.settings.tts_timeout)
        try:
            response = await self._http().post(
                url, headers=headers, params=params, json=payload, timeout=timeout
            )
        except httpx.TimeoutException as exc:
            raise OperationTimeoutError(
                f"cloud text-to-speech timed out after {self.settings.tts_timeout}s"
            ) from exc
        except httpx.TransportError as exc:
            raise DependencyUnavailableError(
                "tts", reason=f"ElevenLabs is unreachable: {exc}", cause=exc
            ) from exc
        if response.status_code >= 400:
            reason = _error_reason(response.status_code)
            _LOGGER.warning(
                "elevenlabs synthesis failed",
                extra={
                    "event": "tts.elevenlabs_failed",
                    "status": response.status_code,
                    "engine": self.name,
                },
            )
            raise DependencyUnavailableError("tts", reason=reason)
        audio = response.content
        if not audio:
            raise DependencyUnavailableError(
                "tts", reason="ElevenLabs returned no audio"
            )
        return audio

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
            self._owns_client = True
        return self._client

    def _require_available(self) -> None:
        if not self.available:
            raise DependencyUnavailableError(
                "tts", reason=self.unavailable_reason or "text-to-speech is unavailable"
            )

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None


def _content_type(output_format: str) -> str:
    return "audio/L16" if output_format.startswith("pcm") else "audio/mpeg"


def _sample_rate(settings: VoiceSettings) -> int:
    """Derive the playback rate from the ElevenLabs output format.

    ``pcm_22050`` names its own rate; a ``mp3_*`` (or unknown) format falls back
    to the configured ``TTS_SAMPLE_RATE``, which the decoder reports anyway.
    """
    fmt = settings.elevenlabs_output_format
    if fmt.startswith("pcm_"):
        try:
            return int(fmt.removeprefix("pcm_"))
        except ValueError:
            return settings.tts_sample_rate
    return settings.tts_sample_rate


def _error_reason(status: int) -> str:
    if status in (401, 403):
        return f"ElevenLabs rejected the API key (HTTP {status})"
    if status == 429:
        return "ElevenLabs is rate limiting requests (HTTP 429)"
    return f"ElevenLabs synthesis request failed (HTTP {status})"
