"""The OpenCode engine (T082): the first :class:`CodingEngine`, spec sections 8 and 9.

§8 says the engine sits behind the coding-agent interface; §9 pins how the
OpenCode CLI must be driven. This engine is the whole of §9's sheet, mapped
onto :class:`CodingEngine.run`:

- **Configurable executable, model, provider, arguments** — nothing is
  hard-coded; §8: "configurable executable path". The argv is built literally
  and passed to ``asyncio.create_subprocess_exec`` (never a shell, §59.3), so
  a configured path with spaces is a file, not an injection.
- **cwd is the task's workspace, always** — §9: "Every coding task must be
  given an explicit workspace" + "working directory is the workspace". The task
  carries the directory; there is no fallback, and a run whose workspace does
  not exist refuses before spawning anything.
- **Input is the prompt, stdout/stderr are streamed** — §9: "the added code is
  printed, line by line". Each line becomes an ``OUTPUT`` :class:`CodingEvent`
  as soon as it is read, and is also captured — bounded by
  ``max_output_lines`` with ``truncated`` recorded.
- **Exit code is reported, not judged** — §9: "if the exit code is non-zero,
  this is a work failure". So a non-zero exit is a :class:`CodingRunResult`
  with that exit code; the coding agent's verification (T084) decides
  SUCCESS/PARTIAL/FAILED. The engine only *raises* when the run could not
  happen or had to be stopped.
- **Timeout** — §9: "if a coding task's runtime exceeds the configured
  timeout, the process is terminated and the attempt fails". ``wait_for``
  enforces the deadline; on expiry the child is settled (terminate → grace →
  kill), reaped, and ``OperationTimeoutError`` is raised.
- **Cancellation, cooperatively and reaped** — §9: "if the user cancels, the
  process is terminated and the workspace is unlocked". ``stop`` is consulted
  on every output line and raises ``OperationCancelledError``; an
  ``asyncio.CancelledError`` cleans the child up the same way and re-raises, so
  the caller's cancellation still propagates. Either path terminates and
  *awaits the reap* (the workspace manager's ``_settle`` pattern, T080): no
  orphan process, no half-held workspace.
- **The child never inherits server directories** — §9: "the coding agent
  must never inherit the server's directories". Concretely, ``PYTHONPATH`` is
  scrubbed from the child's environment before any spawn; nothing in the
  server's virtualenv or module path can leak into the coding process.
"""

from __future__ import annotations

import asyncio
import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import ClassVar, Final, cast

from app.config.settings import OpenCodeSettings
from app.core.errors import (
    DependencyUnavailableError,
    InvalidInputError,
    OperationCancelledError,
    OperationTimeoutError,
    WorkspaceError,
)

from .engine import (
    CodingEngine,
    CodingEvent,
    CodingEventCallback,
    CodingEventKind,
    CodingRunResult,
    CodingTask,
    dispatch_event,
)

__all__ = ["OpenCodeEngine"]

#: How long a terminated child gets to die before it is killed outright (§9,
#: mirror of the workspace manager's settle, T080).
_TERMINATE_GRACE_SECONDS: Final = 5.0


class OpenCodeEngine(CodingEngine):
    """Drives the OpenCode CLI in a task's workspace (§8, §9)."""

    name: ClassVar[str] = "opencode"

    def __init__(
        self,
        *,
        executable: str = "opencode",
        model: str = "",
        provider: str = "",
        timeout_seconds: float = 1800.0,
        max_output_lines: int = 5000,
        extra_args: tuple[str, ...] = (),
        env: Mapping[str, str] | None = None,
        subcommand: str = "run",
    ) -> None:
        """Declare how the subprocess is built; nothing is run here.

        ``subcommand`` defaults to OpenCode's non-interactive ``run`` and may be
        ``""`` (tests and embedding-only callers pass one of their own).
        ``env`` is an *overlay* on the parent environment, never a replacement:
        the child keeps ``PATH``, ``SystemRoot`` and friends (dropping those
        would break the interpreter or, on Windows, the process launch itself),
        and the caller can add or override variables. ``PYTHONPATH`` is scrubbed
        either way, because the §9 isolation rule ("never inherit the server's
        directories") is not optional.
        """
        if not executable or not executable.strip():
            raise InvalidInputError(
                "a coding engine needs an executable",
                details={"field": "executable"},
            )
        if timeout_seconds <= 0:
            raise InvalidInputError(
                "a coding engine timeout must be positive",
                details={"timeout_seconds": timeout_seconds},
            )
        if max_output_lines < 1:
            raise InvalidInputError(
                "a coding engine must capture at least one line",
                details={"max_output_lines": max_output_lines},
            )
        self._executable = executable
        self._subcommand = subcommand
        self._model = model.strip()
        self._provider = provider.strip()
        self._timeout_seconds = float(timeout_seconds)
        self._max_output_lines = max_output_lines
        self._extra_args = tuple(extra_args)
        scrubbed = dict(os.environ)
        if env is not None:
            scrubbed.update(env)
        scrubbed.pop("PYTHONPATH", None)
        self._env = scrubbed

    @classmethod
    def from_settings(cls, settings: OpenCodeSettings) -> OpenCodeEngine:
        """Build the engine the way the configured server will use it."""
        extra = shlex.split(settings.extra_args) if settings.extra_args else []
        return cls(
            executable=settings.path,
            model=settings.model,
            provider=settings.provider,
            timeout_seconds=float(settings.timeout),
            max_output_lines=settings.max_output_lines,
            extra_args=tuple(extra),
            subcommand="run",
        )

    # -- §9's sheet, as one bounded run ------------------------------------- #
    async def run(
        self,
        task: CodingTask,
        *,
        on_event: CodingEventCallback | None = None,
        stop: asyncio.Event | None = None,
    ) -> CodingRunResult:
        """Run OpenCode on ``task.prompt`` inside ``task.workspace``."""
        await self._ensure_workspace(task)
        started = datetime.now(UTC)
        proc: asyncio.subprocess.Process | None = None
        try:
            await dispatch_event(
                on_event,
                CodingEvent(
                    task_id=task.task_id,
                    kind=CodingEventKind.STARTED,
                    line=f"{self._executable} started in {task.workspace}",
                    engine=self.name,
                ),
            )
            proc = await asyncio.create_subprocess_exec(
                *self._argv(task.prompt),
                cwd=task.workspace,
                env=self._env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            exit_code, capture = await asyncio.wait_for(
                self._run_bounded(proc, task, on_event, stop),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            await self._settle(proc)
            await dispatch_event(
                on_event,
                CodingEvent(
                    task_id=task.task_id,
                    kind=CodingEventKind.FAILED,
                    line=f"timed out after {self._timeout_seconds:g}s",
                    engine=self.name,
                ),
            )
            raise OperationTimeoutError(
                "coding task",
                timeout=self._timeout_seconds,
                details={"engine": self.name, "task_id": task.task_id},
                cause=exc,
            ) from exc
        except asyncio.CancelledError:
            await self._settle(proc)
            raise
        except OperationCancelledError:
            raise
        except (FileNotFoundError, PermissionError) as exc:
            raise DependencyUnavailableError(
                self._executable,
                reason="could not start the coding engine executable",
                cause=exc,
            ) from exc

        return CodingRunResult(
            task_id=task.task_id,
            engine=self.name,
            exit_code=exit_code,
            output=tuple(capture.lines),
            truncated=capture.truncated,
            ran_seconds=(datetime.now(UTC) - started).total_seconds(),
        )

    # -- subprocess plumbing ------------------------------------------------- #
    def _argv(self, prompt: str) -> list[str]:
        """Build the literal argv: engine, subcommand, options, then the prompt."""
        argv = [self._executable]
        if self._subcommand:
            argv.append(self._subcommand)
        if self._model:
            argv.extend(("--model", self._model))
        if self._provider:
            argv.extend(("--provider", self._provider))
        argv.extend(self._extra_args)
        argv.append(prompt)
        return argv

    async def _ensure_workspace(self, task: CodingTask) -> None:
        workspace = task.workspace
        if not workspace.exists() or not workspace.is_dir():
            raise WorkspaceError(
                f"the coding workspace does not exist or is not a directory: {workspace}",
                details={"workspace": str(workspace), "task_id": task.task_id},
            )

    async def _run_bounded(
        self,
        proc: asyncio.subprocess.Process,
        task: CodingTask,
        on_event: CodingEventCallback | None,
        stop: asyncio.Event | None,
    ) -> tuple[int, _Capture]:
        """Read stdout until EOF, streaming and capturing, then the exit code.

        ``stop`` is consulted on every line (and after EOF), so a cancellation
        set mid-run terminates the child promptly instead of waiting for the
        process to talk again. Returns the exit code and the captured output.
        """
        capture = _Capture()
        # stdout is a pipe: we passed ``stdout=PIPE`` and merged stderr into it.
        stdout = cast("asyncio.StreamReader", proc.stdout)
        try:
            async for raw in stdout:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if len(capture.lines) < self._max_output_lines:
                    capture.lines.append(line)
                else:
                    capture.truncated = True
                await dispatch_event(
                    on_event,
                    CodingEvent(
                        task_id=task.task_id,
                        kind=CodingEventKind.OUTPUT,
                        line=line,
                        engine=self.name,
                    ),
                )
                if stop is not None and stop.is_set():
                    await self._settle(proc)
                    await dispatch_event(
                        on_event,
                        CodingEvent(
                            task_id=task.task_id,
                            kind=CodingEventKind.CANCELLED,
                            line="cancelled by request",
                            engine=self.name,
                        ),
                    )
                    raise OperationCancelledError(
                        "coding task",
                        reason="stop event was set mid-run",
                    )
        except asyncio.CancelledError:
            await self._settle(proc)
            raise

        exit_code = await proc.wait()
        terminal_kind = CodingEventKind.COMPLETED if exit_code == 0 else CodingEventKind.FAILED
        await dispatch_event(
            on_event,
            CodingEvent(
                task_id=task.task_id,
                kind=terminal_kind,
                line=f"exited with code {exit_code}",
                engine=self.name,
                exit_code=exit_code,
            ),
        )
        return exit_code, capture

    async def _settle(self, proc: asyncio.subprocess.Process | None) -> None:
        """Terminate a child if needed and reap it (mirror of T080's ``_settle``).

        Idempotent: called from the timeout, cancellation, and stop paths, and
        again safely if a child already exited on its own.
        """
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
        except ProcessLookupError:  # already gone
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=_TERMINATE_GRACE_SECONDS)
        except TimeoutError:
            proc.kill()
            await proc.wait()


@dataclass(slots=True)
class _Capture:
    """Mutable output buffer passed out of the bounded run."""

    lines: list[str] = field(default_factory=list)
    truncated: bool = False
