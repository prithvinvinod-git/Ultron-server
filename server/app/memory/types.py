"""Domain types for the memory system (T120, spec §22).

The ORM model (:class:`app.database.models.memories.Memory`) is a storage
detail: it carries an ``embedding`` column whose type depends on whether
pgvector is installed, an ``is_expired`` helper that has to guess at naive
datetimes, and a pile of foreign keys most callers ignore. Handing it straight
to an agent couples every caller to that shape, so the application layer works
in terms of :class:`MemoryRecord` instead and the model stays behind the
repository.

**``MemoryRecord`` is frozen.** A retrieved memory is a fact about the past: it
has an id, a layer, a creation time. Mutating one in place would make a cached
or logged copy disagree with the store, and there is no write path that goes
through a record -- writes go through the layer classes and the repository. So
the record is read-only, and :attr:`MemoryRecord.score` (the retrieval rank) is
``None`` for a record that was stored or listed rather than searched.

**Expiry is computed from the timestamp, not read off the model.** The model's
``is_expired`` exists too, but duplicating the rule here in three lines is
cheaper than importing the ORM into callers that only hold records. Both treat
a naive datetime as UTC, for the same reason: a value written by a driver that
dropped the offset should not become immortal.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.database.models import Memory, MemoryLayer

#: Layers a cross-layer retrieval walks, in priority order. A memory that can be
#: both long-term and semantic is stored once and reached by the layer it was
#: written as; the order here only affects how ties are broken when several
#: layers contribute to one context.
DEFAULT_LAYERS: tuple[MemoryLayer, ...] = (
    MemoryLayer.LONG_TERM,
    MemoryLayer.EPISODIC,
    MemoryLayer.SEMANTIC,
    MemoryLayer.PROJECT,
    MemoryLayer.SHORT_TERM,
)


def _as_utc(value: datetime | None) -> datetime | None:
    """Return ``value`` as timezone-aware UTC, or ``None``."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """One remembered item, decoupled from the ORM."""

    id: uuid.UUID
    namespace: str
    kind: str
    slug: str
    layer: MemoryLayer
    content: str
    importance: float
    created_at: datetime
    expires_at: datetime | None = None
    access_count: int = 0
    last_accessed_at: datetime | None = None
    user_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    source_event_id: uuid.UUID | None = None
    score: float | None = None

    @classmethod
    def from_model(cls, memory: Memory, *, score: float | None = None) -> MemoryRecord:
        """Build a record from an ORM row, optionally attaching a retrieval score."""
        return cls(
            id=memory.id,
            namespace=memory.namespace,
            kind=memory.namespace_kind,
            slug=memory.namespace_slug,
            layer=MemoryLayer(memory.layer),
            content=memory.content,
            importance=float(memory.importance),
            created_at=memory.created_at,
            expires_at=memory.expires_at,
            access_count=int(memory.access_count),
            last_accessed_at=memory.last_accessed_at,
            user_id=memory.user_id,
            project_id=memory.project_id,
            agent_id=memory.agent_id,
            task_id=memory.task_id,
            source_event_id=memory.source_event_id,
            score=score,
        )

    @property
    def is_expired(self) -> bool:
        """Whether the record's expiry has passed (naive datetimes are UTC)."""
        expires = _as_utc(self.expires_at)
        if expires is None:
            return False
        return expires <= datetime.now(UTC)

    def to_prompt_line(self) -> str:
        """Render the record as one line of agent context."""
        return f"[{self.layer}] {self.content}"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly view, for API responses and events."""
        return {
            "id": str(self.id),
            "namespace": self.namespace,
            "layer": str(self.layer),
            "content": self.content,
            "importance": self.importance,
            "created_at": self.created_at.isoformat(),
            "score": self.score,
        }


@dataclass(frozen=True, slots=True)
class MemoryMessage:
    """One turn in short-term (conversation) memory."""

    role: str
    content: str
    created_at: datetime

    @classmethod
    def now(cls, role: str, content: str) -> MemoryMessage:
        """Build a message stamped at the current instant."""
        return cls(role=role, content=content, created_at=datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly view (the Redis wire form)."""
        return {
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> MemoryMessage:
        """Rebuild a message from its wire form."""
        created = payload.get("created_at")
        parsed = datetime.fromisoformat(created) if isinstance(created, str) else None
        return cls(
            role=str(payload.get("role", "")),
            content=str(payload.get("content", "")),
            created_at=_as_utc(parsed) or datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    """A cross-layer retrieval request.

    ``text`` is what a semantic search ranks against; the keyword layers
    (long-term, episodic, project) ignore it and return their most important
    recent memories. ``layers`` defaults to :data:`DEFAULT_LAYERS`.
    """

    namespace: str
    text: str | None = None
    layers: tuple[MemoryLayer, ...] = DEFAULT_LAYERS
    top_k: int | None = None
    min_similarity: float | None = None


__all__ = ["DEFAULT_LAYERS", "MemoryMessage", "MemoryRecord", "RetrievalQuery"]
