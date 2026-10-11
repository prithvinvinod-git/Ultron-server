"""T095 tests: read-only system inspection tools (spec §59.5).

These observe the machine the tests run on, so the assertions check shape and
invariants (counts, monotonic used/total, bounded lists) rather than fixed
values. GPU and Docker are allowed to report ``available: false``.
"""

from __future__ import annotations

import pytest

from app.config.settings import OllamaSettings
from app.database.models import PermissionLevel
from app.tools.system import build_system_tools
from app.tools.system.resources import CpuTool, DiskTool, MemoryTool, NetworkTool, ProcessesTool
from app.tools.system.services import DockerStatusTool, ServicesTool

pytestmark = pytest.mark.unit


def test_all_tools_are_read_only() -> None:
    tools = build_system_tools(OllamaSettings())
    names = {tool.name for tool in tools}
    assert names == {
        "system.cpu",
        "system.memory",
        "system.disk",
        "system.processes",
        "system.network",
        "system.gpu",
        "system.services",
        "system.ollama",
        "system.docker",
    }
    assert all(tool.permission_level is PermissionLevel.READ_ONLY for tool in tools)


async def test_cpu_reports_counts_and_cores() -> None:
    result = await CpuTool().execute({})
    assert result["count_logical"] is None or result["count_logical"] >= 1
    assert isinstance(result["per_core_percent"], list)
    assert 0.0 <= float(result["percent"]) <= 100.0


async def test_memory_used_within_total() -> None:
    result = await MemoryTool().execute({})
    assert result["total_bytes"] > 0
    assert result["used_bytes"] <= result["total_bytes"]
    assert 0.0 <= float(result["percent"]) <= 100.0


async def test_disk_lists_partitions() -> None:
    result = await DiskTool().execute({})
    assert isinstance(result["partitions"], list)
    assert "io" in result


async def test_processes_are_bounded_and_sorted() -> None:
    result = await ProcessesTool().execute({"limit": 5, "sort_by": "memory"})
    assert result["count"] >= len(result["processes"])
    assert len(result["processes"]) <= 5
    memory = [item["memory_bytes"] for item in result["processes"]]
    assert memory == sorted(memory, reverse=True)


async def test_network_reports_interfaces() -> None:
    result = await NetworkTool().execute({})
    assert isinstance(result["addresses"], dict)
    assert isinstance(result["io"], dict)


async def test_gpu_fails_soft_when_unavailable() -> None:
    tool = next(tool for tool in build_system_tools(OllamaSettings()) if tool.name == "system.gpu")
    result = await tool.execute({})
    assert "available" in result


async def test_services_returns_a_list() -> None:
    result = await ServicesTool().execute({"limit": 5})
    assert isinstance(result["services"], list)
    assert len(result["services"]) <= 5


async def test_docker_status_is_soft() -> None:
    result = await DockerStatusTool().execute({})
    assert "available" in result
    if not result["available"]:
        assert isinstance(result.get("reason"), str)
