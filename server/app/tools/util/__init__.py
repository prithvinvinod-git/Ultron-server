"""The util tool family (spec §14): small, stateless capabilities.

Nothing here reads the host, the network or memory — ``util.time`` and
``util.calculate`` are pure functions of their arguments, so they are the
safest tools in the registry and the ones an agent may always call.
"""

from __future__ import annotations

from app.tools.base import Tool
from app.tools.util.tools import CalculateTool, TimeTool

__all__ = ["build_util_tools"]


def build_util_tools() -> tuple[Tool, ...]:
    """Construct every stateless utility tool."""
    return (TimeTool(), CalculateTool())
