"""Research artifact persistence (T115): reports stored as first-class files.

§59.10 requires that "reports are structured and stored as a first-class
artifact, so a later review can distinguish what was retrieved from what was
concluded". §67.23 forbids inventing a storage system to do it: ULTRON already
keeps durable outputs on disk under ``data/`` (browser screenshots and downloads,
T112). This store follows the same convention — one report is one pretty-printed
JSON file under the configured directory, named by a generated, sanitized id.

Two rules it inherits from the toolbox (§59.4, "resolve before you trust"):

- **The id is generated, never supplied by a caller.** ``save`` mints the id, so
  a request cannot choose where a file lands. ``load`` re-validates the id it is
  given and refuses anything with path separators or traversal, so a crafted id
  cannot read outside the directory.
- **Writes are atomic.** The payload is written to a temp file and then
  ``replace``d onto the target, so a crash never leaves a half-written artifact
  that a later review would read as complete.

The store is deliberately small and synchronous-under-async: file I/O runs on a
worker thread (``asyncio.to_thread``), exactly as the browser tools write their
artifacts, so the event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.errors import InvalidInputError, NotFoundError

from .report import ResearchReport

__all__ = ["ResearchArtifactStore"]

#: Characters an artifact id may contain. Generated ids are alphanumeric with
#: dashes, so anything else (notably ``/`` and ``\``) is a caller error.
_ALLOWED_ID = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._")


class ResearchArtifactStore:
    """Persist and retrieve research reports as JSON files under ``root``."""

    def __init__(self, root: str | Path) -> None:
        """Bind the store to a directory.

        ``root`` is created lazily on the first ``save`` (the same behaviour as
        the browser artifact directories), so a configured-but-unused store
        never litters the filesystem.
        """
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    async def save(self, report: ResearchReport) -> str:
        """Write ``report`` as one artifact and return its generated id.

        The stored document is the report's serialised form (sources, answer,
        citations, unsupported count) plus the artifact id and a storage
        timestamp. The id is a UTC timestamp plus a short random suffix, mirroring
        the browser artifact naming — reviewable and collision-free.
        """
        artifact_id = _new_id()
        document = report.to_dict()
        document["artifact_id"] = artifact_id
        document["stored_at"] = datetime.now(UTC).isoformat()
        payload = json.dumps(document, ensure_ascii=False, indent=2)
        target = self.path_for(artifact_id)
        await asyncio.to_thread(self._write, target, payload)
        return artifact_id

    async def load(self, artifact_id: str) -> dict[str, Any]:
        """Read one artifact back, raising ``NotFoundError`` if it is absent."""
        target = self.path_for(artifact_id)
        try:
            raw = await asyncio.to_thread(target.read_text, encoding="utf-8")
        except FileNotFoundError as exc:
            raise NotFoundError("research artifact", artifact_id) from exc
        loaded = json.loads(raw)
        if not isinstance(loaded, dict):
            raise InvalidInputError(
                "a research artifact must be a JSON object",
                details={"artifact_id": artifact_id},
            )
        return loaded

    def path_for(self, artifact_id: str) -> Path:
        """The file a (validated) id maps to, refusing traversal (§59.4)."""
        if not artifact_id or set(artifact_id) - _ALLOWED_ID:
            raise InvalidInputError(
                "an artifact id may contain only letters, digits, '.', '_' and '-'",
                details={"artifact_id": str(artifact_id)[:200]},
            )
        return self._root / f"{artifact_id}.json"

    @staticmethod
    def _write(target: Path, payload: str) -> None:
        """Create the directory and write ``payload`` atomically (worker thread)."""
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f"{target.name}.tmp")
        temp.write_text(payload, encoding="utf-8")
        temp.replace(target)


def _new_id() -> str:
    """A timestamped, collision-free artifact id (same shape as T112's names)."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"research-{stamp}-{uuid.uuid4().hex[:8]}"
