"""``_system_with_memory``: recall becomes a prompt block, best-effort.

The helper is the seam between memory and the chat prompt, so the suite pins its
two promises: recalled lines are rendered into a ``# Relevant memory`` block with
the caller's own system text kept last, and any failure to recall is swallowed
(the chat must not fail because memory is down).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.api.routes.chat import _system_with_memory

pytestmark = pytest.mark.unit


class _Container:
    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def get_memory_manager(self, session: Any) -> Any:
        return self._manager


class _Memory:
    def __init__(self, *, lines: list[str] | None = None, boom: bool = False) -> None:
        self.lines = lines or []
        self.boom = boom
        self.context_calls: list[dict[str, Any]] = []

    async def build_context(self, **kwargs: Any) -> list[str]:
        self.context_calls.append(kwargs)
        if self.boom:
            raise RuntimeError("redis is down")
        return self.lines


async def test_no_recall_returns_the_caller_system_unchanged() -> None:
    memory = _Memory(lines=[])
    result = await _system_with_memory(
        _Container(memory), None, message="hi", system="be terse"
    )
    assert result == "be terse"


async def test_no_recall_and_no_system_returns_none() -> None:
    result = await _system_with_memory(
        _Container(_Memory(lines=[])), None, message="hi", system=None
    )
    assert result is None


async def test_recalled_lines_become_a_memory_block() -> None:
    memory = _Memory(lines=["user likes Python", "deploys on Fridays"])
    result = await _system_with_memory(
        _Container(memory), None, message="what do I like", system=None
    )
    assert result is not None
    assert result.startswith("# Relevant memory")
    assert "- user likes Python" in result
    assert "- deploys on Fridays" in result


async def test_explicit_system_text_outranks_memory_by_coming_last() -> None:
    memory = _Memory(lines=["user likes Python"])
    result = await _system_with_memory(
        _Container(memory), None, message="q", system="ALWAYS answer in French"
    )
    assert result is not None
    assert result.endswith("ALWAYS answer in French")
    assert result.index("# Relevant memory") < result.index("ALWAYS answer in French")


async def test_recall_is_keyed_by_the_user_message() -> None:
    memory = _Memory(lines=["x"])
    await _system_with_memory(
        _Container(memory), None, message="where did I put the keys", system=None
    )
    assert memory.context_calls[0]["query"] == "where did I put the keys"


async def test_a_memory_failure_never_fails_the_turn() -> None:
    memory = _Memory(lines=["unused"], boom=True)
    result = await _system_with_memory(
        _Container(memory), None, message="hi", system="be terse"
    )
    assert result == "be terse"
