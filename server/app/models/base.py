"""The model-provider ABC and the message/response/tool-call vocabulary (T060).

Spec §20 fixes the shape this module implements: *one* abstraction named
``ModelProvider`` with Ollama, OpenAI, Gemini and Anthropic behind it, and a
router that chooses among them by configuration. §21 fixes what every provider
must be able to do — configurable host, model discovery, health, ``chat``,
streaming, an availability check, a timeout and error handling — and §33 makes
failure a first-class, *recoverable* outcome: a provider that is down is
reported (``LOCAL_MODEL_UNAVAILABLE`` for Ollama) rather than allowed to take
the process with it. §49 gives selection its inputs (task type, capability,
latency, availability, resource usage, provider, user preference) and §66.6
extends the same router with health tracking and fallback — which is why this
module stays *one* interface rather than growing a second one.

The layering rule is §40's, applied to models: a provider is *an adapter to a
model backend* — not an agent (that is `app/agents/base.py`), not a tool (that
is `app/tools/base.py`), not the router (T066) and not the orchestrator. It
knows how to turn a :class:`CompletionRequest` into a :class:`ModelResponse`
and nothing about tasks, permissions or memory.

Two decisions shape the file:

- **The request carries the model.** The router has already chosen *which*
  model (spec §20: "the router chooses the provider/model"), so a provider is
  never asked "which model would you pick?" — it is handed the bare model
  identifier and does exactly that model. The ``provider:model`` identifier
  the settings use (§34's ``model_*`` keys) is the router's format; the colon
  is stripped before a provider ever sees it. This is what keeps §20's "do not
  hard-code model names throughout the application" true: the names live in
  configuration, the router reads them, and providers only receive one.

- **The vocabulary is inert data.** Messages, tool calls, responses and stream
  chunks are frozen value objects with **no behaviour that talks to anything**.
  They are the bytes that cross the provider boundary, in a shape every
  adapter can produce and the tool-calling loop (T068) can consume without
  knowing which provider produced them. A provider type must never leak into
  an agent (§66.6), and this is the boundary that makes that true: agents see
  :class:`ModelResponse`, never ``OllamaProvider``.

The capability model is §49 applied to a name: ``ModelCapability`` is the set
of roles a model can be selected *for* (``fast``, ``coding``, ``research``,
``vision``, ``reasoning``), and it deliberately mirrors the ``ModelRouterSettings``
field names so selection stays pure configuration and never a hard-coded model
name. Streaming is *opt-in*: ``Chat`` is the one abstract method, and the default
:meth:`ModelProvider.stream` degrades gracefully to a single-chunk stream built
from ``chat`` — so a provider that cannot stream is still a complete provider,
and callers that always consume the stream never need to know which they have.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar

from app.database.models import MessageRole

__all__ = [
    "CompletionRequest",
    "FinishReason",
    "ModelCapability",
    "ModelMessage",
    "ModelProvider",
    "ModelResponse",
    "ModelStreamChunk",
    "ProviderHealth",
    "ProviderStatus",
    "TokenUsage",
    "ToolCall",
    "ToolDefinition",
]


class ModelCapability(StrEnum):
    """The roles a model may be selected for (§20's config keys, §49's inputs).

    The values are exactly the ``ModelRouterSettings`` attribute names
    (``default``, ``coding``, ``research``, ``vision``, ``fast``,
    ``reasoning``), so the router resolves a capability to a configured
    ``<provider>:<model>`` with a plain attribute lookup instead of a lookup
    table that could drift from the settings. ``DEFAULT`` is the fallback every
    capability degrades to when the operator has configured nothing specific,
    matching ``ModelRouterSettings.for_capability``.
    """

    DEFAULT = "default"
    FAST = "fast"
    CODING = "coding"
    RESEARCH = "research"
    VISION = "vision"
    REASONING = "reasoning"


class FinishReason(StrEnum):
    """Why a generation stopped, normalised across providers.

    Providers spell this differently (OpenAI ``stop``, Anthropic ``end_turn``,
    Gemini ``STOP``); the adapter is what maps them here, so the tool-calling
    loop (T068) branches on one vocabulary. **Not specified by the spec** — an
    engineering decision, kept deliberately small and explicit.
    """

    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"


class ProviderStatus(StrEnum):
    """The result of a health probe (§21's "health check", §66.6's tracking)."""

    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token counts and cost for one call, as the provider reported them (§23).

    Counts default to ``0`` rather than ``None`` because a completed call
    always has an answer to "how many tokens"; a *failed* call has no usage at
    all and is represented by ``None`` where a ``ModelResponse`` would carry
    one. ``cost_usd`` is ``Decimal`` for the same reason ``model_usage.cost_usd``
    is (T023): money must not accumulate binary rounding error.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: Decimal | None = None

    @property
    def total_tokens(self) -> int:
        """The provider's own two numbers added — never stored twice."""
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """One tool advertised to the model for function calling (§16, T068).

    ``parameters`` is the tool's JSON Schema (``Tool.input_schema``, T034),
    passed through unchanged: the model layer does not reinterpret schemas, it
    only forwards them in whatever shape the provider expects.
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A model's request to run a tool, with arguments already parsed.

    Providers return tool arguments as a JSON *string*; the adapter parses it
    so the pipeline (§16 stage 1) validates a mapping rather than re-parsing a
    string on every hop. ``arguments`` is empty (not missing) when the model
    asked for a no-argument call, which keeps the calling loop uniform.
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelMessage:
    """One turn in the transcript handed to a provider (§16's pipeline).

    ``role`` reuses ``MessageRole`` from the persistence layer — the same four
    words (system/user/assistant/tool) the conversation store already uses, so
    the transcript a provider sees and the transcript the database keeps cannot
    disagree. ``tool_call_id`` links a ``tool`` message back to the assistant's
    ``ToolCall``; ``tool_calls`` is populated on assistant turns that requested
    work. Multimodal content (images, for the vision capability) is deliberately
    out of scope here and is the vision work's extension, not a broken ``str``.
    """

    role: MessageRole
    content: str = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    """Everything one provider call needs, with the model already chosen.

    The router (T066) resolves the ``model`` before a provider sees the
    request, so adapters never select. ``temperature``/``max_tokens`` default to
    ``None`` meaning "provider default", which lets the per-capability settings
    (T066) win without every caller restating them.
    """

    messages: tuple[ModelMessage, ...]
    model: str
    tools: tuple[ToolDefinition, ...] = ()
    temperature: float | None = None
    max_tokens: int | None = None
    stop: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """A completed generation: text, any tool calls, and accounting.

    ``provider`` and ``request_id`` are carried so the usage record (T067) and
    the ``MODEL_RESPONSE`` event (§19) can name where the answer came from
    without the caller tracking it separately.
    """

    model: str
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: FinishReason = FinishReason.STOP
    usage: TokenUsage | None = None
    provider: str = ""
    request_id: str | None = None

    @property
    def has_tool_calls(self) -> bool:
        """True when the model asked for tools rather than answering (§16)."""
        return bool(self.tool_calls)


@dataclass(frozen=True, slots=True)
class ModelStreamChunk:
    """One piece of a streaming generation (§21's "streaming").

    Providers emit text deltas and (once assembled) complete tool calls; the
    terminal chunk is the one carrying ``finish_reason`` and, where the provider
    reports it, ``usage``. :attr:`is_final` is the loop's stop test so a caller
    never has to know which provider it is streaming from.
    """

    delta: str = ""
    tool_call: ToolCall | None = None
    finish_reason: FinishReason | None = None
    usage: TokenUsage | None = None
    model: str = ""

    @property
    def is_final(self) -> bool:
        """True on the last chunk of a stream — the one with a finish reason."""
        return self.finish_reason is not None


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    """The outcome of one availability probe (§21, §33, §66.6).

    ``detail`` is a human-readable reason kept for the ``/health`` surface and
    the ``MODEL_FAILED`` event (§19, §66.8); ``latency_ms`` lets the router's
    circuit breaker (§66.6, T066) act on *how* a provider failed, not just
    whether it did.
    """

    status: ProviderStatus
    detail: str = ""
    latency_ms: int | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_usable(self) -> bool:
        """True when the provider may be tried — ``AVAILABLE`` or ``DEGRADED``."""
        return self.status is not ProviderStatus.UNAVAILABLE


class ModelProvider(ABC):
    """One way to reach models (§20): declared identity, one chat, safe defaults.

    The ABC is deliberately small. ``chat`` is the only *required* behaviour —
    discovery, health, availability and streaming all have honest defaults that
    a working provider inherits, so adding a provider never means stubbing five
    methods to satisfy a type checker. The declared attributes mirror the
    provider's place in routing (§66.6): ``name`` is the config key, ``local``
    is the local/cloud preference input, and the ``supports_*`` flags let the
    router skip a provider lacking a needed capability instead of failing at
    call time.
    """

    # -- Declarations, mandatory on a concrete provider --------------------- #
    name: ClassVar[str]
    """Config key and the ``<provider>`` half of ``<provider>:<model>`` (§20)."""

    # -- Declarations with honest defaults ---------------------------------- #
    local: ClassVar[bool] = False
    """True when the provider runs locally (Ollama) — §66.6's privacy/preference
    input. Default ``False``: a cloud provider is the safe assumption."""
    supports_tools: ClassVar[bool] = True
    """Whether the provider can return tool calls (§16, T068). The router skips
    a provider that cannot before it is asked to do function calling."""
    supports_streaming: ClassVar[bool] = False
    """Declared for routing transparency; correctness rests on :meth:`stream`,
    which degrades to a single chunk when a provider does not implement it."""

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """True when credentials/host are present, so the provider may be tried.

        A provider with no key is *unavailable*, not broken (§33): the router
        skips it (``ProviderNotConfiguredError`` is non-retryable for the same
        reason) rather than letting an unauthenticated call surface as an error.
        """

    @abstractmethod
    async def chat(self, request: CompletionRequest) -> ModelResponse:
        """Run one non-streaming completion, raising a typed error on failure.

        Failures map to ``app/core/errors.py``: ``LocalModelUnavailableError``
        when Ollama is unreachable (§33), ``ModelTimeoutError``,
        ``ModelRateLimitedError``, ``ModelResponseInvalidError`` and
        ``ProviderNotConfiguredError`` otherwise. Returning normally means a
        usable response — the caller never inspects provider internals.
        """

    async def stream(self, request: CompletionRequest) -> AsyncIterator[ModelStreamChunk]:
        """Stream a generation, degrading to one terminal chunk (§21, §33).

        The default is honest rather than empty: a provider that cannot stream
        still *narrows* correctly under this contract, so callers always consume
        a stream and never branch on provider type (§66.6). An adapter with real
        streaming (Ollama NDJSON, OpenAI/Anthropic SSE, Gemini's chunked JSON)
        overrides this.
        """
        response = await self.chat(request)
        if response.content:
            yield ModelStreamChunk(delta=response.content, model=response.model)
        for call in response.tool_calls:
            yield ModelStreamChunk(tool_call=call, model=response.model)
        yield ModelStreamChunk(
            finish_reason=response.finish_reason,
            usage=response.usage,
            model=response.model,
        )

    async def health(self) -> ProviderHealth:
        """Probe availability; the default reports configuration, not liveness.

        A provider that has not overridden this has no probe of its own, so the
        honest answer is "available iff configured" — never a claimed liveness
        it did not test (§17: never assume success). Adapters with a real
        endpoint (Ollama's ``/api/tags``) override it.
        """
        if self.is_configured:
            return ProviderHealth(status=ProviderStatus.AVAILABLE, detail="configured")
        return ProviderHealth(
            status=ProviderStatus.UNAVAILABLE,
            detail=f"provider '{self.name}' is not configured",
        )

    async def list_models(self) -> list[str]:
        """Discover the models this provider offers (§21's "model discovery").

        Default is empty — a provider without discovery is valid, and the empty
        list lets the router fall back to configured names instead of an error.
        """
        return []

    async def is_available(self, model: str) -> bool:
        """Whether ``model`` can be called now (§21's "model availability check").

        The default ties availability to configuration; providers that can
        discover models (Ollama) override this to also check the model is
        actually pulled. ``model`` is the bare name — the router stripped the
        provider prefix.
        """
        return self.is_configured

    async def aclose(self) -> None:
        """Release any held client resources (httpx pools). Default: nothing."""
        return None

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} {self.name}{'' if self.is_configured else ' (unconfigured)'}>"
        )

    # ---------------------------------------------------------------------- #
    # Definition-time guards
    # ---------------------------------------------------------------------- #
    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Fail at import when a concrete provider is mis-declared (§20, §21).

        Skipped while the class is still abstract, so an intermediate base may
        declare on behalf of a family. A concrete provider must declare a
        non-empty ``name`` (the config key the router selects by, §20) and must
        implement ``chat`` as a coroutine — a sync ``chat`` would type-check and
        then explode on the router's first ``await`` (§21).
        """
        super().__init_subclass__(**kwargs)
        if inspect.isabstract(cls):
            return

        name = getattr(cls, "name", None)
        if not isinstance(name, str) or not name.strip():
            raise TypeError(
                f"{cls.__name__}.name must be a non-empty string "
                "(spec 20: the config key the router selects the provider by)"
            )

        if not inspect.iscoroutinefunction(getattr(cls, "chat", None)):
            raise TypeError(
                f"{cls.__name__}.chat must be an async function "
                "(spec 21: the router awaits every provider call)"
            )
