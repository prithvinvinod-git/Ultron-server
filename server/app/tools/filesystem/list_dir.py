"""`filesystem.list` (T090, spec §59.4): bounded directory listing.

Read-only and safe. The listing is bounded by ``max_entries`` and reports
``truncated`` when it stops early, so a directory with a million files cannot
be turned into a million-line transcript. Symlinked entries are listed but
never followed during recursion — a symlink that leaves the workspace would be
a sandbox escape, and §59.4 forbids it at the layer that holds the path.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from app.core.errors import WorkspaceError
from app.database.models import PermissionLevel
from app.tools.arguments import as_bool, as_int
from app.tools.base import Reversibility
from app.tools.filesystem.base import FilesystemTool

__all__ = ["ListDirectoryTool"]

_DEFAULT_MAX_ENTRIES = 1000


class ListDirectoryTool(FilesystemTool):
    """List the entries of a directory inside the workspace."""

    name = "filesystem.list"
    description = "List files and directories under a workspace path, bounded by max_entries."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "recursive": {"type": "boolean", "default": False},
            "max_entries": {"type": "integer", "minimum": 1},
            "include_hidden": {"type": "boolean", "default": False},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 10.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        root = self._sandbox.resolve_directory(str(arguments["path"]))
        recursive = as_bool(arguments.get("recursive"), "recursive")
        include_hidden = as_bool(arguments.get("include_hidden"), "include_hidden")
        max_entries = as_int(
            arguments.get("max_entries"), "max_entries", default=_DEFAULT_MAX_ENTRIES, minimum=1
        )
        entries, truncated = _walk(
            root,
            recursive=recursive,
            include_hidden=include_hidden,
            max_entries=max_entries,
        )
        return {
            "path": str(root),
            "entries": entries,
            "count": len(entries),
            "truncated": truncated,
        }


def _walk(
    root: Path,
    *,
    recursive: bool,
    include_hidden: bool,
    max_entries: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Collect entries breadth-first, stopping once ``max_entries`` is reached."""
    entries: list[dict[str, Any]] = []
    directories = [root]
    while directories:
        current = directories.pop(0)
        for child in _sorted_children(current):
            if not include_hidden and child.name.startswith("."):
                continue
            if len(entries) >= max_entries:
                return entries, True
            entries.append(_describe(child))
            if recursive and child.is_dir() and not child.is_symlink():
                directories.append(child)
    return entries, False


def _sorted_children(path: Path) -> list[Path]:
    try:
        return sorted(path.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise WorkspaceError("directory could not be read", details={"path": str(path)}) from exc


def _describe(path: Path) -> dict[str, Any]:
    """A single directory entry: name, real path, type, size and mtime."""
    is_symlink = path.is_symlink()
    is_directory = path.is_dir()
    kind = "symlink" if is_symlink else ("directory" if is_directory else "file")
    size: int | None = None
    modified: str | None = None
    try:
        info = path.stat()
        modified = datetime.fromtimestamp(info.st_mtime, tz=UTC).isoformat()
        if not is_directory:
            size = info.st_size
    except OSError:
        pass
    return {
        "name": path.name,
        "path": str(path),
        "type": kind,
        "size": size,
        "modified": modified,
    }
