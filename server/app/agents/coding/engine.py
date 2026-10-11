"""The coding-engine interface (T081): §8's abstraction, and nothing else.

§8 says the actual coding engine *must be abstracted* and that no engine
should be hard-coded through the system:

```text
Coding Agent
      ↓
CodingEngine     <- this module
      ↓
OpenCodeEngine   -> OpenCode CLI   (T082)
ClaudeCodeEngine / AiderEngine / CustomEngine  (future)
```

The purpose is software engineering (inspect repository, understand project,
plan, edit files, run tests/builds, inspect git, create branches, inspect
diffs, report changes, verify results), so the interface is deliberately the
*minimum common to every subprocess coding engine*: a task that fixes the
workspace, a stream of events, and a bounded run that reports the process's
own exit code. Everything richer — the prompt text, the model/provider, the
executable path, the timeouts — is either carried in the task + options or
left to the concrete engine, so a future ``ClaudeCodeEngine`` swaps in without
the coding agent (T083) or its verification (T084) changing.

The rules this contract fixes:

- **An explicit workspace, always** (§9: "Every coding task must receive an
  explicit workspace"). :class:`CodingTask` carries ``workspace`` as a
  required directory; an engine refuses to run with no workspace or one that
  does not exist. The agent never inherits server directories by accident —
  there is no default cwd to fall back to.

- **The report is a stream, not one string.** ``on_event`` receives a
  :class:`CodingEvent` per line (``OUTPUT``) bracketed by ``STARTED`` and a
  terminal event, so a subscriber can publish progress without buffering the
  whole run. Output is *also* captured into ``CodingRunResult.output`` —
  bounded, with ``truncated`` set — because verification (T084) needs the
  text after the fact even when nobody was listening live.

- **Exit codes are facts, not exceptions.** A git or test failure is a work
  outcome the caller verifies. The engine returns :class:`CodingRunResult`
  whatever the process answered, and only *raises* for what the server
  mis-has: an unusable task, a missing executable, an operation timeout, or a
  cancellation. That split is what lets T084 decide SUCCESS/PARTIAL/FAILED
  from the exit code instead of from exception types.

- **Cancellation is cooperative and reaped.** ``run`` accepts a ``stop``
  event (and propagates ``asyncio.CancelledError``); a well-behaved engine
  terminates its child promptly and awaits the reap (the workspace manager's
  ``_settle`` pattern, T080), so a cancelled coding task leaves no orphan
  subprocess and no half-closed workspace.

Deliberately absent, mirroring the agent ABC (T042): persistence, bus event
publication (duty of CodingAgent T083), and permission logic. The engine
knows how to run a subprocess in a workspace; it does not decide who may.
"""

from __future__ import annotations

import asyncio
import inspect
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PureWindowsPath
from typing import Any, ClassVar

from app.core.errors import InvalidInputError

__all__ = [
    "CodingEngine",
    "CodingEvent",
    "CodingEventCallback",
    "CodingEventKind",
    "CodingRunResult",
    "CodingTask",
    "dispatch_event",
]


class CodingEventKind(StrEnum):
    """What a :class:`CodingEvent` is telling the listener.

    The terminal kinds mirror process facts, not verdicts: ``COMPLETED`` means
    "the process exited 0", ``FAILED`` means "it exited non-zero or was cut
    short", ``CANCELLED`` means "it was stopped on purpose". Deciding what the
    exit *means* is verification's job (T084), not the engine's.
    """

    STARTED = "started"
    OUTPUT = "output"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CodingTask:
    """One coding task: a prompt and the explicit workspace it acts in (§9).

    ``files`` lists the workspace-relative paths the task is expected to touch
    or produce; T084's verification checks exactly these against the workspace
    afterwards. They are *expectations*, validated as sane relative paths at
    construction — an absolute path or a ``..`` escape is refused before it can
    reach verification.
    """

    task_id: str
    prompt: str
    workspace: Path
    files: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_text("task_id", self.task_id))
        object.__setattr__(self, "prompt", _require_text("prompt", self.prompt))
        object.__setattr__(
            self,
            "files",
            tuple(_require_relative(relative) for relative in self.files),
        )


@dataclass(frozen=True, slots=True)
class CodingEvent:
    """A point in a coding run's progress, streamed through ``on_event``.

    ``line`` is the meaningful text: one output line for ``OUTPUT``, the reason
    (or "") for the terminal kinds. ``exit_code`` is only ever set on the
    terminal event, so a listener can react to it without joining the result.
    """

    task_id: str
    kind: CodingEventKind
    line: str = ""
    engine: str = ""
    exit_code: int | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CodingEventKind):
            raise InvalidInputError(
                "a coding event needs a kind",
                details={"line": str(self.line)[:200]},
            )
        object.__setattr__(
            self, "line", self.line if isinstance(self.line, str) else str(self.line)
        )


@dataclass(frozen=True, slots=True)
class CodingRunResult:
    """What one run produced: the exit code and the bounded output.

    ``output`` keeps the streamed lines in order for later inspection
    (verification, logs); ``truncated`` records an engine's
    ``max_output_lines`` ceiling was reached, so a caller knows the captured
    output is a prefix, not the whole story. ``ran_seconds`` is wall-clock
    time in the engine, as a float, for the report.
    """

    task_id: str
    engine: str
    exit_code: int
    output: tuple[str, ...]
    truncated: bool
    ran_seconds: float

    @property
    def output_text(self) -> str:
        """The captured output as one block with trailing newline."""
        return "\n".join(self.output) + ("\n" if self.output else "")


#: A callback that receives a :class:`CodingEvent`. May be sync or async; a
#: coroutine-returning callback is awaited before the engine continues, so a
#: subscriber that publishes to a bus can do so durably.
CodingEventCallback = Callable[[CodingEvent], Awaitable[None] | None]


async def dispatch_event(
    callback: CodingEventCallback | None,
    event: CodingEvent,
) -> None:
    """Send one event to a callback, awaiting a coroutine-returning one."""
    if callback is None:
        return
    result = callback(event)
    if inspect.isawaitable(result):
        await result


class CodingEngine(ABC):
    """A subprocess coding engine (§8): one bounded run in one workspace."""

    #: The engine's name — the registry key in reports and events. Declared on
    #: each concrete engine, mirroring ``Agent.agent_type`` (T042).
    name: ClassVar[str]

    @abstractmethod
    async def run(
        self,
        task: CodingTask,
        *,
        on_event: CodingEventCallback | None = None,
        stop: asyncio.Event | None = None,
    ) -> CodingRunResult:
        """Run ``task`` in its workspace and return the bounded output.

        Streams ``STARTED``, ``OUTPUT`` per line and a terminal event through
        ``on_event``. ``stop`` (when given) is polled during the run: setting
        it must make the engine terminate its child and raise
        ``OperationCancelledError``; the same applies to the caller cancelling
        this coroutine. Timeouts raise ``OperationTimeoutError``; a missing
        executable or unusable workspace raise their typed errors. A non-zero
        process exit is returned, never raised — verification decides it.
        """

    # -- Definition-time guard, mirroring T042 ------------------------------ #
    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Fail at import when a concrete engine is mis-declared (§8, §40)."""
        super().__init_subclass__(**kwargs)
        if inspect.isabstract(cls):
            return
        if not isinstance(getattr(cls, "name", None), str) or not cls.name.strip():
            raise TypeError(
                f"{cls.__name__}.name must be a non-empty string "
                "(spec 8: engines are declared, never hard-coded into the coding agent)"
            )
        if not inspect.iscoroutinefunction(getattr(cls, "run", None)):
            raise TypeError(
                f"{cls.__name__}.run must be an async function "
                "(spec 8: coding engines execute concurrently, so their work is awaited)"
            )


def _require_text(what: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(
            f"a coding task needs a {what}",
            details={what: str(value)[:200]},
        )
    return value.strip()


def _require_relative(value: Any) -> str:
    """Validate an expected file as a sane workspace-relative path.

    An expectation is a path *inside* the workspace: refuse anything absolute
    (POSIX or Windows — a drive letter or UNC would be a path the workspace
    check never sees), empty, or stepping out with ``..``. Verification acts on
    these, so a smuggled path here would be verification perusing a place the
    task was never allowed to touch.
    """
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(
            "an expected file must be a non-empty relative path",
            details={"file": str(value)[:200]},
        )
    path = Path(value)
    if path.is_absolute() or PureWindowsPath(value).is_absolute():
        raise InvalidInputError(
            "an expected file must be relative to the workspace",
            details={"file": value[:200]},
        )
    if ".." in path.parts:
        raise InvalidInputError(
            "an expected file must not step outside the workspace",
            details={"file": value[:200]},
        )
    return value.strip()
