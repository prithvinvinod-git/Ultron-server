"""The Google Gemini adapter (T063): optional, unavailable when unconfigured.

Same contract as the other adapters (T060): one ``ModelProvider`` interface, key
checked lazily, failures typed. §3 makes every cloud backend optional — the
server runs with all of them empty — so :attr:`is_configured` is a lazy check on
``GEMINI_API_KEY`` and the provider is simply never selected when it is blank.

Gemini's REST surface differs from OpenAI's, and the adapter absorbs the
difference so nothing above it can tell:

* Generation is ``POST {base}/v1beta/models/{model}:generateContent`` (or
  ``:streamGenerateContent?alt=sse`` for streaming), with the key in the
  ``x-goog-api-key`` header.
* A conversation is ``contents`` — turns with ``role`` ``user``/``model`` and a
  list of ``parts`` (``text``, ``functionCall``, ``functionResponse``); a system
  prompt is the separate ``systemInstruction``. Tools are
  ``tools[].functionDeclarations[]``. The adapter maps the T060 vocabulary onto
  these and back.
* Token counts live in ``usageMetadata`` (``promptTokenCount`` /
  ``candidatesTokenCount``) and the stop reason in ``candidates[].finishReason``;
  :data:`_FINISH_REASONS` normalises both, and a returned ``functionCall`` forces
  :attr:`FinishReason.TOOL_CALLS` because Gemini reports ``STOP`` for it.

Errors follow §33 and match the other adapters: 401/403 →
``ProviderNotConfiguredError``, 404 → ``ModelNotSupportedError``, 429 →
``ModelRateLimitedError``, other HTTP → ``ModelError``, timeout →
``ModelTimeoutError``, unreachable → ``LocalModelUnavailableError``, non-JSON →
``ModelResponseInvalidError``. Health probes the model list and never raises.
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

__all__ = ["GeminiProvider"]

_PROVIDER = "gemini"

#: Gemini's ``finishReason`` values mapped to the one vocabulary (T060).
_FINISH_REASONS: dict[str, FinishReason] = {
    "STOP": FinishReason.STOP,
    "MAX_TOKENS": FinishReason.LENGTH,
    "SAFETY": FinishReason.CONTENT_FILTER,
    "RECITATION": FinishReason.CONTENT_FILTER,
    "BLOCKLIST": FinishReason.CONTENT_FILTER,
    "PROHIBITED_CONTENT": FinishReason.CONTENT_FILTER,
    "SPII": FinishReason.CONTENT_FILTER,
    "MALFORMED_FUNCTION_CALL": FinishReason.ERROR,
    "OTHER": FinishReason.STOP,
}


def _normalize_model(model: str) -> str:
    """Drop a ``provider:`` or ``models/`` prefix (the router sends ``gemini:x``)."""
    if model.startswith("gemini:"):
        model = model[len("gemini:") :]
    if model.startswith("models/"):
        model = model[len("models/") :]
    return model


def _base_name(model: str) -> str:
    return model.split(":", 1)[0]


class GeminiProvider(ModelProvider):
    """The Google Gemini provider over the Generative Language REST API."""

    name = "gemini"
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
        self._url = self._settings.gemini_base_url.rstrip("/")
        self._timeout = httpx.Timeout(self._settings.gemini_timeout)
        self._client = client
        self._owns_client = client is None

    @property
    def is_configured(self) -> bool:
        """True only when a key is present — no key means unavailable (§3)."""
        return self._settings.gemini_configured

    def _headers(self) -> dict[str, str]:
        return {
            "x-goog-api-key": self._settings.gemini_api_key.get_secret_value(),
            "content-type": "application/json",
        }

    def _generate_url(self, model: str, *, stream: bool) -> str:
        verb = "streamGenerateContent" if stream else "generateContent"
        url = f"{self._url}/v1beta/models/{model}:{verb}"
        return f"{url}?alt=sse" if stream else url

    def _list_url(self) -> str:
        return f"{self._url}/v1beta/models"

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
            self._owns_client = True
        return self._client

    def _configured_or_raise(self, model: str) -> None:
        if not self.is_configured:
            raise ProviderNotConfiguredError(
                _PROVIDER, "Gemini API key is not configured", model=model
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
                f"Gemini rejected the credentials (HTTP {status_code})",
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
                "Gemini is rate limiting requests",
                model=model,
                details={"status": status_code},
            )
        raise ModelError(
            _PROVIDER,
            f"Gemini request failed (HTTP {status_code})",
            model=model,
            details={"status": status_code},
        )

    def _json(self, response: httpx.Response, model: str) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise ModelResponseInvalidError(
                _PROVIDER, "Gemini returned a non-JSON response", model=model, cause=exc
            ) from exc
        if not isinstance(data, dict):
            raise ModelResponseInvalidError(
                _PROVIDER, "Gemini returned a non-object JSON value", model=model
            )
        return data

    def _payload(self, request: CompletionRequest) -> dict[str, Any]:
        system, contents = self._contents(request.messages)
        payload: dict[str, Any] = {"contents": contents}
        if system:
            payload["systemInstruction"] = {"parts": system}
        if request.tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.parameters,
                        }
                        for tool in request.tools
                    ]
                }
            ]
        config: dict[str, Any] = {}
        if request.temperature is not None:
            config["temperature"] = request.temperature
        if request.max_tokens is not None:
            config["maxOutputTokens"] = request.max_tokens
        if request.stop:
            config["stopSequences"] = list(request.stop)
        if config:
            payload["generationConfig"] = config
        return payload

    def _contents(
        self, messages: tuple[ModelMessage, ...]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        system: list[dict[str, Any]] = []
        contents: list[dict[str, Any]] = []
        for message in messages:
            role = MessageRole(message.role)
            if role is MessageRole.SYSTEM:
                system.append({"text": message.content})
                continue
            if role is MessageRole.TOOL:
                name = message.name or message.tool_call_id or "tool"
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": name,
                                    "response": {"content": message.content},
                                }
                            }
                        ],
                    }
                )
                continue
            parts: list[dict[str, Any]] = []
            if message.content:
                parts.append({"text": message.content})
            for call in message.tool_calls:
                parts.append({"functionCall": {"name": call.name, "args": call.arguments}})
            contents.append(
                {"role": "model" if role is MessageRole.ASSISTANT else "user", "parts": parts}
            )
        return system, contents

    def _finish_reason(self, value: Any, *, has_tools: bool) -> FinishReason:
        if has_tools:
            return FinishReason.TOOL_CALLS
        if isinstance(value, str):
            return _FINISH_REASONS.get(value, FinishReason.STOP)
        return FinishReason.STOP

    def _usage(self, data: dict[str, Any]) -> TokenUsage:
        usage = data.get("usageMetadata")
        usage = usage if isinstance(usage, dict) else {}
        prompt = usage.get("promptTokenCount")
        completion = usage.get("candidatesTokenCount")
        return TokenUsage(
            prompt_tokens=prompt if isinstance(prompt, int) else 0,
            completion_tokens=completion if isinstance(completion, int) else 0,
        )

    def _tool_calls(self, parts: list[Any]) -> tuple[ToolCall, ...]:
        calls: list[ToolCall] = []
        for index, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            call = part.get("functionCall")
            if not isinstance(call, dict):
                continue
            name = call.get("name")
            if not isinstance(name, str) or not name:
                continue
            args = call.get("args")
            calls.append(
                ToolCall(
                    id=f"{_PROVIDER}-call-{index}",
                    name=name,
                    arguments=dict(args) if isinstance(args, dict) else {},
                )
            )
        return tuple(calls)

    def _parts(self, data: dict[str, Any]) -> tuple[dict[str, Any], list[Any]]:
        candidates = data.get("candidates")
        candidate = candidates[0] if isinstance(candidates, list) and candidates else {}
        candidate = candidate if isinstance(candidate, dict) else {}
        content = candidate.get("content")
        content = content if isinstance(content, dict) else {}
        parts = content.get("parts")
        return candidate, parts if isinstance(parts, list) else []

    def _response(self, data: dict[str, Any], requested_model: str) -> ModelResponse:
        candidate, parts = self._parts(data)
        if not candidate:
            raise ModelResponseInvalidError(
                _PROVIDER, "Gemini response carried no candidates", model=requested_model
            )
        tool_calls = self._tool_calls(parts)
        text = "".join(
            part["text"]
            for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
        resolved = data.get("modelVersion")
        model = resolved if isinstance(resolved, str) and resolved else requested_model
        return ModelResponse(
            model=model,
            content=text,
            tool_calls=tool_calls,
            finish_reason=self._finish_reason(
                candidate.get("finishReason"), has_tools=bool(tool_calls)
            ),
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
                self._generate_url(model, stream=False),
                json=self._payload(request),
                headers=self._headers(),
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError(
                _PROVIDER,
                "Gemini did not answer before the timeout",
                model=model,
                timeout=self._settings.gemini_timeout,
                cause=exc,
            ) from exc
        except httpx.TransportError as exc:
            raise LocalModelUnavailableError(
                _PROVIDER, f"Gemini is unreachable: {exc}", model=model, cause=exc
            ) from exc
        self._raise_for_status(response.status_code, model)
        return self._response(self._json(response, model), model)

    async def stream(self, request: CompletionRequest) -> AsyncIterator[ModelStreamChunk]:
        model = _normalize_model(request.model)
        self._configured_or_raise(model)
        try:
            async with self._http().stream(
                "POST",
                self._generate_url(model, stream=True),
                json=self._payload(request),
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
                    for chunk in self._stream_chunks(event, model):
                        yield chunk
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError(
                _PROVIDER,
                "Gemini timed out mid-stream",
                model=model,
                timeout=self._settings.gemini_timeout,
                cause=exc,
            ) from exc
        except httpx.TransportError as exc:
            raise LocalModelUnavailableError(
                _PROVIDER, f"Gemini is unreachable: {exc}", model=model, cause=exc
            ) from exc

    def _stream_chunks(self, event: dict[str, Any], requested_model: str) -> list[ModelStreamChunk]:
        candidate, parts = self._parts(event)
        resolved = event.get("modelVersion")
        model = resolved if isinstance(resolved, str) and resolved else requested_model
        chunks: list[ModelStreamChunk] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text:
                chunks.append(ModelStreamChunk(delta=text, model=model))
            call = self._tool_calls([part])
            if call:
                chunks.append(ModelStreamChunk(tool_call=call[0], model=model))
        finish = candidate.get("finishReason") if candidate else None
        if finish is not None:
            chunks.append(
                ModelStreamChunk(
                    finish_reason=self._finish_reason(finish, has_tools=False),
                    usage=self._usage(event),
                    model=model,
                )
            )
        return chunks

    async def list_models(self) -> list[str]:
        if not self.is_configured:
            return []
        data = await self._get_models()
        raw = data.get("models")
        if not isinstance(raw, list):
            return []
        models: list[str] = []
        for item in raw:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                models.append(item["name"].removeprefix("models/"))
        return models

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
                self._list_url(), headers=self._headers(), timeout=self._timeout
            )
        except httpx.TimeoutException:
            return ProviderHealth(
                status=ProviderStatus.UNAVAILABLE, detail="health probe timed out"
            )
        except httpx.TransportError as exc:
            return ProviderHealth(
                status=ProviderStatus.UNAVAILABLE, detail=f"Gemini is unreachable: {exc}"
            )
        if response.status_code == 200:
            return ProviderHealth(status=ProviderStatus.AVAILABLE, detail="Gemini is reachable")
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
                self._list_url(), headers=self._headers(), timeout=self._timeout
            )
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError(
                _PROVIDER, "Gemini did not answer the model list request in time", cause=exc
            ) from exc
        except httpx.TransportError as exc:
            raise LocalModelUnavailableError(
                _PROVIDER, f"Gemini is unreachable: {exc}", cause=exc
            ) from exc
        self._raise_for_status(response.status_code, "")
        return self._json(response, "")


def _sse_event(line: str) -> dict[str, Any] | None:
    """Parse one SSE line into its JSON object, or ``None`` for non-events."""
    if not line.startswith("data:"):
        return None
    raw = line[len("data:") :].strip()
    if not raw or raw == "[DONE]":
        return None
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise ModelResponseInvalidError(
            _PROVIDER, "Gemini streamed an SSE event that was not JSON", cause=exc
        ) from exc
    if not isinstance(parsed, dict):
        raise ModelResponseInvalidError(
            _PROVIDER, "Gemini streamed an SSE event that was not a JSON object"
        )
    return parsed
