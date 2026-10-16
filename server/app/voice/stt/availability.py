"""faster-whisper availability probe (T150, spec §59.17).

``faster-whisper`` is an optional, heavyweight local dependency (CTranslate2 +
a downloaded model). ULTRON must never assume it is installed: the honest answer
is a probe that says *why* STT is unavailable, so the voice subsystem can
degrade or report rather than crash at import (§59.17, §64.11's "a degraded path
must be visible"). This mirrors the browser subsystem's Playwright probe (T111).

The probe only proves the *package* is importable. The model weights are
downloaded on first use, and a download or load failure is reported when the
engine is first used -- not at process start, where it would take the server
down for a feature that may never be called.
"""

from __future__ import annotations

import functools
import importlib.util

__all__ = ["faster_whisper_available", "faster_whisper_unavailable_reason"]


@functools.lru_cache(maxsize=1)
def faster_whisper_available() -> bool:
    """Whether the ``faster_whisper`` package can be imported.

    Cached: the answer cannot change without a process restart, and probing it
    on every utterance would be pointless work.
    """
    return importlib.util.find_spec("faster_whisper") is not None


@functools.lru_cache(maxsize=1)
def faster_whisper_unavailable_reason() -> str | None:
    """A human-readable reason faster-whisper is unavailable, else ``None``.

    Even when the package is present the model may still be absent (first-use
    download), so a caller that needs the engine should still handle a failure
    from :meth:`~app.voice.stt.STTEngine.transcribe`.
    """
    if faster_whisper_available():
        return None
    return (
        "the 'faster-whisper' package is not installed; install it with "
        "'pip install faster-whisper' to enable local speech-to-text"
    )
