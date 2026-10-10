"""Unit tests for the model-provider ABC and its vocabulary (T060).

The ABC's job is to make a provider's *defaults* honest and its *declarations*
mandatory, so the tests split the same way the tool/agent base tests do: the
concrete provider is checked for what it inherits (a real one-chunk stream from
``chat``, health that only claims what it tested, discovery that returns
nothing rather than lying), and the definition-time guards are checked for
refusing a provider that ships without a name or with a sync ``chat`` that
would explode on the router's first await.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, ClassVar

import pytest

from app.config.settings import ModelRouterSettings
from app.database.models import MessageRole
from app.models.base import (
    CompletionRequest,
    FinishReason,
    ModelCapability,
    ModelMessage,
    ModelProvider,
    ModelResponse,
    ModelStreamChunk,
    ProviderHealth,
    ProviderStatus,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)

pytestmark = pytest.mark.unit


class EchoProvider(ModelProvider):
    """The smallest legal provider: a name, a configuration, one async chat."""

    name = "echo"

    def __init__(self, *, configured: bool = True, content: str = "hi") -> None:
        self._configured = configured
        self._content = content

    @property
    def is_configured(self) -> bool:
        return self._configured

    async def chat(self, request: CompletionRequest) -> ModelResponse:
        return ModelResponse(
            model=request.model,
            content=self._content,
            provider=self.name,
            usage=TokenUsage(prompt_tokens=1, completion_tokens=2),
        )


def _request(**overrides: Any) -> CompletionRequest:
    values: dict[str, Any] = {
        "messages": (ModelMessage(role=MessageRole.USER, content="hello"),),
        "model": "test-model",
    }
    values.update(overrides)
    return CompletionRequest(**values)


# --------------------------------------------------------------------------- #
# ABC shape and honest defaults
# --------------------------------------------------------------------------- #
def test_a_provider_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        ModelProvider()  # type: ignore[abstract]


def test_a_concrete_provider_exposes_its_declarations() -> None:
    provider = EchoProvider()
    assert provider.name == "echo"
    # Defaults: a provider is presumed remote, tool-capable, non-streaming.
    assert EchoProvider.local is False
    assert EchoProvider.supports_tools is True
    assert EchoProvider.supports_streaming is False
    assert provider.is_configured is True


async def test_chat_returns_the_provider_response() -> None:
    response = await EchoProvider(content="answer").chat(_request())
    assert response.content == "answer"
    assert response.provider == "echo"
    assert response.usage == TokenUsage(prompt_tokens=1, completion_tokens=2)


async def test_the_default_stream_degrades_to_one_terminal_chunk() -> None:
    """§21/§33: a provider that cannot stream is still a complete provider."""
    chunks = [chunk async for chunk in EchoProvider(content="streamed").stream(_request())]
    assert [c.delta for c in chunks if c.delta] == ["streamed"]
    final = chunks[-1]
    assert final.is_final
    assert final.finish_reason is FinishReason.STOP
    assert final.usage == TokenUsage(prompt_tokens=1, completion_tokens=2)
    assert all(c.model == "test-model" for c in chunks)


async def test_the_default_stream_forwards_tool_calls() -> None:
    call = ToolCall(id="call-1", name="mock.echo", arguments={"text": "x"})

    class ToolCallProvider(EchoProvider):
        async def chat(self, request: CompletionRequest) -> ModelResponse:
            return ModelResponse(
                model=request.model,
                tool_calls=(call,),
                finish_reason=FinishReason.TOOL_CALLS,
            )

    chunks = [chunk async for chunk in ToolCallProvider(content="").stream(_request())]
    assert [c.tool_call for c in chunks if c.tool_call] == [call]
    assert chunks[-1].finish_reason is FinishReason.TOOL_CALLS


async def test_default_health_reports_configuration_not_liveness() -> None:
    """§17: never assume success — an unprobed provider claims only what it knows."""
    healthy = await EchoProvider().health()
    assert healthy.status is ProviderStatus.AVAILABLE
    assert healthy.is_usable

    unhealthy = await EchoProvider(configured=False).health()
    assert unhealthy.status is ProviderStatus.UNAVAILABLE
    assert not unhealthy.is_usable
    assert "echo" in unhealthy.detail


async def test_default_discovery_is_empty_and_availability_follows_config() -> None:
    provider = EchoProvider()
    assert await provider.list_models() == []
    assert await provider.is_available("test-model") is True
    assert await EchoProvider(configured=False).is_available("test-model") is False
    # Closing a provider that opened nothing is a no-op, not an error.
    await provider.aclose()


# --------------------------------------------------------------------------- #
# Value objects
# --------------------------------------------------------------------------- #
def test_token_usage_sums_its_own_numbers() -> None:
    assert TokenUsage(prompt_tokens=3, completion_tokens=4).total_tokens == 7
    assert TokenUsage().total_tokens == 0


def test_model_response_reports_tool_calls() -> None:
    assert ModelResponse(model="m").has_tool_calls is False
    response = ModelResponse(model="m", tool_calls=(ToolCall(id="1", name="t"),))
    assert response.has_tool_calls is True


def test_stream_chunk_is_final_only_with_a_finish_reason() -> None:
    assert ModelStreamChunk(delta="x").is_final is False
    assert ModelStreamChunk(finish_reason=FinishReason.STOP).is_final is True


def test_provider_health_usable_unless_unavailable() -> None:
    assert ProviderHealth(status=ProviderStatus.AVAILABLE).is_usable
    assert ProviderHealth(status=ProviderStatus.DEGRADED).is_usable
    assert not ProviderHealth(status=ProviderStatus.UNAVAILABLE).is_usable


def test_capabilities_are_the_router_settings_keys() -> None:
    """The capability model must not drift from the configuration it resolves."""
    capability_names = {member.value for member in ModelCapability}
    assert capability_names == {
        "default",
        "fast",
        "coding",
        "research",
        "vision",
        "reasoning",
    }
    settings = ModelRouterSettings()
    for name in capability_names:
        assert settings.for_capability(name)


def test_messages_reuse_the_store_vocabulary() -> None:
    """A transcript the provider sees and one the database keeps must agree."""
    assert ModelMessage(role=MessageRole.TOOL, tool_call_id="c1").role is MessageRole.TOOL
    definition = ToolDefinition(name="t", description="d", parameters={"type": "object"})
    assert definition.parameters["type"] == "object"


# --------------------------------------------------------------------------- #
# Definition-time guards
# --------------------------------------------------------------------------- #
class _ConfiguredMixIn:
    @property
    def is_configured(self) -> bool:
        return True


def test_a_provider_without_a_name_fails_to_define() -> None:
    async def chat(self: ModelProvider, request: CompletionRequest) -> ModelResponse:
        return ModelResponse(model=request.model)

    with pytest.raises(TypeError, match="name"):
        type(
            "NoName",
            (_ConfiguredMixIn, ModelProvider),
            {"chat": chat},
        )


def test_a_provider_with_a_blank_name_fails_to_define() -> None:
    async def chat(self: ModelProvider, request: CompletionRequest) -> ModelResponse:
        return ModelResponse(model=request.model)

    with pytest.raises(TypeError, match="name"):
        type(
            "BlankName",
            (_ConfiguredMixIn, ModelProvider),
            {"name": "   ", "chat": chat},
        )


def test_chat_must_be_async() -> None:
    """A sync chat passes most type checkers and explodes on the first await."""

    def sync_chat(self: ModelProvider, request: CompletionRequest) -> ModelResponse:
        return ModelResponse(model=request.model)

    with pytest.raises(TypeError, match="async"):
        type(
            "SyncProvider",
            (_ConfiguredMixIn, ModelProvider),
            {"name": "sync", "chat": sync_chat},
        )


def test_an_abstract_provider_may_still_be_undeclared() -> None:
    """A work-in-progress provider is not selectable, so its name is due later."""

    class WorkInProgress(ModelProvider):
        name: ClassVar[str] = "wip"

        async def chat(self, request: CompletionRequest) -> ModelResponse:
            raise NotImplementedError

        async def stream(self, request: CompletionRequest) -> AsyncIterator[ModelStreamChunk]:
            yield ModelStreamChunk(model=request.model)

    # Still abstract because ``is_configured`` is unimplemented.
    assert WorkInProgress.__abstractmethods__ == {"is_configured"}
