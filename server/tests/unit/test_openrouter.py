"""Unit tests for the OpenRouter adapter (spec §59.7).

OpenRouter is OpenAI-compatible, so :class:`OpenRouterProvider` is the T062
:class:`~app.models.openai.OpenAIProvider` pointed at OpenRouter's base URL with
its own key. These tests pin the two things that make it a *distinct* provider
to the router (T066): its provider ``name`` is ``"openrouter"`` so
``openrouter:<slug>`` resolves to it, and its configuration is read from the
``openrouter_*`` settings — including the §3 rule that an absent key leaves it
*unavailable*, not broken.

``respx`` intercepts the real httpx transport; no live network is touched.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx

from app.config import ProviderSettings
from app.core.errors import ProviderNotConfiguredError
from app.models.base import CompletionRequest, ModelMessage, ToolDefinition
from app.models.openrouter import OpenRouterProvider

import pytest

pytestmark = pytest.mark.unit

BASE = "https://openrouter.test/api/v1"


def _settings(**overrides: Any) -> ProviderSettings:
    values: dict[str, Any] = {
        "openrouter_api_key": "sk-or-test",
        "openrouter_base_url": BASE,
    }
    values.update(overrides)
    return ProviderSettings(**values)


def _completion(content: str = "answer") -> dict[str, Any]:
    return {
        "id": "gen-1",
        "model": "nvidia/nemotron-3.5-lightning:free",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 5},
    }


def test_provider_name_is_openrouter() -> None:
    assert OpenRouterProvider.name == "openrouter"
    assert OpenRouterProvider(settings=_settings()).name == "openrouter"


def test_unconfigured_without_key() -> None:
    provider = OpenRouterProvider(settings=_settings(openrouter_api_key=""))
    assert provider.is_configured is False


def test_configured_with_key() -> None:
    provider = OpenRouterProvider(settings=_settings())
    assert provider.is_configured is True


def test_uses_openrouter_base_url_and_bearer_key() -> None:
    provider = OpenRouterProvider(settings=_settings())
    assert provider._url == BASE
    assert provider._authorization == "Bearer sk-or-test"


@respx.mock
async def test_chat_posts_to_openrouter_endpoint() -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=respx.MockResponse(200, json=_completion())
    )
    provider = OpenRouterProvider(settings=_settings())
    request = CompletionRequest(
        messages=(ModelMessage(role="user", content="hi"),),
        model="openrouter:nvidia/nemotron-3.5-lightning:free",
    )

    response = await provider.chat(request)

    assert route.called
    assert response.content == "answer"
    assert response.provider == "openrouter"
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer sk-or-test"
    payload = json.loads(sent.content)
    assert payload["model"] == "nvidia/nemotron-3.5-lightning:free"


async def test_chat_without_key_raises_provider_not_configured() -> None:
    provider = OpenRouterProvider(settings=_settings(openrouter_api_key=""))
    request = CompletionRequest(
        messages=(ModelMessage(role="user", content="hi"),),
        model="openrouter:some/model:free",
    )
    with pytest.raises(ProviderNotConfiguredError):
        await provider.chat(request)


# --------------------------------------------------------------------------- #
# streaming
# --------------------------------------------------------------------------- #
def _sse(*events: dict[str, Any]) -> str:
    lines = [f"data: {json.dumps(event)}" for event in events]
    lines.append("data: [DONE]")
    return "\n\n".join(lines) + "\n\n"


@respx.mock
async def test_stream_is_an_async_iterator() -> None:
    """Regression: the override used to return a coroutine, so ``async for`` failed.

    ``OpenAIProvider.stream`` is an async generator; a plain ``async def``
    wrapper is not iterable. Every streamed turn that fell back to OpenRouter
    died with "requires an object with __aiter__ method, got coroutine".
    """
    body = _sse(
        {
            "model": "nvidia/nemotron-3.5-lightning:free",
            "choices": [{"index": 0, "delta": {"content": "Hi"}}],
        },
        {
            "model": "nvidia/nemotron-3.5-lightning:free",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        },
    )
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, text=body))
    provider = OpenRouterProvider(settings=_settings())
    request = CompletionRequest(
        messages=(ModelMessage(role="user", content="hi"),),
        model="openrouter:nvidia/nemotron-3.5-lightning:free",
    )

    chunks = [chunk async for chunk in provider.stream(request)]

    assert "".join(chunk.delta for chunk in chunks) == "Hi"
    assert chunks[-1].is_final


@respx.mock
async def test_chat_maps_dotted_tool_names_onto_the_openrouter_wire() -> None:
    """OpenRouter rejects dotted function names with HTTP 400, so they are mapped."""
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=respx.MockResponse(
            200,
            json={
                "model": "nvidia/nemotron-3.5-lightning:free",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "filesystem_delete",
                                        "arguments": '{"path": "notes.txt"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
        )
    )
    provider = OpenRouterProvider(settings=_settings())
    request = CompletionRequest(
        messages=(ModelMessage(role="user", content="delete it"),),
        model="openrouter:nvidia/nemotron-3.5-lightning:free",
        tools=(ToolDefinition(name="filesystem.delete", description="delete"),),
    )

    response = await provider.chat(request)

    payload = json.loads(route.calls.last.request.content)
    assert payload["tools"][0]["function"]["name"] == "filesystem_delete"
    assert response.tool_calls[0].name == "filesystem.delete"
    assert response.tool_calls[0].arguments == {"path": "notes.txt"}
