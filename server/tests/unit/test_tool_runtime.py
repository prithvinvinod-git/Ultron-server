"""Phase 5 tool-runtime tests (T098).

§59.6 fixes the inventory: every tool reaches the registry once, with its §14
contract and its declared permission level intact, and the container hands that
registry to §16's pipeline. These tests pin the *whole* inventory rather than a
single family:

- the default registry contains every built tool exactly once, sorted by name;
- every registered tool carries the full contract (name, description, schema,
  level, timeout, reversibility) that §14 and §66.7 make mandatory;
- the sensitive git tools (§59.3 EXPLICIT: push/reset/clean/delete_branch/
  rebase) declare DESTRUCTIVE + IRREVERSIBLE, and §66.17's confirmation still
  applies even when the level would allow them;
- the executor refuses a call below the tool's required level, and a workspace
  escape attempt through the pipeline never reaches the filesystem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.config.settings import WorkspaceSettings
from app.container import Container
from app.core.errors import ConfirmationRequiredError, PermissionDeniedError, WorkspaceError
from app.core.permissions import PermissionManager
from app.database.models import PermissionLevel
from app.events.bus import EventBus
from app.security.audit import AuditLogger
from app.tools.base import Reversibility
from app.tools.bootstrap import build_tool_registry
from app.tools.executor import ToolExecutor
from app.tools.filesystem import FilesystemSandbox
from app.tools.registry import ToolRegistry
from app.workspaces.manager import SubprocessGitRunner

pytestmark = pytest.mark.unit

_EXPLICIT_GIT_TOOLS = {"git.push", "git.reset", "git.clean", "git.delete_branch", "git.rebase"}

_EXPECTED_TOOLS = frozenset(
    {
        "filesystem.read",
        "filesystem.write",
        "filesystem.list",
        "filesystem.search",
        "filesystem.delete",
        "git.status",
        "git.diff",
        "git.log",
        "git.branch",
        "git.show",
        "git.worktree_list",
        "git.create_branch",
        "git.switch_branch",
        "git.commit",
        "git.pull",
        "git.stash_push",
        "git.stash_pop",
        "git.worktree_add",
        "git.worktree_remove",
        "git.push",
        "git.reset",
        "git.clean",
        "git.delete_branch",
        "git.rebase",
        "terminal.run",
        "python.run",
        "web.fetch",
        "system.cpu",
        "system.memory",
        "system.disk",
        "system.processes",
        "system.network",
        "system.gpu",
        "system.services",
        "system.ollama",
        "system.docker",
    }
)


@pytest.fixture()
def workspace_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture()
def settings(workspace_root: Path) -> Settings:
    return Settings(workspaces=WorkspaceSettings(root=str(workspace_root)))


@pytest.fixture()
def registry(settings: Settings) -> ToolRegistry:
    sandbox = FilesystemSandbox(settings.workspaces)
    return build_tool_registry(settings, sandbox=sandbox, git_runner=SubprocessGitRunner())


def make_executor(
    audit_sink: Any, registry: ToolRegistry, *, default_level: PermissionLevel
) -> ToolExecutor:
    return ToolExecutor(
        registry=registry,
        permissions=PermissionManager(
            audit=AuditLogger(audit_sink),
            default_level=default_level,
        ),
        events=EventBus(),
    )


# --------------------------------------------------------------------------- #
# The inventory (§59.6): complete, unique, sorted
# --------------------------------------------------------------------------- #
def test_default_registry_registers_every_family(registry: ToolRegistry) -> None:
    names = {tool.name for tool in registry.list()}
    assert names == _EXPECTED_TOOLS


def test_default_registry_names_are_unique_and_sorted(registry: ToolRegistry) -> None:
    names = [tool.name for tool in registry.list()]
    assert len(names) == len(set(names))
    assert names == sorted(names)


# --------------------------------------------------------------------------- #
# Every tool carries the §14 contract
# --------------------------------------------------------------------------- #
def test_every_registered_tool_declares_the_full_contract(registry: ToolRegistry) -> None:
    for tool in registry.list():
        assert tool.name
        assert tool.description
        assert tool.input_schema.get("type") == "object"
        assert isinstance(tool.permission_level, PermissionLevel)
        assert tool.timeout > 0
        assert isinstance(tool.reversibility, Reversibility)
        assert tool.node_scope is None or isinstance(tool.node_scope, str)


# --------------------------------------------------------------------------- #
# §59.3 EXPLICIT git tools: declared dangerous + still confirmed
# --------------------------------------------------------------------------- #
def test_explicit_git_tools_declare_destructive(registry: ToolRegistry) -> None:
    for name in _EXPLICIT_GIT_TOOLS:
        tool = registry.lookup(name)
        assert tool.permission_level is PermissionLevel.DESTRUCTIVE
        assert tool.reversibility is Reversibility.IRREVERSIBLE
        assert tool.audit is True


async def test_irreversible_tool_requires_confirmation_even_at_full_level(
    audit_sink: Any, registry: ToolRegistry, workspace_root: Path
) -> None:
    executor = make_executor(audit_sink, registry, default_level=PermissionLevel.DESTRUCTIVE)
    with pytest.raises(ConfirmationRequiredError) as excinfo:
        await executor.run(
            "git.reset", {"path": str(workspace_root), "target": "HEAD~1"}, principal="alice"
        )
    assert excinfo.value.details["tool"] == "git.reset"


# --------------------------------------------------------------------------- #
# §16: the pipeline refuses anything below the declared level
# --------------------------------------------------------------------------- #
async def test_below_required_level_is_denied_before_execution(
    audit_sink: Any, registry: ToolRegistry, workspace_root: Path
) -> None:
    executor = make_executor(audit_sink, registry, default_level=PermissionLevel.MODIFY_PROJECT)
    with pytest.raises(PermissionDeniedError) as excinfo:
        await executor.run("git.clean", {"path": str(workspace_root)}, principal="alice")
    assert excinfo.value.details["tool"] == "git.clean"
    assert excinfo.value.details["required_level"] == PermissionLevel.DESTRUCTIVE.level


# --------------------------------------------------------------------------- #
# §59.4: a workspace escape attempt never reaches the filesystem
# --------------------------------------------------------------------------- #
async def test_workspace_escape_is_refused_through_the_pipeline(
    audit_sink: Any, registry: ToolRegistry, workspace_root: Path
) -> None:
    executor = make_executor(audit_sink, registry, default_level=PermissionLevel.DESTRUCTIVE)
    outside = str(workspace_root.parent.parent / "secrets.txt")
    with pytest.raises(WorkspaceError):
        await executor.run("filesystem.read", {"path": outside}, principal="alice")


# --------------------------------------------------------------------------- #
# The container wires the inventory (§59.22)
# --------------------------------------------------------------------------- #
def test_container_builds_the_default_tool_inventory(workspace_root: Path) -> None:
    container = Container(settings=Settings(workspaces=WorkspaceSettings(root=str(workspace_root))))
    names = {tool.name for tool in container.tools.list()}
    assert names == _EXPECTED_TOOLS
    assert container.tools is container.tools
    assert container.sandbox is container.sandbox
    assert container.git_runner is container.git_runner
