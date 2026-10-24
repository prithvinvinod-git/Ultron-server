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

import respx

from app.config import ProviderSettings
from app.core.errors import ProviderNotConfiguredError
from app.models.base import CompletionRequest, ModelMessage
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
