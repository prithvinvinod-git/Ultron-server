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

from app.agents.computer.gateway import NodeGateway
from app.config import Settings
from app.tools.base import Tool
from app.tools.browser import BrowserSessionManager, build_browser_tools
from app.tools.computer import build_computer_tools
from app.tools.filesystem import FilesystemSandbox, build_filesystem_tools
from app.tools.git import build_git_tools
from app.tools.python import build_python_tools
from app.tools.registry import ToolRegistry
from app.tools.system import build_system_tools
from app.tools.terminal import build_terminal_tools
from app.tools.web import DuckDuckGoProvider, build_web_tools
from app.workspaces.manager import GitRunner

__all__ = ["build_tool_registry", "iter_default_tools"]


def iter_default_tools(
    settings: Settings,
    *,
    sandbox: FilesystemSandbox,
    git_runner: GitRunner,
    browser: BrowserSessionManager | None = None,
    computer: NodeGateway | None = None,
) -> tuple[Tool, ...]:
    """Every tool the runtime exposes, constructed but not yet registered.

    The ``browser.*`` family is included only when the browser subsystem is
    enabled (``BROWSER_ENABLED``) and the ``computer.*`` family only when the
    computer-node subsystem is enabled and a gateway is supplied
    (``COMPUTER_NODES_ENABLED``, T143): a disabled deployment exposes no such
    tools at all, rather than tools that always fail. Both are optional so tests
    and minimal deployments can build the inventory without the managers.
    """
    search_provider = DuckDuckGoProvider()
    tools: list[Tool] = [
        *build_filesystem_tools(sandbox),
        *build_git_tools(git_runner, sandbox),
        *build_terminal_tools(settings.terminal, sandbox),
        *build_python_tools(settings.python_tool, sandbox),
        *build_web_tools(settings.web, search_provider=search_provider),
        *build_system_tools(settings.ollama),
    ]
    if browser is not None and browser.enabled:
        tools.extend(
            build_browser_tools(
                browser,
                allow_private_networks=settings.web.allow_private_networks,
            )
        )
    if computer is not None and settings.computer_nodes.enabled:
        tools.extend(build_computer_tools(computer))
    return tuple(tools)


def build_tool_registry(
    settings: Settings,
    *,
    sandbox: FilesystemSandbox,
    git_runner: GitRunner,
    browser: BrowserSessionManager | None = None,
    computer: NodeGateway | None = None,
) -> ToolRegistry:
    """Build a registry populated with every default tool."""
    registry = ToolRegistry()
    for tool in iter_default_tools(
        settings,
        sandbox=sandbox,
        git_runner=git_runner,
        browser=browser,
        computer=computer,
    ):
        registry.register(tool)
    return registry

