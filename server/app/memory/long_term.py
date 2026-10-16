"""Long-term memory: persistent user/project facts (T122, spec §22).

§22's second layer is *"Persistent user/project facts"* (line 1138). What makes
it long-term is the absence of an expiry: a fact about a user or a project is
meant to outlive the conversation that produced it, so unlike short-term memory
(line 1134) nothing here is given a TTL.

**Recall is importance-ordered, not recency-ordered.** The store competes for
one table, and §22 explicitly separates "facts" from "what ULTRON did
previously" (episodic, line 1142). A fact's *when* is not what makes it worth
recalling -- a stable preference should not fall behind a trivial recent note --
so :meth:`LongTermMemory.recall` orders by ``importance`` first, newest within a
tie. The repository already provides exactly that ordering in
:meth:`MemoryRepository.list_by_layer`.

**Recall records the read.** ``access_count`` / ``last_accessed_at`` are
advanced for everything returned, so a later pruning pass can tell a fact that
keeps being used from one written once and never read. The write is a single
``UPDATE`` per row (never load-then-write), so two concurrent readers cannot
lose each other's count.
"""

from __future__ import annotations

import uuid

from app.database.models import MemoryLayer
from app.database.repositories.memories import MemoryRepository
from app.memory import namespaces
from app.memory.types import MemoryRecord


class LongTermMemory:
    """Namespaced, non-expiring facts."""

    def __init__(
        self, repository: MemoryRepository, *, default_namespace: str = namespaces.USER
    ) -> None:
        self._repository = repository
        self._default_namespace = default_namespace

    def _namespace(self, namespace: str | None) -> str:
        return namespaces.parse(namespace or self._default_namespace).raw

    async def remember(
        self,
        content: str,
        *,
        namespace: str | None = None,
        importance: float = 0.5,
        user_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        source_event_id: uuid.UUID | None = None,
    ) -> MemoryRecord:
        """Persist one fact and return it as a record."""
        parsed = namespaces.parse(namespace or self._default_namespace)
        memory = await self._repository.remember(
            namespace=parsed.raw,
            namespace_kind=parsed.kind,
            namespace_slug=parsed.slug,
            content=content,
            layer=MemoryLayer.LONG_TERM,
            importance=importance,
            user_id=user_id,
            project_id=project_id,
            agent_id=agent_id,
            task_id=task_id,
            source_event_id=source_event_id,
        )
        return MemoryRecord.from_model(memory)

    async def recall(
        self,
        *,
        namespace: str | None = None,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        """Return a namespace's facts, most important first, recording the read."""
        parsed = namespaces.parse(namespace or self._default_namespace)
        memories = await self._repository.list_by_layer(
            parsed.raw, MemoryLayer.LONG_TERM, limit=limit
        )
        for memory in memories:
            await self._repository.touch(memory.id)
        return [MemoryRecord.from_model(memory) for memory in memories]

    async def forget(self, memory_id: uuid.UUID | str) -> bool:
        """Delete one fact, returning whether anything was removed."""
        return await self._repository.delete(memory_id)

    async def clear(self, *, namespace: str | None = None) -> int:
        """Delete every long-term fact in a namespace, returning how many."""
        parsed = namespaces.parse(namespace or self._default_namespace)
        return await self._repository.clear_layer(parsed.raw, MemoryLayer.LONG_TERM)


__all__ = ["LongTermMemory"]
