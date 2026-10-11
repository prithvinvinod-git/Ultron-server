"""`terminal.run` (T091): allow-listed commands, no shell.

§59.3 and §59.6 are unambiguous that the tool runtime cannot reach a general
shell: "``free`` is a tool, not ``sh -c free``". This tool is that rule made
concrete. A caller names a program and an argument vector; the program must be
on ``TerminalSettings.allowed_commands`` (an empty allow-list denies every
command), and the vector goes straight to ``asyncio.create_subprocess_exec``
with **no shell** to re-parse it. There is therefore no metacharacter to
smuggle: ``;``, ``|`` and ``>`` are ordinary bytes in an argument.

The handler checks the allow-list, but it does *not* decide permission —
``terminal.run`` declares ``EXECUTE_PROGRAMS`` and the pipeline enforces it
(§16). A tool that also policed permission would be a second, weaker gate; the
allow-list here is a capability check the tool is uniquely placed to make, not
a substitute for the permission layer.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar, cast

from app.config.settings import TerminalSettings
from app.core.errors import (
    CommandNotAllowedError,
    DependencyUnavailableError,
    InvalidInputError,
    OperationTimeoutError,
)
from app.database.models import PermissionLevel
from app.tools.arguments import as_str
from app.tools.base import Reversibility, Tool
from app.tools.filesystem.sandbox import FilesystemSandbox

__all__ = ["RunCommandTool"]

TERMINATE_GRACE_SECONDS = 5.0
_READ_CHUNK = 65_536
_DECLARED_TIMEOUT = 120.0


class RunCommandTool(Tool):
    """Run one allow-listed program with a literal argument vector."""

    name = "terminal.run"
    description = "Run an allow-listed command with a literal argument vector (no shell)."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "minLength": 1},
            "args": {"type": "array", "items": {"type": "string"}},
            "cwd": {"type": "string", "minLength": 1},
            "timeout_seconds": {"type": "number", "exclusiveMinimum": 0},
        },
        "required": ["command"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.EXECUTE_PROGRAMS
    timeout = _DECLARED_TIMEOUT
    reversibility = Reversibility.PARTIALLY_REVERSIBLE

    def __init__(self, settings: TerminalSettings, sandbox: FilesystemSandbox) -> None:
        """Bind the allow-list, output cap and working-directory boundary."""
        self._allowed = frozenset(settings.allowed_commands)
        self._denied = tuple(settings.denied_patterns)
        self._max_output_bytes = settings.max_output_bytes
        self._timeout_seconds = min(float(settings.timeout), _DECLARED_TIMEOUT)
        self._sandbox = sandbox

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        command = as_str(arguments.get("command"), "command")
        args = _string_list(arguments.get("args"), "args")
        self._authorise(command, args)
        cwd = self._resolve_cwd(arguments.get("cwd"))
        timeout_seconds = self._resolve_timeout(arguments.get("timeout_seconds"))

        started = time.monotonic()
        argv = (command, *args)
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise DependencyUnavailableError(
                command,
                reason=f"could not be started in {cwd}",
                cause=error,
            ) from error

        try:
            stdout, stderr, truncated = await asyncio.wait_for(
                _collect(process, self._max_output_bytes), timeout=timeout_seconds
            )
        except TimeoutError as error:
            await _settle(process)
            raise OperationTimeoutError(
                "terminal command",
                timeout=timeout_seconds,
                details={"argv": argv, "cwd": str(cwd)},
                cause=error,
            ) from error
        except asyncio.CancelledError:
            await _settle(process)
            raise

        return {
            "command": command,
            "argv": argv,
            "cwd": str(cwd),
            "exit_code": int(process.returncode or 0),
            "stdout": _decode(stdout),
            "stderr": _decode(stderr),
            "truncated": truncated,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    # -- guards -------------------------------------------------------------- #

    def _authorise(self, command: str, args: Sequence[str]) -> None:
        """Refuse anything not exactly allow-listed, or matching a deny pattern."""
        if "/" in command or "\\" in command:
            raise CommandNotAllowedError(
                self.name,
                "command must be a bare program name, not a path",
                details={"command": command},
            )
        if command not in self._allowed:
            raise CommandNotAllowedError(
                self.name,
                f"command '{command}' is not on the allow-list",
                details={"command": command, "allowed": sorted(self._allowed)},
            )
        line = " ".join((command, *args))
        for pattern in self._denied:
            if pattern and pattern in line:
                raise CommandNotAllowedError(
                    self.name,
                    "command matches a denied pattern",
                    details={"pattern": pattern},
                )

    def _resolve_cwd(self, cwd: Any) -> Path:
        """The command's working directory, always inside the workspace."""
        target = as_str(cwd, "cwd", default=str(self._sandbox.roots[0]))
        return self._sandbox.resolve_directory(target, what="cwd")

    def _resolve_timeout(self, value: Any) -> float:
        """Per-call timeout, never above the configured ceiling."""
        if value is None:
            return self._timeout_seconds
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidInputError("timeout_seconds must be a number")
        if value <= 0:
            raise InvalidInputError("timeout_seconds must be positive")
        return min(float(value), self._timeout_seconds)


def _string_list(value: Any, name: str) -> list[str]:
    """Validate an optional list-of-strings argument."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InvalidInputError(f"{name} must be a list of strings")
    return list(value)


async def _collect(process: asyncio.subprocess.Process, limit: int) -> tuple[bytes, bytes, bool]:
    """Drain both pipes concurrently, keeping at most ``limit`` bytes each."""
    stdout_reader = cast("asyncio.StreamReader", process.stdout)
    stderr_reader = cast("asyncio.StreamReader", process.stderr)
    (out, out_truncated), (err, err_truncated) = await asyncio.gather(
        _drain(stdout_reader, limit),
        _drain(stderr_reader, limit),
    )
    await process.wait()
    return out, err, out_truncated or err_truncated


async def _drain(reader: asyncio.StreamReader, limit: int) -> tuple[bytes, bool]:
    """Read a stream to EOF, retaining only the first ``limit`` bytes."""
    kept = bytearray()
    truncated = False
    while True:
        chunk = await reader.read(_READ_CHUNK)
        if not chunk:
            break
        if len(kept) >= limit:
            truncated = True
            continue
        remaining = limit - len(kept)
        kept.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated = True
    return bytes(kept), truncated


async def _settle(process: asyncio.subprocess.Process) -> None:
    """Terminate, then kill, and always reap the child (§59.6 cleanup)."""
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=TERMINATE_GRACE_SECONDS)
    except TimeoutError:
        process.kill()
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await process.wait()
    except asyncio.CancelledError:
        process.kill()
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await process.wait()
        raise


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")
