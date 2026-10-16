"""Semantic memory: embeddings and vector retrieval (T124, spec §22 / §66.13).

§22's fourth layer is *"Embeddings/vector retrieval"* (line 1146). §66.13 fixes
the store: **one** vector database, PostgreSQL + pgvector, on
``memories.embedding`` (no second vector store, decision D030), written through
the T127 provider abstraction.

**Two honest states, not one broken one.** Semantic memory is either *working*
(an embedder is available and vectors are indexed) or *unavailable* (no embedder
-- ``EMBEDDING_PROVIDER=none`` is a supported setting). When unavailable,
:meth:`SemanticMemory.search` and :meth:`SemanticMemory.index` raise
:class:`~app.core.errors.DependencyUnavailableError`. They never return an empty
list, because an empty list means "nothing matched" and would be
indistinguishable from "there is no semantic search here" -- the first is a
result, the second is a missing capability, and conflating them is how a
deployment runs for months believing retrieval is empty rather than absent.

**Ranking is a bounded, in-Python scan.** Retrieval embeds the query, loads a
bounded candidate set of the namespace's semantic rows, and scores by cosine
similarity. This is deliberate for the deployment this runs on: pgvector is
excluded by policy and absent from the stock Windows PostgreSQL build
(``ENABLE_PGVECTOR=false``), and the alternative -- an ``ORDER BY random()``
stand-in -- would look like retrieval and return unrelated neighbours. A
bounded scan over one namespace is honest: it returns the true nearest
neighbours *within the candidates it examined*, and the candidate cap is stated
rather than hidden. When pgvector is present the same rows can be ranked by an
indexed query without changing this interface.

**Cosine, not distance, and a floor.** Similarity is cosine (independent of
vector magnitude) and results below ``min_similarity`` are dropped, so a weak
match is not offered as a strong one. A vector whose width does not match the
configured dimensions is skipped, not truncated -- a silently shortened vector
would score against the wrong axes.
"""

from __future__ import annotations

import json
import math
import struct
import uuid
from collections.abc import Sequence
from typing import Any

from app.core.errors import DependencyUnavailableError
from app.database.models import Memory, MemoryLayer
from app.database.repositories.memories import MemoryRepository
from app.memory import namespaces
from app.memory.embeddings import EmbeddingProvider
from app.memory.types import MemoryRecord

#: How many candidates to score per requested result. Generous enough that a
#: small namespace is fully scanned, bounded so a large one cannot turn one
#: search into an unbounded read.
_CANDIDATE_FACTOR = 20

#: Hard ceiling on candidates, well under the repository's 1000-row limit.
_CANDIDATE_CEILING = 500


def _coerce_vector(value: Any) -> list[float] | None:
    """Return an ORM embedding as a list of floats, or ``None`` if unusable.

    The value arrives in whatever shape the active column used: a plain list
    from pgvector's adapter, a JSON string from the JSONB fallback, or a raw
    buffer from a driver that handed back bytes. Anything unrecognised returns
    ``None`` so the caller skips the row rather than scoring garbage.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if isinstance(value, memoryview | bytearray | bytes):
        raw = bytes(value)
        if len(raw) % 4 != 0:
            return None
        return [item[0] for item in struct.iter_unpack("<f", raw)]
    if isinstance(value, Sequence):
        try:
            return [float(item) for item in value]
        except (TypeError, ValueError):
            return None
    return None


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two equal-length vectors, or ``-1.0`` if they differ."""
    if len(a) != len(b) or not a:
        return -1.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return -1.0
    return dot / (norm_a * norm_b)


class SemanticMemory:
    """Namespaced embeddings with cosine retrieval."""

    def __init__(
        self,
        repository: MemoryRepository,
        embedder: EmbeddingProvider,
        *,
        min_similarity: float = 0.25,
        enabled: bool = True,
        default_namespace: str = namespaces.USER,
    ) -> None:
        self._repository = repository
        self._embedder = embedder
        self._min_similarity = min_similarity
        self._enabled = enabled
        self._default_namespace = default_namespace

    @property
    def available(self) -> bool:
        """Whether semantic retrieval can run (enabled *and* an embedder exists)."""
        return self._enabled and self._embedder.available

    @property
    def is_fallback(self) -> bool:
        """Whether the active embedder is a non-semantic stand-in (T127)."""
        return self._embedder.is_fallback

    @property
    def provider(self) -> str:
        """The embedder's short name, for logs and health."""
        return self._embedder.name

    def _require_available(self) -> None:
        if not self._enabled:
            raise DependencyUnavailableError("embeddings", reason="semantic memory is disabled")
        if not self._embedder.available:
            raise DependencyUnavailableError(
                "embeddings", reason="no embedding provider is available"
            )

    def _namespace(self, namespace: str | None) -> namespaces.Namespace:
        return namespaces.parse(namespace or self._default_namespace)

    async def index(
        self,
        content: str,
        *,
        namespace: str | None = None,
        importance: float = 0.5,
        project_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        source_event_id: uuid.UUID | None = None,
    ) -> MemoryRecord:
        """Embed ``content`` and store it as a semantic memory."""
        self._require_available()
        parsed = self._namespace(namespace)
        vector = await self._embedder.embed_one(content)
        memory = await self._repository.remember(
            namespace=parsed.raw,
            namespace_kind=parsed.kind,
            namespace_slug=parsed.slug,
            content=content,
            layer=MemoryLayer.SEMANTIC,
            importance=importance,
            project_id=project_id,
            user_id=user_id,
            agent_id=agent_id,
            task_id=task_id,
            source_event_id=source_event_id,
            embedding=vector,
        )
        return MemoryRecord.from_model(memory)

    async def search(
        self,
        query: str,
        *,
        namespace: str | None = None,
        top_k: int = 8,
        min_similarity: float | None = None,
    ) -> list[MemoryRecord]:
        """Return the ``top_k`` most similar memories in the namespace."""
        self._require_available()
        parsed = self._namespace(namespace)
        floor = self._min_similarity if min_similarity is None else min_similarity
        query_vector = await self._embedder.embed_one(query)

        candidates = await self._repository.list_by_layer(
            parsed.raw,
            MemoryLayer.SEMANTIC,
            limit=min(max(top_k, 1) * _CANDIDATE_FACTOR, _CANDIDATE_CEILING),
        )
        scored: list[MemoryRecord] = []
        for memory in candidates:
            score = self._score(query_vector, memory)
            if score is not None and score >= floor:
                scored.append(MemoryRecord.from_model(memory, score=score))
        scored.sort(key=lambda record: record.score or 0.0, reverse=True)
        return scored[:top_k]

    def _score(self, query_vector: Sequence[float], memory: Memory) -> float | None:
        """Cosine similarity for one row, or ``None`` if it has no usable vector."""
        recorded = memory.vector_dimensions
        if recorded is not None and recorded != self._embedder.dimensions:
            return None
        vector = _coerce_vector(memory.embedding)
        if vector is None:
            return None
        return _cosine(query_vector, vector)

    async def forget(self, memory_id: uuid.UUID | str) -> bool:
        """Delete one semantic memory, returning whether anything was removed."""
        return await self._repository.delete(memory_id)

    async def clear(self, *, namespace: str | None = None) -> int:
        """Delete every semantic memory in a namespace, returning how many."""
        parsed = self._namespace(namespace)
        return await self._repository.clear_layer(parsed.raw, MemoryLayer.SEMANTIC)


__all__ = ["SemanticMemory"]
