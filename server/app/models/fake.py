"""A deterministic, scripted ``ModelProvider`` for tests (T065).

The router (T066), the usage record (T067) and the tool-calling loop (T068) all
need a provider they can drive without a network, a key or a timing dependency.
This is that provider: no httpx, no sockets, no randomness — every response, every
streamed chunk and every error is *scripted* by the test, and every received
:class:`CompletionRequest` is recorded for assertion.

It is deliberately the same contract the real adapters implement (T060), so a
test can assert against ``ModelProvider`` behaviour — ``is_configured``,
``is_available``, ``health``, ``chat``, ``stream``, ``aclose`` — without knowing
whether the subject under test is real or fake (§66.6's "a provider type must
never leak into an agent"; the fake proves it).

Scripting is a small queue per reaction:

* :meth:`queue` — the next ``chat`` returns this response.
* :meth:`queue_stream` — the next ``stream`` yields exactly these chunks.
* :meth:`queue_error` — the next call (chat or stream) raises this error, which
  is how the ``LOCAL_MODEL_UNAVAILABLE`` degradation path (§33) is exercised.

With nothing queued the provider answers deterministically from the request
(default text ``"fake response"``, the requested model, a ``STOP`` finish and
fixed usage), so a test that only cares "did the loop call the model?" needs no
setup at all.
"""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator, Iterable

from app.core.errors import ProviderNotConfiguredError
from app.models.base import (
    CompletionRequest,
    FinishReason,
    ModelProvider,
    ModelResponse,
    ModelStreamChunk,
    ProviderHealth,
    ProviderStatus,
    TokenUsage,
)

__all__ = ["FakeProvider"]

#: The text a :class:`FakeProvider` returns when nothing is scripted.
DEFAULT_RESPONSE = "fake response"


class FakeProvider(ModelProvider):
    """A scripted provider: deterministic, offline, fully inspectable."""

    name = "fake"
    local = True
    supports_tools = True
    supports_streaming = True

    def __init__(
        self,
        name: str = "fake",
        *,
        local: bool = True,
        supports_tools: bool = True,
        supports_streaming: bool = True,
        configured: bool = True,
        available: bool | Iterable[str] = True,
        models: Iterable[str] | None = None,
        health_status: ProviderStatus = ProviderStatus.AVAILABLE,
        health_detail: str = "ok",
        response_text: str = DEFAULT_RESPONSE,
        usage: TokenUsage | None = None,
        responses: Iterable[ModelResponse] | None = None,
        streams: Iterable[Iterable[ModelStreamChunk]] | None = None,
        errors: Iterable[Exception] | None = None,
    ) -> None:
        # ``name``/``local``/``supports_*`` are class variables on the ABC;
        # shadow them per-instance so a test can register several distinct fakes.
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "local", local)
        object.__setattr__(self, "supports_tools", supports_tools)
        object.__setattr__(self, "supports_streaming", supports_streaming)
        self._configured = configured
        self._available = available
        self._models = list(models or ())
        self._health_status = health_status
        self._health_detail = health_detail
        self._response_text = response_text
        self._usage = (
            usage if usage is not None else TokenUsage(prompt_tokens=1, completion_tokens=1)
        )
        self._responses: deque[ModelResponse] = deque(responses or ())
        self._streams: deque[tuple[ModelStreamChunk, ...]] = deque(
            tuple(stream) for stream in (streams or ())
        )
        self._errors: deque[Exception] = deque(errors or ())
        self.requests: list[CompletionRequest] = []

    # ---------------------------------------------------------------------- #
    # Scripting
    # ---------------------------------------------------------------------- #
    def queue(self, response: ModelResponse) -> FakeProvider:
        """Make the next ``chat`` return ``response``."""
        self._responses.append(response)
        return self

    def queue_stream(self, *chunks: ModelStreamChunk) -> FakeProvider:
        """Make the next ``stream`` yield exactly ``chunks``."""
        self._streams.append(tuple(chunks))
        return self

    def queue_error(self, error: Exception) -> FakeProvider:
        """Make the next ``chat``/``stream`` raise ``error`` (§33 path)."""
        self._errors.append(error)
        return self

    # ---------------------------------------------------------------------- #
    # Inspection
    # ---------------------------------------------------------------------- #
    @property
    def call_count(self) -> int:
        return len(self.requests)

    @property
    def last_request(self) -> CompletionRequest | None:
        return self.requests[-1] if self.requests else None

    # ---------------------------------------------------------------------- #
    # ModelProvider contract
    # ---------------------------------------------------------------------- #
    @property
    def is_configured(self) -> bool:
        return self._configured

    async def _next_error(self) -> None:
        if self._errors:
            raise self._errors.popleft()

    def _default_response(self, request: CompletionRequest) -> ModelResponse:
        return ModelResponse(
            model=request.model,
            content=self._response_text,
            finish_reason=FinishReason.STOP,
            usage=self._usage,
            provider=self.name,
        )

    async def chat(self, request: CompletionRequest) -> ModelResponse:
        self.requests.append(request)
        await self._next_error()
        if not self._configured:
            raise ProviderNotConfiguredError(self.name, "fake provider is unconfigured")
        if self._responses:
            return self._responses.popleft()
        return self._default_response(request)

    async def stream(self, request: CompletionRequest) -> AsyncIterator[ModelStreamChunk]:
        self.requests.append(request)
        await self._next_error()
        if not self._configured:
            raise ProviderNotConfiguredError(self.name, "fake provider is unconfigured")
        if self._streams:
            for chunk in self._streams.popleft():
                yield chunk
            return
        response = self._responses.popleft() if self._responses else self._default_response(request)
        if response.content:
            yield ModelStreamChunk(delta=response.content, model=response.model)
        for call in response.tool_calls:
            yield ModelStreamChunk(tool_call=call, model=response.model)
        yield ModelStreamChunk(
            finish_reason=response.finish_reason,
            usage=response.usage,
            model=response.model,
        )

    async def list_models(self) -> list[str]:
        if not self._configured:
            return []
        return list(self._models)

    async def is_available(self, model: str) -> bool:
        if not self._configured:
            return False
        if isinstance(self._available, bool):
            return self._available
        available = list(self._available)
        return model in available or any(name.split(":", 1)[0] == model for name in available)

    async def health(self) -> ProviderHealth:
        return ProviderHealth(status=self._health_status, detail=self._health_detail)

    async def aclose(self) -> None:
        return None
