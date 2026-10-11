"""`filesystem.read` (T090, spec §59.4): bounded text read.

§59.4 makes the filesystem layer the only component that touches a path, so the
handler's first act is always ``sandbox.resolve_file`` — never ``open(path)`` on
raw input. The read is bounded by ``max_bytes`` so a tool call can never pull an
unbounded amount of the server's disk into a transcript; the response says
whether it truncated rather than pretending the file ended there.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from app.core.errors import InvalidInputError
from app.database.models import PermissionLevel
from app.tools.arguments import as_int
from app.tools.base import Reversibility
from app.tools.filesystem.base import FilesystemTool

__all__ = ["ReadFileTool"]

_DEFAULT_MAX_BYTES = 1_048_576


class ReadFileTool(FilesystemTool):
    """Read a UTF-8 text file inside the workspace."""

    name = "filesystem.read"
    description = "Read a UTF-8 text file inside the workspace, bounded by max_bytes."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "max_bytes": {"type": "integer", "minimum": 1},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 10.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        target = self._sandbox.resolve_file(str(arguments["path"]), what="file")
        limit = as_int(
            arguments.get("max_bytes"), "max_bytes", default=_DEFAULT_MAX_BYTES, minimum=1
        )
        content, size, truncated = _read_text(target, limit)
        return {
            "path": str(target),
            "content": content,
            "bytes": size,
            "truncated": truncated,
            "encoding": "utf-8",
        }


def _read_text(path: Path, limit: int) -> tuple[str, int, bool]:
    """Read at most ``limit`` bytes, decoding UTF-8 strictly."""
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    truncated = len(raw) > limit
    if truncated:
        raw = raw[:limit]
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidInputError(
            "file is not valid UTF-8 text", details={"file": str(path)}
        ) from exc
    return content, len(raw), truncated
