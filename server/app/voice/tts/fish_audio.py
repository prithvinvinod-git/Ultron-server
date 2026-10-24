"""Fish Audio cloud TTS adapter (cloned voice).

Fish Audio provides a TTS API that uses cloned voices. The endpoint is
``POST {base}/v1/tts`` with the cloned voice's ``reference_id`` in the request
body -- the id Fish Audio hands out when a voice is cloned, which is what
``FISH_AUDIO_VOICE_ID`` holds.

**Availability is a key check** -- the engine is available exactly when
``FISH_AUDIO_API_KEY`` is set and a ``FISH_AUDIO_VOICE_ID`` is configured.

**Output format is explicit.** The default ``mp3`` returns MP3 audio, so
:attr:`content_type`` is ``audio/mpeg``. The media type follows the configured
format, so a WAV request is played back as WAV rather than mislabelled.

**The model id is optional.** Fish Audio picks a default model, so
``FISH_AUDIO_MODEL`` is only sent when it is set: sending a name the account
does not have would fail a request that would otherwise work.

**Delivery cues are inline markers.** Fish Audio reads emotions and voice styles
from the text itself: the S2 models use square brackets (``[calm]``, ``[excited]``,
``[angry]``), the legacy S1 models use parentheses. The cues are rendered at the
start of the text, which is where Fish Audio expects a sentence-level emotion
marker to sit, and they can be combined (``[sad][whispering]``).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence

import httpx

from app.config import VoiceSettings
from app.core.errors import DependencyUnavailableError, OperationTimeoutError
from app.observability import get_logger
from app.voice.tts.base import TTSEngine

__all__ = ["FishAudioTTS"]

_LOGGER = get_logger(__name__)

#: Synthesises ``text`` in ``voice`` and returns the audio bytes.
Synthesizer = Callable[[str, str | None], Awaitable[bytes]]

#: A marker already written into the text, e.g. ``[calm]`` or ``(calm)``.
_MARKER = re.compile(r"^\s*[\[(][^\])]*[\])]")


class FishAudioTTS(TTSEngine):
    """Cloud TTS over Fish Audio (cloned voice)."""

    name = "fish-audio"

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
        self.sample_rate = 24000  # Fish Audio typically outputs 24kHz
        self.content_type = _content_type(settings.fish_audio_output_format)

    # -- availability ----------------------------------------------------- #
    @property
    def available(self) -> bool:
        if self._synthesizer is not None:
            return True
        api_key = self.settings.fish_audio_api_key.get_secret_value().strip()
        voice_id = self.settings.fish_audio_voice_id.strip()
        return bool(api_key and voice_id)

    @property
    def unavailable_reason(self) -> str | None:
        if self.available:
            return None
        api_key = self.settings.fish_audio_api_key.get_secret_value().strip()
        voice_id = self.settings.fish_audio_voice_id.strip()
        if not api_key:
            return "FISH_AUDIO_API_KEY is not configured"
        if not voice_id:
            return "FISH_AUDIO_VOICE_ID is not configured"
        return "Fish Audio TTS is not configured"

    # -- synthesis -------------------------------------------------------- #
    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> bytes:
        """Synthesise one sentence and return MP3 audio."""
        self._require_available()
        if not text.strip():
            return b""
        if self._synthesizer is not None:
            return await self._synthesizer(text, voice)
        return await self._synthesize_http(text, voice, self._resolve_tags(tags))

    def _resolve_tags(self, tags: Sequence[str] | None) -> list[str]:
        """The cues to apply: the caller's, or the deployment's default.

        An empty list means "no cues": a caller that sends an explicit empty
        list turns the default off for that request.
        """
        if tags is None:
            return _split_tags(self.settings.fish_audio_tags)
        return _split_tags(",".join(tags))

    async def _synthesize_http(
        self, text: str, voice: str | None, tags: Sequence[str]
    ) -> bytes:
        voice_id = voice or self.settings.fish_audio_voice_id
        url = f"{self.settings.fish_audio_base_url.rstrip('/')}/v1/tts"
        headers = {
            "Authorization": f"Bearer {self.settings.fish_audio_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        payload = {
            "text": _apply_tags(text, tags, self.settings.fish_audio_tag_style),
            "reference_id": voice_id,
            "format": self.settings.fish_audio_output_format,
        }
        model = self.settings.fish_audio_model.strip()
        if model:
            payload["model_id"] = model
        timeout = httpx.Timeout(self.settings.tts_timeout)
        try:
            response = await self._http().post(
                url, headers=headers, json=payload, timeout=timeout
            )
        except httpx.TimeoutException as exc:
            raise OperationTimeoutError(
                f"cloud text-to-speech timed out after {self.settings.tts_timeout}s"
            ) from exc
        except httpx.TransportError as exc:
            raise DependencyUnavailableError(
                "tts", reason=f"Fish Audio is unreachable: {exc}", cause=exc
            ) from exc
        if response.status_code >= 400:
            reason = _error_reason(response.status_code, response.text)
            _LOGGER.warning(
                "fish_audio synthesis failed",
                extra={
                    "event": "tts.fish_audio_failed",
                    "status": response.status_code,
                    "engine": self.name,
                },
            )
            raise DependencyUnavailableError("tts", reason=reason)
        audio = response.content
        if not audio:
            raise DependencyUnavailableError(
                "tts", reason="Fish Audio returned no audio"
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


def _error_reason(status: int, body: str) -> str:
    if status in (401, 403):
        return f"Fish Audio rejected the API key (HTTP {status})"
    if status == 402:
        return (
            "Fish Audio reports insufficient API credit (HTTP 402); add credit at "
            "https://fish.audio/app/developers to use this provider"
        )
    if status == 429:
        return "Fish Audio is rate limiting requests (HTTP 429)"
    if status == 404:
        return f"Fish Audio voice not found (HTTP {status})"
    return f"Fish Audio synthesis request failed (HTTP {status}): {body[:200]}"


def _split_tags(raw: str) -> list[str]:
    """Normalise a list of cues: ``"[calm], excited"`` -> ``["calm", "excited"]``.

    Brackets and parentheses are stripped, so a cue can be written the way it
    is read (``calm``), the way Fish Audio spells it (``[calm]``) or the way the
    legacy model spells it (``(calm)``).
    """
    cues: list[str] = []
    for part in raw.split(","):
        cue = part.strip().strip("[]()").strip().lower()
        if cue:
            cues.append(cue)
    return cues


def _apply_tags(text: str, tags: Sequence[str], style: str) -> str:
    """Prefix ``text`` with the delivery cues Fish Audio reads as markers.

    The markers are placed at the start of the text, which is where a
    sentence-level emotion belongs, and are joined without a space so several
    cues read as one marker block (``[sad][whispering]``). Text that already
    starts with a marker is left alone: the caller wrote the cue themselves.
    """
    if not tags or _MARKER.match(text):
        return text
    open_, close = ("(", ")") if style == "paren" else ("[", "]")
    markers = "".join(f"{open_}{cue}{close}" for cue in tags)
    return f"{markers} {text.strip()}"


def _content_type(output_format: str) -> str:
    """The media type of the audio Fish Audio returns for ``output_format``."""
    fmt = (output_format or "mp3").strip().lower()
    if fmt == "wav":
        return "audio/wav"
    if fmt in {"pcm", "pcm_s16le", "l16"}:
        return "audio/L16"
    return "audio/mpeg"
