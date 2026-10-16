"""Piper executable availability probe (T151, spec §59.18).

Piper is an external **binary** (a local neural TTS engine), not a Python
package, so unlike the STT probe this one resolves an executable. ULTRON must
never assume it is installed: the probe returns ``False`` with a reason so the
voice subsystem can degrade honestly rather than crash (§59.18, §64.11).

The probe only proves the binary is resolvable on this machine; the voice model
(``*.onnx``) is a separate download and a missing model is reported when the
engine is first used.
"""

from __future__ import annotations

import functools
import shutil
from pathlib import Path

__all__ = ["piper_available", "piper_unavailable_reason"]


def _resolve(binary_path: str) -> str | None:
    if not binary_path:
        return None
    # An explicit path is used as-is; a bare name is looked up on PATH.
    if Path(binary_path).is_file():
        return binary_path
    return shutil.which(binary_path)


@functools.lru_cache(maxsize=8)
def piper_available(binary_path: str) -> bool:
    """Whether the Piper executable named by ``binary_path`` is resolvable.

    Cached per path: the answer cannot change without a process restart, and the
    lookup touches the filesystem.
    """
    return _resolve(binary_path) is not None


@functools.lru_cache(maxsize=8)
def piper_unavailable_reason(binary_path: str) -> str | None:
    """A human-readable reason Piper is unavailable, else ``None``."""
    if piper_available(binary_path):
        return None
    return (
        f"the Piper executable '{binary_path or '<unset>'}' was not found; "
        "install Piper and set TTS_BINARY_PATH to its location to enable "
        "local text-to-speech"
    )
