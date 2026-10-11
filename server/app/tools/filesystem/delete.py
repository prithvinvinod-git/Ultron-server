"""`filesystem.delete` (T090, spec §59.4): the irreversible one.

§59.4 is blunt about this tool: deletion is irreversible and cannot be undone by
the agent, so it is declared ``DESTRUCTIVE`` and ``IRREVERSIBLE`` — the two
declarations that make the permission engine demand explicit confirmation
(§15, §66.17). It also sets ``audit = True``: a deletion is exactly the kind of
sensitive operation that should leave a durable record beyond the routine
permission audit.

Two guards sit above the permission gate. The target is resolved and must fall
inside an allowed root like everything else, and an allowed root itself is
never a valid target — deleting the workspace would delete the sandbox with it.
"""

from __future__ import annotations

import contextlib
import shutil
import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, ClassVar

from app.core.errors import InvalidInputError
from app.database.models import PermissionLevel, VerificationOutcome
from app.tools.arguments import as_bool
from app.tools.base import Reversibility
from app.tools.filesystem.base import FilesystemTool

__all__ = ["DeletePathTool"]


class DeletePathTool(FilesystemTool):
    """Delete a file or directory inside the workspace."""

    name = "filesystem.delete"
    description = "Delete a file or directory inside the workspace (irreversible)."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "recursive": {"type": "boolean", "default": False},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.DESTRUCTIVE
    timeout = 30.0
    reversibility = Reversibility.IRREVERSIBLE
    audit = True

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        target = self._sandbox.resolve_existing(str(arguments["path"]), what="path")
        self._sandbox.ensure_not_root(target, what="delete")
        recursive = as_bool(arguments.get("recursive"), "recursive")
        kind = _delete(target, recursive=recursive)
        return {"path": str(target), "deleted": True, "type": kind}

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        target = self._sandbox.resolve(str(arguments["path"]), what="path")
        return VerificationOutcome.SUCCESS if _is_gone(target) else VerificationOutcome.FAILED


def _is_gone(path: Path) -> bool:
    """Whether the path no longer exists on disk."""
    return not path.exists()


def _delete(target: Path, *, recursive: bool) -> str:
    """Remove the target, returning the kind of thing removed."""
    if target.is_symlink():
        target.unlink()
        return "symlink"
    if target.is_dir():
        if not recursive:
            raise InvalidInputError(
                "directory delete requires recursive=true",
                details={"path": str(target)},
            )
        shutil.rmtree(target, onexc=_force_unlink)
        return "directory"
    target.unlink()
    return "file"


def _force_unlink(func: Callable[[str], object], path: str, exc: BaseException) -> None:
    """Retry a failed unlink once the read-only bit is cleared (Windows)."""
    del exc  # unused: the retry is unconditional after clearing the bit
    with contextlib.suppress(OSError):
        Path(path).chmod(stat.S_IWRITE)
    func(path)
