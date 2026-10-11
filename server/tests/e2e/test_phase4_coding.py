"""Phase 4 end-to-end: a coding task over a real repository (T085, T086, §8/§9).

This is the phase gate made executable: the fixture in
``tests/fixtures/sample_repo`` is copied to a temp directory, ``git init``-ed,
registered as a project, and a real :class:`~app.workspaces.manager.WorkspaceManager`
clones it. The *engine* is the test interpreter running a one-line script
(standing in for the OpenCode CLI, whose contract is unchanged), so the whole
path is real — real git, a real isolated clone, a real subprocess, real
verification — with no OpenCode install required. The tests are ``e2e`` and
skip cleanly when git is absent.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.agents.coding import CodingAgent, OpenCodeEngine
from app.config.settings import WorkspaceSettings
from app.core.errors import OperationTimeoutError
from app.workspaces.manager import WorkspaceManager

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed"),
]

SAMPLE_REPO = Path(__file__).resolve().parents[1] / "fixtures" / "sample_repo"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _git(repo: Path, *args: str) -> str:
    """Run one git command in ``repo`` synchronously (a test helper only)."""
    process = subprocess.run(  # noqa: S603 - test-only; args come from test literals
        ["git", *args],  # noqa: S607 - PATH lookup mirrors the manager's §36 default
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        raise AssertionError(f"git {args} failed ({process.returncode}): {process.stderr}")
    return process.stdout


def init_sample_repo(dest: Path) -> Path:
    """Copy the fixture and make it a one-commit git repository."""
    shutil.copytree(SAMPLE_REPO, dest)
    _git(dest, "init", "-q", "-b", "main")
    _git(dest, "config", "user.email", "tests@ultron.local")
    _git(dest, "config", "user.name", "ULTRON Tests")
    _git(dest, "add", "-A")
    _git(dest, "commit", "-q", "-m", "sample repository")
    return dest


def make_manager(tmp_path: Path) -> WorkspaceManager:
    settings = WorkspaceSettings(root=str(tmp_path / "ws"), allowed_roots=[str(tmp_path)])
    return WorkspaceManager(settings=settings)


def python_engine(script: str, *, timeout_seconds: float = 1800.0) -> OpenCodeEngine:
    """An engine that runs ``script`` with the test interpreter."""
    return OpenCodeEngine(
        executable=sys.executable,
        subcommand="",
        extra_args=("-c", script),
        timeout_seconds=timeout_seconds,
    )


def _same_location(seen: str, expected: Path) -> bool:
    """Whether two paths name the same directory (case/8.3-insensitive)."""
    return Path(seen).resolve() == expected.resolve()


#: A stand-in for OpenCode: add a feature and its test, then run the suite.
CODING_TASK = "\n".join(
    [
        "import pathlib, subprocess, sys",
        "pathlib.Path('feature.py').write_text(",
        "    'def multiply(a, b):\\n    return a * b\\n', encoding='utf-8')",
        "test = pathlib.Path('test_calculator.py')",
        "test.write_text(",
        "    test.read_text(encoding='utf-8') + '\\n\\ndef test_multiply():\\n'",
        "    '    from feature import multiply\\n    assert multiply(2, 3) == 6\\n',",
        "    encoding='utf-8')",
        "print('added feature.py and its test')",
        "raise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', '-q']))",
    ]
)


# --------------------------------------------------------------------------- #
# The phase gate
# --------------------------------------------------------------------------- #
async def test_a_coding_task_runs_against_the_sample_repo_and_verifies(tmp_path: Path) -> None:
    project = init_sample_repo(tmp_path / "project")
    manager = make_manager(tmp_path)
    manager.register_project("sample", project)
    workspace = await manager.create_workspace("ws1", "sample")

    agent = CodingAgent(engine=python_engine(CODING_TASK), workspaces=manager)
    report = await agent.run(
        {
            "prompt": "add a multiply function with a test",
            "workspace": "ws1",
            "files": ["feature.py"],
            "task_id": "e2e-1",
        }
    )

    assert report["verification"] == "success"
    assert report["exit_code"] == 0
    assert (workspace.path / "feature.py").is_file()
    output = "".join(report["output_tail"])
    assert "3 passed" in output  # add/subtract plus the added test, all green
    assert "added feature.py and its test" in report["output_tail"]


async def test_the_engine_runs_in_the_workspace_directory(tmp_path: Path) -> None:
    project = init_sample_repo(tmp_path / "project")
    manager = make_manager(tmp_path)
    manager.register_project("sample", project)
    workspace = await manager.create_workspace("ws1", "sample")

    agent = CodingAgent(
        engine=python_engine("import os;print('CWD=' + os.getcwd())"),
        workspaces=manager,
    )
    report = await agent.run({"prompt": "where am i", "workspace": "ws1"})

    cwd_line = next(line for line in report["output_tail"] if line.startswith("CWD="))
    assert _same_location(cwd_line[4:], workspace.path)


async def test_a_slow_coding_task_times_out_and_fails(tmp_path: Path) -> None:
    project = init_sample_repo(tmp_path / "project")
    manager = make_manager(tmp_path)
    manager.register_project("sample", project)
    await manager.create_workspace("ws1", "sample")

    agent = CodingAgent(
        engine=python_engine("import time;time.sleep(20)", timeout_seconds=0.4),
        workspaces=manager,
    )
    with pytest.raises(OperationTimeoutError):
        await agent.run({"prompt": "hang", "workspace": "ws1"})


# --------------------------------------------------------------------------- #
# Isolation and cleanup
# --------------------------------------------------------------------------- #
async def test_the_clone_is_isolated_from_the_project(tmp_path: Path) -> None:
    project = init_sample_repo(tmp_path / "project")
    original_calculator = (project / "calculator.py").read_text(encoding="utf-8")
    manager = make_manager(tmp_path)
    manager.register_project("sample", project)
    workspace = await manager.create_workspace("ws1", "sample")

    agent = CodingAgent(engine=python_engine(CODING_TASK), workspaces=manager)
    await agent.run({"prompt": "add a multiply function", "workspace": "ws1"})

    # The agent's work landed in the clone…
    assert (workspace.path / "feature.py").is_file()
    assert (workspace.path / ".git").is_dir()
    # …and the source project is untouched: no new file, no edited file.
    assert not (project / "feature.py").exists()
    assert (project / "calculator.py").read_text(encoding="utf-8") == original_calculator


async def test_cleanup_removes_the_workspace_but_not_the_project(tmp_path: Path) -> None:
    project = init_sample_repo(tmp_path / "project")
    manager = make_manager(tmp_path)
    manager.register_project("sample", project)
    workspace = await manager.create_workspace("ws1", "sample")
    assert workspace.path.exists()

    await manager.cleanup("ws1")

    assert not workspace.path.exists()
    assert project.exists()
    assert (project / "calculator.py").is_file()
