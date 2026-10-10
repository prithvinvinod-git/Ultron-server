"""Unit tests for the scripted fake provider (T065).

The fake is infrastructure for T066-T069, so the tests pin the behaviour those
tests will depend on: deterministic defaults, FIFO scripting, request recording,
and — importantly — that the fake honours the *same* ``ModelProvider`` contract
the real adapters do, including a configurable unavailable state.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.errors import ProviderNotConfiguredError
from app.database.models import MessageRole
from app.models.base import (
    CompletionRequest,
    FinishReason,
    ModelMessage,
    ModelResponse,
    ModelStreamChunk,
    ProviderStatus,
    TokenUsage,
)
from app.models.fake import DEFAULT_RESPONSE, FakeProvider

pytestmark = pytest.mark.unit


def _request(**overrides: Any) -> CompletionRequest:
    values: dict[str, Any] = {
        "messages": (ModelMessage(role=MessageRole.USER, content="hello"),),
        "model": "fake-1",
    }
    values.update(overrides)
    return CompletionRequest(**values)


def test_the_fake_declares_itself_like_a_provider() -> None:
    provider = FakeProvider()
    assert provider.name == "fake"
    assert provider.local is True
    assert provider.supports_tools is True
    assert provider.supports_streaming is True
    assert provider.is_configured is True
    assert "fake" in repr(provider)


async def test_default_chat_is_deterministic() -> None:
    provider = FakeProvider()

    response = await provider.chat(_request(model="fake-1"))

    assert response.content == DEFAULT_RESPONSE
    assert response.model == "fake-1"
    assert response.provider == "fake"
    assert response.finish_reason is FinishReason.STOP
    assert response.usage is not None
    assert provider.call_count == 1


def test_a_custom_name_is_recorded_per_instance() -> None:
    assert FakeProvider("alpha").name == "alpha"
    assert FakeProvider("beta").name == "beta"


async def test_queued_responses_are_returned_in_order() -> None:
    provider = FakeProvider()
    first = ModelResponse(model="fake-1", content="first", provider="fake")
    second = ModelResponse(model="fake-1", content="second", provider="fake")
    provider.queue(first).queue(second)

    assert (await provider.chat(_request())).content == "first"
    assert (await provider.chat(_request())).content == "second"
    assert (await provider.chat(_request())).content == DEFAULT_RESPONSE


async def test_a_queued_error_is_raised_once() -> None:
    provider = FakeProvider()
    provider.queue_error(ProviderNotConfiguredError("fake", "boom"))

    with pytest.raises(ProviderNotConfiguredError):
        await provider.chat(_request())

    assert (await provider.chat(_request())).content == DEFAULT_RESPONSE


async def test_requests_are_recorded_in_order() -> None:
    provider = FakeProvider()
    first = _request(model="a")
    second = _request(model="b")

    await provider.chat(first)
    await provider.chat(second)

    assert provider.requests == [first, second]
    assert provider.call_count == 2
    assert provider.last_request == second


async def test_stream_degrades_to_a_single_terminal_chunk() -> None:
    provider = FakeProvider()

    chunks = [chunk async for chunk in provider.stream(_request())]

    assert [chunk.delta for chunk in chunks if chunk.delta] == [DEFAULT_RESPONSE]
    assert chunks[-1].is_final
    assert chunks[-1].finish_reason is FinishReason.STOP


async def test_a_queued_stream_is_yielded_verbatim() -> None:
    provider = FakeProvider()
    provider.queue_stream(
        ModelStreamChunk(delta="Hel", model="m"),
        ModelStreamChunk(delta="lo", model="m"),
        ModelStreamChunk(finish_reason=FinishReason.STOP, usage=TokenUsage(), model="m"),
    )

    chunks = [chunk async for chunk in provider.stream(_request())]

    assert [chunk.delta for chunk in chunks if chunk.delta] == ["Hel", "lo"]
    assert chunks[-1].is_final


async def test_stream_can_replay_a_queued_response() -> None:
    provider = FakeProvider()
    provider.queue(
        ModelResponse(
            model="m", content="answer", provider="fake", finish_reason=FinishReason.LENGTH
        )
    )

    chunks = [chunk async for chunk in provider.stream(_request())]

    assert [chunk.delta for chunk in chunks if chunk.delta] == ["answer"]
    assert chunks[-1].finish_reason is FinishReason.LENGTH


async def test_an_unconfigured_fake_is_unavailable_not_broken() -> None:
    provider = FakeProvider(configured=False)

    assert provider.is_configured is False
    assert await provider.list_models() == []
    assert await provider.is_available("anything") is False
    with pytest.raises(ProviderNotConfiguredError):
        await provider.chat(_request())


async def test_availability_can_be_a_flag_or_a_model_set() -> None:
    always = FakeProvider(available=True)
    never = FakeProvider(available=False)
    some = FakeProvider(available=["llama3:latest", "qwen2.5"])

    assert await always.is_available("anything") is True
    assert await never.is_available("anything") is False
    assert await some.is_available("llama3:latest") is True
    assert await some.is_available("qwen2.5") is True
    assert await some.is_available("ghost") is False


async def test_list_models_and_health_are_scripted() -> None:
    provider = FakeProvider(
        models=["m1", "m2"],
        health_status=ProviderStatus.DEGRADED,
        health_detail="slow",
    )

    assert await provider.list_models() == ["m1", "m2"]
    health = await provider.health()
    assert health.status is ProviderStatus.DEGRADED
    assert health.detail == "slow"


async def test_aclose_is_a_no_op() -> None:
    provider = FakeProvider()
    await provider.aclose()
