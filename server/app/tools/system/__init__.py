"""The system tool family (T095/T232, spec §59.5).

Read-only host inspection: CPU, memory, disk, processes, network, GPU, services,
Ollama and Docker. Nothing here starts, stops or deploys anything — §59.5 keeps
those in the EXPLICIT-AUTHORIZATION tier, which is a later task.
"""

from __future__ import annotations

from app.config.settings import OllamaSettings
from app.tools.base import Tool
from app.tools.system.resources import (
    CpuTool,
    DiskTool,
    GpuTool,
    MemoryTool,
    NetworkTool,
    ProcessesTool,
)
from app.tools.system.services import DockerStatusTool, OllamaStatusTool, ServicesTool

__all__ = ["build_system_tools"]


def build_system_tools(ollama: OllamaSettings) -> tuple[Tool, ...]:
    """Construct every read-only system tool."""
    return (
        CpuTool(),
        MemoryTool(),
        DiskTool(),
        ProcessesTool(),
        NetworkTool(),
        GpuTool(),
        ServicesTool(),
        OllamaStatusTool(ollama),
        DockerStatusTool(),
    )
