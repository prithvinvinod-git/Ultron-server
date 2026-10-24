"""Unit tests for the model capability registry (T220, §59.8)."""

from __future__ import annotations

import pytest

from app.models.registry import CostClass, ModelRecord, ModelRegistry

pytestmark = pytest.mark.unit


def test_registry_starts_empty() -> None:
    # Clear any test pollution
    ModelRegistry._registry.clear()
    assert ModelRegistry.list_all() == []


def test_register_and_get() -> None:
    ModelRegistry._registry.clear()
    record = ModelRecord(
        model_id="openrouter:nvidia/nemotron-3.5-lightning:free",
        provider="openrouter",
        context_length=4096,
        supports_tools=True,
        supports_reasoning=True,
        supports_coding=True,
        cost_class=CostClass.FREE,
    )
    ModelRegistry.register(record)
    assert ModelRegistry.get("openrouter:nvidia/nemotron-3.5-lightning:free") == record


def test_list_all_returns_registered() -> None:
    ModelRegistry._registry.clear()
    records = [
        ModelRecord(model_id="openrouter:model1", provider="openrouter", context_length=4096),
        ModelRecord(model_id="ollama:llama3", provider="ollama", context_length=8192),
    ]
    for r in records:
        ModelRegistry.register(r)
    assert set(ModelRegistry.list_all()) == set(records)


def test_cost_class_enum() -> None:
    assert CostClass.FREE == "free"
    assert CostClass.PAID == "paid"