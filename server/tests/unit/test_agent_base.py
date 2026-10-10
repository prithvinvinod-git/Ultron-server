"""Agent ABC + lifecycle state machine tests (T042).

The ABC is a pure contract, so the tests are too — a minimal concrete agent
defined in this file, no database, no bus, no events. Two walls are pinned
here: the transition table (the whole §6 lifecycle as a legality question,
parametrised so every status is its own case) and the definition-time guards
(a concrete agent that ships undeclared, or with a sync ``run``, fails at
import, not at first call). One test asserts every one of the twelve §6
attributes exists on a fresh instance.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from itertools import pairwise
from typing import Any, ClassVar

import pytest

from app.agents.base import (
    ACTIVE_STATUSES,
    AGENT_TERMINAL_STATUSES,
    LEGAL_AGENT_TRANSITIONS,
    Agent,
    agent_can_transition,
    agent_is_terminal,
    ensure_legal_agent_transition,
)
from app.core.errors import ConflictError
from app.database.models import AgentStatus

pytestmark = pytest.mark.unit

STATUSES = list(AgentStatus)


class EchoAgent(Agent):
    """The minimal honest concrete agent: declares its type and returns."""

    agent_type: ClassVar[str] = "mock.echo"
    description: ClassVar[str] = "a test agent that returns its context"

    async def run(self, context: Mapping[str, Any]) -> Any:
        return dict(context)


class MidAgent(Agent):
    """An abstract intermediate; declarations not yet due."""


# --------------------------------------------------------------------------- #
# State machine: law of the whole table
# --------------------------------------------------------------------------- #
class TestStateMachineTable:
    def test_covers_every_status(self) -> None:
        assert set(LEGAL_AGENT_TRANSITIONS) == set(AgentStatus)

    def test_no_self_transitions(self) -> None:
        for status in STATUSES:
            assert status not in LEGAL_AGENT_TRANSITIONS[status]

    def test_active_and_terminal_partition_the_lifecycle(self) -> None:
        # CREATED is the one status that is neither active nor terminal: it is
        # the state before the lifecycle ran (matches Agent.is_active on the
        # row model, which is why the ABC mirrors it).
        assert ACTIVE_STATUSES | AGENT_TERMINAL_STATUSES | {AgentStatus.CREATED} == set(AgentStatus)
        assert ACTIVE_STATUSES.isdisjoint(AGENT_TERMINAL_STATUSES)

    def test_terminal_statuses_are_absolute(self) -> None:
        for status in AGENT_TERMINAL_STATUSES:
            assert LEGAL_AGENT_TRANSITIONS[status] == frozenset()

    def test_only_terminal_statuses_are_terminal(self) -> None:
        for status in STATUSES:
            assert agent_is_terminal(status) == (status in AGENT_TERMINAL_STATUSES)


# --------------------------------------------------------------------------- #
# State machine: the happy and the sad paths
# --------------------------------------------------------------------------- #
class TestHappyPath:
    def test_the_release_cycle_is_legal(self) -> None:
        path = [
            AgentStatus.CREATED,
            AgentStatus.INITIALIZING,
            AgentStatus.READY,
            AgentStatus.RUNNING,
            AgentStatus.WAITING,
            AgentStatus.RUNNING,
            AgentStatus.VERIFYING,
            AgentStatus.COMPLETED,
        ]
        for current, target in pairwise(path):
            assert agent_can_transition(current, target)

        current = AgentStatus.CREATED
        for target in path[1:]:
            current = ensure_legal_agent_transition(current, target)
        assert current is AgentStatus.COMPLETED

    def test_waiting_doubles_as_pause_and_resume_only_through_running(self) -> None:
        # §7 pause/resume with no PAUSED status in §6: waiting is what a paused
        # agent is doing, and it resumes through RUNNING (it was mid-flight).
        assert agent_can_transition(AgentStatus.RUNNING, AgentStatus.WAITING)
        assert agent_can_transition(AgentStatus.WAITING, AgentStatus.RUNNING)
        assert not agent_can_transition(AgentStatus.WAITING, AgentStatus.READY)
        assert not agent_can_transition(AgentStatus.READY, AgentStatus.WAITING)

    def test_verifying_is_the_last_door(self) -> None:
        assert agent_can_transition(AgentStatus.VERIFYING, AgentStatus.COMPLETED)
        assert agent_can_transition(AgentStatus.VERIFYING, AgentStatus.FAILED)
        # No VERIFYING -> RUNNING: a failed verification is a failed attempt,
        # and retrying is a fresh agent (§66.16 history is not rewritten).
        assert not agent_can_transition(AgentStatus.VERIFYING, AgentStatus.RUNNING)

    def test_there_is_no_retry_through_an_instance(self) -> None:
        assert not agent_can_transition(AgentStatus.FAILED, AgentStatus.READY)
        assert not agent_can_transition(AgentStatus.FAILED, AgentStatus.RUNNING)
        assert not agent_can_transition(AgentStatus.TIMEOUT, AgentStatus.RUNNING)


class TestRefusals:
    def test_illegal_transition_raises_with_legal_targets(self) -> None:
        with pytest.raises(ConflictError) as exc:
            ensure_legal_agent_transition(AgentStatus.READY, AgentStatus.VERIFYING)
        details = exc.value.details or {}
        assert details["current"] == "ready"
        assert details["target"] == "verifying"
        assert details["legal_targets"] == ["cancelled", "running"]

    def test_agent_named_in_the_refusal(self) -> None:
        with pytest.raises(ConflictError) as exc:
            ensure_legal_agent_transition(
                AgentStatus.COMPLETED,
                AgentStatus.RUNNING,
                agent="agent-1",
            )
        assert (exc.value.details or {})["agent"] == "agent-1"

    def test_coerces_plain_string_statuses(self) -> None:
        # A status hydrated from a string column must not KeyError the table.
        assert (
            ensure_legal_agent_transition("created", "initializing")  # type: ignore[arg-type]
            is AgentStatus.INITIALIZING
        )
        with pytest.raises(ConflictError):
            ensure_legal_agent_transition("ready", "verifying")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# The ABC
# --------------------------------------------------------------------------- #
class TestAgentABC:
    def test_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            Agent()  # type: ignore[abstract]

    def test_all_twelve_section_6_attributes_exist(self) -> None:
        agent = EchoAgent(name="Echo #1")

        assert isinstance(agent.agent_id, uuid.UUID)  # identity exists pre-persistence
        assert agent.agent_type == "mock.echo"
        assert agent.name == "Echo #1"
        assert agent.description == "a test agent that returns its context"
        assert agent.status is AgentStatus.CREATED
        assert agent.task_id is None
        assert agent.model is None
        assert agent.tools == []
        assert agent.permissions == []
        assert agent.memory_namespace is None
        assert agent.created_at is not None
        assert agent.updated_at is not None

    def test_starts_live_and_ready_attributes(self) -> None:
        agent = EchoAgent(name="Echo", tools=["mock.echo"], permissions=["READ_ONLY"])
        assert agent.tools == ["mock.echo"]
        assert agent.permissions == ["READ_ONLY"]
        assert agent.is_active is False
        assert agent.is_terminal is False

    def test_copying_default_lists(self) -> None:
        agent = EchoAgent(name="Echo")
        agent.tools.append("later")
        other = EchoAgent(name="Other")
        assert other.tools == []

    def test_transition_drives_status_and_updated_at(self) -> None:
        agent = EchoAgent(name="Echo")
        before = agent.updated_at

        assert agent.transition(AgentStatus.INITIALIZING) is AgentStatus.INITIALIZING
        assert agent.status is AgentStatus.INITIALIZING
        assert agent.updated_at >= before
        assert agent.is_active is True

    def test_illegal_transition_changes_nothing(self) -> None:
        agent = EchoAgent(name="Echo")
        with pytest.raises(ConflictError):
            agent.transition(AgentStatus.RUNNING)
        assert agent.status is AgentStatus.CREATED

    def test_is_active_and_is_terminal_follow_the_machine(self) -> None:
        agent = EchoAgent(name="Echo")
        agent.transition(AgentStatus.INITIALIZING)
        agent.transition(AgentStatus.READY)
        agent.transition(AgentStatus.RUNNING)
        agent.transition(AgentStatus.COMPLETED)
        assert agent.is_active is False
        assert agent.is_terminal is True

    def test_run_is_awaitable_and_returns(self) -> None:
        agent = EchoAgent(name="Echo")

        result = asyncio.run(agent.run({"goal": "x"}))
        assert result == {"goal": "x"}


# --------------------------------------------------------------------------- #
# Definition-time guards (T034 pattern applied to agents)
# --------------------------------------------------------------------------- #
class TestDefinitionTimeGuards:
    def test_undeclared_agent_refused_at_definition(self) -> None:
        with pytest.raises(TypeError, match="must declare agent_type"):

            class NoType(Agent):
                async def run(self, context: Mapping[str, Any]) -> Any:  # pragma: no cover
                    return None

    def test_empty_description_refused(self) -> None:
        with pytest.raises(TypeError, match="description"):

            class NoDescription(Agent):
                agent_type: ClassVar[str] = "mock.none"
                description: ClassVar[str] = "   "

                async def run(self, context: Mapping[str, Any]) -> Any:  # pragma: no cover
                    return None

    def test_sync_run_refused(self) -> None:
        with pytest.raises(TypeError, match="run must be an async function"):

            class SyncRun(Agent):
                agent_type: ClassVar[str] = "mock.sync"
                description: ClassVar[str] = "a sync run that must never register"

                def run(self, context: Mapping[str, Any]) -> Any:  # pragma: no cover
                    return None

    def test_abstract_intermediates_are_exempt(self) -> None:
        # MidAgent up top stays undeclared; that is legal because it is not
        # registrable. Its concrete child inherits the declarations.
        class ConcreteMid(MidAgent):
            agent_type: ClassVar[str] = "mock.mid"
            description: ClassVar[str] = "an abstract family, declared here"

            async def run(self, context: Mapping[str, Any]) -> Any:
                return None

        assert ConcreteMid.agent_type == "mock.mid"
