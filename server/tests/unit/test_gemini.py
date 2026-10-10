"""Unit tests for the Gemini adapter (T063).

Optional-by-design (§3): no key means unavailable, not broken. The adapter
absorbs Gemini's ``contents``/``parts``/``usageMetadata`` shape behind the T060
vocabulary, so these tests pin the mapping — system prompt vs contents, tool
declarations, ``functionCall``/``functionResponse``, the finish-reason and usage
translation — and the same §33 typed-error map the other adapters use.

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
from app.models.base import (
    CompletionRequest,
    FinishReason,
    ModelMessage,
    ToolCall,
    ToolDefinition,
)
from app.models.gemini import GeminiProvider

pytestmark = pytest.mark.unit

BASE = "https://gemini.test"
MODEL = "gemini-1.5-flash"
GENERATE = f"{BASE}/v1beta/models/{MODEL}:generateContent"
STREAM = f"{BASE}/v1beta/models/{MODEL}:streamGenerateContent?alt=sse"


def _settings(**overrides: Any) -> ProviderSettings:
    values: dict[str, Any] = {
        "gemini_api_key": "sk-test",
        "gemini_base_url": BASE,
    }
    values.update(overrides)
    return ProviderSettings(**values)


def _completion(
    *, parts: list[dict[str, Any]] | None = None, finish: str = "STOP"
) -> dict[str, Any]:
    return {
        "modelVersion": "gemini-1.5-flash-001",
        "candidates": [
            {
                "content": {"role": "model", "parts": parts or [{"text": "answer"}]},
                "finishReason": finish,
            }
        ],
        "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
    }


def _tool_completion() -> dict[str, Any]:
    return _completion(
        parts=[{"text": ""}, {"functionCall": {"name": "mock.echo", "args": {"text": "x"}}}]
    )


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
    provider = GeminiProvider(_settings())
    assert provider.name == "gemini"
    assert provider.local is False
    assert provider.supports_tools is True
    assert provider.supports_streaming is True
    assert provider.is_configured is True


def test_no_key_means_unconfigured() -> None:
    assert GeminiProvider(ProviderSettings()).is_configured is False


async def test_chat_without_a_key_is_provider_not_configured() -> None:
    with pytest.raises(ProviderNotConfiguredError):
        await GeminiProvider(ProviderSettings()).chat(_request())


async def test_unconfigured_provider_is_quietly_unavailable() -> None:
    provider = GeminiProvider(ProviderSettings())
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
    route = respx.post(GENERATE).mock(return_value=httpx.Response(200, json=_completion()))
    provider = GeminiProvider(_settings())

    response = await provider.chat(_request())

    assert response.content == "answer"
    assert response.model == "gemini-1.5-flash-001"
    assert response.provider == "gemini"
    assert response.finish_reason is FinishReason.STOP
    assert response.usage is not None
    assert response.usage.prompt_tokens == 3
    assert response.usage.completion_tokens == 2
    assert route.called


@respx.mock
async def test_chat_builds_contents_tools_and_config() -> None:
    route = respx.post(GENERATE).mock(return_value=httpx.Response(200, json=_completion()))
    provider = GeminiProvider(_settings())

    await provider.chat(
        _request(
            model="gemini:gemini-1.5-flash",
            temperature=0.2,
            max_tokens=64,
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
    assert body["systemInstruction"] == {"parts": [{"text": "be brief"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "hello"}]}]
    assert body["generationConfig"]["temperature"] == 0.2
    assert body["generationConfig"]["maxOutputTokens"] == 64
    assert body["generationConfig"]["stopSequences"] == ["END"]
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "mock.echo"


@respx.mock
async def test_chat_maps_assistant_tool_calls_and_tool_results() -> None:
    route = respx.post(GENERATE).mock(return_value=httpx.Response(200, json=_completion()))
    provider = GeminiProvider(_settings())

    await provider.chat(
        _request(
            messages=(
                ModelMessage(
                    role=MessageRole.ASSISTANT,
                    content="",
                    tool_calls=(ToolCall(id="c1", name="mock.echo", arguments={"text": "x"}),),
                ),
                ModelMessage(
                    role=MessageRole.TOOL, content="result", name="mock.echo", tool_call_id="c1"
                ),
            )
        )
    )

    contents = _json_body(route)["contents"]
    assert contents[0]["role"] == "model"
    assert contents[0]["parts"] == [{"functionCall": {"name": "mock.echo", "args": {"text": "x"}}}]
    assert contents[1]["role"] == "user"
    assert contents[1]["parts"] == [
        {"functionResponse": {"name": "mock.echo", "response": {"content": "result"}}}
    ]


@respx.mock
async def test_chat_sends_the_key_in_a_header() -> None:
    route = respx.post(GENERATE).mock(return_value=httpx.Response(200, json=_completion()))
    provider = GeminiProvider(_settings())

    await provider.chat(_request())

    assert route.calls[0].request.headers["x-goog-api-key"] == "sk-test"


@respx.mock
async def test_chat_parses_a_function_call() -> None:
    respx.post(GENERATE).mock(return_value=httpx.Response(200, json=_tool_completion()))
    provider = GeminiProvider(_settings())

    response = await provider.chat(_request())

    assert response.has_tool_calls is True
    call = response.tool_calls[0]
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
    respx.post(GENERATE).mock(return_value=httpx.Response(status))
    provider = GeminiProvider(_settings())

    with pytest.raises(exc):
        await provider.chat(_request())


@respx.mock
async def test_a_timeout_is_typed() -> None:
    respx.post(GENERATE).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ModelTimeoutError):
        await GeminiProvider(_settings()).chat(_request())


@respx.mock
async def test_an_unreachable_host_is_typed() -> None:
    respx.post(GENERATE).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LocalModelUnavailableError):
        await GeminiProvider(_settings()).chat(_request())


@respx.mock
async def test_invalid_json_is_a_response_error() -> None:
    respx.post(GENERATE).mock(return_value=httpx.Response(200, text="not json"))
    with pytest.raises(ModelResponseInvalidError):
        await GeminiProvider(_settings()).chat(_request())


@respx.mock
async def test_no_candidates_is_a_response_error() -> None:
    respx.post(GENERATE).mock(return_value=httpx.Response(200, json={"candidates": []}))
    with pytest.raises(ModelResponseInvalidError):
        await GeminiProvider(_settings()).chat(_request())


# --------------------------------------------------------------------------- #
# streaming
# --------------------------------------------------------------------------- #
@respx.mock
async def test_stream_parses_text_and_the_terminal_chunk() -> None:
    body = (
        "data: "
        + json.dumps({"candidates": [{"content": {"parts": [{"text": "Hel"}]}}]})
        + "\n\n"
        + "data: "
        + json.dumps({"candidates": [{"content": {"parts": [{"text": "lo"}]}}]})
        + "\n\n"
        + "data: "
        + json.dumps(
            {
                "candidates": [{"content": {"parts": []}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
            }
        )
        + "\n\n"
    )
    respx.post(STREAM).mock(return_value=httpx.Response(200, text=body))
    provider = GeminiProvider(_settings())

    chunks = [chunk async for chunk in provider.stream(_request())]

    assert [chunk.delta for chunk in chunks if chunk.delta] == ["Hel", "lo"]
    final = chunks[-1]
    assert final.is_final
    assert final.finish_reason is FinishReason.STOP
    assert final.usage is not None and final.usage.completion_tokens == 2


@respx.mock
async def test_stream_forwards_a_function_call() -> None:
    body = (
        "data: "
        + json.dumps(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"functionCall": {"name": "mock.echo", "args": {"text": "x"}}}
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ]
            }
        )
        + "\n\n"
    )
    respx.post(STREAM).mock(return_value=httpx.Response(200, text=body))
    provider = GeminiProvider(_settings())

    chunks = [chunk async for chunk in provider.stream(_request())]

    assert next(c.tool_call for c in chunks if c.tool_call).name == "mock.echo"


@respx.mock
async def test_stream_maps_an_http_error() -> None:
    respx.post(STREAM).mock(return_value=httpx.Response(429))
    with pytest.raises(ModelRateLimitedError):
        _ = [chunk async for chunk in GeminiProvider(_settings()).stream(_request())]


@respx.mock
async def test_stream_maps_a_connect_error() -> None:
    respx.post(STREAM).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LocalModelUnavailableError):
        _ = [chunk async for chunk in GeminiProvider(_settings()).stream(_request())]


# --------------------------------------------------------------------------- #
# discovery, availability and health
# --------------------------------------------------------------------------- #
@respx.mock
async def test_list_models_strips_the_models_prefix() -> None:
    respx.get(f"{BASE}/v1beta/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "models": [
                    {"name": "models/gemini-1.5-flash"},
                    {"name": "models/gemini-1.5-pro"},
                ]
            },
        )
    )
    provider = GeminiProvider(_settings())

    assert await provider.list_models() == ["gemini-1.5-flash", "gemini-1.5-pro"]


@respx.mock
async def test_is_available_matches_the_model() -> None:
    respx.get(f"{BASE}/v1beta/models").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "models/gemini-1.5-flash"}]})
    )
    provider = GeminiProvider(_settings())

    assert await provider.is_available("gemini-1.5-flash") is True
    assert await provider.is_available("gemini:gemini-1.5-flash") is True
    assert await provider.is_available("models/gemini-1.5-flash") is True
    assert await provider.is_available("ghost") is False


@respx.mock
async def test_is_available_is_false_when_unreachable() -> None:
    respx.get(f"{BASE}/v1beta/models").mock(side_effect=httpx.ConnectError("refused"))
    assert await GeminiProvider(_settings()).is_available("gemini-1.5-flash") is False


@respx.mock
async def test_health_reports_available() -> None:
    respx.get(f"{BASE}/v1beta/models").mock(return_value=httpx.Response(200, json={"models": []}))
    health = await GeminiProvider(_settings()).health()
    assert health.status.value == "available"


@respx.mock
async def test_health_reports_rejected_credentials_without_raising() -> None:
    respx.get(f"{BASE}/v1beta/models").mock(return_value=httpx.Response(401))
    health = await GeminiProvider(_settings()).health()
    assert health.status.value == "unavailable"
    assert health.detail


# --------------------------------------------------------------------------- #
# client ownership
# --------------------------------------------------------------------------- #
async def test_the_adapter_closes_a_client_it_owns() -> None:
    provider = GeminiProvider(_settings())
    client = provider._http()

    await provider.aclose()

    assert client.is_closed


async def test_the_adapter_leaves_an_injected_client_open() -> None:
    injected = httpx.AsyncClient()
    provider = GeminiProvider(_settings(), client=injected)

    await provider.aclose()

    assert injected.is_closed is False
    await injected.aclose()
