"""Unit tests for the Ollama adapter (T061).

The adapter is the seam where §21's eight requirements meet §33's "never crash
on a model failure", so the tests pin both halves: the request it builds and the
response it parses (including NDJSON streaming and function calling), and the
exact typed error every failure maps to — an unreachable host must become
``LOCAL_MODEL_UNAVAILABLE``, not an httpx exception escaping into the router.

``respx`` intercepts the real httpx transport, so the tests exercise the same
request/response path production uses — no hand-rolled HTTP double, and no live
Ollama (the ``requires_ollama`` marker is for the server-half check).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from app.config import OllamaSettings
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
from app.models.ollama import OllamaProvider

pytestmark = pytest.mark.unit

BASE = "http://localhost:11434"


def _chat_response(
    *, content: str = "answer", tools: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tools:
        message["tool_calls"] = tools
    return {
        "model": "llama3",
        "message": message,
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 3,
        "eval_count": 2,
    }


def _request(**overrides: Any) -> CompletionRequest:
    values: dict[str, Any] = {
        "messages": (ModelMessage(role=MessageRole.USER, content="hello"),),
        "model": "llama3",
    }
    values.update(overrides)
    return CompletionRequest(**values)


# --------------------------------------------------------------------------- #
# Declarations
# --------------------------------------------------------------------------- #
def test_the_adapter_declares_itself_local_and_streaming() -> None:
    provider = OllamaProvider(OllamaSettings())
    assert provider.name == "ollama"
    assert provider.local is True
    assert provider.supports_tools is True
    assert provider.supports_streaming is True
    assert provider.is_configured is True


# --------------------------------------------------------------------------- #
# chat
# --------------------------------------------------------------------------- #
@respx.mock
async def test_chat_parses_a_completion() -> None:
    route = respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(200, json=_chat_response(content="answer"))
    )
    provider = OllamaProvider(OllamaSettings())

    response = await provider.chat(_request())

    assert response.content == "answer"
    assert response.model == "llama3"
    assert response.provider == "ollama"
    assert response.finish_reason is FinishReason.STOP
    assert response.usage is not None
    assert response.usage.prompt_tokens == 3
    assert response.usage.completion_tokens == 2
    assert response.has_tool_calls is False
    assert route.called


@respx.mock
async def test_chat_builds_the_request_body() -> None:
    route = respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(200, json=_chat_response())
    )
    provider = OllamaProvider(OllamaSettings(num_ctx=2048, keepalive="10m"))

    await provider.chat(
        CompletionRequest(
            messages=(ModelMessage(role=MessageRole.USER, content="hello"),),
            model="ollama:llama3",
            tools=(
                ToolDefinition(name="mock.echo", description="echo", parameters={"type": "object"}),
            ),
            temperature=0.2,
            max_tokens=128,
            stop=("END",),
        )
    )

    body = _json_body(route)
    assert body["model"] == "llama3"
    assert body["stream"] is False
    assert body["keep_alive"] == "10m"
    assert body["options"]["num_ctx"] == 2048
    assert body["options"]["temperature"] == 0.2
    assert body["options"]["num_predict"] == 128
    assert body["options"]["stop"] == ["END"]
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["tools"][0]["function"]["name"] == "mock.echo"


@respx.mock
async def test_chat_parses_tool_calls() -> None:
    respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json=_chat_response(
                content="",
                tools=[{"function": {"name": "mock.echo", "arguments": {"text": "x"}}}],
            ),
        )
    )
    provider = OllamaProvider(OllamaSettings())

    response = await provider.chat(_request())

    assert response.has_tool_calls is True
    assert response.tool_calls[0].name == "mock.echo"
    assert response.tool_calls[0].arguments == {"text": "x"}
    assert response.finish_reason is FinishReason.TOOL_CALLS


@respx.mock
async def test_chat_parses_string_arguments() -> None:
    respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json=_chat_response(
                content="",
                tools=[{"function": {"name": "t", "arguments": '{"a": 1}'}}],
            ),
        )
    )
    provider = OllamaProvider(OllamaSettings())

    response = await provider.chat(_request())

    assert response.tool_calls[0].arguments == {"a": 1}


# --------------------------------------------------------------------------- #
# chat error mapping (§33)
# --------------------------------------------------------------------------- #
@respx.mock
async def test_an_unreachable_host_is_local_model_unavailable() -> None:
    respx.post("http://localhost:11434/api/chat").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    provider = OllamaProvider(OllamaSettings())

    with pytest.raises(LocalModelUnavailableError) as caught:
        await provider.chat(_request())

    assert caught.value.code == "LOCAL_MODEL_UNAVAILABLE"
    assert caught.value.retryable is True


@respx.mock
async def test_a_timeout_is_model_timeout() -> None:
    respx.post("http://localhost:11434/api/chat").mock(side_effect=httpx.ConnectTimeout("too slow"))
    provider = OllamaProvider(OllamaSettings())

    with pytest.raises(ModelTimeoutError):
        await provider.chat(_request())


@respx.mock
async def test_a_missing_model_is_not_supported() -> None:
    respx.post("http://localhost:11434/api/chat").mock(return_value=httpx.Response(404))
    provider = OllamaProvider(OllamaSettings())

    with pytest.raises(ModelNotSupportedError):
        await provider.chat(_request(model="ghost"))


@respx.mock
async def test_rate_limiting_is_typed() -> None:
    respx.post("http://localhost:11434/api/chat").mock(return_value=httpx.Response(429))
    provider = OllamaProvider(OllamaSettings())

    with pytest.raises(ModelRateLimitedError):
        await provider.chat(_request())


@respx.mock
async def test_authentication_failure_is_provider_not_configured() -> None:
    respx.post("http://localhost:11434/api/chat").mock(return_value=httpx.Response(401))
    provider = OllamaProvider(OllamaSettings())

    with pytest.raises(ProviderNotConfiguredError):
        await provider.chat(_request())


@respx.mock
async def test_a_server_error_is_a_model_error() -> None:
    respx.post("http://localhost:11434/api/chat").mock(return_value=httpx.Response(500))
    provider = OllamaProvider(OllamaSettings())

    with pytest.raises(ModelError):
        await provider.chat(_request())


@respx.mock
async def test_invalid_json_is_a_response_error() -> None:
    respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(200, text="not json")
    )
    provider = OllamaProvider(OllamaSettings())

    with pytest.raises(ModelResponseInvalidError):
        await provider.chat(_request())


@respx.mock
async def test_a_response_without_a_message_is_invalid() -> None:
    respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(200, json={"model": "llama3", "done": True})
    )
    provider = OllamaProvider(OllamaSettings())

    with pytest.raises(ModelResponseInvalidError):
        await provider.chat(_request())


# --------------------------------------------------------------------------- #
# streaming
# --------------------------------------------------------------------------- #
@respx.mock
async def test_stream_yields_deltas_and_a_terminal_chunk() -> None:
    body = "\n".join(
        [
            json.dumps({"model": "llama3", "message": {"content": "Hel"}, "done": False}),
            json.dumps({"model": "llama3", "message": {"content": "lo"}, "done": False}),
            json.dumps(
                {
                    "model": "llama3",
                    "message": {"content": ""},
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 3,
                    "eval_count": 2,
                }
            ),
        ]
    )
    route = respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(200, text=body)
    )
    provider = OllamaProvider(OllamaSettings())

    chunks = [chunk async for chunk in provider.stream(_request())]

    assert [chunk.delta for chunk in chunks if chunk.delta] == ["Hel", "lo"]
    final = chunks[-1]
    assert final.is_final
    assert final.finish_reason is FinishReason.STOP
    assert final.usage is not None and final.usage.completion_tokens == 2
    assert _json_body(route)["stream"] is True


@respx.mock
async def test_stream_forwards_tool_calls() -> None:
    body = json.dumps(
        {
            "model": "llama3",
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "mock.echo", "arguments": {"text": "x"}}}],
            },
            "done": True,
            "done_reason": "stop",
        }
    )
    respx.post("http://localhost:11434/api/chat").mock(return_value=httpx.Response(200, text=body))
    provider = OllamaProvider(OllamaSettings())

    chunks = [chunk async for chunk in provider.stream(_request())]

    assert next(c.tool_call for c in chunks if c.tool_call).name == "mock.echo"
    assert chunks[-1].finish_reason is FinishReason.TOOL_CALLS


@respx.mock
async def test_stream_reports_an_error_line() -> None:
    respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(200, text=json.dumps({"error": "model crashed"}))
    )
    provider = OllamaProvider(OllamaSettings())

    with pytest.raises(ModelResponseInvalidError, match="model crashed"):
        _ = [chunk async for chunk in provider.stream(_request())]


@respx.mock
async def test_stream_maps_an_http_error() -> None:
    respx.post("http://localhost:11434/api/chat").mock(return_value=httpx.Response(429))
    provider = OllamaProvider(OllamaSettings())

    with pytest.raises(ModelRateLimitedError):
        _ = [chunk async for chunk in provider.stream(_request())]


@respx.mock
async def test_stream_maps_a_connect_error() -> None:
    respx.post("http://localhost:11434/api/chat").mock(side_effect=httpx.ConnectError("refused"))
    provider = OllamaProvider(OllamaSettings())

    with pytest.raises(LocalModelUnavailableError):
        _ = [chunk async for chunk in provider.stream(_request())]


# --------------------------------------------------------------------------- #
# discovery, availability and health
# --------------------------------------------------------------------------- #
@respx.mock
async def test_list_models_reads_the_tags_endpoint() -> None:
    respx.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(
            200, json={"models": [{"name": "llama3:latest"}, {"name": "nomic-embed"}]}
        )
    )
    provider = OllamaProvider(OllamaSettings())

    assert await provider.list_models() == ["llama3:latest", "nomic-embed"]


@respx.mock
async def test_list_models_honours_the_allowlist() -> None:
    respx.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(
            200, json={"models": [{"name": "llama3:latest"}, {"name": "other"}]}
        )
    )
    provider = OllamaProvider(OllamaSettings(models=["llama3:latest"]))

    assert await provider.list_models() == ["llama3:latest"]


@respx.mock
async def test_is_available_matches_a_model_and_its_base_name() -> None:
    respx.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(
            200, json={"models": [{"name": "qwen2.5:7b-instruct"}, {"name": "llama3:latest"}]}
        )
    )
    provider = OllamaProvider(OllamaSettings())

    assert await provider.is_available("llama3:latest") is True
    assert await provider.is_available("ollama:qwen2.5") is True
    assert await provider.is_available("missing") is False


@respx.mock
async def test_is_available_is_false_when_the_host_is_down() -> None:
    respx.get("http://localhost:11434/api/tags").mock(side_effect=httpx.ConnectError("refused"))
    provider = OllamaProvider(OllamaSettings())

    assert await provider.is_available("llama3") is False


@respx.mock
async def test_health_reports_available_with_latency() -> None:
    respx.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": []})
    )
    provider = OllamaProvider(OllamaSettings())

    health = await provider.health()

    assert health.status.value == "available"
    assert health.latency_ms is not None


@respx.mock
async def test_health_reports_unreachable_without_raising() -> None:
    respx.get("http://localhost:11434/api/tags").mock(side_effect=httpx.ConnectError("refused"))
    provider = OllamaProvider(OllamaSettings())

    health = await provider.health()

    assert health.status.value == "unavailable"
    assert health.detail


# --------------------------------------------------------------------------- #
# client ownership
# --------------------------------------------------------------------------- #
async def test_the_adapter_closes_a_client_it_owns() -> None:
    provider = OllamaProvider(OllamaSettings())
    client = provider._http()

    await provider.aclose()

    assert client.is_closed


async def test_the_adapter_leaves_an_injected_client_open() -> None:
    injected = httpx.AsyncClient()
    provider = OllamaProvider(OllamaSettings(), client=injected)

    await provider.aclose()

    assert injected.is_closed is False
    await injected.aclose()


def _json_body(route: respx.Route) -> dict[str, Any]:
    """The JSON body the adapter sent, from the recorded request."""
    body: dict[str, Any] = json.loads(route.calls[0].request.content)
    return body
