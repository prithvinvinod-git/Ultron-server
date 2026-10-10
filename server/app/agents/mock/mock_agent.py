"""`mock.agent` (T045): the lifecycle's no-op worker.

Returns its context unchanged so a test can prove a worker ran on exactly what
it was handed — spawned by `AgentRegistry.create`, walked through the manager's
lifecycle (T044), dispatched by the orchestrator (T049) — and nothing else. It
performs no I/O, so its result can never lie about the world.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.agents.base import Agent

__all__ = ["MockAgent"]


class MockAgent(Agent):
    """Return a fresh copy of the context it was given."""

    agent_type = "mock.agent"
    description = "Return the context unchanged; the no-op worker probe."

    async def run(self, context: Mapping[str, Any]) -> Any:
        return dict(context)
