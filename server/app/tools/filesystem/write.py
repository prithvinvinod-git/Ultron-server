"""`filesystem.write` (T090, spec §59.4): bounded, verified text write.

A write is treated as **CONTROLLED**, not safe: §59.4 says any write is at least
MODIFY_PROJECT, and this tool declares exactly that. It is
``PARTIALLY_REVERSIBLE`` because creating a new file is undoable while
overwriting an existing one is not — the local agent cannot restore what it
clobbered.

``verify`` is what makes the write honest (§17): instead of trusting that
``write_text`` returned, it reads the file back and compares bytes, reporting
SUCCESS only when the bytes on disk match the request.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.database.models import PermissionLevel, VerificationOutcome
from app.tools.arguments import as_bool, as_str
from app.tools.base import Reversibility
from app.tools.filesystem.base import FilesystemTool
from app.tools.filesystem.sandbox import FilesystemSandbox

__all__ = ["WriteFileTool"]


class WriteFileTool(FilesystemTool):
    """Write a UTF-8 text file inside the workspace."""

    name = "filesystem.write"
    description = "Write a UTF-8 text file inside the workspace, optionally creating parents."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "content": {"type": "string"},
            "create_dirs": {"type": "boolean", "default": False},
            "overwrite": {"type": "boolean", "default": True},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.MODIFY_PROJECT
    timeout = 15.0
    reversibility = Reversibility.PARTIALLY_REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        target = self._sandbox.resolve(str(arguments["path"]), what="file")
        content = as_str(arguments.get("content"), "content", allow_empty=True)
        create_dirs = as_bool(arguments.get("create_dirs"), "create_dirs")
        overwrite = as_bool(arguments.get("overwrite"), "overwrite", default=True)
        created = _write_text(
            target,
            content,
            create_dirs=create_dirs,
            overwrite=overwrite,
            sandbox=self._sandbox,
        )
        return {
            "path": str(target),
            "bytes": len(content.encode("utf-8")),
            "created": created,
        }

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        target = self._sandbox.resolve(str(arguments["path"]), what="file")
        expected = as_str(arguments.get("content"), "content", allow_empty=True)
        return (
            VerificationOutcome.SUCCESS
            if _content_matches(target, expected)
            else VerificationOutcome.FAILED
        )


def _write_text(
    target: Path,
    content: str,
    *,
    create_dirs: bool,
    overwrite: bool,
    sandbox: FilesystemSandbox,
) -> bool:
    """Write the file, returning whether it was newly created."""
    if target.is_dir():
        raise InvalidInputError("file path is a directory", details={"file": str(target)})
    parent = target.parent
    if not parent.exists():
        if not create_dirs:
            raise NotFoundError("directory", str(parent))
        sandbox.ensure_within(parent, what="directory")
        parent.mkdir(parents=True, exist_ok=True)
    created = not target.exists()
    if not created and not overwrite:
        raise ConflictError("file already exists", details={"file": str(target)})
    target.write_text(content, encoding="utf-8")
    return created


def _content_matches(target: Path, expected: str) -> bool:
    """Whether the file on disk currently holds exactly ``expected``."""
    try:
        return target.read_text(encoding="utf-8") == expected
    except (OSError, UnicodeDecodeError):
        return False
