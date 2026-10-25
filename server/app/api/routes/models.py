"""Model catalog route (T066, spec §20 / §49).

The Settings model picker needs an honest list of what this deployment can run.
The router only ever selects from **configured** ``provider:model`` strings (§20),
so this route reports exactly that set — no model name is invented here, and a
provider with no credentials is omitted rather than shown as a dead option
(§33: an absent provider is unavailable, not broken).

Two things are returned, both straight from configuration:

- ``providers`` — the configured providers, with their capability flags, so the
  picker can group models and skip a provider that cannot do tool calls.
- ``models`` — the de-duplicated capability model plus the fallback chain,
  *filtered to configured providers* so a stale id in the chain never appears as
  a selectable option that would silently fall back at call time.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import Container

router = APIRouter(prefix="/models", tags=["models"])


class ModelProviderInfo(BaseModel):
    """One configured provider, as the picker needs to see it."""

    model_config = ConfigDict(extra="forbid")

    name: str
    local: bool
    configured: bool
    supports_tools: bool
    supports_streaming: bool


class ModelOption(BaseModel):
    """One selectable ``provider:model`` identifier."""

    model_config = ConfigDict(extra="forbid")

    id: str
    provider: str
    model: str
    label: str
    reason: str = ""


class ModelCatalog(BaseModel):
    """The configured providers and the models they can serve."""

    model_config = ConfigDict(extra="forbid")

    default: str
    providers: list[ModelProviderInfo]
    models: list[ModelOption]


def _split(model_id: str) -> tuple[str, str]:
    head, sep, rest = model_id.partition(":")
    return (head, rest) if sep else (model_id, model_id)


@router.get(
    "",
    response_model=ModelCatalog,
    summary="List configured model providers and the models to choose from",
)
async def models(container: Container) -> ModelCatalog:
    """Report the configured providers and the models this deployment can run.

    Only providers whose credentials/host are present appear, so the picker never
    offers a choice that would fail at call time. The default and fallback chain
    are de-duplicated and filtered the same way.
    """
    router_ = container.model_router
    settings = container.settings.model_router

    provider_infos: list[ModelProviderInfo] = []
    configured_names: set[str] = set()
    for name, provider in router_.providers.items():
        if not provider.is_configured:
            continue
        configured_names.add(name)
        provider_infos.append(
            ModelProviderInfo(
                name=name,
                local=bool(provider.local),
                configured=True,
                supports_tools=bool(provider.supports_tools),
                supports_streaming=bool(provider.supports_streaming),
            )
        )

    options: list[ModelOption] = []
    seen: set[str] = set()
    ordered = [settings.default, *settings.fallback_chain]
    for model_id in ordered:
        if not model_id or model_id in seen:
            continue
        provider_name, model_name = _split(model_id)
        if provider_name not in configured_names:
            continue
        seen.add(model_id)
        label = f"{provider_name} · {model_name}" if provider_name != model_name else model_id
        options.append(
            ModelOption(
                id=model_id,
                provider=provider_name,
                model=model_name,
                label=label,
                reason="default" if model_id == settings.default else "fallback",
            )
        )

    # The free-LLM router exposes hundreds of upstream slots whose availability
    # changes per provider quota. Showing all of them would make the picker
    # unusable and mostly dead, so only the ids the operator named (or the
    # built-in shortlist) are offered. A slot that is rate-limited at call time
    # is handled by the fallback chain, not hidden here.
    provider_settings = container.settings.providers
    if "freellmapi" in configured_names:
        for model_name in provider_settings.freellmapi_models:
            model_id = f"freellmapi:{model_name}"
            if model_id in seen:
                continue
            seen.add(model_id)
            options.append(
                ModelOption(
                    id=model_id,
                    provider="freellmapi",
                    model=model_name,
                    label=f"freellmapi · {model_name}",
                    reason="available",
                )
            )

    return ModelCatalog(
        default=settings.default,
        providers=provider_infos,
        models=options,
    )


__all__ = ["ModelCatalog", "ModelOption", "ModelProviderInfo", "router"]
