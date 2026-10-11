"""Filesystem tools, sandboxed to explicitly allowed roots (T090, spec §59.4).

One package, one boundary. Every tool here resolves its paths through
:class:`~app.tools.filesystem.sandbox.FilesystemSandbox`, which owns the
allow-list and the real-path resolution §59.4 requires. The tools declare what
they are — ``filesystem.read`` is READ_ONLY, ``filesystem.write`` is
MODIFY_PROJECT, ``filesystem.delete`` is DESTRUCTIVE and irreversible — and the
pipeline decides whether a call is allowed. Nothing here checks a permission
itself; a tool that did would be a second, weaker gate beside the real one.
"""

from __future__ import annotations

from app.tools.base import Tool
from app.tools.filesystem.base import FilesystemTool
from app.tools.filesystem.delete import DeletePathTool
from app.tools.filesystem.list_dir import ListDirectoryTool
from app.tools.filesystem.read import ReadFileTool
from app.tools.filesystem.sandbox import FilesystemSandbox
from app.tools.filesystem.search import SearchFilesTool
from app.tools.filesystem.write import WriteFileTool

__all__ = [
    "DeletePathTool",
    "FilesystemSandbox",
    "FilesystemTool",
    "ListDirectoryTool",
    "ReadFileTool",
    "SearchFilesTool",
    "WriteFileTool",
    "build_filesystem_tools",
]


def build_filesystem_tools(sandbox: FilesystemSandbox) -> tuple[Tool, ...]:
    """The standard filesystem tool set, bound to one sandbox (T090)."""
    return (
        ReadFileTool(sandbox),
        WriteFileTool(sandbox),
        ListDirectoryTool(sandbox),
        SearchFilesTool(sandbox),
        DeletePathTool(sandbox),
    )
