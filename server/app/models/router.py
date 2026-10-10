"""The model router (T066): one place that chooses *which* model answers.

Spec §20 gives the system *one* abstraction with several providers behind it and
a router that chooses among them "by configuration"; §49 lists what selection
must consider — **task type, capability, latency, availability, resource usage,
provider, user preference** — and insists it "must remain configurable"; §66.6
extends the same router with **fallback and provider health tracking plus
temporary circuit breaking** and repeats the hard rule: providers stay
config-selected and **no provider type leaks into agents**.

This module is that router and nothing else. It never names a model itself: the
``model_*`` settings (§34) hold ``provider:model`` strings, :meth:`ModelRouter.select`
resolves a *capability* to one of them, and the mapping from the §49 inputs to a
candidate order is a handful of small, auditable rules:

* **Capability** — ``ModelCapability`` mirrors the ``ModelRouterSettings`` field
  names (pinned by T060's test), so ``for_capability`` is a plain attribute
  lookup and capability selection can never drift from configuration.
* **Fallback chain** — the configured ``fallback_chain`` (§59.9) is appended
  after the capability's own model, de-duplicated, so a primary failure walks
  the chain rather than failing the task.
* **Latency / provider / user preference** — ``speed`` (``"fast"``/``"quality"``),
  ``prefer_local`` and an explicit ``preferred`` list *re-order* the candidates;
  they never invent a model. ``fast`` and ``prefer_local`` favour a local
  provider (§49's "fast local model", §66.6's privacy input).
* **Availability** — a candidate whose provider is unconfigured (§33: absent,
  not broken), circuit-open, or reports the model unavailable is skipped.
* **Resource usage** — an optional ``resource_guard`` and a per-provider
  ``max_in_flight`` cap let the host say "not right now" without the router
  knowing why.

**Health tracking and circuit breaking** (§66.6): :meth:`health_snapshot`
probes providers with a TTL cache (``health_refresh``), and a provider that
fails ``circuit_threshold`` retryable calls in a row is skipped for
``circuit_cooldown`` seconds instead of being retried on every request — the
"temporary circuit breaking" the spec asks for. A success resets the count.

The router is the only caller of the ``ModelProvider`` interface below an agent:
:meth:`complete` and :meth:`stream` select, apply the per-capability defaults
(``temperature``/``max_output_tokens``), walk the fallback chain, and return the
provider-neutral :class:`ModelResponse`/:class:`ModelStreamChunk` vocabulary —
so an agent literally cannot name a provider.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Literal, NoReturn, Protocol

from app.config import ModelRouterSettings, get_settings
from app.core.errors import (
    LocalModelUnavailableError,
    ModelNotSupportedError,
    ModelTimeoutError,
    UltronError,
)
from app.database.models import ModelUsageStatus
from app.database.repositories.usage import ModelUsageRepository
from app.database.session import AsyncSession
from app.models.base import (
    CompletionRequest,
    ModelProvider,
    ModelResponse,
    ModelStreamChunk,
    ProviderHealth,
)

__all__ = ["EventPublisher", "ModelRouter", "ModelSelection", "Speed"]

__all__ = ["EventPublisher", "ModelRouter", "ModelSelection", "Speed"]

#: ``speed=`` preferences: a fast local model, or the higher-quality default.
Speed = Literal["fast", "quality"]


class EventPublisher(Protocol):
    """The tiny slice of the event bus the router uses (kept structural)."""

    async def publish(self, event_type: str, payload: Mapping[str, Any] | None = None) -> Any: ...


@dataclass(frozen=True, slots=True)
class ModelSelection:
    """The outcome of :meth:`ModelRouter.select`, for audit and reuse.

    ``provider`` is the provider key and ``model`` the **bare** model id (the
    ``<provider>:`` prefix removed), ready to be placed on a
    :class:`CompletionRequest` — the adapter never sees the router's format.
    """

    provider: str
    model: str
    model_id: str
    reason: str = ""

    @property
    def identifier(self) -> str:
        """The ``provider:model`` identifier this selection came from."""
        return self.model_id


@dataclass(frozen=True, slots=True)
class _Candidate:
    provider: ModelProvider
    model_id: str
    model: str
    reason: str


def _provider_of(model_id: str) -> str | None:
    """The ``<provider>`` half of ``<provider>:<model>``, or ``None`` if absent."""
    head, sep, _ = model_id.partition(":")
    return head if sep and head else None


def _bare_model(model_id: str) -> str:
    """The ``<model>`` half of ``<provider>:<model>`` (colons in tags kept)."""
    _, sep, rest = model_id.partition(":")
    return rest if sep else model_id


class ModelRouter:
    """Selects a configured model for a capability and calls it with fallback."""

    def __init__(
        self,
        providers: Sequence[ModelProvider] = (),
        *,
        settings: ModelRouterSettings | None = None,
        bus: EventPublisher | None = None,
        health_ttl: float | None = None,
        circuit_threshold: int = 3,
        circuit_cooldown: float = 30.0,
        max_in_flight: int | None = None,
        resource_guard: Callable[[str], bool] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        monotonic: Callable[[], float] | None = None,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None = None,
    ) -> None:
        self._settings = settings or get_settings().model_router
        self._providers: dict[str, ModelProvider] = {}
        self._bus = bus
        self._session_factory = session_factory
        self._health_ttl = (
            float(self._settings.health_refresh) if health_ttl is None else health_ttl
        )
        self._circuit_threshold = max(1, circuit_threshold)
        self._circuit_cooldown = circuit_cooldown
        self._max_in_flight = max_in_flight
        self._resource_guard = resource_guard
        self._sleep = sleep or asyncio.sleep
        self._mono = monotonic or time.monotonic
        self._health: dict[str, ProviderHealth] = {}
        self._health_at: dict[str, float] = {}
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}
        self._in_flight: dict[str, int] = {}
        for provider in providers:
            self.register(provider)

    # ---------------------------------------------------------------------- #
    # Registry
    # ---------------------------------------------------------------------- #
    def register(self, provider: ModelProvider) -> None:
        """Add or replace a provider under its own ``name`` (§20's config key)."""
        self._providers[provider.name] = provider

    def unregister(self, name: str) -> None:
        self._providers.pop(name, None)
        self._forget(name)

    def get(self, name: str) -> ModelProvider | None:
        return self._providers.get(name)

    @property
    def providers(self) -> Mapping[str, ModelProvider]:
        return dict(self._providers)

    @property
    def provider_names(self) -> tuple[str, ...]:
        return tuple(self._providers)

    async def aclose(self) -> None:
        """Close every provider's held resources (best effort)."""
        for provider in self._providers.values():
            await provider.aclose()

    # ---------------------------------------------------------------------- #
    # Health tracking and circuit breaking (§66.6)
    # ---------------------------------------------------------------------- #
    def is_circuit_open(self, name: str) -> bool:
        """True while a provider is being skipped after repeated failures."""
        opened = self._open_until.get(name)
        if opened is None:
            return False
        if self._mono() >= opened:
            self._forget_failures(name)
            return False
        return True

    def record_failure(self, name: str, error: BaseException | None = None) -> None:
        """Record a provider failure; open the circuit past the threshold."""
        failures = self._failures.get(name, 0) + 1
        self._failures[name] = failures
        if failures >= self._circuit_threshold:
            self._open_until[name] = self._mono() + self._circuit_cooldown

    def record_success(self, name: str) -> None:
        """Clear a provider's failure streak after a usable response."""
        self._forget_failures(name)

    @property
    def failure_counts(self) -> Mapping[str, int]:
        return dict(self._failures)

    async def health_snapshot(self, *, refresh: bool = False) -> dict[str, ProviderHealth]:
        """Probe every provider, honouring the ``health_refresh`` TTL cache."""
        now = self._mono()
        snapshot: dict[str, ProviderHealth] = {}
        for name, provider in self._providers.items():
            cached = self._health.get(name)
            fresh = cached is not None and now - self._health_at.get(name, 0.0) < self._health_ttl
            if fresh and not refresh:
                snapshot[name] = cached  # type: ignore[assignment]
                continue
            health = await provider.health()
            self._health[name] = health
            self._health_at[name] = self._mono()
            snapshot[name] = health
        return snapshot

    def cached_health(self, name: str) -> ProviderHealth | None:
        return self._health.get(name)

    def _forget_failures(self, name: str) -> None:
        self._failures.pop(name, None)
        self._open_until.pop(name, None)

    def _forget(self, name: str) -> None:
        self._forget_failures(name)
        self._health.pop(name, None)
        self._health_at.pop(name, None)
        self._in_flight.pop(name, None)

    # ---------------------------------------------------------------------- #
    # Selection
    # ---------------------------------------------------------------------- #
    def candidate_models(self, capability: str = "default") -> list[str]:
        """The configured ``provider:model`` ids for a capability, in order.

        The capability's own model first, then the configured fallback chain,
        de-duplicated. Never a hard-coded name — every id comes from settings.
        """
        ordered: list[str] = []
        primary = self._settings.for_capability(capability)
        if primary:
            ordered.append(primary)
        ordered.extend(self._settings.fallback_chain)
        seen: set[str] = set()
        unique: list[str] = []
        for model_id in ordered:
            if model_id and model_id not in seen:
                seen.add(model_id)
                unique.append(model_id)
        return unique

    async def select(
        self,
        *,
        capability: str = "default",
        speed: Speed | None = None,
        tools: bool = False,
        streaming: bool = False,
        preferred: Sequence[str] = (),
        prefer_local: bool | None = None,
        exclude: Sequence[str] = (),
    ) -> ModelSelection:
        """Choose the first available configured model for a capability.

        Raises ``ModelNotSupportedError`` when nothing is configured, and
        ``LocalModelUnavailableError`` (503, §33) when candidates exist but none
        is usable right now — a degraded model is reported, never a crash.
        """
        candidates = self._ordered_candidates(
            capability=capability,
            speed=speed,
            preferred=preferred,
            prefer_local=prefer_local,
            exclude=exclude,
            tools=tools,
            streaming=streaming,
        )
        for candidate in candidates:
            if await candidate.provider.is_available(candidate.model):
                return ModelSelection(
                    provider=candidate.provider.name,
                    model=candidate.model,
                    model_id=candidate.model_id,
                    reason=candidate.reason,
                )
        if not candidates:
            raise self._no_candidates_error(capability)
        raise self._unavailable_error(capability, None)

    async def complete(
        self,
        request: CompletionRequest,
        *,
        capability: str = "default",
        speed: Speed | None = None,
        preferred: Sequence[str] = (),
        prefer_local: bool | None = None,
        exclude: Sequence[str] = (),
    ) -> ModelResponse:
        """Select a model and run ``chat`` with fallback and bounded retries."""
        candidates = self._ordered_candidates(
            capability=capability,
            speed=speed,
            preferred=preferred,
            prefer_local=prefer_local,
            exclude=exclude,
            tools=bool(request.tools),
        )
        if not candidates:
            raise self._no_candidates_error(capability)
        last_error: UltronError | None = None
        for candidate in candidates:
            if not await candidate.provider.is_available(candidate.model):
                continue
            provider_request = self._for_provider(request, candidate.model)
            started = self._mono()
            result, error = await self._attempt(candidate, provider_request)
            latency_ms = int((self._mono() - started) * 1000)
            if result is not None:
                await self._publish_selected(candidate, capability, streaming=False)
                if self._should_record_usage():
                    await self._record_usage(
                        candidate=candidate,
                        capability=capability,
                        response=result,
                        error=None,
                        latency_ms=latency_ms,
                    )
                return result
            last_error = error
        if self._should_record_usage() and last_error is not None:
            await self._record_usage(
                candidate=candidates[0] if candidates else None,
                capability=capability,
                response=None,
                error=last_error,
            )
        self._fail(capability, last_error)

    async def stream(
        self,
        request: CompletionRequest,
        *,
        capability: str = "default",
        speed: Speed | None = None,
        preferred: Sequence[str] = (),
        prefer_local: bool | None = None,
        exclude: Sequence[str] = (),
    ) -> AsyncIterator[ModelStreamChunk]:
        """Stream from the first usable candidate, falling back before output."""
        candidates = self._ordered_candidates(
            capability=capability,
            speed=speed,
            preferred=preferred,
            prefer_local=prefer_local,
            exclude=exclude,
            tools=bool(request.tools),
            streaming=True,
        )
        if not candidates:
            raise self._no_candidates_error(capability)
        last_error: UltronError | None = None
        for candidate in candidates:
            if not await candidate.provider.is_available(candidate.model):
                continue
            provider_request = self._for_provider(request, candidate.model)
            started = False
            self._enter(candidate.provider.name)
            started_at = self._mono()
            last_chunk: ModelStreamChunk | None = None
            try:
                async for chunk in candidate.provider.stream(provider_request):
                    started = True
                    last_chunk = chunk
                    yield chunk
            except UltronError as exc:
                self.record_failure(candidate.provider.name, exc)
                await self._publish_failed(candidate, exc)
                if started:
                    raise
                last_error = exc
                continue
            else:
                self.record_success(candidate.provider.name)
                await self._publish_selected(candidate, capability, streaming=True)
                if self._should_record_usage():
                    latency_ms = int((self._mono() - started_at) * 1000)
                    usage = last_chunk.usage if last_chunk else None
                    await self._record_usage(
                        candidate=candidate,
                        capability=capability,
                        response=ModelResponse(
                            model=candidate.model,
                            usage=usage,
                            provider=candidate.provider.name,
                        ),
                        error=None,
                        latency_ms=latency_ms,
                    )
                return
            finally:
                self._leave(candidate.provider.name)
        if self._should_record_usage() and last_error is not None:
            await self._record_usage(
                candidate=candidates[0] if candidates else None,
                capability=capability,
                response=None,
                error=last_error,
            )
        self._fail(capability, last_error)

    # ---------------------------------------------------------------------- #
    # Internals
    # ---------------------------------------------------------------------- #
    async def _attempt(
        self, candidate: _Candidate, request: CompletionRequest
    ) -> tuple[ModelResponse | None, UltronError | None]:
        name = candidate.provider.name
        last_error: UltronError | None = None
        for attempt in range(self._settings.max_retries + 1):
            try:
                response = await candidate.provider.chat(request)
            except UltronError as exc:
                last_error = exc
                if exc.retryable:
                    self.record_failure(name, exc)
                    if attempt < self._settings.max_retries:
                        await self._sleep(self._settings.retry_backoff * (attempt + 1))
                        continue
                break
            else:
                self.record_success(name)
                return response, None
        await self._publish_failed(candidate, last_error)
        return None, last_error

    def _ordered_candidates(
        self,
        *,
        capability: str,
        speed: Speed | None,
        preferred: Sequence[str],
        prefer_local: bool | None,
        exclude: Sequence[str],
        tools: bool = False,
        streaming: bool = False,
    ) -> list[_Candidate]:
        models = self._order(self.candidate_models(capability), speed, preferred, prefer_local)
        return self._filter(models, capability, exclude, tools=tools, streaming=streaming)

    def _order(
        self,
        models: Sequence[str],
        speed: Speed | None,
        preferred: Sequence[str],
        prefer_local: bool | None,
    ) -> list[str]:
        order = list(dict.fromkeys(models))
        effective_local = prefer_local
        if effective_local is None:
            if speed == "fast":
                effective_local = True
            elif speed == "quality":
                effective_local = False
        if effective_local is not None:
            order.sort(key=lambda model: self._is_local(model) != effective_local)
        if preferred:
            rank = {value: index for index, value in enumerate(preferred)}
            order.sort(key=lambda model: self._preference_rank(model, rank))
        return order

    def _is_local(self, model_id: str) -> bool:
        provider_name = _provider_of(model_id)
        provider = self._providers.get(provider_name) if provider_name else None
        return bool(provider and provider.local)

    def _preference_rank(self, model_id: str, rank: Mapping[str, int]) -> int:
        if model_id in rank:
            return rank[model_id]
        provider_name = _provider_of(model_id)
        return rank.get(provider_name, len(rank)) if provider_name else len(rank)

    def _filter(
        self,
        models: Sequence[str],
        capability: str,
        exclude: Sequence[str],
        *,
        tools: bool,
        streaming: bool,
    ) -> list[_Candidate]:
        primary = self._settings.for_capability(capability)
        excluded = set(exclude)
        candidates: list[_Candidate] = []
        for model_id in models:
            provider_name = _provider_of(model_id)
            if provider_name is None or model_id in excluded or provider_name in excluded:
                continue
            provider = self._providers.get(provider_name)
            if provider is None or not provider.is_configured:
                continue
            if self.is_circuit_open(provider_name):
                continue
            if tools and not provider.supports_tools:
                continue
            if streaming and not provider.supports_streaming:
                continue
            if self._resource_guard is not None and not self._resource_guard(provider_name):
                continue
            if (
                self._max_in_flight is not None
                and self._in_flight.get(provider_name, 0) >= self._max_in_flight
            ):
                continue
            reason = "capability" if model_id == primary else "fallback"
            candidates.append(_Candidate(provider, model_id, _bare_model(model_id), reason))
        return candidates

    def _for_provider(self, request: CompletionRequest, model: str) -> CompletionRequest:
        return CompletionRequest(
            messages=request.messages,
            model=model,
            tools=request.tools,
            temperature=(
                request.temperature
                if request.temperature is not None
                else self._settings.temperature
            ),
            max_tokens=(
                request.max_tokens
                if request.max_tokens is not None
                else self._settings.max_output_tokens
            ),
            stop=request.stop,
        )

    def _enter(self, name: str) -> None:
        self._in_flight[name] = self._in_flight.get(name, 0) + 1

    def _leave(self, name: str) -> None:
        remaining = self._in_flight.get(name, 0) - 1
        if remaining > 0:
            self._in_flight[name] = remaining
        else:
            self._in_flight.pop(name, None)

    def _no_candidates_error(self, capability: str) -> ModelNotSupportedError:
        return ModelNotSupportedError(
            "router",
            f"no model is configured for capability '{capability}'",
            details={"capability": capability},
        )

    def _unavailable_error(self, capability: str, last_error: UltronError | None) -> UltronError:
        return LocalModelUnavailableError(
            "router",
            f"no model is available for capability '{capability}'",
            details={"capability": capability},
            cause=last_error,
        )

    def _fail(self, capability: str, last_error: UltronError | None) -> NoReturn:
        """Raise the right error once every candidate has been exhausted.

        A definitive non-retryable failure (missing key, unsupported model) is
        surfaced as itself: retrying or relabelling it as "unavailable" would
        hide a configuration problem (§33). Only when every failure was
        retryable/unavailable does the router report the degraded-capability
        ``LocalModelUnavailableError``.
        """
        if last_error is not None and not last_error.retryable:
            raise last_error
        raise self._unavailable_error(capability, last_error)

    # ---------------------------------------------------------------------- #
    # Usage recording (T067)
    # ---------------------------------------------------------------------- #
    def _should_record_usage(self) -> bool:
        """Check if usage tracking is enabled and a session factory is available."""
        return self._settings.usage_tracking and self._session_factory is not None

    async def _record_usage(
        self,
        candidate: _Candidate | None,
        capability: str,
        response: ModelResponse | None,
        error: UltronError | None,
        latency_ms: int | None = None,
    ) -> None:
        """Record a model usage entry to the database.

        Args:
            candidate: The candidate that was attempted (None if no candidates).
            capability: The capability for which the model was selected.
            response: The successful response, or None if the call failed.
            error: The error that caused failure, or None on success.
            latency_ms: Call duration in milliseconds, or None if unmeasured.
        """
        if not self._session_factory or not candidate:
            return

        provider_name = candidate.provider.name
        model_name = candidate.model
        request_id = response.request_id if response else None

        # Determine status and error details
        if error is None:
            status = ModelUsageStatus.SUCCESS
            error_detail = None
        elif isinstance(error, ModelTimeoutError):
            status = ModelUsageStatus.TIMEOUT
            error_detail = str(error)
        else:
            status = ModelUsageStatus.ERROR
            error_detail = str(error)

        # Extract token usage and cost from response
        prompt_tokens = None
        completion_tokens = None
        cost_usd = None
        if response and response.usage:
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens
            if response.usage.cost_usd is not None:
                cost_usd = response.usage.cost_usd

        async with self._session_factory() as session:
            repo = ModelUsageRepository(session)
            await repo.record(
                model=model_name,
                provider=provider_name,
                status=status,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                request_id=request_id,
                correlation_id=None,
                task_id=None,
                agent_id=None,
                user_id=None,
                conversation_id=None,
                error=error_detail,
            )

    # ---------------------------------------------------------------------- #
    # Events (§19)
    # ---------------------------------------------------------------------- #
    async def _publish_selected(
        self, candidate: _Candidate, capability: str, *, streaming: bool
    ) -> None:
        await self._publish(
            "MODEL_SELECTED",
            {
                "provider": candidate.provider.name,
                "model": candidate.model,
                "capability": capability,
                "reason": candidate.reason,
                "streaming": streaming,
            },
        )

    async def _publish_failed(self, candidate: _Candidate, error: UltronError | None) -> None:
        await self._publish(
            "MODEL_FAILED",
            {
                "provider": candidate.provider.name,
                "model": candidate.model,
                "error": getattr(error, "code", None),
            },
        )

    async def _publish(self, event_type: str, payload: Mapping[str, Any]) -> None:
        if self._bus is None:
            return
        await self._bus.publish(event_type, dict(payload))

    def in_flight(self, name: str) -> int:
        """Active calls currently attributed to a provider (resource usage)."""
        return self._in_flight.get(name, 0)
