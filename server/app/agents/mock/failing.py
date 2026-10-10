"""`mock.failing` (T045): the deterministic failure probe.

§59.6 requires typed errors, and §40's worker contract binds agents to the same
rule, so this mock raises `AgentError` (500, `AGENT_FAILED`) carrying the
caller's message. Tests use it to pin the failure path — the manager's `FAILED`
transition and `AGENT_FAILED` event (§19), the orchestrator's recovery (T049) —
without waiting for a real capability to break.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.agents.base import Agent
from app.core.errors import AgentError

__all__ = ["FailingAgent"]


class FailingAgent(Agent):
    """Always fail with a typed agent error."""

    agent_type = "mock.failing"
    description = "Always fail with a typed agent error; the failure probe."

    async def run(self, context: Mapping[str, Any]) -> Any:
        message = str(context.get("message", "mock.failing: deterministic failure"))
        raise AgentError(str(self.agent_id), message)
