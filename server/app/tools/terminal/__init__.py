"""Terminal tools. Allow-listed commands only; raw LLM shell is never executed.

T091. The single tool here — :class:`~app.tools.terminal.run.RunCommandTool` —
takes a program name and a literal argument vector, never a command string, so
there is no shell on the path (§59.4, §59.6). The allow-list lives in
``TerminalSettings.allowed_commands`` and an empty list denies everything.
"""

from __future__ import annotations

from app.config.settings import TerminalSettings
from app.tools.filesystem.sandbox import FilesystemSandbox
from app.tools.terminal.run import RunCommandTool

__all__ = ["RunCommandTool", "build_terminal_tools"]


def build_terminal_tools(
    settings: TerminalSettings, sandbox: FilesystemSandbox
) -> tuple[RunCommandTool, ...]:
    """The standard terminal tool set, bound to settings and a sandbox."""
    return (RunCommandTool(settings, sandbox),)
