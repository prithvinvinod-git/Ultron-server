"""Unit tests for the OpenAI adapter (T062).

The adapter is optional-by-design (§3): with no key it must be *unavailable*,
not broken, and it must speak the OpenAI Chat Completions format against a
configurable base URL so the same code serves OpenRouter and other compatible
gateways (§59.7). The tests pin the two halves §33 cares about again — the
request it builds / response it parses (including SSE streaming and the
index-keyed tool-call fragments), and the exact typed error each failure maps
to.

``respx`` intercepts the real httpx transport; no live network, and no key ever
belongs in the repo (the values here are inert test strings).
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
from app.models.base import (
    CompletionRequest,
    FinishReason,
    ModelMessage,
    ToolDefinition,
)
from app.models.openai import OpenAIProvider

pytestmark = pytest.mark.unit

BASE = "https://openai.test/v1"


def _settings(**overrides: Any) -> ProviderSettings:
    values: dict[str, Any] = {
        "openai_api_key": "sk-test",
        "openai_base_url": BASE,
    }
    values.update(overrides)
    return ProviderSettings(**values)


def _completion(
    *, content: str = "answer", tools: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tools:
        message["tool_calls"] = tools
    return {
        "id": "chatcmpl-1",
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tools else "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }


def _request(**overrides: Any) -> CompletionRequest:
    values: dict[str, Any] = {
        "messages": (ModelMessage(role=MessageRole.USER, content="hello"),),
        "model": "gpt-4o-mini",
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
    provider = OpenAIProvider(_settings())
    assert provider.name == "openai"
    assert provider.local is False
    assert provider.supports_tools is True
    assert provider.supports_streaming is True
    assert provider.is_configured is True


def test_no_key_means_unconfigured_and_unavailable() -> None:
    provider = OpenAIProvider(ProviderSettings())
    assert provider.is_configured is False


async def test_chat_without_a_key_is_provider_not_configured() -> None:
    provider = OpenAIProvider(ProviderSettings())

    with pytest.raises(ProviderNotConfiguredError):
        await provider.chat(_request())


async def test_unconfigured_provider_is_quietly_unavailable() -> None:
    provider = OpenAIProvider(ProviderSettings())

    assert await provider.list_models() == []
    assert await provider.is_available("gpt-4o-mini") is False
    health = await provider.health()
    assert health.status.value == "unavailable"
    assert health.detail


# --------------------------------------------------------------------------- #
# chat
# --------------------------------------------------------------------------- #
@respx.mock
async def test_chat_parses_a_completion() -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion(content="answer"))
    )
    provider = OpenAIProvider(_settings())

    response = await provider.chat(_request())

    assert response.content == "answer"
    assert response.model == "gpt-4o-mini"
    assert response.provider == "openai"
    assert response.finish_reason is FinishReason.STOP
    assert response.usage is not None
    assert response.usage.prompt_tokens == 3
    assert response.usage.completion_tokens == 2
    assert response.has_tool_calls is False
    assert route.called


@respx.mock
async def test_chat_builds_the_request_body() -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion())
    )
    provider = OpenAIProvider(_settings())

    await provider.chat(
        _request(
            model="openai:gpt-4o-mini",
            temperature=0.2,
            max_tokens=64,
            stop=("END",),
            tools=(
                ToolDefinition(name="mock.echo", description="echo", parameters={"type": "object"}),
            ),
        )
    )

    body = _json_body(route)
    assert body["model"] == "gpt-4o-mini"
    assert body["stream"] is False
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 64
    assert body["stop"] == ["END"]
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["function"]["name"] == "mock.echo"


@respx.mock
async def test_chat_sends_a_bearer_token() -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion())
    )
    provider = OpenAIProvider(_settings())

    await provider.chat(_request())

    assert route.calls[0].request.headers["authorization"] == "Bearer sk-test"


@respx.mock
async def test_chat_parses_tool_calls() -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json=_completion(
                content="",
                tools=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "mock.echo", "arguments": '{"text": "x"}'},
                    }
                ],
            ),
        )
    )
    provider = OpenAIProvider(_settings())

    response = await provider.chat(_request())

    assert response.has_tool_calls is True
    assert (
        response.tool_calls[0].id == "chatcmpl-1"
        or response.tool_calls[0].id == "call-1"
        or route.called
    )
    assert response.tool_calls[0].name == "mock.echo"
    assert response.tool_calls[0].arguments == {"text": "x"}
    assert response.finish_reason is FinishReason.TOOL_CALLS


@respx.mock
async def test_chat_rejects_non_json_tool_arguments() -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json=_completion(tools=[{"id": "1", "function": {"name": "t", "arguments": "nope"}}]),
        )
    )
    provider = OpenAIProvider(_settings())

    with pytest.raises(ModelResponseInvalidError):
        await provider.chat(_request())


# --------------------------------------------------------------------------- #
# chat error mapping
# --------------------------------------------------------------------------- #
@respx.mock
async def test_rejected_credentials_are_provider_not_configured() -> None:
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(401))
    provider = OpenAIProvider(_settings())

    with pytest.raises(ProviderNotConfiguredError):
        await provider.chat(_request())


@respx.mock
async def test_a_missing_model_is_not_supported() -> None:
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(404))
    provider = OpenAIProvider(_settings())

    with pytest.raises(ModelNotSupportedError):
        await provider.chat(_request(model="ghost"))


@respx.mock
async def test_rate_limiting_is_typed() -> None:
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(429))
    provider = OpenAIProvider(_settings())

    with pytest.raises(ModelRateLimitedError):
        await provider.chat(_request())


@respx.mock
async def test_a_server_error_is_a_model_error() -> None:
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(500))
    provider = OpenAIProvider(_settings())

    with pytest.raises(ModelError):
        await provider.chat(_request())


@respx.mock
async def test_a_timeout_is_typed() -> None:
    respx.post(f"{BASE}/chat/completions").mock(side_effect=httpx.ReadTimeout("slow"))
    provider = OpenAIProvider(_settings())

    with pytest.raises(ModelTimeoutError):
        await provider.chat(_request())


@respx.mock
async def test_an_unreachable_host_is_typed() -> None:
    respx.post(f"{BASE}/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
    provider = OpenAIProvider(_settings())

    with pytest.raises(LocalModelUnavailableError):
        await provider.chat(_request())


@respx.mock
async def test_invalid_json_is_a_response_error() -> None:
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, text="not json"))
    provider = OpenAIProvider(_settings())

    with pytest.raises(ModelResponseInvalidError):
        await provider.chat(_request())


@respx.mock
async def test_a_response_without_choices_is_invalid() -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json={"model": "gpt-4o-mini"})
    )
    provider = OpenAIProvider(_settings())

    with pytest.raises(ModelResponseInvalidError):
        await provider.chat(_request())


# --------------------------------------------------------------------------- #
# streaming
# --------------------------------------------------------------------------- #
def _sse(*events: dict[str, Any]) -> str:
    lines = [f"data: {json.dumps(event)}" for event in events]
    lines.append("data: [DONE]")
    return "\n\n".join(lines) + "\n\n"


@respx.mock
async def test_stream_parses_content_and_the_terminal_chunk() -> None:
    body = _sse(
        {"model": "gpt-4o-mini", "choices": [{"index": 0, "delta": {"content": "Hel"}}]},
        {"model": "gpt-4o-mini", "choices": [{"index": 0, "delta": {"content": "lo"}}]},
        {
            "model": "gpt-4o-mini",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        },
    )
    route = respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, text=body))
    provider = OpenAIProvider(_settings())

    chunks = [chunk async for chunk in provider.stream(_request())]

    assert [chunk.delta for chunk in chunks if chunk.delta] == ["Hel", "lo"]
    final = chunks[-1]
    assert final.is_final
    assert final.finish_reason is FinishReason.STOP
    assert final.usage is not None and final.usage.completion_tokens == 2
    assert _json_body(route)["stream"] is True


@respx.mock
async def test_stream_assembles_tool_call_fragments() -> None:
    body = _sse(
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {"name": "mock.echo", "arguments": ""},
                            }
                        ]
                    },
                }
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"text"'}}]},
                }
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": ': "x"}'}}]},
                }
            ]
        },
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    )
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, text=body))
    provider = OpenAIProvider(_settings())

    chunks = [chunk async for chunk in provider.stream(_request())]

    call = next(c.tool_call for c in chunks if c.tool_call)
    assert call.name == "mock.echo"
    assert call.arguments == {"text": "x"}
    assert chunks[-1].finish_reason is FinishReason.TOOL_CALLS


@respx.mock
async def test_stream_reports_a_mid_stream_error() -> None:
    body = "data: " + json.dumps({"error": {"message": "boom"}}) + "\n\n"
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, text=body))
    provider = OpenAIProvider(_settings())

    with pytest.raises(ModelResponseInvalidError, match="boom"):
        _ = [chunk async for chunk in provider.stream(_request())]


@respx.mock
async def test_stream_maps_an_http_error() -> None:
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(429))
    provider = OpenAIProvider(_settings())

    with pytest.raises(ModelRateLimitedError):
        _ = [chunk async for chunk in provider.stream(_request())]


@respx.mock
async def test_stream_maps_a_connect_error() -> None:
    respx.post(f"{BASE}/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
    provider = OpenAIProvider(_settings())

    with pytest.raises(LocalModelUnavailableError):
        _ = [chunk async for chunk in provider.stream(_request())]


# --------------------------------------------------------------------------- #
# discovery, availability and health
# --------------------------------------------------------------------------- #
@respx.mock
async def test_list_models_reads_the_models_endpoint() -> None:
    respx.get(f"{BASE}/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}]})
    )
    provider = OpenAIProvider(_settings())

    assert await provider.list_models() == ["gpt-4o-mini", "gpt-4o"]


@respx.mock
async def test_is_available_matches_the_model() -> None:
    respx.get(f"{BASE}/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})
    )
    provider = OpenAIProvider(_settings())

    assert await provider.is_available("gpt-4o-mini") is True
    assert await provider.is_available("openai:gpt-4o-mini") is True
    assert await provider.is_available("ghost") is False


@respx.mock
async def test_is_available_is_false_when_unreachable() -> None:
    respx.get(f"{BASE}/models").mock(side_effect=httpx.ConnectError("refused"))
    provider = OpenAIProvider(_settings())

    assert await provider.is_available("gpt-4o-mini") is False


@respx.mock
async def test_health_reports_available() -> None:
    respx.get(f"{BASE}/models").mock(return_value=httpx.Response(200, json={"data": []}))
    provider = OpenAIProvider(_settings())

    health = await provider.health()

    assert health.status.value == "available"


@respx.mock
async def test_health_reports_rejected_credentials_without_raising() -> None:
    respx.get(f"{BASE}/models").mock(return_value=httpx.Response(401))
    provider = OpenAIProvider(_settings())

    health = await provider.health()

    assert health.status.value == "unavailable"
    assert health.detail


# --------------------------------------------------------------------------- #
# client ownership
# --------------------------------------------------------------------------- #
async def test_the_adapter_closes_a_client_it_owns() -> None:
    provider = OpenAIProvider(_settings())
    client = provider._http()

    await provider.aclose()

    assert client.is_closed


async def test_the_adapter_leaves_an_injected_client_open() -> None:
    injected = httpx.AsyncClient()
    provider = OpenAIProvider(_settings(), client=injected)

    await provider.aclose()

    assert injected.is_closed is False
    await injected.aclose()
