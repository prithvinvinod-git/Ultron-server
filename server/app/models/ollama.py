"""The Ollama adapter (T061): the local provider, and the only mandatory one.

Spec §21 fixes the requirements verbatim — *configurable host, model discovery,
health, chat, streaming, model availability check, timeout, error handling* —
and §33 fixes the one non-negotiable outcome: if Ollama is unreachable ULTRON
must report ``LOCAL_MODEL_UNAVAILABLE`` and keep running, never crash. The
adapter is the concrete half of the ``ModelProvider`` contract (T060) for the
provider that has to work with the least infrastructure: no API key, often no
network beyond the deployment host.

Everything is read from ``OllamaSettings`` (``.env.example`` lines 101-121),
including the two deliberate subtleties the settings already encode:

* **The host is configurable and is not assumed to be localhost.** A hosted
  Ollama speaks the identical HTTP API, so the only difference is that a hosted
  instance authenticates — which is what :meth:`OllamaSettings.auth_headers`
  exists for. This module never special-cases either deployment.
* **The health budget is ``connect_timeout``, not the generation ``timeout``.**
  A probe answers "is it there", not "is a generation fast"; the settings
  comment says as much, and :meth:`health` honours it.

Transport is **httpx**, not the ``ollama`` SDK (pyproject lists the provider
SDKs as optional and even the Ollama one is not a dependency): one HTTP call
shape, no extra package, and the same client the cloud adapters (T062-T064) will
use. The response vocabulary is translated at this boundary and nowhere else —
Ollama's ``done_reason`` and unbranded tool calls become T060's
``FinishReason``/``ToolCall``, so the tool-calling loop (T068) never sees an
Ollama-shaped object.

Failures map onto ``app/core/errors.py`` rather than leaking httpx types (§33's
"typed exceptions, structured errors"): an unreachable host is
``LocalModelUnavailableError`` (503, retryable — the §33 report), a slow one is
``ModelTimeoutError`` (504), a missing model is ``ModelNotSupportedError``
(400), a throttled one ``ModelRateLimitedError`` (429), a 401/403
``ProviderNotConfiguredError`` (503, non-retryable — a hosted instance without a
usable token cannot succeed), and malformed JSON is ``ModelResponseInvalidError``
(502). The router (T066) is what turns these into fallback and circuit breaking;
this module only names the failure precisely.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import OllamaSettings, get_settings
from app.core.errors import (
    LocalModelUnavailableError,
    ModelError,
    ModelNotSupportedError,
    ModelRateLimitedError,
    ModelResponseInvalidError,
    ModelTimeoutError,
    ProviderNotConfiguredError,
    UltronError,
)
from app.database.models import MessageRole
from app.models.base import (
    CompletionRequest,
    FinishReason,
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

__all__ = ["OllamaProvider"]

#: Ollama's ``done_reason`` values mapped to the one vocabulary (T060). ``load``
#: and ``unload`` are operational endings, not truncations, so they are STOP.
_FINISH_REASONS: dict[str, FinishReason] = {
    "stop": FinishReason.STOP,
    "length": FinishReason.LENGTH,
    "load": FinishReason.STOP,
    "unload": FinishReason.STOP,
}

_STATUS_PROVIDER = "ollama"


def _normalize_model(model: str) -> str:
    """Drop a ``provider:`` prefix — the router passes ``<provider>:<model>``.

    T060's contract says a provider is handed a bare model; this is the one
    place that is enforced for Ollama, so a router bug degrades to a wrong-name
    error from Ollama rather than a silent ``ollama:ollama:...`` request.
    """
    prefix = f"{_STATUS_PROVIDER}:"
    return model[len(prefix) :] if model.startswith(prefix) else model


def _base_name(model: str) -> str:
    """The part before ``:`` — Ollama tags models (``qwen2.5:7b-instruct``)."""
    return model.split(":", 1)[0]


def _parse_arguments(value: Any) -> dict[str, Any]:
    """Parse a tool call's arguments, which Ollama may return as a JSON string."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        if not value.strip():
            return {}
        try:
            parsed = json.loads(value)
        except ValueError as exc:
            raise ModelResponseInvalidError(
                _STATUS_PROVIDER,
                "tool call arguments were not valid JSON",
                details={"arguments": value[:200]},
                cause=exc,
            ) from exc
        if isinstance(parsed, dict):
            return parsed
        raise ModelResponseInvalidError(
            _STATUS_PROVIDER,
            "tool call arguments were not a JSON object",
            details={"arguments": value[:200]},
        )
    raise ModelResponseInvalidError(
        _STATUS_PROVIDER,
        "tool call arguments were neither an object nor a string",
        details={"type": type(value).__name__},
    )


def _parse_tool_calls(raw: Any) -> tuple[ToolCall, ...]:
    """Translate Ollama's ``message.tool_calls`` into T060 ``ToolCall``s."""
    if not isinstance(raw, list):
        return ()
    calls: list[ToolCall] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        call_id = item.get("id")
        if not isinstance(call_id, str) or not call_id:
            call_id = f"ollama-call-{index}"
        calls.append(
            ToolCall(
                id=call_id,
                name=name,
                arguments=_parse_arguments(function.get("arguments")),
            )
        )
    return tuple(calls)


class OllamaProvider(ModelProvider):
    """The local model provider over the Ollama HTTP API (§21)."""

    name = _STATUS_PROVIDER
    local = True
    supports_tools = True
    supports_streaming = True

    def __init__(
        self,
        settings: OllamaSettings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Configure from ``OllamaSettings`` and optionally inject a client.

        An injected client is *borrowed*: :meth:`aclose` leaves it open because
        the caller owns it. The adapter only closes a client it created, so the
        shared-client case (one pooled client across providers, T066) does not
        get torn down by a single provider's shutdown.
        """
        self._settings = settings or get_settings().ollama
        self._url = self._settings.url.rstrip("/")
        self._headers = self._settings.auth_headers()
        self._timeout = httpx.Timeout(
            self._settings.timeout, connect=self._settings.connect_timeout
        )
        self._probe_timeout = httpx.Timeout(self._settings.connect_timeout)
        self._allowed = list(self._settings.models)
        self._client = client
        self._owns_client = client is None

    @property
    def is_configured(self) -> bool:
        """Ollama needs no credential: a URL is the whole configuration (§21)."""
        return bool(self._url)

    # ---------------------------------------------------------------------- #
    # HTTP plumbing
    # ---------------------------------------------------------------------- #
    def _http(self) -> httpx.AsyncClient:
        """Return the client, creating (and owning) one on first use."""
        if self._client is None:
            self._client = httpx.AsyncClient(headers=self._headers)
            self._owns_client = True
        return self._client

    def _endpoint(self, path: str) -> str:
        return f"{self._url}{path}"

    async def aclose(self) -> None:
        """Close the HTTP client, unless the caller injected it."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    def _raise_for_status(self, status_code: int, model: str) -> None:
        """Map an HTTP failure onto the typed model errors (§33)."""
        if 200 <= status_code < 300:
            return
        if status_code in (401, 403):
            raise ProviderNotConfiguredError(
                _STATUS_PROVIDER,
                f"Ollama rejected the request (HTTP {status_code}); check OLLAMA_API_KEY",
                model=model,
                details={"status": status_code},
            )
        if status_code == 404:
            raise ModelNotSupportedError(
                _STATUS_PROVIDER,
                f"model '{model}' is not available from Ollama",
                model=model,
                details={"status": status_code},
            )
        if status_code == 429:
            raise ModelRateLimitedError(
                _STATUS_PROVIDER,
                "Ollama is rate limiting requests",
                model=model,
                details={"status": status_code},
            )
        raise ModelError(
            _STATUS_PROVIDER,
            f"Ollama request failed (HTTP {status_code})",
            model=model,
            details={"status": status_code},
        )

    def _json(self, response: httpx.Response, model: str) -> dict[str, Any]:
        """Parse a JSON object body, or raise ``ModelResponseInvalidError``."""
        try:
            data = response.json()
        except ValueError as exc:
            raise ModelResponseInvalidError(
                _STATUS_PROVIDER,
                "Ollama returned a non-JSON response",
                model=model,
                cause=exc,
            ) from exc
        if not isinstance(data, dict):
            raise ModelResponseInvalidError(
                _STATUS_PROVIDER,
                "Ollama returned a JSON value that is not an object",
                model=model,
            )
        return data

    # ---------------------------------------------------------------------- #
    # Request building
    # ---------------------------------------------------------------------- #
    def _payload(self, request: CompletionRequest, *, stream: bool) -> dict[str, Any]:
        """Build the ``/api/chat`` body from T060's request shape."""
        options: dict[str, Any] = {"num_ctx": self._settings.num_ctx}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens
        if request.stop:
            options["stop"] = list(request.stop)
        payload: dict[str, Any] = {
            "model": _normalize_model(request.model),
            "messages": [self._message(message) for message in request.messages],
            "stream": stream,
            "options": options,
            "keep_alive": self._settings.keepalive,
        }
        if request.tools:
            payload["tools"] = [self._tool(tool) for tool in request.tools]
        return payload

    def _message(self, message: ModelMessage) -> dict[str, Any]:
        """One transcript turn in Ollama's message shape."""
        role = MessageRole(message.role).value
        body: dict[str, Any] = {"role": role, "content": message.content}
        if message.tool_call_id is not None:
            body["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            body["tool_calls"] = [
                {
                    "function": {"name": call.name, "arguments": dict(call.arguments)},
                }
                for call in message.tool_calls
            ]
        return body

    def _tool(self, tool: ToolDefinition) -> dict[str, Any]:
        """One tool advertised in Ollama's function-calling shape."""
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }

    # ---------------------------------------------------------------------- #
    # Responses
    # ---------------------------------------------------------------------- #
    def _finish_reason(self, data: dict[str, Any], *, has_tool_calls: bool) -> FinishReason:
        if has_tool_calls:
            return FinishReason.TOOL_CALLS
        reason = data.get("done_reason")
        if isinstance(reason, str):
            return _FINISH_REASONS.get(reason.lower(), FinishReason.STOP)
        return FinishReason.STOP

    def _usage(self, data: dict[str, Any]) -> TokenUsage:
        prompt = data.get("prompt_eval_count")
        completion = data.get("eval_count")
        return TokenUsage(
            prompt_tokens=prompt if isinstance(prompt, int) else 0,
            completion_tokens=completion if isinstance(completion, int) else 0,
        )

    def _response(self, data: dict[str, Any], requested_model: str) -> ModelResponse:
        """Translate a completed ``/api/chat`` body into a T060 response."""
        message = data.get("message")
        if not isinstance(message, dict):
            raise ModelResponseInvalidError(
                _STATUS_PROVIDER,
                "Ollama response carried no message object",
                model=requested_model,
            )
        content = message.get("content")
        tool_calls = _parse_tool_calls(message.get("tool_calls"))
        resolved = data.get("model")
        model = resolved if isinstance(resolved, str) and resolved else requested_model
        return ModelResponse(
            model=model,
            content=content if isinstance(content, str) else "",
            tool_calls=tool_calls,
            finish_reason=self._finish_reason(data, has_tool_calls=bool(tool_calls)),
            usage=self._usage(data),
            provider=self.name,
        )

    def _chunks_from_line(self, line: str, requested_model: str) -> list[ModelStreamChunk]:
        """Translate one NDJSON line of a streamed body into chunks."""
        try:
            data = json.loads(line)
        except ValueError as exc:
            raise ModelResponseInvalidError(
                _STATUS_PROVIDER,
                "Ollama streamed a line that was not JSON",
                model=requested_model,
                cause=exc,
            ) from exc
        if not isinstance(data, dict):
            raise ModelResponseInvalidError(
                _STATUS_PROVIDER,
                "Ollama streamed a JSON value that is not an object",
                model=requested_model,
            )
        error = data.get("error")
        if error:
            raise ModelResponseInvalidError(
                _STATUS_PROVIDER,
                f"Ollama reported an error mid-stream: {error}",
                model=requested_model,
            )
        resolved = data.get("model")
        model = resolved if isinstance(resolved, str) and resolved else requested_model
        message = data.get("message")
        message = message if isinstance(message, dict) else {}
        content = message.get("content")
        chunks: list[ModelStreamChunk] = []
        if isinstance(content, str) and content:
            chunks.append(ModelStreamChunk(delta=content, model=model))
        tool_calls = _parse_tool_calls(message.get("tool_calls"))
        chunks.extend(ModelStreamChunk(tool_call=call, model=model) for call in tool_calls)
        if data.get("done"):
            chunks.append(
                ModelStreamChunk(
                    finish_reason=self._finish_reason(data, has_tool_calls=bool(tool_calls)),
                    usage=self._usage(data),
                    model=model,
                )
            )
        return chunks

    # ---------------------------------------------------------------------- #
    # ModelProvider contract (§21)
    # ---------------------------------------------------------------------- #
    async def chat(self, request: CompletionRequest) -> ModelResponse:
        """Run one non-streaming completion (§21's "chat")."""
        payload = self._payload(request, stream=False)
        model = _normalize_model(request.model)
        try:
            response = await self._http().post(
                self._endpoint("/api/chat"),
                json=payload,
                headers=self._headers,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError(
                _STATUS_PROVIDER,
                "Ollama did not answer before the timeout",
                model=model,
                timeout=self._settings.timeout,
                cause=exc,
            ) from exc
        except httpx.TransportError as exc:
            raise LocalModelUnavailableError(
                _STATUS_PROVIDER,
                f"Ollama is unreachable: {exc}",
                model=model,
                cause=exc,
            ) from exc
        self._raise_for_status(response.status_code, model)
        return self._response(self._json(response, model), model)

    async def stream(self, request: CompletionRequest) -> AsyncIterator[ModelStreamChunk]:
        """Stream a completion over Ollama's NDJSON response (§21's "streaming")."""
        payload = self._payload(request, stream=True)
        model = _normalize_model(request.model)
        try:
            async with self._http().stream(
                "POST",
                self._endpoint("/api/chat"),
                json=payload,
                headers=self._headers,
                timeout=self._timeout,
            ) as response:
                if response.status_code >= 300:
                    await response.aread()
                    self._raise_for_status(response.status_code, model)
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    for chunk in self._chunks_from_line(line, model):
                        yield chunk
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError(
                _STATUS_PROVIDER,
                "Ollama timed out mid-stream",
                model=model,
                timeout=self._settings.timeout,
                cause=exc,
            ) from exc
        except httpx.TransportError as exc:
            raise LocalModelUnavailableError(
                _STATUS_PROVIDER,
                f"Ollama is unreachable: {exc}",
                model=model,
                cause=exc,
            ) from exc

    async def list_models(self) -> list[str]:
        """Discover available models (§21's "model discovery").

        When ``OLLAMA_MODELS`` is set it is an allowlist: only those names are
        offered to the router. The allowlist filters what is *pulled*, it does
        not invent models, so a configured-but-absent model simply does not
        appear and :meth:`is_available` reports it unavailable.
        """
        data = await self._get_tags()
        raw = data.get("models")
        names = (
            [str(item["name"]) for item in raw if isinstance(item, dict) and item.get("name")]
            if isinstance(raw, list)
            else []
        )
        if self._allowed:
            return [name for name in names if name in self._allowed]
        return names

    async def is_available(self, model: str) -> bool:
        """Whether ``model`` is pulled and reachable (§21's availability check)."""
        target = _normalize_model(model)
        try:
            available = await self.list_models()
        except UltronError:
            return False
        return target in available or any(
            _base_name(name) == _base_name(target) for name in available
        )

    async def health(self) -> ProviderHealth:
        """Probe ``/api/tags`` under the short connect budget (§21, §33).

        Never raises: a probe's job is to answer, and an exception here would
        make "Ollama is down" indistinguishable from "the probe is broken".
        """
        if not self.is_configured:
            return ProviderHealth(status=ProviderStatus.UNAVAILABLE, detail="no URL configured")
        start = time.perf_counter()
        try:
            response = await self._http().get(
                self._endpoint("/api/tags"),
                headers=self._headers,
                timeout=self._probe_timeout,
            )
        except httpx.TimeoutException:
            return ProviderHealth(
                status=ProviderStatus.UNAVAILABLE,
                detail=f"health probe timed out after {self._settings.connect_timeout}s",
                latency_ms=int((time.perf_counter() - start) * 1000),
            )
        except httpx.TransportError as exc:
            return ProviderHealth(
                status=ProviderStatus.UNAVAILABLE,
                detail=f"Ollama is unreachable: {exc}",
                latency_ms=int((time.perf_counter() - start) * 1000),
            )
        latency_ms = int((time.perf_counter() - start) * 1000)
        if response.status_code == 200:
            return ProviderHealth(
                status=ProviderStatus.AVAILABLE,
                detail="Ollama is serving",
                latency_ms=latency_ms,
            )
        return ProviderHealth(
            status=ProviderStatus.UNAVAILABLE,
            detail=f"Ollama answered HTTP {response.status_code}",
            latency_ms=latency_ms,
        )

    async def _get_tags(self) -> dict[str, Any]:
        """GET ``/api/tags``, mapping transport failures to typed errors."""
        try:
            response = await self._http().get(
                self._endpoint("/api/tags"),
                headers=self._headers,
                timeout=self._probe_timeout,
            )
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError(
                _STATUS_PROVIDER,
                "Ollama did not answer the model list request in time",
                timeout=self._settings.connect_timeout,
                cause=exc,
            ) from exc
        except httpx.TransportError as exc:
            raise LocalModelUnavailableError(
                _STATUS_PROVIDER,
                f"Ollama is unreachable: {exc}",
                cause=exc,
            ) from exc
        self._raise_for_status(response.status_code, "")
        return self._json(response, "")
