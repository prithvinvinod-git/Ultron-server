"""Memory facade and cross-layer retrieval (T126, spec §22 / §66.3).

The five layers are separate classes because they have genuinely different
storage rules -- short-term expires and lives in Redis, episodic expires and is
structured, long-term and project persist, semantic needs vectors. Callers
rarely want "the short-term layer"; they want *the memory of this namespace*.
This facade is that seam: one namespace, one write, and one retrieval that reads
across layers and returns a single ranked list for §66.3's agent context.

**Everything here is namespace-scoped, and the default is the only shortcut.**
Every method takes an optional namespace and falls back to the configured
default. There is deliberately no "search all namespaces" method: the namespace
is the access boundary (§22), and a cross-namespace read is how one project's
context ends up in another's prompt.

**Cross-layer recall is best-effort by layer, strict by namespace.** A layer
that cannot contribute is skipped rather than failing the whole retrieval: with
no embedder, semantic memory is simply absent from the merged result while
long-term and episodic still answer. But a namespace is never widened to
compensate -- the boundary holds even when a layer is missing.

**Ranking is one scale.** Semantic results carry a cosine ``score`` in ``[0, 1]``;
the keyword layers carry their ``importance`` in the same range. Merging them on
one 0..1 axis lets a strongly similar memory outrank a weakly important one
without a magic weighting, and ties fall back to recency. Records are de-duped by
id so a memory reachable through two layers is offered once.
"""

from __future__ import annotations

import uuid

from app.database.models import MemoryLayer
from app.memory import namespaces
from app.memory.episodic import Episode, EpisodicMemory
from app.memory.long_term import LongTermMemory
from app.memory.project import ProjectMemory
from app.memory.semantic import SemanticMemory
from app.memory.short_term import ShortTermMemory
from app.memory.types import MemoryMessage, MemoryRecord, RetrievalQuery


class MemoryManager:
    """The single entry point to a namespace's memory."""

    def __init__(
        self,
        *,
        short_term: ShortTermMemory,
        long_term: LongTermMemory,
        episodic: EpisodicMemory,
        semantic: SemanticMemory,
        project: ProjectMemory,
        default_namespace: str = namespaces.USER,
        retrieval_top_k: int = 8,
        min_similarity: float = 0.25,
    ) -> None:
        self.short_term = short_term
        self.long_term = long_term
        self.episodic = episodic
        self.semantic = semantic
        self.project = project
        self._default_namespace = default_namespace
        self._top_k = retrieval_top_k
        self._min_similarity = min_similarity

    def _namespace(self, namespace: str | None) -> namespaces.Namespace:
        return namespaces.parse(namespace or self._default_namespace)

    # -- writes -----------------------------------------------------------

    async def remember(
        self,
        content: str,
        *,
        namespace: str | None = None,
        layer: MemoryLayer = MemoryLayer.LONG_TERM,
        importance: float = 0.5,
        outcome: str = "",
        project_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        source_event_id: uuid.UUID | None = None,
    ) -> MemoryRecord:
        """Store one memory in the named layer, returning it as a record.

        The layer decides the storage class: :data:`MemoryLayer.LONG_TERM` a
        persistent fact, :data:`MemoryLayer.EPISODIC` an expiring episode (use
        ``outcome`` for its result), :data:`MemoryLayer.SEMANTIC` an embedded
        item, :data:`MemoryLayer.PROJECT` project context (the namespace must be
        a ``project:<slug>`` one). :data:`MemoryLayer.SHORT_TERM` is not a valid
        target here -- a conversation message has a role, so it is written
        through :meth:`append_message` instead.
        """
        parsed = self._namespace(namespace)
        if layer == MemoryLayer.EPISODIC:
            episode = await self.episodic.record(
                content,
                outcome=outcome,
                namespace=parsed.raw,
                importance=importance,
                task_id=task_id,
                agent_id=agent_id,
                source_event_id=source_event_id,
            )
            return _episode_record(episode)
        if layer == MemoryLayer.SEMANTIC:
            return await self.semantic.index(
                content,
                namespace=parsed.raw,
                importance=importance,
                project_id=project_id,
                user_id=user_id,
                agent_id=agent_id,
                task_id=task_id,
                source_event_id=source_event_id,
            )
        if layer == MemoryLayer.PROJECT:
            if not parsed.is_project:
                msg = "project memory needs a 'project:<slug>' namespace"
                raise ValueError(msg)
            return await self.project.remember(
                parsed.slug,
                content,
                importance=importance,
                project_id=project_id,
                user_id=user_id,
                agent_id=agent_id,
                task_id=task_id,
                source_event_id=source_event_id,
            )
        if layer == MemoryLayer.SHORT_TERM:
            msg = "short-term memory is written with append_message, not remember"
            raise ValueError(msg)
        return await self.long_term.remember(
            content,
            namespace=parsed.raw,
            importance=importance,
            user_id=user_id,
            project_id=project_id,
            agent_id=agent_id,
            task_id=task_id,
            source_event_id=source_event_id,
        )

    async def append_message(
        self, role: str, content: str, *, namespace: str | None = None
    ) -> MemoryMessage:
        """Append a conversation message to short-term memory."""
        parsed = self._namespace(namespace)
        return await self.short_term.append(role, content, namespace=parsed.raw)

    async def record_episode(
        self,
        summary: str,
        *,
        outcome: str = "",
        namespace: str | None = None,
        importance: float = 0.5,
        task_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        source_event_id: uuid.UUID | None = None,
    ) -> Episode:
        """Record one episode, the convenience path for task completion."""
        parsed = self._namespace(namespace)
        return await self.episodic.record(
            summary,
            outcome=outcome,
            namespace=parsed.raw,
            importance=importance,
            task_id=task_id,
            agent_id=agent_id,
            source_event_id=source_event_id,
        )

    async def forget(self, memory_id: uuid.UUID | str) -> bool:
        """Delete one memory regardless of layer."""
        return await self.long_term.forget(memory_id)

    # -- reads ------------------------------------------------------------

    async def recent_messages(
        self, *, namespace: str | None = None, limit: int | None = None
    ) -> list[MemoryMessage]:
        """Return the namespace's recent conversation messages."""
        parsed = self._namespace(namespace)
        return await self.short_term.recent(namespace=parsed.raw, limit=limit)

    async def recall(
        self,
        query: RetrievalQuery | None = None,
        *,
        namespace: str | None = None,
        text: str | None = None,
        layers: tuple[MemoryLayer, ...] | None = None,
        top_k: int | None = None,
    ) -> list[MemoryRecord]:
        """Read across layers and return one ranked, de-duplicated list."""
        effective = query or RetrievalQuery(namespace=namespace or self._default_namespace)
        parsed = self._namespace(effective.namespace)
        wanted = layers or effective.layers
        limit = top_k or effective.top_k or self._top_k
        question = text if text is not None else effective.text

        collected: list[MemoryRecord] = []
        for layer in wanted:
            collected.extend(await self._recall_layer(layer, parsed, question, limit))

        seen: set[uuid.UUID] = set()
        unique: list[MemoryRecord] = []
        for record in collected:
            if record.id in seen:
                continue
            seen.add(record.id)
            unique.append(record)
        unique.sort(key=_rank_key, reverse=True)
        return unique[:limit]

    async def _recall_layer(
        self,
        layer: MemoryLayer,
        parsed: namespaces.Namespace,
        text: str | None,
        limit: int,
    ) -> list[MemoryRecord]:
        """Contribute one layer's records, or nothing if it cannot."""
        if layer == MemoryLayer.LONG_TERM:
            return await self.long_term.recall(namespace=parsed.raw, limit=limit)
        if layer == MemoryLayer.EPISODIC:
            episodes = await self.episodic.recent(namespace=parsed.raw, limit=limit)
            return [_episode_record(episode) for episode in episodes]
        if layer == MemoryLayer.SHORT_TERM:
            messages = await self.short_term.recent(namespace=parsed.raw, limit=limit)
            return [_message_record(message, parsed.raw) for message in messages]
        if layer == MemoryLayer.SEMANTIC:
            if text and self.semantic.available:
                return await self.semantic.search(
                    text,
                    namespace=parsed.raw,
                    top_k=limit,
                    min_similarity=self._min_similarity,
                )
            return []
        if layer == MemoryLayer.PROJECT and parsed.is_project:
            return await self.project.recall(parsed.slug, limit=limit)
        return []

    async def build_context(
        self,
        *,
        namespace: str | None = None,
        query: str | None = None,
        limit: int | None = None,
    ) -> list[str]:
        """Return the recall result as prompt-ready lines (§66.3)."""
        records = await self.recall(namespace=namespace, text=query, top_k=limit)
        return [record.to_prompt_line() for record in records]


def _rank_key(record: MemoryRecord) -> tuple[float, float]:
    """Sort key: the record's score (or importance), then recency."""
    score = record.score if record.score is not None else record.importance
    return (score, record.created_at.timestamp())


def _episode_record(episode: Episode) -> MemoryRecord:
    """Wrap an episode as a record so it can be merged into retrieval.

    The stored content is a summary/outcome envelope; the record exposes the
    human-readable summary (the episode's :class:`Episode` already parsed it).
    """
    kind, _, slug = episode.namespace.partition(":")
    return MemoryRecord(
        id=episode.id,
        namespace=episode.namespace,
        kind=kind,
        slug=slug,
        layer=MemoryLayer.EPISODIC,
        content=episode.summary,
        importance=episode.importance,
        created_at=episode.created_at,
        expires_at=episode.expires_at,
        task_id=episode.task_id,
        agent_id=episode.agent_id,
    )


def _message_record(message: MemoryMessage, namespace: str) -> MemoryRecord:
    """Wrap a short-term message as a record so it can be merged into retrieval."""
    kind, _, slug = namespace.partition(":")
    return MemoryRecord(
        id=uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{namespace}:{message.created_at.isoformat()}:{message.content}",
        ),
        namespace=namespace,
        kind=kind,
        slug=slug,
        layer=MemoryLayer.SHORT_TERM,
        content=f"{message.role}: {message.content}",
        importance=0.4,
        created_at=message.created_at,
    )


__all__ = ["MemoryManager"]
