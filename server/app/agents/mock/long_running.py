"""`mock.long_running` (T045): the timing and cancellation probe.

Sleeps a bounded number of seconds (``context["seconds"]``, default 0.05) and
then returns what it slept. It holds a real ``asyncio`` sleep, so a caller can
cancel it mid-flight (the manager's `CANCELLED` path, T044) or run two at once
to prove §7's concurrency, without a real capability to hang on.

The budget is bounded outright: a probe that could be asked to sleep for an hour
is a test that hangs, not a test that proves anything.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Final

from app.agents.base import Agent
from app.core.errors import AgentError

__all__ = ["LongRunningAgent"]

#: The largest sleep the probe will accept — a bound, so no test hangs on it.
MAX_SECONDS: Final[float] = 5.0


class LongRunningAgent(Agent):
    """Sleep a bounded number of seconds, then return what it slept."""

    agent_type = "mock.long_running"
    description = "Sleep for N seconds then return; the timing/cancellation probe."

    async def run(self, context: Mapping[str, Any]) -> Any:
        raw = context.get("seconds", 0.05)
        try:
            seconds = float(raw)
        except (TypeError, ValueError) as error:
            raise AgentError(
                str(self.agent_id),
                "'seconds' must be a number",
                details={"seconds": str(raw)[:100]},
                cause=error,
            ) from error
        if not 0 <= seconds <= MAX_SECONDS:
            raise AgentError(
                str(self.agent_id),
                f"'seconds' must be between 0 and {MAX_SECONDS}",
                details={"seconds": seconds},
            )
        await asyncio.sleep(seconds)
        return {"slept": seconds}
