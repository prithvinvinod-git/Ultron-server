"""`filesystem.search` (T090, spec §59.4): bounded content search.

Search walks the tree under an allowed directory and reports lines containing a
substring. Two bounds keep it honest: ``max_results`` caps the matches returned
and a module-level scan cap stops a pathological tree from running forever.
Every candidate is re-resolved and checked against the sandbox before it is
read, so a symlink planted in the workspace cannot coax a read of a file
outside it (§59.4's boundary holds even during traversal).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, ClassVar

from app.database.models import PermissionLevel
from app.tools.arguments import as_bool, as_int, as_str
from app.tools.base import Reversibility
from app.tools.filesystem.base import FilesystemTool
from app.tools.filesystem.sandbox import is_within

__all__ = ["SearchFilesTool"]

_DEFAULT_MAX_RESULTS = 200
_DEFAULT_MAX_FILE_BYTES = 262_144
_SCAN_FILE_LIMIT = 5000
_TEXT_PREVIEW_CHARS = 500


class SearchFilesTool(FilesystemTool):
    """Search workspace files for lines containing a substring."""

    name = "filesystem.search"
    description = "Search workspace files for lines containing a substring, bounded by max_results."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "query": {"type": "string", "minLength": 1},
            "glob": {"type": "string", "default": "*"},
            "max_results": {"type": "integer", "minimum": 1},
            "max_file_bytes": {"type": "integer", "minimum": 1},
            "case_sensitive": {"type": "boolean", "default": False},
        },
        "required": ["path", "query"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 30.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        root = self._sandbox.resolve_directory(str(arguments["path"]))
        query = as_str(arguments.get("query"), "query")
        pattern = as_str(arguments.get("glob"), "glob", default="*")
        case_sensitive = as_bool(arguments.get("case_sensitive"), "case_sensitive")
        max_results = as_int(
            arguments.get("max_results"), "max_results", default=_DEFAULT_MAX_RESULTS, minimum=1
        )
        max_file_bytes = as_int(
            arguments.get("max_file_bytes"),
            "max_file_bytes",
            default=_DEFAULT_MAX_FILE_BYTES,
            minimum=1,
        )
        matches, scanned, truncated = _search(
            root,
            query=query,
            pattern=pattern,
            case_sensitive=case_sensitive,
            max_results=max_results,
            max_file_bytes=max_file_bytes,
        )
        return {
            "path": str(root),
            "query": query,
            "matches": matches,
            "files_scanned": scanned,
            "truncated": truncated,
        }


def _search(
    root: Path,
    *,
    query: str,
    pattern: str,
    case_sensitive: bool,
    max_results: int,
    max_file_bytes: int,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Return matches, the number of files read, and whether results are capped."""
    needle = query if case_sensitive else query.lower()
    matches: list[dict[str, Any]] = []
    scanned = 0
    for candidate in _iter_files(root, pattern):
        if scanned >= _SCAN_FILE_LIMIT:
            return matches, scanned, True
        text = _read_preview(candidate, max_file_bytes)
        scanned += 1
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            haystack = line if case_sensitive else line.lower()
            if needle not in haystack:
                continue
            if len(matches) >= max_results:
                return matches, scanned, True
            matches.append(
                {"path": str(candidate), "line": number, "text": line[:_TEXT_PREVIEW_CHARS]}
            )
    return matches, scanned, False


def _iter_files(root: Path, pattern: str) -> Iterator[Path]:
    """Yield real file paths under ``root`` that stay inside ``root``."""
    for candidate in sorted(root.glob(f"**/{pattern}")):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not is_within(resolved, root) or not resolved.is_file():
            continue
        yield resolved


def _read_preview(path: Path, limit: int) -> str | None:
    """Read up to ``limit`` bytes as text, or ``None`` if unreadable."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit)
    except OSError:
        return None
    return raw.decode("utf-8", errors="replace")
