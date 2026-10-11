"""Python tool (T093, spec §59.6): restricted Python execution.

The python tool runs a short Python snippet inside the sandboxed workspace and
times it out. It runs a literal program (no shell), returns bounded output, and
uses the exit code in ``verify``.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

from app.config.settings import PythonToolSettings
from app.core.errors import InvalidInputError, OperationCancelledError, OperationTimeoutError
from app.database.models import PermissionLevel, VerificationOutcome
from app.tools.arguments import as_int, as_str
from app.tools.base import Reversibility, Tool
from app.tools.filesystem.sandbox import FilesystemSandbox

__all__ = ["PythonTool", "build_python_tool"]


async def _run_python(
    cwd: Path,
    code: str,
    timeout_seconds: float,
    args: Sequence[str],
) -> Mapping[str, object]:
    argv: list[str] = [sys.executable, "-c", code, *args]
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout_seconds)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 5.0)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
        raise OperationTimeoutError(
            f"Python execution timed out after {timeout_seconds}s"
        ) from None
    except asyncio.CancelledError:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        with contextlib.suppress(Exception):
            await proc.wait()
        raise OperationCancelledError("Python execution cancelled") from None
    exit_code = proc.returncode if proc.returncode is not None else -1
    return {
        "exit_code": exit_code,
        "stdout": stdout_b.decode("utf-8", errors="replace"),
        "stderr": stderr_b.decode("utf-8", errors="replace"),
    }


class PythonTool(Tool):
    """Run a restricted Python program with a hard timeout."""

    name = "python.run"
    description = "Run a Python snippet inside the workspace with a bounded timeout."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "code": {"type": "string", "minLength": 1},
            "timeout_seconds": {"type": "number", "minimum": 0.01},
            "args": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["path", "code"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.EXECUTE_PROGRAMS
    timeout: ClassVar[float] = 60.0
    reversibility = Reversibility.PARTIALLY_REVERSIBLE

    def __init__(self, settings: PythonToolSettings, sandbox: FilesystemSandbox) -> None:
        self._settings = settings
        self._sandbox = sandbox
        self._python_timeout = float(getattr(settings, "timeout", 60))

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        code = as_str(arguments.get("code"), "code")
        cwd = self._sandbox.resolve_directory(str(arguments["path"]), what="path")
        raw_timeout = arguments.get("timeout_seconds")
        if isinstance(raw_timeout, (int, float)) and not isinstance(raw_timeout, bool):
            timeout = float(raw_timeout)
        elif raw_timeout is not None:
            timeout = float(as_int(raw_timeout, "timeout_seconds"))
        else:
            timeout = self._python_timeout
        if timeout < 0.01:
            raise InvalidInputError(
                "timeout_seconds must be >= 0.01", details={"timeout_seconds": str(timeout)}
            )
        if timeout > self._python_timeout:
            timeout = self._python_timeout
        args = arguments.get("args")
        if args is not None:
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                raise InvalidInputError(
                    "args must be a list of strings", details={"args": repr(args)[:100]}
                )
            argv_args: Sequence[str] = tuple(args)
        else:
            argv_args = ()
        return await _run_python(cwd, code, timeout, argv_args)

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        if isinstance(result, Mapping):
            if result.get("exit_code") == 0:
                return VerificationOutcome.SUCCESS
            return VerificationOutcome.FAILED
        return VerificationOutcome.FAILED


def build_python_tool(settings: PythonToolSettings, sandbox: FilesystemSandbox) -> PythonTool:
    return PythonTool(settings, sandbox)


def build_python_tools(
    settings: PythonToolSettings, sandbox: FilesystemSandbox
) -> tuple[Tool, ...]:
    """Construct the python tool family (one tool today)."""
    return (build_python_tool(settings, sandbox),)
