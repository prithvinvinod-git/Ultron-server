"""The OpenAI adapter (T062): optional, and unavailable when unconfigured.

Spec §3 lists OpenAI among the optional cloud backends and makes the rule
explicit: *the server boots and runs with every cloud provider unconfigured.*
§20 keeps it behind the same ``ModelProvider`` interface as the local adapter
(T060), so nothing above the adapter can tell which provider answered. §59.7
(added with §66.6) puts OpenRouter's free tier first among cloud options, and
OpenRouter is OpenAI-compatible — so this module speaks the OpenAI Chat
Completions wire format against a **configurable base URL**
(``OPENAI_BASE_URL``), which is the one change needed to point it at OpenRouter
or any other compatible gateway. That is why the provider is named ``openai``
but is not hard-coded to ``api.openai.com``.

Two §33 rules drive the shape:

* **An absent key is unavailable, not broken.** :attr:`is_configured` is a lazy
  check on ``OPENAI_API_KEY``; when it is empty the server still boots, the
  router simply never selects this provider, and a direct call raises
  ``ProviderNotConfiguredError`` (503, non-retryable) rather than a 401 from the
  network.
* **Failures are typed.** 401/403 → ``ProviderNotConfiguredError``, 404 →
  ``ModelNotSupportedError`` (400), 429 → ``ModelRateLimitedError``, other 4xx/5xx
  → ``ModelError`` (502), a timeout → ``ModelTimeoutError`` (504), an
  unreachable host ``LocalModelUnavailableError`` (503) — the same vocabulary
  the router (T066) already branches on for Ollama, which is the whole point of
  one interface.

Streaming is Server-Sent Events (``data: {json}`` lines terminated by
``data: [DONE]``), including the incremental tool-call fragments the API
streams by index; the adapter assembles them into complete ``ToolCall``s before
the terminal chunk, so the tool-calling loop (T068) sees the same shape it gets
from Ollama's non-fragmented NDJSON.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import ProviderSettings, get_settings
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

__all__ = ["OpenAIProvider"]

_PROVIDER = "openai"

#: OpenAI's ``finish_reason`` values mapped to the one vocabulary (T060).
_FINISH_REASONS: dict[str, FinishReason] = {
    "stop": FinishReason.STOP,
    "length": FinishReason.LENGTH,
    "tool_calls": FinishReason.TOOL_CALLS,
    "function_call": FinishReason.TOOL_CALLS,
    "content_filter": FinishReason.CONTENT_FILTER,
}


def _normalize_model(model: str) -> str:
    """Drop a ``provider:`` prefix (the router passes ``<provider>:<model>``)."""
    prefix = f"{_PROVIDER}:"
    return model[len(prefix) :] if model.startswith(prefix) else model


def _base_name(model: str) -> str:
    return model.split(":", 1)[0]


class OpenAIProvider(ModelProvider):
    """The OpenAI (and OpenAI-compatible) provider over Chat Completions."""

    name = _PROVIDER
    local = False
    supports_tools = True
    supports_streaming = True

    def __init__(
        self,
        settings: ProviderSettings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings().providers
        self._url = self._settings.openai_base_url.rstrip("/")
        self._timeout = httpx.Timeout(self._settings.openai_timeout)
        self._client = client
        self._owns_client = client is None

    @property
    def is_configured(self) -> bool:
        """True only when a key is present — no key means unavailable (§3)."""
        return self._settings.openai_configured

    @property
    def _authorization(self) -> str:
        return f"Bearer {self._settings.openai_api_key.get_secret_value()}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._authorization,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
            self._owns_client = True
        return self._client

    def _endpoint(self, path: str) -> str:
        return f"{self._url}{path}"

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    def _require_configured(self, model: str) -> None:
        if not self.is_configured:
            raise ProviderNotConfiguredError(
                _PROVIDER,
                "OPENAI_API_KEY is not set",
                model=model,
            )

    # ---------------------------------------------------------------------- #
    # Errors and parsing
    # ---------------------------------------------------------------------- #
    def _raise_for_status(self, status_code: int, model: str) -> None:
        if 200 <= status_code < 300:
            return
        if status_code in (401, 403):
            raise ProviderNotConfiguredError(
                _PROVIDER,
                f"OpenAI rejected the credentials (HTTP {status_code})",
                model=model,
                details={"status": status_code},
            )
        if status_code == 404:
            raise ModelNotSupportedError(
                _PROVIDER,
                f"model '{model}' is not available",
                model=model,
                details={"status": status_code},
            )
        if status_code == 429:
            raise ModelRateLimitedError(
                _PROVIDER,
                "OpenAI is rate limiting requests",
                model=model,
                details={"status": status_code},
            )
        raise ModelError(
            _PROVIDER,
            f"OpenAI request failed (HTTP {status_code})",
            model=model,
            details={"status": status_code},
        )

    def _json(self, response: httpx.Response, model: str) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise ModelResponseInvalidError(
                _PROVIDER, "OpenAI returned a non-JSON response", model=model, cause=exc
            ) from exc
        if not isinstance(data, dict):
            raise ModelResponseInvalidError(
                _PROVIDER, "OpenAI returned a non-object JSON value", model=model
            )
        return data

    def _payload(self, request: CompletionRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": _normalize_model(request.model),
            "messages": [self._message(message) for message in request.messages],
            "stream": stream,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.stop:
            payload["stop"] = list(request.stop)
        if request.tools:
            payload["tools"] = [self._tool(tool) for tool in request.tools]
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _message(self, message: ModelMessage) -> dict[str, Any]:
        body: dict[str, Any] = {
            "role": MessageRole(message.role).value,
            "content": message.content,
        }
        if message.name is not None:
            body["name"] = message.name
        if message.tool_call_id is not None:
            body["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            body["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in message.tool_calls
            ]
        return body

    def _tool(self, tool: ToolDefinition) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }

    def _finish_reason(self, value: Any) -> FinishReason:
        if isinstance(value, str):
            return _FINISH_REASONS.get(value, FinishReason.STOP)
        return FinishReason.STOP

    def _usage(self, data: dict[str, Any]) -> TokenUsage:
        usage = data.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        return TokenUsage(
            prompt_tokens=prompt if isinstance(prompt, int) else 0,
            completion_tokens=completion if isinstance(completion, int) else 0,
        )

    def _tool_calls(self, raw: Any) -> tuple[ToolCall, ...]:
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
                call_id = f"{_PROVIDER}-call-{index}"
            calls.append(
                ToolCall(
                    id=call_id,
                    name=name,
                    arguments=self._arguments(function.get("arguments")),
                )
            )
        return tuple(calls)

    def _arguments(self, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return dict(value)
        if not isinstance(value, str) or not value.strip():
            return {}
        try:
            parsed = json.loads(value)
        except ValueError as exc:
            raise ModelResponseInvalidError(
                _PROVIDER,
                "tool call arguments were not valid JSON",
                details={"arguments": value[:200]},
                cause=exc,
            ) from exc
        if isinstance(parsed, dict):
            return parsed
        raise ModelResponseInvalidError(
            _PROVIDER,
            "tool call arguments were not a JSON object",
            details={"arguments": value[:200]},
        )

    def _response(self, data: dict[str, Any], requested_model: str) -> ModelResponse:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelResponseInvalidError(
                _PROVIDER, "OpenAI response carried no choices", model=requested_model
            )
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ModelResponseInvalidError(
                _PROVIDER, "OpenAI response choice was not an object", model=requested_model
            )
        message = choice.get("message")
        message = message if isinstance(message, dict) else {}
        content = message.get("content")
        tool_calls = self._tool_calls(message.get("tool_calls"))
        resolved = data.get("model")
        model = resolved if isinstance(resolved, str) and resolved else requested_model
        return ModelResponse(
            model=model,
            content=content if isinstance(content, str) else "",
            tool_calls=tool_calls,
            finish_reason=self._finish_reason(choice.get("finish_reason")),
            usage=self._usage(data),
            provider=self.name,
        )

    # ---------------------------------------------------------------------- #
    # Streaming assembly
    # ---------------------------------------------------------------------- #
    def _stream_chunks(
        self, event: dict[str, Any], accumulator: _ToolCallAccumulator, requested_model: str
    ) -> list[ModelStreamChunk]:
        resolved = event.get("model")
        model = resolved if isinstance(resolved, str) and resolved else requested_model
        chunks: list[ModelStreamChunk] = []
        choices = event.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else {}
        choice = choice if isinstance(choice, dict) else {}
        delta = choice.get("delta")
        delta = delta if isinstance(delta, dict) else {}
        content = delta.get("content")
        if isinstance(content, str) and content:
            chunks.append(ModelStreamChunk(delta=content, model=model))
        fragments = delta.get("tool_calls")
        if isinstance(fragments, list):
            accumulator.add(fragments)
        finish = choice.get("finish_reason")
        if finish is not None:
            for call in accumulator.result():
                chunks.append(ModelStreamChunk(tool_call=call, model=model))
            chunks.append(
                ModelStreamChunk(
                    finish_reason=self._finish_reason(finish),
                    usage=self._usage(event),
                    model=model,
                )
            )
        return chunks

    # ---------------------------------------------------------------------- #
    # ModelProvider contract
    # ---------------------------------------------------------------------- #
    async def chat(self, request: CompletionRequest) -> ModelResponse:
        model = _normalize_model(request.model)
        self._require_configured(model)
        try:
            response = await self._http().post(
                self._endpoint("/chat/completions"),
                json=self._payload(request, stream=False),
                headers=self._headers(),
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError(
                _PROVIDER,
                "OpenAI did not answer before the timeout",
                model=model,
                timeout=self._settings.openai_timeout,
                cause=exc,
            ) from exc
        except httpx.TransportError as exc:
            raise LocalModelUnavailableError(
                _PROVIDER, f"OpenAI is unreachable: {exc}", model=model, cause=exc
            ) from exc
        self._raise_for_status(response.status_code, model)
        return self._response(self._json(response, model), model)

    async def stream(self, request: CompletionRequest) -> AsyncIterator[ModelStreamChunk]:
        model = _normalize_model(request.model)
        self._require_configured(model)
        accumulator = _ToolCallAccumulator()
        try:
            async with self._http().stream(
                "POST",
                self._endpoint("/chat/completions"),
                json=self._payload(request, stream=True),
                headers=self._headers(),
                timeout=self._timeout,
            ) as response:
                if response.status_code >= 300:
                    await response.aread()
                    self._raise_for_status(response.status_code, model)
                async for line in response.aiter_lines():
                    event = _sse_event(line)
                    if event is None:
                        continue
                    error = event.get("error")
                    if error:
                        raise ModelResponseInvalidError(
                            _PROVIDER, f"OpenAI reported an error mid-stream: {error}", model=model
                        )
                    for chunk in self._stream_chunks(event, accumulator, model):
                        yield chunk
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError(
                _PROVIDER,
                "OpenAI timed out mid-stream",
                model=model,
                timeout=self._settings.openai_timeout,
                cause=exc,
            ) from exc
        except httpx.TransportError as exc:
            raise LocalModelUnavailableError(
                _PROVIDER, f"OpenAI is unreachable: {exc}", model=model, cause=exc
            ) from exc

    async def list_models(self) -> list[str]:
        if not self.is_configured:
            return []
        data = await self._get_models()
        raw = data.get("data")
        if not isinstance(raw, list):
            return []
        return [str(item["id"]) for item in raw if isinstance(item, dict) and item.get("id")]

    async def is_available(self, model: str) -> bool:
        if not self.is_configured:
            return False
        target = _normalize_model(model)
        try:
            available = await self.list_models()
        except UltronError:
            return False
        return target in available or any(
            _base_name(name) == _base_name(target) for name in available
        )

    async def health(self) -> ProviderHealth:
        if not self.is_configured:
            return ProviderHealth(status=ProviderStatus.UNAVAILABLE, detail="no API key configured")
        try:
            response = await self._http().get(
                self._endpoint("/models"),
                headers=self._headers(),
                timeout=self._timeout,
            )
        except httpx.TimeoutException:
            return ProviderHealth(
                status=ProviderStatus.UNAVAILABLE, detail="health probe timed out"
            )
        except httpx.TransportError as exc:
            return ProviderHealth(
                status=ProviderStatus.UNAVAILABLE, detail=f"OpenAI is unreachable: {exc}"
            )
        if response.status_code == 200:
            return ProviderHealth(status=ProviderStatus.AVAILABLE, detail="OpenAI is reachable")
        if response.status_code in (401, 403):
            return ProviderHealth(
                status=ProviderStatus.UNAVAILABLE,
                detail=f"credentials rejected (HTTP {response.status_code})",
            )
        return ProviderHealth(
            status=ProviderStatus.UNAVAILABLE, detail=f"HTTP {response.status_code}"
        )

    async def _get_models(self) -> dict[str, Any]:
        try:
            response = await self._http().get(
                self._endpoint("/models"),
                headers=self._headers(),
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError(
                _PROVIDER, "OpenAI did not answer the model list request in time", cause=exc
            ) from exc
        except httpx.TransportError as exc:
            raise LocalModelUnavailableError(
                _PROVIDER, f"OpenAI is unreachable: {exc}", cause=exc
            ) from exc
        self._raise_for_status(response.status_code, "")
        return self._json(response, "")


class _ToolCallAccumulator:
    """Reassemble streamed tool-call fragments, which arrive by index.

    OpenAI streams a tool call across several deltas: index 0 gets the id and
    function name, later deltas append to the JSON ``arguments`` string. The
    accumulator keys on index and joins the fragments, so the loop (T068) sees
    one complete :class:`ToolCall` exactly as the non-streaming path produces.
    """

    def __init__(self) -> None:
        self._parts: dict[int, dict[str, str]] = {}

    def add(self, fragments: list[Any]) -> None:
        for fragment in fragments:
            if not isinstance(fragment, dict):
                continue
            index = fragment.get("index")
            index = index if isinstance(index, int) else 0
            part = self._parts.setdefault(index, {"id": "", "name": "", "arguments": ""})
            call_id = fragment.get("id")
            if isinstance(call_id, str) and call_id:
                part["id"] = call_id
            function = fragment.get("function")
            if isinstance(function, dict):
                name = function.get("name")
                if isinstance(name, str) and name:
                    part["name"] = name
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    part["arguments"] += arguments

    def result(self) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for index in sorted(self._parts):
            part = self._parts[index]
            if not part["name"]:
                continue
            raw = part["arguments"]
            arguments: dict[str, Any] = {}
            if raw.strip():
                try:
                    parsed = json.loads(raw)
                except ValueError as exc:
                    raise ModelResponseInvalidError(
                        _PROVIDER,
                        "streamed tool call arguments were not valid JSON",
                        details={"arguments": raw[:200]},
                        cause=exc,
                    ) from exc
                if isinstance(parsed, dict):
                    arguments = parsed
            calls.append(
                ToolCall(
                    id=part["id"] or f"{_PROVIDER}-call-{index}",
                    name=part["name"],
                    arguments=arguments,
                )
            )
        return calls


def _sse_event(line: str) -> dict[str, Any] | None:
    """Parse one SSE line into its JSON object, or ``None`` for non-events.

    ``data: [DONE]`` ends the stream and blank/comment lines carry nothing; both
    return ``None`` so the caller's loop is uniform.
    """
    if not line.startswith("data:"):
        return None
    raw = line[len("data:") :].strip()
    if not raw or raw == "[DONE]":
        return None
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise ModelResponseInvalidError(
            _PROVIDER, "OpenAI streamed an SSE event that was not JSON", cause=exc
        ) from exc
    if not isinstance(parsed, dict):
        raise ModelResponseInvalidError(
            _PROVIDER, "OpenAI streamed an SSE event that was not a JSON object"
        )
    return parsed
