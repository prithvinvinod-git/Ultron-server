"""The memory tool family (T126+, spec §22/§66.3).

Two tools expose the memory facade to the agent loop:

* ``memory.store`` — write a fact/episode to long-term/episodic memory.
* ``memory.recall`` — search across layers for relevant context.

Both tools read the per-request :class:`~app.memory.manager.MemoryManager`
from the :func:`~app.observability.logging.memory_context` context variable,
bound by the chat route handler before the loop runs. No construction arguments
are needed; the tools are stateless at registration time.
"""

from __future__ import annotations

from app.tools.base import Tool
from app.tools.memory.tools import RecallMemoryTool, StoreMemoryTool

__all__ = ["build_memory_tools"]


def build_memory_tools() -> tuple[Tool, ...]:
    """Construct every memory tool."""
    return (
        StoreMemoryTool(),
        RecallMemoryTool(),
    )
