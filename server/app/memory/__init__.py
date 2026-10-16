"""Memory system: short-term, long-term, episodic, semantic and project memory.

Spec §22 defines five layers over one namespaced store; §66.12-§66.13 extend it
preference/task/device/agent memory as *scopes on these same layers* (not a
second store) and knowledge ingestion into the same pgvector column. This
package is those layers plus the facade that presents them as one memory.

The public surface is deliberately small: :class:`MemoryManager` for callers
that want "the memory of a namespace", the five layer classes for callers that
want one layer, the embedding seam (:class:`EmbeddingProvider` and
:func:`create_embedding_provider`), and the namespace rules.
"""

from __future__ import annotations

from app.memory import namespaces
from app.memory.embeddings import (
    EmbeddingProvider,
    HashEmbeddingProvider,
    NullEmbeddingProvider,
    OllamaEmbeddingProvider,
    OpenAIEmbeddingProvider,
    create_embedding_provider,
)
from app.memory.episodic import Episode, EpisodicMemory
from app.memory.long_term import LongTermMemory
from app.memory.manager import MemoryManager
from app.memory.project import ProjectMemory
from app.memory.recorder import MemoryRecorder
from app.memory.semantic import SemanticMemory
from app.memory.short_term import ShortTermMemory
from app.memory.types import MemoryMessage, MemoryRecord, RetrievalQuery

__all__ = [
    "EmbeddingProvider",
    "Episode",
    "EpisodicMemory",
    "HashEmbeddingProvider",
    "LongTermMemory",
    "MemoryManager",
    "MemoryMessage",
    "MemoryRecord",
    "MemoryRecorder",
    "NullEmbeddingProvider",
    "OllamaEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "ProjectMemory",
    "RetrievalQuery",
    "SemanticMemory",
    "ShortTermMemory",
    "create_embedding_provider",
    "namespaces",
]
