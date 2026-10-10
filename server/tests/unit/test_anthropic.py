"""Unit tests for the Anthropic adapter (T064).

Optional-by-design (§3): no key means unavailable, not broken. The adapter
absorbs Anthropic's Messages API shape — a top-level ``system``, ``content``
blocks, ``tool_use``/``tool_result``, ``stop_reason`` and
``usage.input_tokens``/``output_tokens`` — behind the T060 vocabulary, so these
tests pin the mapping and the same §33 typed-error map the other adapters use.

``respx`` intercepts the real httpx transport; the key here is an inert string.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from app.config import ProviderSettings
from app.core.errors import (
    LocalModelUnavailableError,
    ModelError,
    ModelNotSupportedError,
    ModelRateLimitedError,
    ModelResponseInvalidError,
    ModelTimeoutError,
    ProviderNotConfiguredError,
)
from app.database.models import MessageRole
from app.models.anthropic import AnthropicProvider
from app.models.base import (
    CompletionRequest,
    FinishReason,
    ModelMessage,
    ToolCall,
    ToolDefinition,
)

pytestmark = pytest.mark.unit

BASE = "https://anthropic.test"
MODEL = "claude-3-5-sonnet-20241022"
MESSAGES = f"{BASE}/v1/messages"
MODELS = f"{BASE}/v1/models"


def _settings(**overrides: Any) -> ProviderSettings:
    values: dict[str, Any] = {
        "anthropic_api_key": "sk-test",
        "anthropic_base_url": BASE,
    }
    values.update(overrides)
    return ProviderSettings(**values)


def _completion(
    *, blocks: list[dict[str, Any]] | None = None, stop: str = "end_turn"
) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "model": MODEL,
        "content": blocks if blocks is not None else [{"type": "text", "text": "answer"}],
        "stop_reason": stop,
        "usage": {"input_tokens": 3, "output_tokens": 2},
    }


def _request(**overrides: Any) -> CompletionRequest:
    values: dict[str, Any] = {
        "messages": (ModelMessage(role=MessageRole.USER, content="hello"),),
        "model": MODEL,
    }
    values.update(overrides)
    return CompletionRequest(**values)


def _json_body(route: respx.Route) -> dict[str, Any]:
    """The JSON body the adapter sent, from the recorded request."""
    body: dict[str, Any] = json.loads(route.calls[0].request.content)
    return body


# --------------------------------------------------------------------------- #
# Declarations and the unconfigured path
# --------------------------------------------------------------------------- #
def test_the_adapter_is_cloud_and_reports_configuration() -> None:
    provider = AnthropicProvider(_settings())
    assert provider.name == "anthropic"
    assert provider.local is False
    assert provider.supports_tools is True
    assert provider.supports_streaming is True
    assert provider.is_configured is True


def test_no_key_means_unconfigured() -> None:
    assert AnthropicProvider(ProviderSettings()).is_configured is False


async def test_chat_without_a_key_is_provider_not_configured() -> None:
    with pytest.raises(ProviderNotConfiguredError):
        await AnthropicProvider(ProviderSettings()).chat(_request())


async def test_unconfigured_provider_is_quietly_unavailable() -> None:
    provider = AnthropicProvider(ProviderSettings())
    assert await provider.list_models() == []
    assert await provider.is_available(MODEL) is False
    health = await provider.health()
    assert health.status.value == "unavailable"
    assert health.detail


# --------------------------------------------------------------------------- #
# chat
# --------------------------------------------------------------------------- #
@respx.mock
async def test_chat_parses_a_completion() -> None:
    route = respx.post(MESSAGES).mock(return_value=httpx.Response(200, json=_completion()))
    provider = AnthropicProvider(_settings())

    response = await provider.chat(_request())

    assert response.content == "answer"
    assert response.model == MODEL
    assert response.provider == "anthropic"
    assert response.finish_reason is FinishReason.STOP
    assert response.usage is not None
    assert response.usage.prompt_tokens == 3
    assert response.usage.completion_tokens == 2
    assert response.has_tool_calls is False
    assert route.called


@respx.mock
async def test_chat_builds_system_messages_and_tools() -> None:
    route = respx.post(MESSAGES).mock(return_value=httpx.Response(200, json=_completion()))
    provider = AnthropicProvider(_settings())

    await provider.chat(
        _request(
            model="anthropic:claude-3-5-sonnet-20241022",
            max_tokens=128,
            temperature=0.2,
            stop=("END",),
            messages=(
                ModelMessage(role=MessageRole.SYSTEM, content="be brief"),
                ModelMessage(role=MessageRole.USER, content="hello"),
            ),
            tools=(
                ToolDefinition(name="mock.echo", description="echo", parameters={"type": "object"}),
            ),
        )
    )

    body = _json_body(route)
    assert body["model"] == MODEL
    assert body["max_tokens"] == 128
    assert body["system"] == "be brief"
    assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
    assert body["temperature"] == 0.2
    assert body["stop_sequences"] == ["END"]
    assert body["tools"][0]["name"] == "mock.echo"
    assert body["tools"][0]["input_schema"] == {"type": "object"}


@respx.mock
async def test_chat_defaults_max_tokens_when_unset() -> None:
    route = respx.post(MESSAGES).mock(return_value=httpx.Response(200, json=_completion()))
    provider = AnthropicProvider(_settings())

    await provider.chat(_request())

    assert _json_body(route)["max_tokens"] >= 1


@respx.mock
async def test_chat_sends_the_key_and_version_headers() -> None:
    route = respx.post(MESSAGES).mock(return_value=httpx.Response(200, json=_completion()))
    provider = AnthropicProvider(_settings())

    await provider.chat(_request())

    headers = route.calls[0].request.headers
    assert headers["x-api-key"] == "sk-test"
    assert headers["anthropic-version"] == "2023-06-01"


@respx.mock
async def test_chat_maps_assistant_tool_use_and_tool_results() -> None:
    route = respx.post(MESSAGES).mock(return_value=httpx.Response(200, json=_completion()))
    provider = AnthropicProvider(_settings())

    await provider.chat(
        _request(
            messages=(
                ModelMessage(
                    role=MessageRole.ASSISTANT,
                    content="",
                    tool_calls=(ToolCall(id="t1", name="mock.echo", arguments={"text": "x"}),),
                ),
                ModelMessage(
                    role=MessageRole.TOOL, content="result", name="mock.echo", tool_call_id="t1"
                ),
            )
        )
    )

    messages = _json_body(route)["messages"]
    assert messages[0] == {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "t1", "name": "mock.echo", "input": {"text": "x"}}],
    }
    assert messages[1] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "result"}],
    }


@respx.mock
async def test_chat_parses_a_tool_use_block() -> None:
    respx.post(MESSAGES).mock(
        return_value=httpx.Response(
            200,
            json=_completion(
                blocks=[
                    {"type": "text", "text": ""},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "mock.echo",
                        "input": {"text": "x"},
                    },
                ],
                stop="tool_use",
            ),
        )
    )
    provider = AnthropicProvider(_settings())

    response = await provider.chat(_request())

    assert response.has_tool_calls is True
    call = response.tool_calls[0]
    assert call.id == "toolu_1"
    assert call.name == "mock.echo"
    assert call.arguments == {"text": "x"}
    assert response.finish_reason is FinishReason.TOOL_CALLS


# --------------------------------------------------------------------------- #
# chat error mapping
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("status", "exc"),
    [
        (401, ProviderNotConfiguredError),
        (403, ProviderNotConfiguredError),
        (404, ModelNotSupportedError),
        (429, ModelRateLimitedError),
        (500, ModelError),
    ],
)
@respx.mock
async def test_http_errors_map_to_typed_errors(status: int, exc: type[Exception]) -> None:
    respx.post(MESSAGES).mock(return_value=httpx.Response(status))
    provider = AnthropicProvider(_settings())

    with pytest.raises(exc):
        await provider.chat(_request())


@respx.mock
async def test_a_timeout_is_typed() -> None:
    respx.post(MESSAGES).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ModelTimeoutError):
        await AnthropicProvider(_settings()).chat(_request())


@respx.mock
async def test_an_unreachable_host_is_typed() -> None:
    respx.post(MESSAGES).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LocalModelUnavailableError):
        await AnthropicProvider(_settings()).chat(_request())


@respx.mock
async def test_invalid_json_is_a_response_error() -> None:
    respx.post(MESSAGES).mock(return_value=httpx.Response(200, text="not json"))
    with pytest.raises(ModelResponseInvalidError):
        await AnthropicProvider(_settings()).chat(_request())


@respx.mock
async def test_missing_content_blocks_is_a_response_error() -> None:
    respx.post(MESSAGES).mock(return_value=httpx.Response(200, json={"stop_reason": "end_turn"}))
    with pytest.raises(ModelResponseInvalidError):
        await AnthropicProvider(_settings()).chat(_request())


# --------------------------------------------------------------------------- #
# streaming
# --------------------------------------------------------------------------- #
def _sse(*events: dict[str, Any]) -> str:
    lines: list[str] = []
    for event in events:
        lines.append(f"event: {event.get('type')}")
        lines.append(f"data: {json.dumps(event)}")
        lines.append("")
    return "\n".join(lines) + "\n"


@respx.mock
async def test_stream_parses_text_and_the_terminal_chunk() -> None:
    body = _sse(
        {"type": "message_start", "message": {"usage": {"input_tokens": 3, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hel"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "lo"},
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 2},
        },
        {"type": "message_stop"},
    )
    respx.post(MESSAGES).mock(return_value=httpx.Response(200, text=body))
    provider = AnthropicProvider(_settings())

    chunks = [chunk async for chunk in provider.stream(_request())]

    assert [chunk.delta for chunk in chunks if chunk.delta] == ["Hel", "lo"]
    final = chunks[-1]
    assert final.is_final
    assert final.finish_reason is FinishReason.STOP
    assert final.usage is not None
    assert final.usage.prompt_tokens == 3
    assert final.usage.completion_tokens == 2


@respx.mock
async def test_stream_assembles_a_tool_use_block() -> None:
    body = _sse(
        {"type": "message_start", "message": {"usage": {"input_tokens": 3, "output_tokens": 1}}},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "toolu_1", "name": "mock.echo"},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"text"'},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": ': "x"}'},
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 2},
        },
        {"type": "message_stop"},
    )
    respx.post(MESSAGES).mock(return_value=httpx.Response(200, text=body))
    provider = AnthropicProvider(_settings())

    chunks = [chunk async for chunk in provider.stream(_request())]

    call = next(c.tool_call for c in chunks if c.tool_call)
    assert call.name == "mock.echo"
    assert call.arguments == {"text": "x"}
    assert chunks[-1].finish_reason is FinishReason.TOOL_CALLS


@respx.mock
async def test_stream_maps_an_http_error() -> None:
    respx.post(MESSAGES).mock(return_value=httpx.Response(429))
    with pytest.raises(ModelRateLimitedError):
        _ = [chunk async for chunk in AnthropicProvider(_settings()).stream(_request())]


@respx.mock
async def test_stream_maps_a_connect_error() -> None:
    respx.post(MESSAGES).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LocalModelUnavailableError):
        _ = [chunk async for chunk in AnthropicProvider(_settings()).stream(_request())]


# --------------------------------------------------------------------------- #
# discovery, availability and health
# --------------------------------------------------------------------------- #
@respx.mock
async def test_list_models_reads_the_models_endpoint() -> None:
    respx.get(MODELS).mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": MODEL}, {"id": "claude-3-haiku-20240307"}]}
        )
    )
    provider = AnthropicProvider(_settings())

    assert await provider.list_models() == [MODEL, "claude-3-haiku-20240307"]


@respx.mock
async def test_is_available_matches_the_model() -> None:
    respx.get(MODELS).mock(return_value=httpx.Response(200, json={"data": [{"id": MODEL}]}))
    provider = AnthropicProvider(_settings())

    assert await provider.is_available(MODEL) is True
    assert await provider.is_available(f"anthropic:{MODEL}") is True
    assert await provider.is_available("ghost") is False


@respx.mock
async def test_is_available_is_false_when_unreachable() -> None:
    respx.get(MODELS).mock(side_effect=httpx.ConnectError("refused"))
    assert await AnthropicProvider(_settings()).is_available(MODEL) is False


@respx.mock
async def test_health_reports_available() -> None:
    respx.get(MODELS).mock(return_value=httpx.Response(200, json={"data": []}))
    health = await AnthropicProvider(_settings()).health()
    assert health.status.value == "available"


@respx.mock
async def test_health_reports_rejected_credentials_without_raising() -> None:
    respx.get(MODELS).mock(return_value=httpx.Response(401))
    health = await AnthropicProvider(_settings()).health()
    assert health.status.value == "unavailable"
    assert health.detail


# --------------------------------------------------------------------------- #
# client ownership
# --------------------------------------------------------------------------- #
async def test_the_adapter_closes_a_client_it_owns() -> None:
    provider = AnthropicProvider(_settings())
    client = provider._http()

    await provider.aclose()

    assert client.is_closed


async def test_the_adapter_leaves_an_injected_client_open() -> None:
    injected = httpx.AsyncClient()
    provider = AnthropicProvider(_settings(), client=injected)

    await provider.aclose()

    assert injected.is_closed is False
    await injected.aclose()
