"""Coding agent tests (T083, spec sections 8 and 9).

The agent is exercised with a *fake* engine (a pure :class:`CodingEngine`
implementation) over a *real* :class:`WorkspaceManager` driven by a recording
git runner — so the flow (inspect → plan → run → verify → report) is real while
no OpenCode process is needed. The engine contract's own behaviour over a real
subprocess is T082/T086; here the wall is the agent's orchestration: what it
requires, what it publishes, what it reports, and how it fails.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar

import pytest

from app.agents.coding import (
    CodingAgent,
    CodingEngine,
    CodingEvent,
    CodingEventCallback,
    CodingEventKind,
    CodingRunResult,
    CodingTask,
)
from app.agents.coding.engine import dispatch_event
from app.config.settings import WorkspaceSettings
from app.core.errors import AgentError, InvalidInputError, NotFoundError, WorkspaceError
from app.events.bus import EventBus
from app.events.types import EventType
from app.workspaces.manager import GitResult, Workspace, WorkspaceManager

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #
class FakeEngine(CodingEngine):
    """A scripted engine: records its task, optionally writes files, returns."""

    name: ClassVar[str] = "fake"

    def __init__(
        self,
        *,
        exit_code: int = 0,
        output: tuple[str, ...] = ("done",),
        produce: Any = None,
        raises: BaseException | None = None,
    ) -> None:
        self.exit_code = exit_code
        self.output = output
        self.produce = produce
        self.raises = raises
        self.tasks: list[CodingTask] = []
        self.stops: list[Any] = []

    async def run(
        self,
        task: CodingTask,
        *,
        on_event: CodingEventCallback | None = None,
        stop: Any = None,
    ) -> CodingRunResult:
        self.tasks.append(task)
        self.stops.append(stop)
        if self.raises is not None:
            raise self.raises
        if self.produce is not None:
            self.produce(task)
        await dispatch_event(
            on_event,
            CodingEvent(task_id=task.task_id, kind=CodingEventKind.STARTED, engine=self.name),
        )
        for line in self.output:
            await dispatch_event(
                on_event,
                CodingEvent(
                    task_id=task.task_id,
                    kind=CodingEventKind.OUTPUT,
                    line=line,
                    engine=self.name,
                ),
            )
        terminal = CodingEventKind.COMPLETED if self.exit_code == 0 else CodingEventKind.FAILED
        await dispatch_event(
            on_event,
            CodingEvent(
                task_id=task.task_id,
                kind=terminal,
                engine=self.name,
                exit_code=self.exit_code,
            ),
        )
        return CodingRunResult(
            task_id=task.task_id,
            engine=self.name,
            exit_code=self.exit_code,
            output=self.output,
            truncated=False,
            ran_seconds=0.01,
        )


class FakeGitRunner:
    """A git runner that records calls and always succeeds."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], Path]] = []

    async def run(
        self, argv: Sequence[str], *, cwd: Path, timeout_seconds: float = 60.0
    ) -> GitResult:
        del timeout_seconds
        self.calls.append((tuple(argv), cwd))
        return GitResult(argv=tuple(argv), exit_code=0, stdout="", stderr="")


class RecordingBus(EventBus):
    """A real bus that also keeps every published event, for assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, event_type: Any, payload: Any = None, **kwargs: Any) -> Any:
        self.published.append((str(event_type), dict(payload or {})))
        return await super().publish(event_type, payload, **kwargs)


def make_manager(tmp_path: Path, runner: FakeGitRunner) -> WorkspaceManager:
    settings = WorkspaceSettings(root=str(tmp_path / "ws"), allowed_roots=[str(tmp_path)])
    return WorkspaceManager(settings=settings, runner=runner, git_isolation=False)


def register_project(manager: WorkspaceManager, tmp_path: Path, name: str = "proj") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / ".git").mkdir()  # the marker locate_repository walks up to find
    manager.register_project(name, root)
    return root


async def make_workspace(manager: WorkspaceManager, tmp_path: Path, name: str = "ws1") -> Workspace:
    register_project(manager, tmp_path)
    return await manager.create_workspace(name, "proj")


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
async def test_agent_runs_verifies_and_reports(tmp_path: Path) -> None:
    runner = FakeGitRunner()
    manager = make_manager(tmp_path, runner)
    workspace = await make_workspace(manager, tmp_path)
    workspace.path.mkdir(parents=True, exist_ok=True)
    bus = RecordingBus()

    def produce(task: CodingTask) -> None:
        (task.workspace / "out.py").write_text("print('hi')\n", encoding="utf-8")

    engine = FakeEngine(produce=produce)
    agent = CodingAgent(engine=engine, workspaces=manager, bus=bus, name="coder")
    report = await agent.run(
        {
            "prompt": "add out.py",
            "workspace": "ws1",
            "files": ["out.py"],
            "task_id": "task-42",
        }
    )

    assert report["verification"] == "success"
    assert report["engine"] == "fake"
    assert report["exit_code"] == 0
    assert report["files_expected"] == ["out.py"]
    assert report["files_present"] == ["out.py"]
    assert report["task_id"] == "task-42"
    assert engine.tasks[0].prompt == "add out.py"
    assert engine.tasks[0].workspace == workspace.path


async def test_agent_publishes_inspect_plan_and_engine_progress(tmp_path: Path) -> None:
    bus = RecordingBus()
    manager = make_manager(tmp_path, FakeGitRunner())
    await make_workspace(manager, tmp_path)
    agent = CodingAgent(engine=FakeEngine(), workspaces=manager, bus=bus)

    await agent.run({"prompt": "go", "workspace": "ws1"})

    phases = [payload["phase"] for kind, payload in bus.published if "phase" in payload]
    assert phases[0] == "inspect"
    assert phases[1] == "plan"
    assert "engine" in phases
    assert all(kind == str(EventType.TASK_PROGRESS) for kind, _ in bus.published)


async def test_agent_reports_a_non_zero_exit_as_failed(tmp_path: Path) -> None:
    manager = make_manager(tmp_path, FakeGitRunner())
    await make_workspace(manager, tmp_path)
    agent = CodingAgent(engine=FakeEngine(exit_code=1), workspaces=manager, bus=None)
    report = await agent.run({"prompt": "go", "workspace": "ws1"})
    assert report["exit_code"] == 1
    assert report["verification"] == "failed"


async def test_agent_caps_the_reported_output_tail(tmp_path: Path) -> None:
    manager = make_manager(tmp_path, FakeGitRunner())
    await make_workspace(manager, tmp_path)
    engine = FakeEngine(output=tuple(str(i) for i in range(50)))
    agent = CodingAgent(engine=engine, workspaces=manager, bus=None)
    report = await agent.run({"prompt": "go", "workspace": "ws1"})
    assert len(report["output_tail"]) == 20
    assert report["output_tail"][-1] == "49"


# --------------------------------------------------------------------------- #
# Preconditions
# --------------------------------------------------------------------------- #
async def test_agent_requires_a_prompt(tmp_path: Path) -> None:
    manager = make_manager(tmp_path, FakeGitRunner())
    agent = CodingAgent(engine=FakeEngine(), workspaces=manager)
    with pytest.raises(InvalidInputError):
        await agent.run({"workspace": "ws1"})


async def test_agent_requires_a_workspace_or_project(tmp_path: Path) -> None:
    manager = make_manager(tmp_path, FakeGitRunner())
    agent = CodingAgent(engine=FakeEngine(), workspaces=manager)
    with pytest.raises(InvalidInputError):
        await agent.run({"prompt": "go"})


async def test_agent_rejects_an_unknown_workspace(tmp_path: Path) -> None:
    manager = make_manager(tmp_path, FakeGitRunner())
    agent = CodingAgent(engine=FakeEngine(), workspaces=manager)
    with pytest.raises(NotFoundError):
        await agent.run({"prompt": "go", "workspace": "missing"})


async def test_agent_rejects_a_non_sequence_files_value(tmp_path: Path) -> None:
    manager = make_manager(tmp_path, FakeGitRunner())
    agent = CodingAgent(engine=FakeEngine(), workspaces=manager)
    with pytest.raises(InvalidInputError):
        await agent.run({"prompt": "go", "workspace": "ws1", "files": "out.py"})


async def test_agent_rejects_a_non_event_stop(tmp_path: Path) -> None:
    manager = make_manager(tmp_path, FakeGitRunner())
    await make_workspace(manager, tmp_path)
    agent = CodingAgent(engine=FakeEngine(), workspaces=manager)
    with pytest.raises(InvalidInputError):
        await agent.run({"prompt": "go", "workspace": "ws1", "stop": "not-an-event"})


# --------------------------------------------------------------------------- #
# Project-driven workspace creation
# --------------------------------------------------------------------------- #
async def test_agent_creates_a_workspace_from_a_project(tmp_path: Path) -> None:
    bus = RecordingBus()
    manager = make_manager(tmp_path, FakeGitRunner())
    register_project(manager, tmp_path)
    agent = CodingAgent(engine=FakeEngine(), workspaces=manager, bus=bus)

    report = await agent.run({"prompt": "go", "project": "proj", "task_id": "t-1"})

    names = [workspace.name for workspace in manager.list_workspaces()]
    assert names == ["code-t1"]
    assert Path(report["workspace"]).name == "code-t1"
    plan = next(p for _, p in bus.published if p.get("phase") == "plan")
    assert plan["created"] is True


# --------------------------------------------------------------------------- #
# Failure translation and pass-through
# --------------------------------------------------------------------------- #
async def test_agent_passes_a_typed_error_through_untouched(tmp_path: Path) -> None:
    manager = make_manager(tmp_path, FakeGitRunner())
    await make_workspace(manager, tmp_path)
    boom = WorkspaceError("the workspace is gone")
    agent = CodingAgent(engine=FakeEngine(raises=boom), workspaces=manager)
    with pytest.raises(WorkspaceError) as excinfo:
        await agent.run({"prompt": "go", "workspace": "ws1"})
    assert excinfo.value is boom


async def test_agent_wraps_an_unexpected_error(tmp_path: Path) -> None:
    manager = make_manager(tmp_path, FakeGitRunner())
    await make_workspace(manager, tmp_path)
    agent = CodingAgent(engine=FakeEngine(raises=ValueError("boom")), workspaces=manager)
    with pytest.raises(AgentError) as excinfo:
        await agent.run({"prompt": "go", "workspace": "ws1"})
    assert isinstance(excinfo.value.cause, ValueError)


# --------------------------------------------------------------------------- #
# Cancellation plumbing and declarations
# --------------------------------------------------------------------------- #
async def test_agent_hands_the_stop_event_to_the_engine(tmp_path: Path) -> None:
    import asyncio

    manager = make_manager(tmp_path, FakeGitRunner())
    await make_workspace(manager, tmp_path)
    stop = asyncio.Event()
    engine = FakeEngine()
    agent = CodingAgent(engine=engine, workspaces=manager)
    await agent.run({"prompt": "go", "workspace": "ws1", "stop": stop})
    assert engine.stops[0] is stop


def test_agent_declares_its_type() -> None:
    assert CodingAgent.agent_type == "coding.agent"
    assert CodingAgent.description
