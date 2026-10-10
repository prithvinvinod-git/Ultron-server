"""Unit tests for the model router (T066).

The router is where §49 (selection inputs) and §66.6 (fallback, health tracking,
temporary circuit breaking) meet, so the tests are a behaviour matrix: capability
and fallback resolution, the latency/local/user-preference reordering, the
availability and resource filters, the fallback-and-retry path, and the circuit
breaker's open/close lifecycle. Every provider is a ``FakeProvider`` (T065), so
the matrix runs offline and deterministically — the router is exercised through
the *same* ``ModelProvider`` contract the real adapters implement.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest

from app.config import ModelRouterSettings
from app.core.errors import (
    LocalModelUnavailableError,
    ModelNotSupportedError,
    ModelTimeoutError,
    ProviderNotConfiguredError,
)
from app.database.models import MessageRole
from app.models.base import (
    CompletionRequest,
    FinishReason,
    ModelMessage,
    ModelStreamChunk,
    ProviderHealth,
    ProviderStatus,
)
from app.models.fake import DEFAULT_RESPONSE, FakeProvider
from app.models.router import ModelRouter, ModelSelection

pytestmark = pytest.mark.unit


def _request(**overrides: Any) -> CompletionRequest:
    values: dict[str, Any] = {
        "messages": (ModelMessage(role=MessageRole.USER, content="hello"),),
        "model": "unused",
    }
    values.update(overrides)
    return CompletionRequest(**values)


def _settings(**overrides: Any) -> ModelRouterSettings:
    values: dict[str, Any] = {
        "default": "ollama:llama3",
        "fallback_chain": ["openai:gpt-4o-mini"],
        "max_retries": 0,
        "retry_backoff": 1.0,
    }
    values.update(overrides)
    return ModelRouterSettings(**values)


def _providers() -> tuple[FakeProvider, FakeProvider]:
    local = FakeProvider(name="ollama", local=True)
    cloud = FakeProvider(name="openai", local=False)
    return local, cloud


class _CountingFake(FakeProvider):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.health_calls = 0

    async def health(self) -> ProviderHealth:
        self.health_calls += 1
        return await super().health()


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def publish(
        self, event_type: str, payload: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        self.events.append((event_type, dict(payload or {})))


# --------------------------------------------------------------------------- #
# Registry and candidate derivation
# --------------------------------------------------------------------------- #
def test_registry_lists_providers() -> None:
    local, cloud = _providers()
    router = ModelRouter([local, cloud], settings=_settings())
    assert set(router.provider_names) == {"ollama", "openai"}
    assert router.get("ollama") is local
    assert router.get("ghost") is None


def test_candidate_models_are_capability_then_fallback_deduplicated() -> None:
    router = ModelRouter(
        [], settings=_settings(default="ollama:a", fallback_chain=["ollama:b", "ollama:a"])
    )
    assert router.candidate_models("default") == ["ollama:a", "ollama:b"]


# --------------------------------------------------------------------------- #
# select
# --------------------------------------------------------------------------- #
async def test_select_returns_the_capability_model() -> None:
    local, cloud = _providers()
    router = ModelRouter([local, cloud], settings=_settings())

    selection = await router.select(capability="default")

    assert selection == ModelSelection("ollama", "llama3", "ollama:llama3", "capability")


async def test_select_falls_back_when_the_primary_is_unavailable() -> None:
    local = FakeProvider(name="ollama", local=True, available=False)
    cloud = FakeProvider(name="openai", local=False)
    router = ModelRouter([local, cloud], settings=_settings())

    selection = await router.select(capability="default")

    assert selection.provider == "openai"
    assert selection.reason == "fallback"


async def test_select_ignores_unregistered_and_unconfigured_providers() -> None:
    cloud = FakeProvider(name="openai", local=False)
    router = ModelRouter([cloud], settings=_settings())
    # ollama is neither registered nor configured; openai is the fallback.
    selection = await router.select(capability="default")
    assert selection.provider == "openai"


async def test_select_with_no_candidates_is_not_supported() -> None:
    router = ModelRouter([], settings=_settings())

    with pytest.raises(ModelNotSupportedError):
        await router.select(capability="default")


async def test_select_when_all_candidates_are_down_is_local_model_unavailable() -> None:
    local = FakeProvider(name="ollama", local=True, available=False)
    cloud = FakeProvider(name="openai", local=False, available=False)
    router = ModelRouter([local, cloud], settings=_settings())

    with pytest.raises(LocalModelUnavailableError) as caught:
        await router.select(capability="default")

    assert caught.value.retryable is True


async def test_speed_fast_prefers_the_local_provider() -> None:
    local, cloud = _providers()
    router = ModelRouter(
        [local, cloud],
        settings=_settings(default="openai:gpt-4o-mini", fallback_chain=["ollama:llama3"]),
    )

    fast = await router.select(capability="default", speed="fast")
    quality = await router.select(capability="default", speed="quality")

    assert fast.provider == "ollama"
    assert quality.provider == "openai"


async def test_prefer_local_overrides_speed() -> None:
    local, cloud = _providers()
    router = ModelRouter(
        [local, cloud],
        settings=_settings(default="openai:gpt-4o-mini", fallback_chain=["ollama:llama3"]),
    )

    assert (await router.select(capability="default", prefer_local=True)).provider == "ollama"
    assert (await router.select(capability="default", prefer_local=False)).provider == "openai"


async def test_user_preference_reorders_candidates() -> None:
    local, cloud = _providers()
    router = ModelRouter(
        [local, cloud],
        settings=_settings(default="openai:gpt-4o-mini", fallback_chain=["ollama:llama3"]),
    )

    selection = await router.select(capability="default", preferred=["ollama"])

    assert selection.provider == "ollama"


async def test_tools_and_streaming_filters_exclude_capability() -> None:
    strict = FakeProvider(name="ollama", local=True, supports_tools=False, supports_streaming=False)
    flexible = FakeProvider(name="openai", local=False)
    router = ModelRouter([strict, flexible], settings=_settings())

    assert (await router.select(capability="default", tools=True)).provider == "openai"
    assert (await router.select(capability="default", streaming=True)).provider == "openai"


async def test_resource_guard_and_in_flight_cap_skip_a_provider() -> None:
    local, cloud = _providers()
    guard = ModelRouter(
        [local, cloud], settings=_settings(), resource_guard=lambda name: name != "ollama"
    )
    assert (await guard.select(capability="default")).provider == "openai"

    capped = ModelRouter([local, cloud], settings=_settings(), max_in_flight=1)
    capped._enter("ollama")
    assert (await capped.select(capability="default")).provider == "openai"


# --------------------------------------------------------------------------- #
# complete: defaults, fallback, retries
# --------------------------------------------------------------------------- #
async def test_complete_applies_per_capability_defaults_and_strips_prefix() -> None:
    local, cloud = _providers()
    router = ModelRouter(
        [local, cloud], settings=_settings(temperature=0.3, max_output_tokens=2048)
    )

    await router.complete(_request(), capability="default")

    sent = local.last_request
    assert sent is not None
    assert sent.model == "llama3"
    assert sent.temperature == 0.3
    assert sent.max_tokens == 2048


async def test_complete_returns_the_selected_provider_response() -> None:
    local, cloud = _providers()
    router = ModelRouter([local, cloud], settings=_settings())

    response = await router.complete(_request(), capability="default")

    assert response.provider == "ollama"
    assert response.model == "llama3"
    assert response.content == DEFAULT_RESPONSE


async def test_complete_falls_back_to_the_next_provider() -> None:
    local = FakeProvider(name="ollama", local=True)
    cloud = FakeProvider(name="openai", local=False)
    local.queue_error(ModelTimeoutError("ollama", "down"))
    router = ModelRouter([local, cloud], settings=_settings())

    response = await router.complete(_request())

    assert response.provider == "openai"
    assert local.call_count == 1


async def test_complete_retries_a_retryable_error_then_succeeds() -> None:
    local = FakeProvider(name="ollama", local=True)
    local.queue_error(ModelTimeoutError("ollama", "slow"))
    local.queue_error(ModelTimeoutError("ollama", "slow"))
    delays: list[float] = []

    async def _sleep(seconds: float) -> None:
        delays.append(seconds)

    router = ModelRouter([local], settings=_settings(max_retries=2), sleep=_sleep)

    response = await router.complete(_request())

    assert response.provider == "ollama"
    assert local.call_count == 3
    assert len(delays) == 2


async def test_complete_raises_a_non_retryable_error() -> None:
    local = FakeProvider(name="ollama", local=True)
    local.queue_error(ProviderNotConfiguredError("ollama", "no key"))
    router = ModelRouter([local], settings=_settings())

    with pytest.raises(ProviderNotConfiguredError):
        await router.complete(_request())


async def test_complete_with_no_candidates_is_not_supported() -> None:
    router = ModelRouter([], settings=_settings())
    with pytest.raises(ModelNotSupportedError):
        await router.complete(_request())


async def test_complete_publishes_a_selection_event() -> None:
    local, cloud = _providers()
    bus = _Bus()
    router = ModelRouter([local, cloud], settings=_settings(), bus=bus)

    await router.complete(_request())

    assert bus.events[0][0] == "MODEL_SELECTED"
    assert bus.events[0][1]["provider"] == "ollama"
    assert bus.events[0][1]["model"] == "llama3"


# --------------------------------------------------------------------------- #
# stream
# --------------------------------------------------------------------------- #
async def test_stream_yields_from_the_selected_provider() -> None:
    local, cloud = _providers()
    local.queue_stream(
        ModelStreamChunk(delta="hi", model="llama3"),
        ModelStreamChunk(finish_reason=FinishReason.STOP, model="llama3"),
    )
    router = ModelRouter([local, cloud], settings=_settings())

    chunks = [chunk async for chunk in router.stream(_request())]

    assert [chunk.delta for chunk in chunks if chunk.delta] == ["hi"]
    assert chunks[-1].is_final


async def test_stream_falls_back_before_any_output() -> None:
    local = FakeProvider(name="ollama", local=True)
    cloud = FakeProvider(name="openai", local=False)
    local.queue_error(LocalModelUnavailableError("ollama", "down"))
    router = ModelRouter([local, cloud], settings=_settings())

    chunks = [chunk async for chunk in router.stream(_request())]

    assert any(chunk.delta == DEFAULT_RESPONSE for chunk in chunks)


async def test_stream_does_not_fall_back_after_output_starts() -> None:
    class _Partial(FakeProvider):
        async def stream(self, request: CompletionRequest) -> AsyncIterator[ModelStreamChunk]:
            yield ModelStreamChunk(delta="partial")
            raise LocalModelUnavailableError(self.name, "died mid-stream")

    local = _Partial(name="ollama", local=True)
    cloud = FakeProvider(name="openai", local=False)
    router = ModelRouter([local, cloud], settings=_settings())

    with pytest.raises(LocalModelUnavailableError):
        _ = [chunk async for chunk in router.stream(_request())]


# --------------------------------------------------------------------------- #
# Health tracking and circuit breaking (§66.6)
# --------------------------------------------------------------------------- #
async def test_health_snapshot_is_cached_until_ttl() -> None:
    provider = _CountingFake(name="ollama", local=True, health_status=ProviderStatus.AVAILABLE)
    router = ModelRouter([provider], settings=_settings(), health_ttl=60.0)

    first = await router.health_snapshot()
    second = await router.health_snapshot()

    assert first["ollama"].status is ProviderStatus.AVAILABLE
    assert second["ollama"].status is ProviderStatus.AVAILABLE
    assert provider.health_calls == 1


async def test_circuit_opens_after_repeated_failures_and_closes_after_cooldown() -> None:
    clock = [0.0]
    local = FakeProvider(name="ollama", local=True)
    cloud = FakeProvider(name="openai", local=False)
    router = ModelRouter(
        [local, cloud],
        settings=_settings(),
        circuit_threshold=1,
        circuit_cooldown=10.0,
        monotonic=lambda: clock[0],
    )
    local.queue_error(ModelTimeoutError("ollama", "down"))

    await router.complete(_request())

    assert router.is_circuit_open("ollama") is True
    assert (await router.select(capability="default")).provider == "openai"

    clock[0] = 11.0
    assert router.is_circuit_open("ollama") is False
    assert (await router.select(capability="default")).provider == "ollama"


async def test_a_success_clears_the_failure_count() -> None:
    local = FakeProvider(name="ollama", local=True)
    router = ModelRouter([local], settings=_settings(), circuit_threshold=3)

    router.record_failure("ollama")
    router.record_success("ollama")

    assert router.failure_counts.get("ollama") is None


# --------------------------------------------------------------------------- #
# Events and inspection
# --------------------------------------------------------------------------- #
async def test_model_selected_event_is_published_to_the_bus() -> None:
    local, cloud = _providers()
    bus = _Bus()
    router = ModelRouter([local, cloud], settings=_settings(), bus=bus)

    await router.complete(_request())

    assert bus.events[0][0] == "MODEL_SELECTED"
    assert bus.events[0][1]["provider"] == "ollama"


def test_in_flight_returns_to_zero() -> None:
    router = ModelRouter([], settings=_settings())
    router._enter("ollama")
    assert router.in_flight("ollama") == 1
    router._leave("ollama")
    assert router.in_flight("ollama") == 0


async def test_aclose_closes_every_provider() -> None:
    class _Closer(FakeProvider):
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    provider = _Closer(name="ollama")
    router = ModelRouter([provider], settings=_settings())

    await router.aclose()

    assert provider.closed is True
