"""Playwright availability probe (T111).

Playwright is an optional dependency (``pip install .[browser]``). ULTRON must
never assume it is present: the honest answer is a probe that says why the
feature is unavailable, so a caller can degrade or report rather than crash at
import (§59.3's availability discipline, applied to the browser subsystem).
"""

from __future__ import annotations

import functools
import importlib.util

__all__ = ["playwright_available", "playwright_unavailable_reason"]


@functools.lru_cache(maxsize=1)
def playwright_available() -> bool:
    """Whether the ``playwright`` package can be imported.

    Cached: the answer cannot change without a process restart, and probing it
    on every tool call would be pointless I/O.
    """
    return importlib.util.find_spec("playwright") is not None


@functools.lru_cache(maxsize=1)
def playwright_unavailable_reason() -> str | None:
    """A human-readable reason Playwright is unavailable, or ``None`` if it is.

    Browsers are a separate download from the package (``playwright install``),
    so this only proves the *package* is importable; a launch may still fail if
    the browser binaries are missing, and that failure is reported at launch.
    """
    if playwright_available():
        return None
    return (
        "the 'playwright' package is not installed; install the browser extra "
        "with 'pip install ultron-server[browser]' and run 'playwright install chromium'"
    )
