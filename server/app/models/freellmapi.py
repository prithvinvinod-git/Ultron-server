"""The local free-LLM router adapter (OpenAI-compatible gateway).

The router is a loopback OpenAI-compatible gateway: ULTRON sends Chat
Completions to ``FREELLMAPI_BASE_URL`` (default ``http://127.0.0.1:31415/v1``)
with ``FREELLMAPI_API_KEY``, and the gateway fans out to whichever upstream
provider/model is healthy. Because the wire format is OpenAI's, this adapter is
the T062 :class:`~app.models.openai.OpenAIProvider` with its own endpoint and
key — the same reuse `OpenRouterProvider` performs. Nothing above the adapter
can tell which gateway answered (spec §20).

Why a distinct provider name (``freellmapi``) instead of reusing ``openai``:
``model_*`` settings hold ``provider:model`` ids, so ``freellmapi:auto`` must
resolve here, not to a real OpenAI deployment. The gateway's own catalog is
large and its availability changes per upstream slot, so the Settings picker
shows the ids named in ``FREELLMAPI_MODELS`` only (see
:data:`app.config.settings.DEFAULT_FREELLMAPI_MODELS`); a rate-limited slot is
an expected condition and is mapped to ``ModelRateLimitedError`` so the
existing fallback chain (§59.9) handles it rather than surfacing a 500.

Privacy note: the gateway is on loopback, but inference is not. ``local`` stays
``False`` so the UI never claims this path keeps prompts on the machine.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import ProviderSettings, get_settings
from app.core.errors import (
    ModelError,
    ModelRateLimitedError,
    ModelTimeoutError,
    ProviderNotConfiguredError,
)
from app.models.openai import OpenAIProvider

__all__ = ["FreeLLMProvider"]

_PROVIDER = "freellmapi"


class FreeLLMProvider(OpenAIProvider):
    """OpenAI Chat Completions against the local free-LLM router."""

    name = _PROVIDER
    # Loopback transport, cloud inference: see the module docstring.
    local = False
    supports_tools = True
    supports_streaming = True

    def __init__(
        self,
        settings: ProviderSettings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(settings or get_settings().providers, client=client)
        self._url = self._settings.freellmapi_base_url.rstrip("/")
        self._timeout = httpx.Timeout(self._settings.freellmapi_timeout)

    @property
    def is_configured(self) -> bool:
        """True only when ``FREELLMAPI_API_KEY`` is present — else unavailable."""
        return self._settings.freellmapi_configured

    @property
    def _authorization(self) -> str:
        return f"Bearer {self._settings.freellmapi_api_key.get_secret_value()}"

    def _require_configured(self, model: str) -> None:
        if not self.is_configured:
            raise ProviderNotConfiguredError(
                _PROVIDER,
                "FREELLMAPI_API_KEY is not set",
                model=model,
            )

    def _raise_for_status(self, status_code: int, model: str) -> None:
        """Map gateway failures onto ULTRON's typed model errors.

        A rate-limited *slot* is normal for a free router; treating it as a
        fallback-worthy ``ModelRateLimitedError`` is what lets the chain move on.
        """
        if 200 <= status_code < 300:
            return
        if status_code in (401, 403):
            raise ProviderNotConfiguredError(
                _PROVIDER,
                f"the free-LLM router rejected the credentials (HTTP {status_code})",
                model=model,
                details={"status": status_code},
            )
        if status_code == 429:
            raise ModelRateLimitedError(
                _PROVIDER,
                "the free-LLM router has no upstream slot available right now",
                model=model,
                details={"status": status_code},
            )
        if status_code in (502, 503, 504):
            raise ModelError(
                _PROVIDER,
                f"an upstream behind the free-LLM router failed (HTTP {status_code})",
                model=model,
                details={"status": status_code},
            )
        raise ModelError(
            _PROVIDER,
            f"free-LLM router request failed (HTTP {status_code})",
            model=model,
            details={"status": status_code},
        )

    async def _get_models(self) -> dict[str, Any]:
        """List gateway models, naming this provider in transport failures."""
        try:
            response = await self._http().get(
                self._endpoint("/models"),
                headers=self._headers(),
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError(
                _PROVIDER,
                "the free-LLM router did not answer the model list request in time",
                cause=exc,
            ) from exc
        except httpx.TransportError as exc:
            raise ModelError(
                _PROVIDER,
                f"the free-LLM router is unreachable: {exc}",
                cause=exc,
            ) from exc
        self._raise_for_status(response.status_code, "")
        return self._json(response, "")
