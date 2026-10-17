"""Local text-to-speech availability probes (T151, spec §59.18).

The local adapters are not Python packages in the usual sense: Piper is an
external **binary** resolved on the filesystem, and ``edge-tts`` is a Python
package that shells out to Microsoft's Edge read-aloud service. ULTRON must never
assume either is installed: each probe returns ``False`` with a reason so the
voice subsystem can degrade honestly rather than crash (§59.18, §64.11).

The Piper probe only proves the binary is resolvable on this machine; the voice
model (``*.onnx``) is a separate download and a missing model is reported when
the engine is first used. The ``edge-tts`` probe only proves the package is
importable.
"""

from __future__ import annotations

import functools
import importlib.util
import shutil
from pathlib import Path

__all__ = [
    "edge_tts_available",
    "edge_tts_unavailable_reason",
    "piper_available",
    "piper_unavailable_reason",
]


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


@functools.lru_cache(maxsize=1)
def edge_tts_available() -> bool:
    """Whether the ``edge_tts`` package can be imported.

    Cached: the answer cannot change without a process restart.
    """
    return importlib.util.find_spec("edge_tts") is not None


@functools.lru_cache(maxsize=1)
def edge_tts_unavailable_reason() -> str | None:
    """A human-readable reason edge-tts is unavailable, else ``None``."""
    if edge_tts_available():
        return None
    return (
        "the 'edge-tts' package is not installed; install it with "
        "'pip install edge-tts' to enable local text-to-speech"
    )
