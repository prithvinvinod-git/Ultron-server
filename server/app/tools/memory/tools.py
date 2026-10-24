"""Memory tools (T126+): store and recall memories from the agent's namespace.

Two tools expose the memory facade to the agent loop:

* ``memory.store`` — write a fact/episode to long-term/episodic memory.
* ``memory.recall`` — search across layers for relevant context.

Both tools read the per-request :class:`~app.memory.manager.MemoryManager`
from the :func:`~app.observability.logging.memory_context` context variable,
bound by the chat route handler before the loop runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from app.database.models import MemoryLayer, PermissionLevel, VerificationOutcome
from app.observability.logging import get_memory_manager
from app.tools.base import Reversibility, Tool

__all__ = ["RecallMemoryTool", "StoreMemoryTool"]


class _MemoryTool(Tool):
    """Family base: reads the per-request memory manager from context."""

    timeout = 10.0
    reversibility = Reversibility.REVERSIBLE

    def _manager(self) -> Any:
        manager = get_memory_manager()
        if manager is None:
            raise RuntimeError(
                f"{self.name} requires a memory context but none is bound. "
                "Call this tool from a chat route that wraps execution in "
                "memory_context(container.get_memory_manager(session))."
            )
        return manager


class StoreMemoryTool(_MemoryTool):
    """Store a memory in the agent's namespace.

    The tool writes to long-term memory by default; specify ``layer: episodic``
    to record an episode instead. The namespace defaults to the request's user
    namespace (``user:<id>``), so memories are isolated per user unless the
    agent explicitly names another namespace (e.g. a project namespace).
    """

    name = "memory.store"
    description = (
        "Store a fact or episode in memory for later recall. "
        "Use layer 'long_term' for persistent facts, 'episodic' for expiring episodes."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "minLength": 1,
                "maxLength": 8192,
                "description": "The memory content to store.",
            },
            "layer": {
                "type": "string",
                "enum": ["long_term", "episodic"],
                "default": "long_term",
                "description": "Memory layer: 'long_term' (persistent) or 'episodic' (expiring).",
            },
            "importance": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.5,
                "description": "Importance weight 0..1 for ranking.",
            },
            "outcome": {
                "type": "string",
                "description": "Outcome text for episodic memories (ignored for long_term).",
            },
            "namespace": {
                "type": "string",
                "description": "Optional namespace override (defaults to user namespace).",
            },
        },
        "required": ["content"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.SAFE_ACTIONS

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        content = arguments["content"]
        layer_name = arguments.get("layer", "long_term")
        importance = float(arguments.get("importance", 0.5))
        outcome = arguments.get("outcome", "")
        namespace = arguments.get("namespace")

        layer = MemoryLayer.LONG_TERM if layer_name == "long_term" else MemoryLayer.EPISODIC
        manager = self._manager()

        record = await manager.remember(
            content,
            layer=layer,
            importance=importance,
            outcome=outcome,
            namespace=namespace,
        )
        return {
            "stored": True,
            "id": str(record.id),
            "layer": layer.value,
            "namespace": record.namespace,
        }

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        if not isinstance(result, Mapping):
            return VerificationOutcome.FAILED
        if result.get("stored") is True and result.get("id"):
            return VerificationOutcome.SUCCESS
        return VerificationOutcome.FAILED


class RecallMemoryTool(_MemoryTool):
    """Recall memories relevant to a query.

    Searches across long-term, episodic, short-term, and semantic layers (if
    embeddings are configured) and returns a ranked, de-duplicated list. The
    query text drives semantic search; if omitted, the agent's recent context
    is used implicitly via the active namespace's short-term messages.
    """

    name = "memory.recall"
    description = (
        "Search memory for relevant context. Provide a query text to search "
        "semantically, or omit to list recent memories from the active namespace."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Text to search for semantically. Omit to list recent.",
            },
            "namespace": {
                "type": "string",
                "description": "Optional namespace override (defaults to user namespace).",
            },
            "layers": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "long_term",
                        "episodic",
                        "short_term",
                        "semantic",
                        "project",
                    ],
                },
                "description": "Specific layers to search (defaults to all available).",
            },
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 8,
                "description": "Maximum results to return.",
            },
        },
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        query_text = arguments.get("query")
        namespace = arguments.get("namespace")
        layer_names = arguments.get("layers")
        top_k = int(arguments.get("top_k", 8))

        layers = None
        if layer_names:
            layers = tuple(
                MemoryLayer[name.upper()] for name in layer_names
            )

        manager = self._manager()
        records = await manager.recall(
            namespace=namespace,
            text=query_text,
            layers=layers,
            top_k=top_k,
        )

        return {
            "memories": [
                {
                    "id": str(record.id),
                    "namespace": record.namespace,
                    "kind": record.kind,
                    "slug": record.slug,
                    "layer": record.layer.value,
                    "content": record.content,
                    "importance": record.importance,
                    "score": record.score,
                    "created_at": record.created_at.isoformat(),
                }
                for record in records
            ]
        }

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        if not isinstance(result, Mapping):
            return VerificationOutcome.FAILED
        memories = result.get("memories")
        if isinstance(memories, list):
            return VerificationOutcome.SUCCESS
        return VerificationOutcome.FAILED
