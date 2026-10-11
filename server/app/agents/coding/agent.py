"""The coding agent (T083): §8's worker over an abstracted engine (§9).

§8 names the purpose — software engineering: inspect the repository, understand
the project, plan, edit, run tests/builds, inspect git, report the changes,
verify the result — and names the one rule that keeps it engine-agnostic:
"ULTRON should NOT hard-code OpenCode throughout the system". This agent is
that worker, and it knows only :class:`~app.agents.coding.engine.CodingEngine`
— never the OpenCode CLI. Swap in a future engine and this file does not change.

`run(context)` walks §8's list as a sequence of steps, and the context is the
task payload the orchestrator/executor hands a worker:

- ``prompt`` (required) — the instruction the engine receives. This is §9's
  "input is the prompt".
- ``workspace`` (required unless ``project`` is given) — the *name* of a
  workspace the manager already registered. §9 forbids running without an
  explicit workspace; there is no implicit cwd.
- ``project`` (optional) — a registered project to open a fresh workspace from
  (a clone named ``code-<id>``); the created workspace is where the run happens.
- ``files`` (optional) — the workspace-relative paths the task is expected to
  touch or produce; T084's ``verify_coding_result`` checks exactly these.
- ``task_id`` (optional) — the owning task's id, carried into every event and
  the report; defaults to the agent's own task id, then its agent id.
- ``stop`` (optional) — an :class:`asyncio.Event` passed straight to the
  engine, for cooperative cancellation (§9).

The steps:

1. **Inspect** — read the workspace's git status and current branch through the
   manager (T080) so the report can say what the workspace looked like.
2. **Plan** — announce the intent (engine, workspace, expected files) on
   ``TASK_PROGRESS`` — ephemeral, ``persist=False``, exactly as the executor
   publishes step transitions (§64.9).
3. **Run** — hand a :class:`CodingTask` to the engine. Every
   :class:`CodingEvent` the engine streams becomes one ``TASK_PROGRESS`` event,
   so a client watches the work line by line rather than waiting for the end.
4. **Verify** — ask T084 what the exit code plus the expected files mean
   (§17: never assume success). The report carries the outcome, never a claim.
5. **Report** — a plain mapping: workspace, branch, engine, exit code, the
   verification outcome, which expected files were found, and the output tail.

Failure discipline mirrors the runtime (T044): a typed :class:`UltronError`
(``WorkspaceError``, ``OperationTimeoutError``, …) is re-raised untouched so
its code survives, and anything unexpected is wrapped in ``AgentError``. The
agent does **not** move itself through the lifecycle — the manager owns the
``agents`` row and its ``AGENT_*`` events, exactly as the mock agents rely on.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

from app.agents.base import Agent
from app.core.errors import AgentError, InvalidInputError, UltronError
from app.database.models import VerificationOutcome
from app.events.bus import EventBus
from app.events.types import EventType
from app.workspaces.manager import Workspace, WorkspaceManager

from .engine import (
    CodingEngine,
    CodingEvent,
    CodingEventCallback,
    CodingRunResult,
    CodingTask,
)
from .verify import expected_files_present, verify_coding_result

__all__ = ["CodingAgent"]

#: How much of the engine's output rides along in a report, by line count.
#: Verification reads the workspace, not the log; this is for a human.
_OUTPUT_TAIL_LINES = 20


class CodingAgent(Agent):
    """Inspect, plan, run a coding engine, verify, report (§8)."""

    agent_type = "coding.agent"
    description = "Run a coding engine on an isolated workspace and verify the result."

    def __init__(
        self,
        *,
        engine: CodingEngine,
        workspaces: WorkspaceManager,
        bus: EventBus | None = None,
        **agent_kwargs: Any,
    ) -> None:
        """Wire the agent to its engine and workspace manager.

        All three dependencies are injected (engine as the interface, T081):
        the agent is constructed by the runtime with the engine it was
        configured with, never one it names itself (§8).
        """
        super().__init__(**agent_kwargs)
        self._engine = engine
        self._workspaces = workspaces
        self._bus = bus

    async def run(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        """Perform one coding task over ``context`` and return its report."""
        try:
            return await self._run(context)
        except UltronError:
            raise
        except Exception as exc:
            raise AgentError(
                str(self.agent_id),
                "coding agent failed unexpectedly",
                details={"agent_type": self.agent_type},
                cause=exc,
            ) from exc

    # -- the five steps ----------------------------------------------------- #
    async def _run(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        prompt = _require_text(context, "prompt")
        task_id = _task_id(context) or str(self.agent_id)
        files = _expected_files(context)

        workspace, created = await self._resolve_workspace(context)
        task = CodingTask(
            task_id=task_id,
            prompt=prompt,
            workspace=workspace.path,
            files=files,
        )

        await self._inspect(task_id, workspace.name)

        await self._progress(
            task_id,
            {
                "task_id": task_id,
                "phase": "plan",
                "engine": self._engine.name,
                "workspace": workspace.name,
                "branch": workspace.branch,
                "files": list(files),
                "created": created,
            },
        )

        result = await self._engine.run(
            task,
            on_event=self._engine_listener(task_id),
            stop=_stop_event(context),
        )

        outcome = verify_coding_result(result, task)
        return self._report(task, workspace.branch, result, outcome)

    async def _resolve_workspace(self, context: Mapping[str, Any]) -> tuple[Workspace, bool]:
        """The workspace to run in: an existing one, or a fresh clone.

        Returns the :class:`~app.workspaces.manager.Workspace` and whether this
        call created it. Names are validated by the manager; a name for a
        workspace that does not exist raises ``NotFoundError`` there.
        """
        name = _optional_text(context, "workspace")
        if name:
            return self._workspaces.workspace(name), False

        project = _optional_text(context, "project")
        if project:
            created = await self._workspaces.create_workspace(
                f"code-{_short_id(task_id=_task_id(context) or str(self.agent_id))}",
                project,
            )
            return created, True

        raise InvalidInputError(
            "a coding task needs an explicit workspace or a project",
            details={"keys": sorted(str(key) for key in context)},
        )

    async def _inspect(self, task_id: str, workspace_name: str) -> None:
        """Read git status and branch through the manager and announce them."""
        status = await self._workspaces.status(workspace_name)
        branch = await self._workspaces.current_branch(workspace_name)
        await self._progress(
            task_id,
            {
                "task_id": task_id,
                "phase": "inspect",
                "workspace": workspace_name,
                "clean": status.ok and not status.stdout.strip(),
                "branch": branch.stdout.strip() or None,
            },
        )

    def _engine_listener(self, task_id: str) -> CodingEventCallback:
        """A callback turning each engine event into one ``TASK_PROGRESS``."""

        async def on_event(event: CodingEvent) -> None:
            await self._progress(
                task_id,
                {
                    "task_id": task_id,
                    "phase": "engine",
                    "engine": event.engine,
                    "kind": str(event.kind),
                    "line": event.line[:500],
                    "exit_code": event.exit_code,
                },
            )

        return on_event

    def _report(
        self,
        task: CodingTask,
        branch: str | None,
        result: CodingRunResult,
        outcome: VerificationOutcome,
    ) -> dict[str, Any]:
        return {
            "agent": self.agent_type,
            "task_id": task.task_id,
            "engine": result.engine,
            "workspace": str(task.workspace),
            "branch": branch,
            "exit_code": result.exit_code,
            "verification": str(outcome),
            "files_expected": list(task.files),
            "files_present": list(expected_files_present(task)),
            "truncated": result.truncated,
            "ran_seconds": result.ran_seconds,
            "output_tail": list(result.output[-_OUTPUT_TAIL_LINES:]),
        }

    async def _progress(self, task_id: str, payload: Mapping[str, Any]) -> None:
        """Publish one ephemeral ``TASK_PROGRESS`` (no bus: nowhere to publish)."""
        if self._bus is None:
            return
        await self._bus.publish(
            EventType.TASK_PROGRESS,
            payload,
            task_id=task_id,
            agent_id=str(self.agent_id),
            persist=False,
        )


# --------------------------------------------------------------------------- #
# Context helpers (stateless, mirroring the manager/task split)
# --------------------------------------------------------------------------- #
def _require_text(context: Mapping[str, Any], key: str) -> str:
    value = context.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(
            f"a coding task needs a {key}",
            details={key: str(value)[:200]},
        )
    return value.strip()


def _optional_text(context: Mapping[str, Any], key: str) -> str | None:
    value = context.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _task_id(context: Mapping[str, Any]) -> str | None:
    value = context.get("task_id")
    return str(value) if value else None


def _expected_files(context: Mapping[str, Any]) -> tuple[str, ...]:
    value = context.get("files")
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise InvalidInputError(
            "a coding task's files must be a sequence of paths",
            details={"files": str(value)[:200]},
        )
    return tuple(str(item) for item in value)


def _stop_event(context: Mapping[str, Any]) -> asyncio.Event | None:
    value = context.get("stop")
    if value is None:
        return None
    if not isinstance(value, asyncio.Event):
        raise InvalidInputError(
            "a coding task's stop must be an asyncio.Event",
            details={"stop": type(value).__name__},
        )
    return value


def _short_id(*, task_id: str) -> str:
    """A compact, name-safe suffix for a generated workspace name."""
    return task_id.replace("-", "")[:12]
