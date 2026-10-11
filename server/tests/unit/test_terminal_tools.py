"""T091 tests: the allow-listed terminal tool (spec §59.3, §59.4, §59.6).

The point of the tool is that there is no shell, so the tests prove two things:
a command not on the allow-list never runs, and an argument that *looks* like a
shell metacharacter reaches the program literally rather than being interpreted.
The executing tests use ``git`` (present on PATH in this environment) and skip
cleanly when it is absent.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

import pytest

from app.config.settings import TerminalSettings, WorkspaceSettings
from app.core.errors import CommandNotAllowedError, OperationTimeoutError, WorkspaceError
from app.database.models import PermissionLevel
from app.tools.base import Reversibility
from app.tools.filesystem import FilesystemSandbox
from app.tools.terminal import RunCommandTool
from app.tools.terminal.run import _drain

pytestmark = pytest.mark.unit

_GIT = shutil.which("git")
_requires_git = pytest.mark.skipif(_GIT is None, reason="git is not on PATH")


@pytest.fixture()
def sandbox(tmp_path: Path) -> FilesystemSandbox:
    (tmp_path / "work").mkdir()
    settings = WorkspaceSettings(root=str(tmp_path), allowed_roots=[str(tmp_path)])
    return FilesystemSandbox(settings)


def _tool(sandbox: FilesystemSandbox, *, allowed: list[str], **kwargs: Any) -> RunCommandTool:
    settings = TerminalSettings(allowed_commands=allowed, **kwargs)
    return RunCommandTool(settings, sandbox)


def test_declarations() -> None:
    assert RunCommandTool.name == "terminal.run"
    assert RunCommandTool.permission_level is PermissionLevel.EXECUTE_PROGRAMS
    assert RunCommandTool.reversibility is Reversibility.PARTIALLY_REVERSIBLE


async def test_empty_allow_list_denies_everything(sandbox: FilesystemSandbox) -> None:
    with pytest.raises(CommandNotAllowedError):
        await _tool(sandbox, allowed=[]).execute({"command": "git", "args": ["--version"]})


async def test_command_not_on_list_is_denied(sandbox: FilesystemSandbox) -> None:
    with pytest.raises(CommandNotAllowedError):
        await _tool(sandbox, allowed=["ls"]).execute({"command": "git", "args": ["--version"]})


async def test_path_like_command_is_denied(sandbox: FilesystemSandbox) -> None:
    tool = _tool(sandbox, allowed=["git"])
    with pytest.raises(CommandNotAllowedError):
        await tool.execute({"command": "/usr/bin/git", "args": ["--version"]})


async def test_cwd_outside_workspace_is_refused(sandbox: FilesystemSandbox) -> None:
    tool = _tool(sandbox, allowed=["git"])
    with pytest.raises(WorkspaceError):
        await tool.execute(
            {
                "command": "git",
                "args": ["--version"],
                "cwd": str(sandbox.roots[0] / ".." / "escape"),
            }
        )


@_requires_git
async def test_allowed_command_runs(sandbox: FilesystemSandbox) -> None:
    tool = _tool(sandbox, allowed=["git"])
    result = await tool.execute({"command": "git", "args": ["--version"]})
    assert result["exit_code"] == 0
    assert "git version" in result["stdout"]
    assert result["truncated"] is False


@_requires_git
async def test_arguments_are_literal_no_shell(sandbox: FilesystemSandbox) -> None:
    tool = _tool(sandbox, allowed=["git"])
    result = await tool.execute({"command": "git", "args": ["frobnicate", "&", "echo", "PWNED"]})
    assert result["exit_code"] != 0
    assert "PWNED" not in result["stdout"]


@_requires_git
async def test_timeout_kills_the_command(sandbox: FilesystemSandbox) -> None:
    tool = _tool(sandbox, allowed=["git"])
    with pytest.raises(OperationTimeoutError):
        await tool.execute({"command": "git", "args": ["--version"], "timeout_seconds": 0.0001})


async def test_drain_bounds_output() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"abcdefghij")
    reader.feed_eof()
    data, truncated = await _drain(reader, 4)
    assert data == b"abcd"
    assert truncated is True


async def test_drain_without_overflow() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"abc")
    reader.feed_eof()
    data, truncated = await _drain(reader, 8)
    assert data == b"abc"
    assert truncated is False
