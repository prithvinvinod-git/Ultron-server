"""Tool registration (T097, spec §14/§59.6).

The container asks :func:`build_tool_registry` for the complete inventory of
tools; each family knows how to construct and declare its own members, and this
module only decides *that they are all present*. Duplicate names are impossible
here (the families are disjoint) but the registry still rejects them, so a name
collision added later fails loudly at startup rather than silently shadowing a
tool.

Registration is where §59.6's completeness rule is enforced in practice: the
registry checks every tool's schema on ``register`` and ``Tool``'s
``__init_subclass__`` has already denied any tool that forgot a declaration, so
a tool cannot reach the inventory half-declared.
"""

from __future__ import annotations

from app.config import Settings
from app.tools.base import Tool
from app.tools.filesystem import FilesystemSandbox, build_filesystem_tools
from app.tools.git import build_git_tools
from app.tools.python import build_python_tools
from app.tools.registry import ToolRegistry
from app.tools.system import build_system_tools
from app.tools.terminal import build_terminal_tools
from app.tools.web import build_web_tools
from app.workspaces.manager import GitRunner

__all__ = ["build_tool_registry", "iter_default_tools"]


def iter_default_tools(
    settings: Settings, *, sandbox: FilesystemSandbox, git_runner: GitRunner
) -> tuple[Tool, ...]:
    """Every tool the runtime exposes, constructed but not yet registered."""
    return (
        *build_filesystem_tools(sandbox),
        *build_git_tools(git_runner, sandbox),
        *build_terminal_tools(settings.terminal, sandbox),
        *build_python_tools(settings.python_tool, sandbox),
        *build_web_tools(settings.web),
        *build_system_tools(settings.ollama),
    )


def build_tool_registry(
    settings: Settings, *, sandbox: FilesystemSandbox, git_runner: GitRunner
) -> ToolRegistry:
    """Build a registry populated with every default tool."""
    registry = ToolRegistry()
    for tool in iter_default_tools(settings, sandbox=sandbox, git_runner=git_runner):
        registry.register(tool)
    return registry
