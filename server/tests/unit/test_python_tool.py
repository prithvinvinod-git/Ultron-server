"""T093 tests: the restricted Python tool (spec §59.6).

The tool must run a literal program (no shell), bound the cwd to the workspace,
respect the timeout, and surface the exit code. It runs with the current
interpreter, so the tests are hermetic.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.config.settings import PythonToolSettings, WorkspaceSettings
from app.core.errors import OperationTimeoutError, WorkspaceError
from app.database.models import PermissionLevel, VerificationOutcome
from app.tools.base import Reversibility
from app.tools.filesystem import FilesystemSandbox
from app.tools.python import PythonTool

pytestmark = pytest.mark.unit


@pytest.fixture()
def sandbox(tmp_path: Path) -> FilesystemSandbox:
    (tmp_path / "work").mkdir()
    settings = WorkspaceSettings(root=str(tmp_path), allowed_roots=[str(tmp_path)])
    return FilesystemSandbox(settings)


def _tool(sandbox: FilesystemSandbox, **kwargs: object) -> PythonTool:
    return PythonTool(PythonToolSettings(**kwargs), sandbox)


def test_declarations() -> None:
    assert PythonTool.name == "python.run"
    assert PythonTool.permission_level is PermissionLevel.EXECUTE_PROGRAMS
    assert PythonTool.reversibility is Reversibility.PARTIALLY_REVERSIBLE


async def test_runs_and_captures_stdout(sandbox: FilesystemSandbox) -> None:
    tool = _tool(sandbox)
    result = await tool.execute(
        {"path": str(sandbox.roots[0]), "code": "print('hello from python')"}
    )
    assert result["exit_code"] == 0
    assert "hello from python" in result["stdout"]
    assert await tool.verify(result, {}) is VerificationOutcome.SUCCESS


async def test_runtime_error_is_reported_not_raised(sandbox: FilesystemSandbox) -> None:
    tool = _tool(sandbox)
    result = await tool.execute({"path": str(sandbox.roots[0]), "code": "raise ValueError('boom')"})
    assert result["exit_code"] != 0
    assert "ValueError" in result["stderr"]
    assert await tool.verify(result, {}) is VerificationOutcome.FAILED


async def test_cwd_outside_workspace_is_refused(sandbox: FilesystemSandbox) -> None:
    tool = _tool(sandbox)
    with pytest.raises(WorkspaceError):
        await tool.execute(
            {
                "path": str(sandbox.roots[0] / ".." / "escape"),
                "code": "print('nope')",
            }
        )


async def test_timeout_is_enforced(sandbox: FilesystemSandbox) -> None:
    tool = _tool(sandbox)
    with pytest.raises(OperationTimeoutError):
        await tool.execute(
            {
                "path": str(sandbox.roots[0]),
                "code": "import time; time.sleep(30)",
                "timeout_seconds": 0.25,
            }
        )


async def test_timeout_is_capped_by_settings(sandbox: FilesystemSandbox) -> None:
    tool = _tool(sandbox, timeout=1)
    assert tool._python_timeout == 1.0
    assert sys.executable
