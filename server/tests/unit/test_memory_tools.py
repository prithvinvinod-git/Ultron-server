"""Memory tools (T126+): ``memory.store`` and ``memory.recall``.

Two seams are exercised: the tool reads the per-request manager from the
:func:`~app.observability.logging.memory_context` context variable, and it maps
its arguments onto the facade's ``remember``/``recall`` calls. A fake manager
records what it was asked, so the suite pins the argument translation without a
database or Redis.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from app.database.models import MemoryLayer, VerificationOutcome
from app.memory.types import MemoryRecord
from app.observability.logging import memory_context
from app.tools.memory import build_memory_tools
from app.tools.memory.tools import RecallMemoryTool, StoreMemoryTool

pytestmark = pytest.mark.unit


def _record(content: str, *, layer: MemoryLayer = MemoryLayer.LONG_TERM) -> MemoryRecord:
    return MemoryRecord(
        id=uuid4(),
        namespace="user:test",
        kind="user",
        slug="test",
        layer=layer,
        content=content,
        importance=0.5,
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
    )


class _FakeManager:
    """Records ``remember``/``recall`` calls and returns canned records."""

    def __init__(self) -> None:
        self.remembered: list[dict[str, Any]] = []
        self.recalled: list[dict[str, Any]] = []
        self.records: list[MemoryRecord] = [_record("stored fact")]

    async def remember(self, content: str, **kwargs: Any) -> MemoryRecord:
        self.remembered.append({"content": content, **kwargs})
        return _record(content, layer=kwargs.get("layer", MemoryLayer.LONG_TERM))

    async def recall(self, **kwargs: Any) -> list[MemoryRecord]:
        self.recalled.append(kwargs)
        return self.records


def test_build_memory_tools_exposes_the_two_memory_tools() -> None:
    names = {tool.name for tool in build_memory_tools()}
    assert names == {"memory.store", "memory.recall"}


async def test_store_maps_arguments_and_reports_success() -> None:
    manager = _FakeManager()
    with memory_context(manager):
        result = await StoreMemoryTool().execute(
            {"content": "the answer is 42", "layer": "long_term", "importance": 0.9}
        )

    assert manager.remembered[0]["content"] == "the answer is 42"
    assert manager.remembered[0]["layer"] is MemoryLayer.LONG_TERM
    assert manager.remembered[0]["importance"] == 0.9
    assert result["stored"] is True
    assert result["layer"] == "long_term"


async def test_store_defaults_to_long_term() -> None:
    manager = _FakeManager()
    with memory_context(manager):
        await StoreMemoryTool().execute({"content": "x"})
    assert manager.remembered[0]["layer"] is MemoryLayer.LONG_TERM


async def test_store_can_target_episodic_memory() -> None:
    manager = _FakeManager()
    with memory_context(manager):
        await StoreMemoryTool().execute(
            {"content": "deploy finished", "layer": "episodic", "outcome": "ok"}
        )
    assert manager.remembered[0]["layer"] is MemoryLayer.EPISODIC
    assert manager.remembered[0]["outcome"] == "ok"


async def test_store_verify_reports_success() -> None:
    manager = _FakeManager()
    with memory_context(manager):
        tool = StoreMemoryTool()
        result = await tool.execute({"content": "x"})
        outcome = await tool.verify(result, {"content": "x"})
    assert outcome is VerificationOutcome.SUCCESS


async def test_recall_passes_the_query_and_returns_memories() -> None:
    manager = _FakeManager()
    with memory_context(manager):
        result = await RecallMemoryTool().execute({"query": "what is the answer", "top_k": 3})

    assert manager.recalled[0]["text"] == "what is the answer"
    assert manager.recalled[0]["top_k"] == 3
    assert [memory["content"] for memory in result["memories"]] == ["stored fact"]
    assert result["memories"][0]["layer"] == "long_term"


async def test_recall_can_narrow_to_named_layers() -> None:
    manager = _FakeManager()
    with memory_context(manager):
        await RecallMemoryTool().execute({"layers": ["episodic"]})
    assert manager.recalled[0]["layers"] == (MemoryLayer.EPISODIC,)


async def test_a_tool_without_a_bound_context_refuses() -> None:
    with pytest.raises(RuntimeError, match="memory context"):
        await StoreMemoryTool().execute({"content": "x"})


async def test_recall_verify_reports_success() -> None:
    manager = _FakeManager()
    with memory_context(manager):
        tool = RecallMemoryTool()
        result = await tool.execute({"query": "x"})
        outcome = await tool.verify(result, {"query": "x"})
    assert outcome is VerificationOutcome.SUCCESS
