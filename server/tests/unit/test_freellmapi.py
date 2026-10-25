"""Unit tests for the local free-LLM router adapter.

The router is OpenAI-compatible, so the adapter is the T062
:class:`~app.models.openai.OpenAIProvider` pointed at the gateway's base URL
with the gateway key. These tests pin the three things that make it a distinct
provider to the router (T066): its provider ``name`` is ``"freellmapi"`` so
``freellmapi:<model>`` resolves here, its configuration comes from the
``freellmapi_*`` settings (an absent key is *unavailable*, not broken), and a
rate-limited upstream slot maps onto ``ModelRateLimitedError`` so the existing
fallback chain can move on.

``respx`` intercepts the transport; no live network is touched.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from app.config import DEFAULT_FREELLMAPI_MODELS, ProviderSettings
from app.core.errors import ModelRateLimitedError, ProviderNotConfiguredError
from app.models.base import CompletionRequest, ModelMessage
from app.models.freellmapi import FreeLLMProvider

pytestmark = pytest.mark.unit

BASE = "http://127.0.0.1:31999/v1"


def _settings(**overrides: Any) -> ProviderSettings:
    values: dict[str, Any] = {
        "freellmapi_api_key": "freellmapi-test",
        "freellmapi_base_url": BASE,
        "freellmapi_models": ("auto", "qwen3.8-27b"),
    }
    values.update(overrides)
    return ProviderSettings(**values)


def _completion(content: str = "answer", model: str = "auto") -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 5},
    }


def test_provider_name_is_freellmapi() -> None:
    assert FreeLLMProvider.name == "freellmapi"
    assert FreeLLMProvider(settings=_settings()).name == "freellmapi"


def test_unconfigured_without_key() -> None:
    assert FreeLLMProvider(settings=_settings(freellmapi_api_key="")).is_configured is False


def test_configured_with_key() -> None:
    assert FreeLLMProvider(settings=_settings()).is_configured is True


def test_uses_router_base_url_and_bearer_key() -> None:
    provider = FreeLLMProvider(settings=_settings())
    assert provider._url == BASE
    assert provider._authorization == "Bearer freellmapi-test"


def test_default_picker_models_are_prefixed_ids() -> None:
    """The built-in shortlist is what the Settings picker offers by default."""
    assert "auto" in DEFAULT_FREELLMAPI_MODELS
    assert "qwen3.8-27b" in DEFAULT_FREELLMAPI_MODELS


@respx.mock
async def test_chat_posts_to_router_and_strips_its_prefix() -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=respx.MockResponse(200, json=_completion())
    )
    provider = FreeLLMProvider(settings=_settings())
    request = CompletionRequest(
        messages=(ModelMessage(role="user", content="hi"),),
        model="freellmapi:auto",
    )

    response = await provider.chat(request)

    assert route.called
    assert response.content == "answer"
    assert response.provider == "freellmapi"
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer freellmapi-test"
    assert json.loads(sent.content)["model"] == "auto"


async def test_chat_without_key_raises_provider_not_configured() -> None:
    provider = FreeLLMProvider(settings=_settings(freellmapi_api_key=""))
    request = CompletionRequest(
        messages=(ModelMessage(role="user", content="hi"),),
        model="freellmapi:auto",
    )
    with pytest.raises(ProviderNotConfiguredError):
        await provider.chat(request)


@respx.mock
async def test_rate_limited_slot_is_a_fallback_error() -> None:
    """A 429 from the gateway must be retryable by the model router (§59.9)."""
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(429))
    provider = FreeLLMProvider(settings=_settings())
    request = CompletionRequest(
        messages=(ModelMessage(role="user", content="hi"),),
        model="freellmapi:auto",
    )

    with pytest.raises(ModelRateLimitedError):
        await provider.chat(request)


@respx.mock
async def test_is_available_reads_the_gateway_model_list() -> None:
    respx.get(f"{BASE}/models").mock(
        return_value=respx.MockResponse(
            200,
            json={"object": "list", "data": [{"id": "auto"}, {"id": "qwen3.8-27b"}]},
        )
    )
    provider = FreeLLMProvider(settings=_settings())

    assert await provider.is_available("freellmapi:auto") is True
    assert await provider.is_available("freellmapi:not-a-model") is False
