"""The Anthropic adapter (T064): optional, unavailable when unconfigured.

Same contract as the other adapters (T060): one ``ModelProvider`` interface, key
checked lazily, failures typed. §3 makes every cloud backend optional — the
server runs with all of them empty — so :attr:`is_configured` is a lazy check on
``ANTHROPIC_API_KEY`` and the provider is simply never selected when blank.

Anthropic's Messages API differs from both OpenAI's and Gemini's, and the
adapter absorbs the difference behind the T060 vocabulary:

* Generation is ``POST {base}/v1/messages`` with the key in the ``x-api-key``
  header and an ``anthropic-version`` header. ``max_tokens`` is **required**, so
  when the router has not set one the adapter supplies a conservative default.
* A conversation is ``messages`` of alternating ``user``/``assistant`` turns
  whose ``content`` is a list of **blocks** (``text``, ``tool_use``,
  ``tool_result``); a system prompt is the separate top-level ``system`` string.
  Tools are ``tools[].input_schema``. Assistant ``tool_calls`` become ``tool_use``
  blocks, ``TOOL`` messages become ``user`` turns carrying a ``tool_result``
  block — the adapter maps the T060 vocabulary onto these and back.
* Token counts come from ``usage.input_tokens``/``output_tokens`` and the stop
  reason from ``stop_reason`` (``end_turn``/``max_tokens``/``stop_sequence``/
  ``tool_use``); :data:`_FINISH_REASONS` normalises it, and a returned
  ``tool_use`` block forces :attr:`FinishReason.TOOL_CALLS`.

Streaming is Anthropic's typed SSE (``message_start``, ``content_block_start``,
``content_block_delta`` with ``text_delta``/``input_json_delta``,
``content_block_stop``, ``message_delta``, ``message_stop``); the adapter keys
tool-call fragments by content-block index and assembles them before the
terminal chunk, so T068 sees the same shape as the other providers.

Errors follow §33: 401/403 → ``ProviderNotConfiguredError``, 404 →
``ModelNotSupportedError``, 429 → ``ModelRateLimitedError``, other HTTP →
``ModelError``, timeout → ``ModelTimeoutError``, unreachable →
``LocalModelUnavailableError``, non-JSON → ``ModelResponseInvalidError``. Health
probes the model list and never raises.
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
)

__all__ = ["AnthropicProvider"]

_PROVIDER = "anthropic"
_API_VERSION = "2023-06-01"

#: ``max_tokens`` is required by the Messages API; used when the router leaves
#: it unset (the router normally supplies ``ModelRouterSettings.max_output_tokens``).
_DEFAULT_MAX_TOKENS = 4096

#: Anthropic's ``stop_reason`` values mapped to the one vocabulary (T060).
_FINISH_REASONS: dict[str, FinishReason] = {
    "end_turn": FinishReason.STOP,
    "stop_sequence": FinishReason.STOP,
    "max_tokens": FinishReason.LENGTH,
    "tool_use": FinishReason.TOOL_CALLS,
    "pause_turn": FinishReason.STOP,
    "refusal": FinishReason.CONTENT_FILTER,
}


def _normalize_model(model: str) -> str:
    """Drop an ``anthropic:`` prefix (the router sends ``anthropic:<model>``)."""
    prefix = "anthropic:"
    return model[len(prefix) :] if model.startswith(prefix) else model


def _base_name(model: str) -> str:
    return model.split(":", 1)[0]


class AnthropicProvider(ModelProvider):
    """The Anthropic provider over the Messages API."""

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
        self._url = self._settings.anthropic_base_url.rstrip("/")
        self._timeout = httpx.Timeout(self._settings.anthropic_timeout)
        self._client = client
        self._owns_client = client is None

    @property
    def is_configured(self) -> bool:
        """True only when a key is present — no key means unavailable (§3)."""
        return self._settings.anthropic_configured

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._settings.anthropic_api_key.get_secret_value(),
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
            self._owns_client = True
        return self._client

    def _configured_or_raise(self, model: str) -> None:
        if not self.is_configured:
            raise ProviderNotConfiguredError(
                _PROVIDER, "Anthropic API key is not configured", model=model
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
                f"Anthropic rejected the credentials (HTTP {status_code})",
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
                "Anthropic is rate limiting requests",
                model=model,
                details={"status": status_code},
            )
        raise ModelError(
            _PROVIDER,
            f"Anthropic request failed (HTTP {status_code})",
            model=model,
            details={"status": status_code},
        )

    def _json(self, response: httpx.Response, model: str) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise ModelResponseInvalidError(
                _PROVIDER, "Anthropic returned a non-JSON response", model=model, cause=exc
            ) from exc
        if not isinstance(data, dict):
            raise ModelResponseInvalidError(
                _PROVIDER, "Anthropic returned a non-object JSON value", model=model
            )
        return data

    def _payload(self, request: CompletionRequest) -> dict[str, Any]:
        system, messages = self._messages(request.messages)
        payload: dict[str, Any] = {
            "model": _normalize_model(request.model),
            "max_tokens": request.max_tokens or _DEFAULT_MAX_TOKENS,
            "messages": messages,
        }
        if system:
            payload["system"] = system
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.stop:
            payload["stop_sequences"] = list(request.stop)
        if request.tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters,
                }
                for tool in request.tools
            ]
        return payload

    def _messages(self, messages: tuple[ModelMessage, ...]) -> tuple[str, list[dict[str, Any]]]:
        system_parts: list[str] = []
        converted: list[dict[str, Any]] = []
        for message in messages:
            role = MessageRole(message.role)
            if role is MessageRole.SYSTEM:
                system_parts.append(message.content)
                continue
            if role is MessageRole.TOOL:
                converted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message.tool_call_id or message.name or "tool",
                                "content": message.content,
                            }
                        ],
                    }
                )
                continue
            blocks: list[dict[str, Any]] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            for call in message.tool_calls:
                blocks.append(
                    {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                )
            converted.append(
                {
                    "role": "assistant" if role is MessageRole.ASSISTANT else "user",
                    "content": blocks,
                }
            )
        return "\n\n".join(system_parts), converted

    def _finish_reason(self, value: Any, *, has_tools: bool) -> FinishReason:
        if has_tools:
            return FinishReason.TOOL_CALLS
        if isinstance(value, str):
            return _FINISH_REASONS.get(value, FinishReason.STOP)
        return FinishReason.STOP

    def _usage(self, data: dict[str, Any]) -> TokenUsage:
        usage = data.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        prompt = usage.get("input_tokens")
        completion = usage.get("output_tokens")
        return TokenUsage(
            prompt_tokens=prompt if isinstance(prompt, int) else 0,
            completion_tokens=completion if isinstance(completion, int) else 0,
        )

    def _tool_calls(self, blocks: list[Any]) -> tuple[ToolCall, ...]:
        calls: list[ToolCall] = []
        for index, block in enumerate(blocks):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            if not isinstance(name, str) or not name:
                continue
            call_id = block.get("id")
            if not isinstance(call_id, str) or not call_id:
                call_id = f"{_PROVIDER}-call-{index}"
            payload = block.get("input")
            calls.append(
                ToolCall(
                    id=call_id,
                    name=name,
                    arguments=dict(payload) if isinstance(payload, dict) else {},
                )
            )
        return tuple(calls)

    def _response(self, data: dict[str, Any], requested_model: str) -> ModelResponse:
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise ModelResponseInvalidError(
                _PROVIDER, "Anthropic response carried no content blocks", model=requested_model
            )
        tool_calls = self._tool_calls(blocks)
        text = "".join(
            block["text"]
            for block in blocks
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
        resolved = data.get("model")
        model = resolved if isinstance(resolved, str) and resolved else requested_model
        return ModelResponse(
            model=model,
            content=text,
            tool_calls=tool_calls,
            finish_reason=self._finish_reason(data.get("stop_reason"), has_tools=bool(tool_calls)),
            usage=self._usage(data),
            provider=self.name,
        )

    # ---------------------------------------------------------------------- #
    # ModelProvider contract
    # ---------------------------------------------------------------------- #
    async def chat(self, request: CompletionRequest) -> ModelResponse:
        model = _normalize_model(request.model)
        self._configured_or_raise(model)
        try:
            response = await self._http().post(
                f"{self._url}/v1/messages",
                json=self._payload(request),
                headers=self._headers(),
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError(
                _PROVIDER,
                "Anthropic did not answer before the timeout",
                model=model,
                timeout=self._settings.anthropic_timeout,
                cause=exc,
            ) from exc
        except httpx.TransportError as exc:
            raise LocalModelUnavailableError(
                _PROVIDER, f"Anthropic is unreachable: {exc}", model=model, cause=exc
            ) from exc
        self._raise_for_status(response.status_code, model)
        return self._response(self._json(response, model), model)

    async def stream(self, request: CompletionRequest) -> AsyncIterator[ModelStreamChunk]:
        model = _normalize_model(request.model)
        self._configured_or_raise(model)
        accumulator = _StreamAccumulator()
        usage = TokenUsage()
        try:
            async with self._http().stream(
                "POST",
                f"{self._url}/v1/messages",
                json=self._payload(request) | {"stream": True},
                headers=self._headers(),
                timeout=self._timeout,
            ) as response:
                if response.status_code >= 300:
                    await response.aread()
                    self._raise_for_status(response.status_code, model)
                async for line in response.aiter_lines():
                    event = _sse_data(line)
                    if event is None:
                        continue
                    kind = event.get("type")
                    if kind == "content_block_start":
                        accumulator.start(event.get("index"), event.get("content_block"))
                    elif kind == "content_block_delta":
                        delta = event.get("delta")
                        delta = delta if isinstance(delta, dict) else {}
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            yield ModelStreamChunk(delta=delta["text"], model=model)
                        elif delta.get("type") == "input_json_delta":
                            accumulator.fragment(event.get("index"), delta.get("partial_json"))
                    elif kind == "message_start":
                        message = event.get("message")
                        if isinstance(message, dict):
                            usage = self._usage(message)
                    elif kind == "message_delta":
                        delta = event.get("delta")
                        delta = delta if isinstance(delta, dict) else {}
                        usage = TokenUsage(
                            prompt_tokens=usage.prompt_tokens,
                            completion_tokens=self._usage(event).completion_tokens
                            or usage.completion_tokens,
                        )
                        for call in accumulator.result():
                            yield ModelStreamChunk(tool_call=call, model=model)
                        yield ModelStreamChunk(
                            finish_reason=self._finish_reason(
                                delta.get("stop_reason"), has_tools=bool(accumulator.result())
                            ),
                            usage=usage,
                            model=model,
                        )
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError(
                _PROVIDER,
                "Anthropic timed out mid-stream",
                model=model,
                timeout=self._settings.anthropic_timeout,
                cause=exc,
            ) from exc
        except httpx.TransportError as exc:
            raise LocalModelUnavailableError(
                _PROVIDER, f"Anthropic is unreachable: {exc}", model=model, cause=exc
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
                f"{self._url}/v1/models", headers=self._headers(), timeout=self._timeout
            )
        except httpx.TimeoutException:
            return ProviderHealth(
                status=ProviderStatus.UNAVAILABLE, detail="health probe timed out"
            )
        except httpx.TransportError as exc:
            return ProviderHealth(
                status=ProviderStatus.UNAVAILABLE, detail=f"Anthropic is unreachable: {exc}"
            )
        if response.status_code == 200:
            return ProviderHealth(status=ProviderStatus.AVAILABLE, detail="Anthropic is reachable")
        if response.status_code in (401, 403):
            return ProviderHealth(
                status=ProviderStatus.UNAVAILABLE,
                detail=f"credentials rejected (HTTP {response.status_code})",
            )
        return ProviderHealth(
            status=ProviderStatus.UNAVAILABLE, detail=f"HTTP {response.status_code}"
        )

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def _get_models(self) -> dict[str, Any]:
        try:
            response = await self._http().get(
                f"{self._url}/v1/models", headers=self._headers(), timeout=self._timeout
            )
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError(
                _PROVIDER, "Anthropic did not answer the model list request in time", cause=exc
            ) from exc
        except httpx.TransportError as exc:
            raise LocalModelUnavailableError(
                _PROVIDER, f"Anthropic is unreachable: {exc}", cause=exc
            ) from exc
        self._raise_for_status(response.status_code, "")
        return self._json(response, "")


class _StreamAccumulator:
    """Reassemble Anthropic's streamed tool-use blocks, keyed by block index.

    ``content_block_start`` names the tool and gives its id; subsequent
    ``input_json_delta`` events append JSON fragments. The accumulator joins them
    so the terminal chunk carries complete :class:`ToolCall`s — the same shape the
    non-streaming path and the other adapters produce.
    """

    def __init__(self) -> None:
        self._parts: dict[int, dict[str, str]] = {}

    def start(self, index: Any, block: Any) -> None:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            return
        key = index if isinstance(index, int) else 0
        name = block.get("name")
        call_id = block.get("id")
        self._parts[key] = {
            "id": call_id if isinstance(call_id, str) else "",
            "name": name if isinstance(name, str) else "",
            "json": "",
        }

    def fragment(self, index: Any, partial: Any) -> None:
        key = index if isinstance(index, int) else 0
        part = self._parts.get(key)
        if part is not None and isinstance(partial, str):
            part["json"] += partial

    def result(self) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for index in sorted(self._parts):
            part = self._parts[index]
            if not part["name"]:
                continue
            arguments: dict[str, Any] = {}
            raw = part["json"]
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


def _sse_data(line: str) -> dict[str, Any] | None:
    """Parse the ``data:`` payload of an SSE line, or ``None`` for other lines.

    Anthropic's stream carries both ``event:`` and ``data:`` lines; the JSON in
    ``data`` already includes its ``type``, so the event line is ignored.
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
            _PROVIDER, "Anthropic streamed an SSE event that was not JSON", cause=exc
        ) from exc
    if not isinstance(parsed, dict):
        raise ModelResponseInvalidError(
            _PROVIDER, "Anthropic streamed an SSE event that was not a JSON object"
        )
    return parsed
