"""Test tool result caching in the agent loop."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, ClassVar

import pytest

from app.agents.loop import StopReason, ToolCallingLoop
from app.config import ModelRouterSettings
from app.core.permissions import PermissionManager
from app.database.models import MessageRole, PermissionLevel, VerificationOutcome
from app.events.bus import EventBus
from app.models.base import (
    FinishReason,
    ModelMessage,
    ModelResponse,
    ToolCall,
    ToolDefinition,
)
from app.models.fake import FakeProvider
from app.models.router import ModelRouter
from app.security.audit import AuditLogger
from app.tools.base import Reversibility, Tool
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry


class EchoTool(Tool):
    """Record each call and echo its arguments back as output."""

    name = "mock.echo"
    description = "Echo the text back."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 5.0
    reversibility = Reversibility.REVERSIBLE

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        self.calls.append(dict(arguments))
        return {"echoed": arguments["text"]}

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        return VerificationOutcome.SUCCESS


def _tool_call(name: str, arguments: dict[str, Any], call_id: str = "c1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


def _tool_response(*calls: ToolCall) -> ModelResponse:
    return ModelResponse(
        model="llama3",
        content="",
        tool_calls=tuple(calls),
        finish_reason=FinishReason.TOOL_CALLS,
        provider="ollama",
    )


def _answer(text: str = "done") -> ModelResponse:
    return ModelResponse(
        model="llama3",
        content=text,
        finish_reason=FinishReason.STOP,
        provider="ollama",
    )


def _provider(*responses: ModelResponse) -> FakeProvider:
    return FakeProvider(name="ollama", local=True, responses=responses)


def _router(provider: FakeProvider) -> ModelRouter:
    return ModelRouter(
        [provider],
        settings=ModelRouterSettings(default="ollama:llama3", fallback_chain=[]),
    )


def _harness(
    audit_sink: Any,
    *tools: Tool,
    default_level: PermissionLevel = PermissionLevel.DESTRUCTIVE,
) -> tuple[ToolExecutor, ToolRegistry]:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    executor = ToolExecutor(
        registry=registry,
        permissions=PermissionManager(
            audit=AuditLogger(audit_sink),
            default_level=default_level,
        ),
        events=EventBus(),
    )
    return executor, registry


def _user(text: str = "hello") -> list[ModelMessage]:
    return [ModelMessage(role=MessageRole.USER, content=text)]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_duplicate_tool_calls_in_one_turn_are_cached(audit_sink: Any) -> None:
    """Identical tool calls (same name + arguments) within a turn should be cached."""
    tool = EchoTool()
    # Model requests the SAME tool call twice in one turn
    provider = _provider(
        _tool_response(
            _tool_call("mock.echo", {"text": "hi"}, "c1"),
            _tool_call("mock.echo", {"text": "hi"}, "c2"),  # Duplicate!
        ),
        _answer("you said hi"),
    )
    executor, _ = _harness(audit_sink, tool)
    loop = ToolCallingLoop(router=_router(provider), executor=executor, max_tool_calls=4)

    result = await loop.run(_user("say hi back"), principal="alice")

    # Tool should only execute ONCE due to caching
    assert len(tool.calls) == 1, f"Expected 1 call due to caching, got {len(tool.calls)}"
    assert tool.calls == [{"text": "hi"}]
    # Loop should report 1 tool call (only 1 actually executed due to caching)
    assert result.tool_calls == 1
    assert result.completed


@pytest.mark.unit
@pytest.mark.asyncio
async def test_different_tool_calls_in_one_turn_not_cached(audit_sink: Any) -> None:
    """Different tool calls (different arguments) should not be cached."""
    tool = EchoTool()
    provider = _provider(
        _tool_response(
            _tool_call("mock.echo", {"text": "a"}, "c1"),
            _tool_call("mock.echo", {"text": "b"}, "c2"),  # Different args
        ),
        _answer("done"),
    )
    executor, _ = _harness(audit_sink, tool)
    loop = ToolCallingLoop(router=_router(provider), executor=executor, max_tool_calls=4)

    result = await loop.run(_user(), principal="alice")

    # Both should execute
    assert len(tool.calls) == 2
    assert tool.calls == [{"text": "a"}, {"text": "b"}]
    assert result.tool_calls == 2
    assert result.completed


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_resets_each_turn(audit_sink: Any) -> None:
    """Cache should reset at the start of each turn."""
    tool = EchoTool()
    # Turn 1: call with "hi"
    # Turn 2: call with "hi" again - should execute again because new turn
    provider = _provider(
        _tool_response(_tool_call("mock.echo", {"text": "hi"}, "c1")),
        _tool_response(_tool_call("mock.echo", {"text": "hi"}, "c2")),  # Same args, new turn
        _answer("done"),
    )
    executor, _ = _harness(audit_sink, tool)
    loop = ToolCallingLoop(router=_router(provider), executor=executor, max_tool_calls=4)

    result = await loop.run(_user(), principal="alice")

    # Should execute twice (once per turn)
    assert len(tool.calls) == 2
    assert tool.calls == [{"text": "hi"}, {"text": "hi"}]
    assert result.tool_calls == 2
    assert result.iterations == 3  # 2 tool turns + 1 final answer
    assert result.completed


@pytest.mark.unit
@pytest.mark.asyncio
async def test_wrap_up_when_budget_exhausted(audit_sink: Any) -> None:
    """When per-turn budget is hit, a tools-free wrap-up call should be made."""
    tool = EchoTool()
    # Model requests 5 calls, but budget is 2
    provider = _provider(
        _tool_response(
            _tool_call("mock.echo", {"text": "a"}, "c1"),
            _tool_call("mock.echo", {"text": "b"}, "c2"),
            _tool_call("mock.echo", {"text": "c"}, "c3"),
            _tool_call("mock.echo", {"text": "d"}, "c4"),
            _tool_call("mock.echo", {"text": "e"}, "c5"),
        ),
        # Wrap-up response
        _answer("answer with what I have"),
    )
    executor, _ = _harness(audit_sink, tool)
    loop = ToolCallingLoop(router=_router(provider), executor=executor, max_tool_calls=2)

    result = await loop.run(_user(), principal="alice")

    # Only 2 should execute (budget), then wrap-up
    assert len(tool.calls) == 2
    assert tool.calls == [{"text": "a"}, {"text": "b"}]
    assert result.tool_calls == 2
    assert result.stop_reason is StopReason.MAX_TOOL_CALLS
    assert result.iterations == 2  # 1 tool turn + 1 wrap-up
    # The final answer should come from the wrap-up
    assert result.content == "answer with what I have"


# --------------------------------------------------------------------------- #
# Streaming variants
# --------------------------------------------------------------------------- #


@pytest.mark.unit
@pytest.mark.asyncio
async def test_streaming_duplicate_tool_calls_cached(audit_sink: Any) -> None:
    """Streaming path should also cache duplicate tool calls."""
    tool = EchoTool()
    provider = _provider(
        _tool_response(
            _tool_call("mock.echo", {"text": "hi"}, "c1"),
            _tool_call("mock.echo", {"text": "hi"}, "c2"),  # Duplicate!
        ),
        _answer("you said hi"),
    )
    executor, _ = _harness(audit_sink, tool)
    loop = ToolCallingLoop(router=_router(provider), executor=executor, max_tool_calls=4)

    chunks = []
    async for chunk in loop.stream(_user("say hi back"), principal="alice"):
        chunks.append(chunk)

    # Tool should only execute ONCE due to caching
    assert len(tool.calls) == 1, f"Expected 1 call due to caching, got {len(tool.calls)}"
    # Find the completed chunk
    completed = [c for c in chunks if c.kind == "completed"]
    assert len(completed) == 1
    result = completed[0].result
    assert result.tool_calls == 1
    assert result.completed


@pytest.mark.unit
@pytest.mark.asyncio
async def test_streaming_wrap_up_when_budget_exhausted(audit_sink: Any) -> None:
    """Streaming path should also wrap up when budget exhausted."""
    tool = EchoTool()
    provider = _provider(
        _tool_response(
            _tool_call("mock.echo", {"text": "a"}, "c1"),
            _tool_call("mock.echo", {"text": "b"}, "c2"),
            _tool_call("mock.echo", {"text": "c"}, "c3"),
            _tool_call("mock.echo", {"text": "d"}, "c4"),
            _tool_call("mock.echo", {"text": "e"}, "c5"),
        ),
        _answer("answer with what I have"),
    )
    executor, _ = _harness(audit_sink, tool)
    loop = ToolCallingLoop(router=_router(provider), executor=executor, max_tool_calls=2)

    chunks = []
    async for chunk in loop.stream(_user(), principal="alice"):
        chunks.append(chunk)

    # Only 2 should execute (budget), then wrap-up
    assert len(tool.calls) == 2
    assert tool.calls == [{"text": "a"}, {"text": "b"}]
    completed = [c for c in chunks if c.kind == "completed"]
    assert len(completed) == 1
    result = completed[0].result
    assert result.tool_calls == 2
    assert result.stop_reason is StopReason.MAX_TOOL_CALLS
    assert result.content == "answer with what I have"