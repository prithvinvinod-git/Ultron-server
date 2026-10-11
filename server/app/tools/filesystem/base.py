"""Shared plumbing for the filesystem tools (T090).

Every filesystem tool needs the same two things: a sandbox to resolve and
bound paths, and nothing else. This intermediate base holds that dependency so
each tool stays a declaration plus one method. It is intentionally left
abstract — it implements no ``execute``, so :class:`~app.tools.base.Tool`'s
definition-time guards skip it (an intermediate base is a work in progress, not
a registration), and the concrete tools below it inherit the constructor.
"""

from __future__ import annotations

from app.tools.base import Tool
from app.tools.filesystem.sandbox import FilesystemSandbox

__all__ = ["FilesystemTool"]


class FilesystemTool(Tool):
    """Base for filesystem tools; carries the sandbox, declares nothing else."""

    def __init__(self, sandbox: FilesystemSandbox) -> None:
        """Keep the dependency explicit: no tool builds its own boundary."""
        self._sandbox = sandbox

    @property
    def sandbox(self) -> FilesystemSandbox:
        """The sandbox this tool resolves every path through."""
        return self._sandbox
