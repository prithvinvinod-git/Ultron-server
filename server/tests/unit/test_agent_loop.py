"""Unit tests for the model-driven tool-calling loop (T068).

§16's pipeline runs model → tool → model until the model stops asking, and
these tests pin that cycle without a network: a ``FakeProvider`` (T065) scripts
the model turns, a real ``ToolExecutor`` runs the calls through every stage, and
the assertions are on the *transcript* — what the model saw and what the loop
handed back. They cover the two endings (§59.6's ceilings), the failure path
that is information rather than an abort (§33), and the identity the pipeline
attributes each execution to.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, ClassVar

import pytest

from app.agents.loop import (
    StopReason,
    ToolCallingLoop,
    ToolLoopResult,
    definitions_from_registry,
)
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

pytestmark = pytest.mark.unit


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


class SecondTool(EchoTool):
    name = "mock.second"
    description = "Second tool."


class WriteTool(EchoTool):
    name = "mock.write"
    permission_level = PermissionLevel.MODIFY_PROJECT


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


def _last_tool_turn(result: ToolLoopResult) -> dict[str, Any]:
    """The parsed payload of the last ``tool`` message in the transcript."""
    payload = result.messages[-1]
    assert payload.role is MessageRole.TOOL
    parsed: dict[str, Any] = json.loads(payload.content)
    return parsed


# --------------------------------------------------------------------------- #
# definitions_from_registry
# --------------------------------------------------------------------------- #
def test_definitions_from_registry_are_sorted_and_copied(audit_sink: Any) -> None:
    _, registry = _harness(audit_sink, EchoTool(), SecondTool())

    definitions = definitions_from_registry(registry)

    assert [definition.name for definition in definitions] == ["mock.echo", "mock.second"]
    assert definitions[0] == ToolDefinition(
        name="mock.echo",
        description="Echo the text back.",
        parameters=EchoTool.input_schema,
    )
    definitions[0].parameters["properties"]["text"]["type"] = "number"
    assert registry.lookup("mock.echo").input_schema["properties"]["text"]["type"] == "string"


# --------------------------------------------------------------------------- #
# The happy path: model answers without tools
# --------------------------------------------------------------------------- #
async def test_model_answers_without_tools_completes_in_one_turn(audit_sink: Any) -> None:
    provider = _provider(_answer("hello there"))
    executor, _ = _harness(audit_sink, EchoTool())
    loop = ToolCallingLoop(router=_router(provider), executor=executor)

    result = await loop.run(_user(), principal="alice")

    assert result.completed
    assert result.stop_reason is StopReason.COMPLETED
    assert result.content == "hello there"
    assert result.iterations == 1
    assert result.tool_calls == 0
    assert result.messages == (ModelMessage(role=MessageRole.USER, content="hello"),)


async def test_system_prompt_is_prepended_once(audit_sink: Any) -> None:
    provider = _provider(_answer())
    executor, _ = _harness(audit_sink, EchoTool())
    loop = ToolCallingLoop(router=_router(provider), executor=executor, system_prompt="be terse")

    result = await loop.run(_user(), principal="alice")

    assert result.messages[0] == ModelMessage(role=MessageRole.SYSTEM, content="be terse")
    assert result.messages[1].role is MessageRole.USER


async def test_existing_system_turn_is_not_duplicated(audit_sink: Any) -> None:
    provider = _provider(_answer())
    executor, _ = _harness(audit_sink, EchoTool())
    loop = ToolCallingLoop(router=_router(provider), executor=executor, system_prompt="be terse")
    messages = [
        ModelMessage(role=MessageRole.SYSTEM, content="already set"),
        ModelMessage(role=MessageRole.USER, content="hello"),
    ]

    result = await loop.run(messages, principal="alice")

    assert result.messages[0].content == "already set"
    assert sum(m.role is MessageRole.SYSTEM for m in result.messages) == 1


# --------------------------------------------------------------------------- #
# The cycle: model -> tool -> model
# --------------------------------------------------------------------------- #
async def test_tool_call_is_executed_and_result_returned_to_the_model(
    audit_sink: Any,
) -> None:
    tool = EchoTool()
    provider = _provider(
        _tool_response(_tool_call("mock.echo", {"text": "hi"})),
        _answer("you said hi"),
    )
    executor, registry = _harness(audit_sink, tool)
    definitions = definitions_from_registry(registry)
    loop = ToolCallingLoop(router=_router(provider), executor=executor, tools=definitions)

    result = await loop.run(_user("say hi back"), principal="alice")

    assert result.completed
    assert result.iterations == 2
    assert result.tool_calls == 1
    assert tool.calls == [{"text": "hi"}]
    assert provider.last_request is not None
    assert provider.last_request.tools == tuple(definitions)
    assistant_turn = result.messages[-2]
    assert assistant_turn.role is MessageRole.ASSISTANT
    assert assistant_turn.tool_calls == (_tool_call("mock.echo", {"text": "hi"}),)
    payload = _last_tool_turn(result)
    assert payload["ok"] is True
    assert payload["tool"] == "mock.echo"
    assert payload["verification"] == VerificationOutcome.SUCCESS.value
    assert payload["output"] == {"echoed": "hi"}


async def test_multiple_tool_calls_in_one_turn_all_execute(audit_sink: Any) -> None:
    tool = EchoTool()
    provider = _provider(
        _tool_response(
            _tool_call("mock.echo", {"text": "a"}, "c1"),
            _tool_call("mock.echo", {"text": "b"}, "c2"),
        ),
        _answer(),
    )
    executor, _ = _harness(audit_sink, tool)
    loop = ToolCallingLoop(router=_router(provider), executor=executor)

    result = await loop.run(_user(), principal="alice")

    assert result.tool_calls == 2
    assert tool.calls == [{"text": "a"}, {"text": "b"}]
    tool_turns = [m for m in result.messages if m.role is MessageRole.TOOL]
    assert [m.tool_call_id for m in tool_turns] == ["c1", "c2"]


async def test_loop_without_advertised_tools_sends_none(audit_sink: Any) -> None:
    provider = _provider(_answer())
    executor, _ = _harness(audit_sink, EchoTool())
    loop = ToolCallingLoop(router=_router(provider), executor=executor)

    await loop.run(_user(), principal="alice")

    assert provider.last_request is not None
    assert provider.last_request.tools == ()


# --------------------------------------------------------------------------- #
# Failure is information, not an abort (§33)
# --------------------------------------------------------------------------- #
async def test_unknown_tool_error_is_fed_back_to_the_model(audit_sink: Any) -> None:
    provider = _provider(
        _tool_response(_tool_call("mock.ghost", {})),
        _answer("recovered"),
    )
    executor, _ = _harness(audit_sink, EchoTool())
    loop = ToolCallingLoop(router=_router(provider), executor=executor)

    result = await loop.run(_user(), principal="alice")

    assert result.completed
    payload = _last_tool_turn(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "TOOL_NOT_FOUND"


async def test_invalid_arguments_are_reported_as_a_schema_error(audit_sink: Any) -> None:
    tool = EchoTool()
    provider = _provider(
        _tool_response(_tool_call("mock.echo", {"wrong": True})),
        _answer(),
    )
    executor, _ = _harness(audit_sink, tool)
    loop = ToolCallingLoop(router=_router(provider), executor=executor)

    result = await loop.run(_user(), principal="alice")

    payload = _last_tool_turn(result)
    assert payload["error"]["code"] == "TOOL_SCHEMA_INVALID"
    assert tool.calls == []


async def test_permission_denial_is_fed_back_and_the_loop_continues(
    audit_sink: Any,
) -> None:
    tool = WriteTool()
    provider = _provider(
        _tool_response(_tool_call("mock.write", {"text": "hi"})),
        _answer("understood"),
    )
    executor, _ = _harness(audit_sink, tool, default_level=PermissionLevel.READ_ONLY)
    loop = ToolCallingLoop(router=_router(provider), executor=executor)

    result = await loop.run(_user(), principal="alice")

    assert result.completed
    payload = _last_tool_turn(result)
    assert payload["error"]["code"] == "PERMISSION_DENIED"
    assert tool.calls == []


# --------------------------------------------------------------------------- #
# Bounded by construction (§59.6)
# --------------------------------------------------------------------------- #
async def test_max_tool_calls_stops_the_loop(audit_sink: Any) -> None:
    tool = EchoTool()
    provider = _provider(
        _tool_response(
            _tool_call("mock.echo", {"text": "a"}, "c1"),
            _tool_call("mock.echo", {"text": "b"}, "c2"),
            _tool_call("mock.echo", {"text": "c"}, "c3"),
        ),
    )
    executor, _ = _harness(audit_sink, tool)
    loop = ToolCallingLoop(router=_router(provider), executor=executor, max_tool_calls=2)

    result = await loop.run(_user(), principal="alice")

    assert result.stop_reason is StopReason.MAX_TOOL_CALLS
    assert result.tool_calls == 2
    assert not result.completed
    assert tool.calls == [{"text": "a"}, {"text": "b"}]


async def test_max_iterations_stops_a_model_that_never_stops(audit_sink: Any) -> None:
    tool = EchoTool()
    provider = _provider(
        _tool_response(_tool_call("mock.echo", {"text": "1"}, "c1")),
        _tool_response(_tool_call("mock.echo", {"text": "2"}, "c2")),
        _tool_response(_tool_call("mock.echo", {"text": "3"}, "c3")),
    )
    executor, _ = _harness(audit_sink, tool)
    loop = ToolCallingLoop(router=_router(provider), executor=executor, max_iterations=2)

    result = await loop.run(_user(), principal="alice")

    assert result.stop_reason is StopReason.MAX_ITERATIONS
    assert result.iterations == 2
    assert result.tool_calls == 2


# --------------------------------------------------------------------------- #
# Construction guards
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", [0, -1, True])
def test_max_iterations_must_be_positive(audit_sink: Any, value: int) -> None:
    executor, _ = _harness(audit_sink, EchoTool())
    provider = _provider()
    with pytest.raises(ValueError):
        ToolCallingLoop(router=_router(provider), executor=executor, max_iterations=value)


@pytest.mark.parametrize("value", [0, -1, True])
def test_max_tool_calls_must_be_positive(audit_sink: Any, value: int) -> None:
    executor, _ = _harness(audit_sink, EchoTool())
    provider = _provider()
    with pytest.raises(ValueError):
        ToolCallingLoop(router=_router(provider), executor=executor, max_tool_calls=value)
