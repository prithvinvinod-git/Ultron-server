"""Model capability registry (T220, §59.8).

Stores per-model metadata and handles runtime availability probing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar

from app.models.base import ModelProvider


class CostClass(StrEnum):
    FREE = "free"
    PAID = "paid"


@dataclass(frozen=True, slots=True)
class ModelRecord:
    model_id: str
    provider: str
    context_length: int
    supports_tools: bool = True
    supports_reasoning: bool = False
    supports_coding: bool = False
    supports_vision: bool = False
    approx_resource_requirement: str = "medium"
    cost_class: CostClass = CostClass.FREE
    _available: bool = field(default=False, repr=False)

    @property
    def available(self) -> bool:
        return self._available

    def mark_available(self, value: bool) -> "ModelRecord":
        return ModelRecord(
            model_id=self.model_id,
            provider=self.provider,
            context_length=self.context_length,
            supports_tools=self.supports_tools,
            supports_reasoning=self.supports_reasoning,
            supports_coding=self.supports_coding,
            supports_vision=self.supports_vision,
            approx_resource_requirement=self.approx_resource_requirement,
            cost_class=self.cost_class,
            _available=value,
        )


class ModelRegistry:
    """The central registry for model capabilities (T220, §59.8)."""

    _registry: ClassVar[dict[str, ModelRecord]] = {}
    _probe_task: asyncio.Task | None = None
    _probe_interval: float = 300.0
    _providers: list[ModelProvider] = []

    @classmethod
    def register(cls, record: ModelRecord) -> None:
        cls._registry[record.model_id] = record

    @classmethod
    def get(cls, model_id: str) -> ModelRecord | None:
        return cls._registry.get(model_id)

    @classmethod
    def list_all(cls) -> list[ModelRecord]:
        return list(cls._registry.values())

    @classmethod
    def clear(cls) -> None:
        cls._registry.clear()
        if cls._probe_task is not None:
            cls._probe_task.cancel()
            cls._probe_task = None

    @classmethod
    def set_providers(cls, providers: list[ModelProvider]) -> None:
        cls._providers = providers

    @classmethod
    def set_probe_interval(cls, seconds: float) -> None:
        cls._probe_interval = max(30.0, float(seconds))
        if cls._probe_task is not None:
            cls._probe_task.cancel()
            cls._probe_task = None

    @classmethod
    async def probe(cls, *, force_refresh: bool = False) -> dict[str, bool]:
        """Query each provider for available models and update the registry."""
        results: dict[str, bool] = {}
        for model_id, record in list(cls._registry.items()):
            provider = next(
                (p for p in cls._providers if p.name == record.provider),
                None,
            )
            if provider is None:
                cls._registry[model_id] = record.mark_available(False)
                results[model_id] = False
                continue
            bare_model = model_id.split(":", 1)[-1] if ":" in model_id else model_id
            try:
                is_avail = await provider.is_available(bare_model)
            except Exception:
                is_avail = False
            cls._registry[model_id] = record.mark_available(is_avail)
            results[model_id] = is_avail
        return results

    @classmethod
    async def _probe_loop(cls) -> None:
        while True:
            await asyncio.sleep(cls._probe_interval)
            try:
                await cls.probe()
            except Exception:
                pass

    @classmethod
    def start_background_probe(cls) -> None:
        if cls._probe_task is not None and not cls._probe_task.done():
            return
        cls._probe_task = asyncio.create_task(cls._probe_loop())

    @classmethod
    def stop_background_probe(cls) -> None:
        if cls._probe_task is not None:
            cls._probe_task.cancel()
            cls._probe_task = None
