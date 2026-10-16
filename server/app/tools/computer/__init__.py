"""The computer-node tool family (T143, spec §13/§42/§64).

Structured commands routed to a registered computer node through the gateway
(:mod:`app.agents.computer.gateway`, T141). No tool here hands a model a shell:
each declares a typed schema, a §15 permission level and a capability, and the
gateway ships the command to the node that advertises it.
"""

from __future__ import annotations

from app.tools.computer.tools import (
    ComputerClickTool,
    ComputerCloseApplicationTool,
    ComputerKeysTool,
    ComputerListWindowsTool,
    ComputerOpenApplicationTool,
    ComputerScreenshotTool,
    ComputerTypeTool,
    build_computer_tools,
)

__all__ = [
    "ComputerClickTool",
    "ComputerCloseApplicationTool",
    "ComputerKeysTool",
    "ComputerListWindowsTool",
    "ComputerOpenApplicationTool",
    "ComputerScreenshotTool",
    "ComputerTypeTool",
    "build_computer_tools",
]
