"""Unit tests for the deterministic mock agents (T045).

Three probes that let later suites exercise §7's agent runtime without a real
capability: a no-op worker that returns its context, a bounded sleeper that can
be cancelled mid-flight, and a deterministic failure. They register through the
**real** `AgentRegistry` (T043) — no local doubles — so their `agent_type`
declarations face the same definition-time guards (`app/agents/base.py`) every
real agent will, and `create` spawns each in `CREATED` exactly where §6's
lifecycle begins.
"""

from __future__ import annotations

import asyncio
from time import perf_counter

import pytest

from app.agents.base import Agent
from app.agents.mock.failing import FailingAgent
from app.agents.mock.long_running import MAX_SECONDS, LongRunningAgent
from app.agents.mock.mock_agent import MockAgent
from app.agents.registry import AgentRegistry
from app.core.errors import AgentError, ErrorCode
from app.database.models import AgentStatus

pytestmark = pytest.mark.unit

MOCK_TYPES = ("mock.agent", "mock.failing", "mock.long_running")


def make_registry() -> AgentRegistry:
    registry = AgentRegistry()
    for agent_cls in (MockAgent, LongRunningAgent, FailingAgent):
        registry.register(agent_cls)
    return registry


# --------------------------------------------------------------------------- #
# Declarations — the registry records each probe's job
# --------------------------------------------------------------------------- #
def test_all_three_register_under_the_mock_namespace() -> None:
    registry = make_registry()

    assert [agent_cls.agent_type for agent_cls in registry.list()] == list(MOCK_TYPES)


def test_each_spawns_created_with_its_own_declaration() -> None:
    registry = make_registry()

    for agent_type in MOCK_TYPES:
        agent = registry.create(agent_type, name=agent_type)
        assert isinstance(agent, Agent)
        assert agent.agent_type == agent_type
        assert agent.description
        assert agent.status is AgentStatus.CREATED


# --------------------------------------------------------------------------- #
# mock.agent
# --------------------------------------------------------------------------- #
async def test_mock_agent_returns_the_context_it_was_given() -> None:
    out = await MockAgent().run({"task": "ship it", "n": 3})

    assert out == {"task": "ship it", "n": 3}


async def test_mock_agent_returns_a_fresh_dict() -> None:
    data = {"a": 1}

    out = await MockAgent().run(data)
    out["a"] = 99

    assert data == {"a": 1}


# --------------------------------------------------------------------------- #
# mock.long_running
# --------------------------------------------------------------------------- #
async def test_long_running_returns_after_its_delay() -> None:
    started = perf_counter()

    out = await LongRunningAgent().run({"seconds": 0.05})

    assert out == {"slept": 0.05}
    assert perf_counter() - started >= 0.04


async def test_long_running_defaults_to_a_short_budget() -> None:
    out = await LongRunningAgent().run({})

    assert out["slept"] == 0.05


async def test_long_running_refuses_a_non_number() -> None:
    with pytest.raises(AgentError) as excinfo:
        await LongRunningAgent().run({"seconds": "soon"})

    assert excinfo.value.code is ErrorCode.AGENT_FAILED
    assert excinfo.value.http_status == 500
    assert excinfo.value.details["seconds"] == "soon"


async def test_long_running_refuses_an_unbounded_sleep() -> None:
    with pytest.raises(AgentError) as excinfo:
        await LongRunningAgent().run({"seconds": MAX_SECONDS + 1})

    assert excinfo.value.details["seconds"] == MAX_SECONDS + 1


async def test_long_running_is_cancellable_mid_flight() -> None:
    task = asyncio.create_task(LongRunningAgent().run({"seconds": MAX_SECONDS}))
    await asyncio.sleep(0.01)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --------------------------------------------------------------------------- #
# mock.failing
# --------------------------------------------------------------------------- #
async def test_failing_raises_a_typed_error_with_the_message() -> None:
    with pytest.raises(AgentError) as excinfo:
        await FailingAgent().run({"message": "nope"})

    assert excinfo.value.code is ErrorCode.AGENT_FAILED
    assert excinfo.value.http_status == 500
    assert excinfo.value.message == "nope"


async def test_failing_defaults_its_message() -> None:
    with pytest.raises(AgentError) as excinfo:
        await FailingAgent().run({})

    assert "deterministic failure" in excinfo.value.message


async def test_failing_names_the_agent_that_failed() -> None:
    agent = FailingAgent()

    with pytest.raises(AgentError) as excinfo:
        await agent.run({})

    assert excinfo.value.details["agent_id"] == str(agent.agent_id)
