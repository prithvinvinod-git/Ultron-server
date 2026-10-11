"""Bounded subprocess helper for the system tools (T095).

The GPU and Docker inspections shell out to a fixed, literal program — never a
shell — and every call is time-bounded (§59.6).
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from collections.abc import Sequence

from app.core.errors import OperationTimeoutError

__all__ = ["find_program", "run_command"]


def find_program(name: str) -> str | None:
    """Return the absolute path of ``name`` on PATH, or ``None``."""
    return shutil.which(name)


async def run_command(argv: Sequence[str], *, timeout_seconds: float) -> tuple[int, str, str]:
    """Run a literal argv with no shell; return (exit_code, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_seconds)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise OperationTimeoutError(f"{argv[0]} did not finish within {timeout_seconds}s") from None
    return (
        proc.returncode if proc.returncode is not None else -1,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
    )
