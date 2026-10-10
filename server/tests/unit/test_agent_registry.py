"""Unit tests for the agent type registry (T043).

§7 defines the runtime as "registry, manager, lifecycle"; §40 says the kind of
agent is declared *on the agent*, never hard-coded in the core. These tests pin
the registry's slice of that: it takes classes, not instances (agents carry
lifecycle state), a type name is unique or registration refuses, an unknown
type is a 404 and not a KeyError, only concrete registrable `Agent` subclasses
can enter, and `create` spawns a fresh CREATED instance for the manager (T044).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, cast
from uuid import uuid4

import pytest

from app.agents.base import Agent
from app.agents.registry import AgentRegistry
from app.core.errors import AgentTypeNotFoundError, ConflictError, ErrorCode
from app.database.models import AgentStatus

pytestmark = pytest.mark.unit


class MockPingAgent(Agent):
    agent_type: ClassVar[str] = "mock.ping"
    description: ClassVar[str] = "Return the context unchanged."

    async def run(self, context: Mapping[str, Any]) -> Any:
        return dict(context)


class MockPongAgent(Agent):
    agent_type: ClassVar[str] = "mock.pong"
    description: ClassVar[str] = "Return a fixed reply."

    async def run(self, context: Mapping[str, Any]) -> Any:
        return {"reply": "pong"}


class NotAnAgent:
    pass


@pytest.fixture
def registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.register(MockPingAgent)
    reg.register(MockPongAgent)
    return reg


# --------------------------------------------------------------------------- #
# Register / lookup / list
# --------------------------------------------------------------------------- #
def test_register_then_lookup_returns_the_same_class() -> None:
    registry = AgentRegistry()
    registry.register(MockPingAgent)
    assert registry.lookup("mock.ping") is MockPingAgent


def test_duplicate_types_are_refused(registry: AgentRegistry) -> None:
    with pytest.raises(ConflictError) as excinfo:
        registry.register(MockPingAgent)
    assert excinfo.value.code is ErrorCode.CONFLICT
    assert excinfo.value.http_status == 409
    assert excinfo.value.details["agent_type"] == "mock.ping"


def test_an_unknown_type_is_a_404_not_a_keyerror(registry: AgentRegistry) -> None:
    with pytest.raises(AgentTypeNotFoundError) as excinfo:
        registry.lookup("mock.missing")
    assert excinfo.value.code is ErrorCode.AGENT_NOT_FOUND
    assert excinfo.value.http_status == 404
    assert excinfo.value.agent_type == "mock.missing"
    assert excinfo.value.identifier == "mock.missing"
    assert "mock.missing" in excinfo.value.message


def test_membership(registry: AgentRegistry) -> None:
    assert "mock.ping" in registry
    assert "mock.missing" not in registry
    assert 42 not in registry


def test_list_is_sorted_by_type(registry: AgentRegistry) -> None:
    assert [cls.agent_type for cls in registry.list()] == ["mock.ping", "mock.pong"]


def test_an_empty_registry_lists_nothing() -> None:
    assert AgentRegistry().list() == []


# --------------------------------------------------------------------------- #
# Registration-time guards
# --------------------------------------------------------------------------- #
def test_a_non_agent_class_is_refused() -> None:
    registry = AgentRegistry()
    with pytest.raises(TypeError, match="not an Agent subclass"):
        registry.register(cast(type[Agent], NotAnAgent))


def test_an_instance_is_refused_not_just_a_bad_class() -> None:
    registry = AgentRegistry()
    with pytest.raises(TypeError, match="not an Agent subclass"):
        registry.register(cast(type[Agent], object()))


def test_an_abstract_agent_is_refused() -> None:
    class AbstractInner(Agent):
        agent_type: ClassVar[str] = "mock.inner"
        description: ClassVar[str] = "declared but never implemented"

    registry = AgentRegistry()
    # run is never implemented, so the class is abstract and cannot be spawned.
    with pytest.raises(TypeError, match="abstract"):
        registry.register(cast(type[Agent], AbstractInner))


# --------------------------------------------------------------------------- #
# Spawning (§7's "create agents", at the type level)
# --------------------------------------------------------------------------- #
def test_create_spawns_a_fresh_created_instance(registry: AgentRegistry) -> None:
    agent = registry.create("mock.ping", name="P#1")

    assert isinstance(agent, MockPingAgent)
    assert agent.agent_type == "mock.ping"
    assert agent.name == "P#1"
    assert agent.status is AgentStatus.CREATED
    assert agent.is_active is False
    assert agent.tools == []
    assert agent.permissions == []


def test_create_passes_the_assignment_kwargs(registry: AgentRegistry) -> None:
    task_id = uuid4()
    agent = registry.create(
        "mock.pong",
        name="Q#2",
        task_id=task_id,
        model="mock-model",
        tools=["mock.echo"],
        permissions=["READ_ONLY"],
        memory_namespace="chan",
    )

    assert agent.task_id == task_id
    assert agent.model == "mock-model"
    assert agent.tools == ["mock.echo"]
    assert agent.permissions == ["READ_ONLY"]
    assert agent.memory_namespace == "chan"


def test_create_of_an_unknown_type_is_a_404(registry: AgentRegistry) -> None:
    with pytest.raises(AgentTypeNotFoundError):
        registry.create("mock.missing", name="never")


def test_each_spawn_is_an_independent_instance(registry: AgentRegistry) -> None:
    first = registry.create("mock.ping", name="A")
    second = registry.create("mock.ping", name="B")

    assert first is not second
    assert first.agent_id != second.agent_id
    first.tools.append("later")
    assert second.tools == []
