"""Piper TTS adapter (T151, spec §59.18).

The first local text-to-speech engine. §59.18 fixes a priority order, and it is
a requirement: **low latency, low RAM, CPU compatibility, adequate naturalness,
offline operation, chunked playback**. Piper (a small ONNX voice on CPU) fits
that order; nothing here assumes a GPU.

**Nothing is spawned until first use.** The adapter probes for the executable and
builds its argv lazily, spawns Piper only on the first :meth:`synthesize`, and
reports an honest :class:`~app.core.errors.DependencyUnavailableError` when the
binary or the voice model is missing (§59.18, §64.11).

**Sentence-level playback comes from the interface.** :meth:`synthesize` handles
one sentence and the inherited
:meth:`~app.voice.tts.base.TTSEngine.synthesize_stream` drives it sentence by
sentence, so playback begins before a whole response is synthesised (§59.19).

**The command is a literal argv, never a shell** -- the same rule the system tools
follow. The text goes in on stdin and the audio (raw 16-bit PCM at
``TTS_SAMPLE_RATE``) comes out on stdout, so no path or text is ever interpreted
by a shell. The subprocess runner is injectable (:attr:`runner`) so the adapter is
unit-testable with no Piper install.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Sequence

from app.config import VoiceSettings
from app.core.errors import DependencyUnavailableError, OperationTimeoutError, UltronError
from app.observability import get_logger
from app.voice.tts.availability import piper_available, piper_unavailable_reason
from app.voice.tts.base import TTSEngine

__all__ = ["PiperTTS"]

_LOGGER = get_logger(__name__)

#: Runs ``argv`` with ``text`` on stdin under a timeout and returns raw audio.
BinaryRunner = Callable[[Sequence[str], str, float], Awaitable[bytes]]


class PiperTTS(TTSEngine):
    """Local TTS over the Piper executable (see the module docstring)."""

    name = "piper"

    def __init__(self, settings: VoiceSettings, *, runner: BinaryRunner | None = None) -> None:
        self.settings = settings
        self.sample_rate = settings.tts_sample_rate
        self._runner = runner

    # -- availability ----------------------------------------------------- #
    @property
    def available(self) -> bool:
        if self._runner is not None:
            return True
        if not self.settings.tts_model_path:
            return False
        return piper_available(self.settings.tts_binary_path)

    @property
    def unavailable_reason(self) -> str | None:
        if self._runner is not None:
            return None
        if not self.settings.tts_model_path:
            return (
                "no Piper voice model is configured; set TTS_MODEL_PATH to a "
                "Piper .onnx voice"
            )
        return piper_unavailable_reason(self.settings.tts_binary_path)

    # -- synthesis -------------------------------------------------------- #
    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        """Synthesise one sentence and return raw PCM audio."""
        self._require_available()
        if not text.strip():
            return b""
        argv = self._argv(voice)
        runner = self._runner or self._run_binary
        audio = await runner(argv, text, float(self.settings.tts_timeout))
        if not audio:
            raise UltronError("Piper produced no audio")
        return audio

    def _argv(self, voice: str | None) -> list[str]:
        model = voice or self.settings.tts_model_path
        # `--output_raw` writes headerless 16-bit PCM to stdout, which is what a
        # streaming player wants; the rate is fixed by TTS_SAMPLE_RATE.
        return [self.settings.tts_binary_path, "--model", model, "--output_raw"]

    async def _run_binary(self, argv: Sequence[str], text: str, limit: float) -> bytes:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(text.encode("utf-8")), limit)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise OperationTimeoutError(f"Piper did not finish within {limit}s") from None
        if proc.returncode != 0:
            detail = err.decode("utf-8", errors="replace").strip() or f"exit code {proc.returncode}"
            raise DependencyUnavailableError("tts", reason=f"Piper failed: {detail}")
        _LOGGER.debug("piper synthesised %d bytes", len(out))
        return out

    def _require_available(self) -> None:
        if not self.available:
            raise DependencyUnavailableError(
                "tts", reason=self.unavailable_reason or "text-to-speech is unavailable"
            )
