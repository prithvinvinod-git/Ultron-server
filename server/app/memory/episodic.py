"""Episodic memory: what ULTRON did previously (T123, spec §22).

§22's third layer is *"What ULTRON did previously"* (line 1142) -- a record of
accomplished work, not of facts about the world. The distinction matters for two
reasons. First, an episode is inherently *bounded*: recalling what ULTRON did in
2019 is not useful, so episodes carry a retention window
(``settings.memory.episodic_retention_days``) and expire, unlike long-term
facts. Second, an episode is *structured*: "ran the test suite, it failed" is
useful to a later review in a way the raw sentence is not, so the summary and
the outcome are stored together as one self-describing JSON document rather than
flattened into a sentence that would have to be re-parsed.

**Retention is a timestamp, not a sweeper.** An episode is written with
``expires_at`` set; the read path filters expired rows in SQL
(:meth:`MemoryRepository.list_live`) and :meth:`EpisodicMemory.purge_expired`
reclaims them. Nothing depends on a background job having run, so an episode is
never *visible* past its retention even if its row has not yet been deleted --
the deletion is housekeeping, not correctness.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.database.models import MemoryLayer
from app.database.repositories.memories import MemoryRepository
from app.memory import namespaces
from app.memory.types import MemoryRecord


@dataclass(frozen=True, slots=True)
class Episode:
    """One thing ULTRON did."""

    id: uuid.UUID
    namespace: str
    summary: str
    outcome: str
    created_at: datetime
    importance: float
    task_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    expires_at: datetime | None = None

    @classmethod
    def from_record(cls, record: MemoryRecord) -> Episode:
        """Rebuild an episode from the JSON envelope stored as its content."""
        summary, outcome = _decode(record.content)
        return cls(
            id=record.id,
            namespace=record.namespace,
            summary=summary,
            outcome=outcome,
            created_at=record.created_at,
            importance=record.importance,
            task_id=record.task_id,
            agent_id=record.agent_id,
            expires_at=record.expires_at,
        )


def _encode(summary: str, outcome: str) -> str:
    """Serialise the summary/outcome pair as the memory's text content."""
    return json.dumps({"summary": summary, "outcome": outcome}, separators=(",", ":"))


def _decode(content: str) -> tuple[str, str]:
    """Recover ``(summary, outcome)`` from stored content.

    Content that is not the expected envelope -- a row written by an older
    version, or by hand -- is returned whole as the summary with no outcome,
    rather than being dropped or raising. Losing the text would be worse than
    losing the structure.
    """
    try:
        payload = json.loads(content)
    except ValueError:
        return content, ""
    if not isinstance(payload, dict):
        return content, ""
    summary = payload.get("summary")
    outcome = payload.get("outcome")
    return (
        summary if isinstance(summary, str) else content,
        outcome if isinstance(outcome, str) else "",
    )


class EpisodicMemory:
    """Namespaced, expiring record of accomplished work."""

    def __init__(
        self,
        repository: MemoryRepository,
        *,
        retention_days: int = 180,
        default_namespace: str = namespaces.USER,
    ) -> None:
        if retention_days < 1:
            msg = "episodic retention_days must be positive"
            raise ValueError(msg)
        self._repository = repository
        self._retention_days = retention_days
        self._default_namespace = default_namespace

    def _namespace(self, namespace: str | None) -> namespaces.Namespace:
        return namespaces.parse(namespace or self._default_namespace)

    def _expiry(self, now: datetime | None = None) -> datetime:
        stamp = _as_utc(now) or datetime.now(UTC)
        return stamp + timedelta(days=self._retention_days)

    async def record(
        self,
        summary: str,
        *,
        outcome: str = "",
        namespace: str | None = None,
        importance: float = 0.5,
        task_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        source_event_id: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> Episode:
        """Write one episode and return it."""
        parsed = self._namespace(namespace)
        memory = await self._repository.remember(
            namespace=parsed.raw,
            namespace_kind=parsed.kind,
            namespace_slug=parsed.slug,
            content=_encode(summary, outcome),
            layer=MemoryLayer.EPISODIC,
            importance=importance,
            task_id=task_id,
            agent_id=agent_id,
            source_event_id=source_event_id,
            expires_at=self._expiry(now),
        )
        return Episode.from_record(MemoryRecord.from_model(memory))

    async def recent(
        self,
        *,
        namespace: str | None = None,
        limit: int | None = None,
        now: datetime | None = None,
    ) -> list[Episode]:
        """Return unexpired episodes, newest first."""
        parsed = self._namespace(namespace)
        memories = await self._repository.list_live(parsed.raw, now=now, limit=limit)
        records = [MemoryRecord.from_model(memory) for memory in memories]
        return [
            Episode.from_record(record)
            for record in records
            if record.layer == MemoryLayer.EPISODIC
        ]

    async def clear(self, *, namespace: str | None = None) -> int:
        """Delete every episode in a namespace, returning how many."""
        parsed = self._namespace(namespace)
        return await self._repository.clear_layer(parsed.raw, MemoryLayer.EPISODIC)

    async def purge_expired(self, *, now: datetime | None = None) -> int:
        """Delete expired memories, returning how many."""
        return await self._repository.purge_expired(now=now)


def _as_utc(value: datetime | None) -> datetime | None:
    """Return ``value`` as UTC, treating a naive datetime as already UTC."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


__all__ = ["Episode", "EpisodicMemory"]
