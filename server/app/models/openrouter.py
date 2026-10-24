"""The OpenRouter adapter (spec §59.7): the free-tier cloud gateway.

Spec §59.7 puts OpenRouter's free tier first among cloud options, and
OpenRouter speaks the OpenAI Chat Completions wire format — so this adapter is
the T062 :class:`~app.models.openai.OpenAIProvider` pointed at OpenRouter's base
URL and its own key. Reusing the OpenAI adapter verbatim is the whole point of
one ``ModelProvider`` interface (§20): nothing above the adapter can tell which
provider answered, and the only difference is configuration.

Its own provider ``name`` (``"openrouter"``) matters to the router (T066): the
``model_*`` settings hold ``provider:model`` strings, so ``openrouter:<slug>``
must resolve to this provider rather than to ``openai``. The key is checked
lazily (an absent key is *unavailable*, not broken, §3) and every failure is the
same typed vocabulary the other adapters raise.

Free-only enforcement (T221): the adapter rejects a non-free configured model
at settings load with ``ConfigError``. A free-tier rate limit (HTTP 429) is an
expected condition, not an outage — it is mapped to ``ModelRateLimitedError``
and handled by §59.9's fallback chain.
"""

from __future__ import annotations

import httpx

from app.config import ProviderSettings, get_settings
from app.core.errors import ConfigError, ModelRateLimitedError, ModelNotSupportedError, ProviderNotConfiguredError, ModelError, ModelContextOverflowError
from app.models.openai import OpenAIProvider

__all__ = ["OpenRouterProvider"]

_PROVIDER = "openrouter"


def _normalize_model(model: str) -> str:
    """Drop a ``provider:`` prefix (the router passes ``<provider>:<model>``)."""
    prefix = f"{_PROVIDER}:"
    return model[len(prefix):] if model.startswith(prefix) else model


def _base_name(model: str) -> str:
    return model.split(":", 1)[0]


def _is_free_model(model_id: str) -> bool:
    """Check if an OpenRouter model is a free model (has :free suffix)."""
    # OpenRouter free models have ":free" in their slug
    # e.g., "nvidia/nemotron-3.5-lightning:free" or "openrouter:nvidia/nemotron-3.5-lightning:free"
    normalized = _normalize_model(model_id)
    return ":free" in normalized


def _validate_model_router_settings(router_settings) -> None:
    """Validate that all OpenRouter models in router settings are free models."""
    fields_to_check = [
        ("default", router_settings.default),
        ("coding", router_settings.coding),
        ("research", router_settings.research),
        ("vision", router_settings.vision),
        ("fast", router_settings.fast),
        ("reasoning", router_settings.reasoning),
    ]

    non_free_models = []
    for field_name, model_id in fields_to_check:
        if model_id and model_id.startswith(f"{_PROVIDER}:"):
            if not _is_free_model(model_id):
                non_free_models.append(f"{field_name}={model_id}")

    # Also check fallback chain
    for i, model_id in enumerate(router_settings.fallback_chain):
        if model_id.startswith(f"{_PROVIDER}:"):
            if not _is_free_model(model_id):
                non_free_models.append(f"fallback_chain[{i}]={model_id}")

    if non_free_models:
        raise ConfigError(
            "OpenRouter free-only policy violation (§59.7): "
            f"the following models are not free-tier models (missing ':free' suffix): {', '.join(non_free_models)}. "
            "OpenRouter is configured for free-tier only; all models must have ':free' in their slug."
        )


class OpenRouterProvider(OpenAIProvider):
    """OpenRouter over the OpenAI-compatible Chat Completions API.

    All wire behaviour (payloads, SSE streaming, tool-call reassembly, typed
    errors, health) is inherited from :class:`OpenAIProvider`; only the provider
    ``name`` and the endpoint/key differ, and both come from settings.

    Enforces the §59.7 free-only policy: a model that is not in the free tier
    raises ``ConfigError`` at construction time. The 429 rate limit is mapped to
    ``ModelRateLimitedError`` for the router's fallback chain (§59.9).
    """

    name = _PROVIDER
    local = False
    supports_tools = True
    supports_streaming = True

    def __init__(
        self,
        settings: ProviderSettings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        router_settings=None,
    ) -> None:
        super().__init__(settings or get_settings().providers, client=client)
        self._url = self._settings.openrouter_base_url.rstrip("/")
        self._timeout = httpx.Timeout(self._settings.openrouter_timeout)
        # Validate free-only policy at construction (settings load)
        if router_settings is not None:
            _validate_model_router_settings(router_settings)
        else:
            # Fallback: get from global settings
            _validate_model_router_settings(get_settings().model_router)

    @property
    def is_configured(self) -> bool:
        """True only when ``OPENROUTER_API_KEY`` is present — else unavailable."""
        return self._settings.openrouter_configured

    @property
    def _authorization(self) -> str:
        return f"Bearer {self._settings.openrouter_api_key.get_secret_value()}"

    def _require_configured(self, model: str) -> None:
        if not self.is_configured:
            raise ProviderNotConfiguredError(
                _PROVIDER,
                "OPENROUTER_API_KEY is not set",
                model=model,
            )

    def _validate_free_model(self) -> None:
        """Enforce §59.7 free-only policy.

        The configured model for this provider (from ``model_*`` settings) must
        be a free model. OpenRouter's free models have ``:free`` in their slug
        (e.g., ``nvidia/nemotron-3.5-lightning:free``). If a non-free model is
        configured, raise ``ConfigError`` immediately so the deployment fails
        fast and visibly.
        """
        # Validation is done in __init__ via _validate_model_router_settings
        pass

    # Override methods that use _normalize_model to use OpenRouter's prefix
    def _payload(self, request, *, stream: bool) -> dict:
        payload = super()._payload(request, stream=stream)
        payload["model"] = _normalize_model(request.model)
        return payload

    async def chat(self, request):
        model = _normalize_model(request.model)
        self._require_configured(model)
        # ... rest is handled by parent's _raise_for_status
        return await super().chat(request)

    async def stream(self, request):
        model = _normalize_model(request.model)
        self._require_configured(model)
        return await super().stream(request)

    async def is_available(self, model: str) -> bool:
        if not self.is_configured:
            return False
        target = _normalize_model(model)
        try:
            available = await self.list_models()
        except Exception:
            return False
        return target in available or any(_base_name(name) == _base_name(target) for name in available)

    def _raise_for_status(self, status_code: int, model: str) -> None:
        """Override to map 429 to ModelRateLimitedError for OpenRouter."""
        if 200 <= status_code < 300:
            return
        if status_code in (401, 403):
            raise ProviderNotConfiguredError(
                _PROVIDER,
                f"OpenRouter rejected the credentials (HTTP {status_code})",
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
                "OpenRouter is rate limiting requests (free tier limit reached)",
                model=model,
                details={"status": status_code},
            )
        raise ModelError(
            _PROVIDER,
            f"OpenRouter request failed (HTTP {status_code})",
            model=model,
            details={"status": status_code},
        )