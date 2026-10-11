"""Read-only host-resource tools (T095/T232, spec §59.5).

Every tool here observes the machine and changes nothing — §59.5 keeps restart,
stop and deploy in the EXPLICIT-AUTHORIZATION tier and out of this module. The
blocking ``psutil`` reads run off the event loop via ``asyncio.to_thread`` and
each tool declares a short timeout (§59.6).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Any, ClassVar

import psutil

from app.database.models import PermissionLevel
from app.tools.arguments import as_int
from app.tools.base import Reversibility, Tool
from app.tools.system._exec import find_program, run_command


class CpuTool(Tool):
    """CPU utilisation, per-core load and load average."""

    name = "system.cpu"
    description = "Report CPU counts, utilisation, per-core load and load average."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "interval": {"type": "number", "minimum": 0.0, "maximum": 5.0, "default": 0.1}
        },
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 15.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        interval = float(arguments.get("interval", 0.1) or 0.0)
        return await asyncio.to_thread(_cpu_snapshot, min(max(interval, 0.0), 5.0))


class MemoryTool(Tool):
    """Physical and swap memory usage."""

    name = "system.memory"
    description = "Report physical and swap memory usage."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 15.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        return await asyncio.to_thread(_memory_snapshot)


class DiskTool(Tool):
    """Disk partitions, usage and I/O counters."""

    name = "system.disk"
    description = "Report mounted filesystems, their usage and disk I/O counters."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 15.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        return await asyncio.to_thread(_disk_snapshot)


class ProcessesTool(Tool):
    """The busiest running processes."""

    name = "system.processes"
    description = "List the busiest running processes by CPU or memory."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
            "sort_by": {"type": "string", "enum": ["cpu", "memory"], "default": "cpu"},
        },
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 15.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        limit = as_int(arguments.get("limit"), "limit", default=20, minimum=1)
        sort_by = str(arguments.get("sort_by") or "cpu")
        if sort_by not in {"cpu", "memory"}:
            sort_by = "cpu"
        return await asyncio.to_thread(_process_snapshot, min(limit, 200), sort_by)


class NetworkTool(Tool):
    """Network interface addresses and I/O counters."""

    name = "system.network"
    description = "Report network interface addresses and per-interface I/O counters."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 15.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        return await asyncio.to_thread(_network_snapshot)


class GpuTool(Tool):
    """GPU status via ``nvidia-smi`` when present."""

    name = "system.gpu"
    description = "Report GPU utilisation and memory via nvidia-smi when available."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 20.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        program = find_program("nvidia-smi")
        if program is None:
            return {"available": False, "reason": "nvidia-smi not found"}
        code, out, err = await run_command(
            [
                program,
                "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            timeout_seconds=15.0,
        )
        if code != 0:
            return {"available": False, "reason": err.strip() or "nvidia-smi failed"}
        return {"available": True, "gpus": _parse_gpu_csv(out)}


def _cpu_snapshot(interval: float) -> dict[str, Any]:
    load = psutil.getloadavg() if hasattr(psutil, "getloadavg") else ()
    return {
        "count_logical": psutil.cpu_count(logical=True),
        "count_physical": psutil.cpu_count(logical=False),
        "percent": psutil.cpu_percent(interval=interval),
        "per_core_percent": psutil.cpu_percent(interval=None, percpu=True),
        "load_average": [round(value, 2) for value in load],
    }


def _memory_snapshot() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return {
        "total_bytes": vm.total,
        "available_bytes": vm.available,
        "used_bytes": vm.used,
        "percent": vm.percent,
        "swap_total_bytes": swap.total,
        "swap_used_bytes": swap.used,
        "swap_percent": swap.percent,
    }


def _disk_snapshot() -> dict[str, Any]:
    partitions: list[dict[str, Any]] = []
    for part in psutil.disk_partitions(all=False):
        entry: dict[str, Any] = {
            "device": part.device,
            "mountpoint": part.mountpoint,
            "fstype": part.fstype,
        }
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            entry["error"] = "unavailable"
        else:
            entry.update(
                total_bytes=usage.total,
                used_bytes=usage.used,
                free_bytes=usage.free,
                percent=usage.percent,
            )
        partitions.append(entry)
    io = psutil.disk_io_counters()
    io_dict = (
        {
            "read_bytes": io.read_bytes,
            "write_bytes": io.write_bytes,
            "read_count": io.read_count,
            "write_count": io.write_count,
        }
        if io is not None
        else {}
    )
    return {"partitions": partitions, "io": io_dict}


def _process_snapshot(limit: int, sort_by: str) -> dict[str, Any]:
    processes: list[dict[str, Any]] = []
    for proc in psutil.process_iter(
        ["pid", "name", "username", "memory_info", "cpu_percent", "status"]
    ):
        try:
            info = proc.info
            memory = info.get("memory_info")
            processes.append(
                {
                    "pid": info.get("pid"),
                    "name": info.get("name"),
                    "username": info.get("username"),
                    "status": info.get("status"),
                    "memory_bytes": getattr(memory, "rss", 0),
                    "cpu_percent": info.get("cpu_percent") or 0.0,
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    key = "memory_bytes" if sort_by == "memory" else "cpu_percent"
    processes.sort(key=lambda item: item.get(key) or 0, reverse=True)
    return {"count": len(processes), "processes": processes[:limit]}


def _network_snapshot() -> dict[str, Any]:
    addresses: dict[str, list[dict[str, str]]] = {}
    for name, addrs in psutil.net_if_addrs().items():
        entries: list[dict[str, str]] = []
        for addr in addrs:
            entries.append(
                {
                    "family": str(addr.family),
                    "address": addr.address,
                    "netmask": addr.netmask or "",
                }
            )
        addresses[name] = entries
    counters: dict[str, dict[str, int]] = {}
    for name, io in psutil.net_io_counters(pernic=True).items():
        counters[name] = {
            "bytes_sent": io.bytes_sent,
            "bytes_recv": io.bytes_recv,
            "packets_sent": io.packets_sent,
            "packets_recv": io.packets_recv,
            "errors_in": io.errin,
            "errors_out": io.errout,
        }
    return {
        "addresses": addresses,
        "io": counters,
        "hostname": os.uname().nodename if hasattr(os, "uname") else "",
    }


def _parse_gpu_csv(output: str) -> list[dict[str, Any]]:
    gpus: list[dict[str, Any]] = []
    for line in output.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) < 6:
            continue
        gpus.append(
            {
                "index": _to_int(fields[0]),
                "name": fields[1],
                "utilization_percent": _to_int(fields[2]),
                "memory_used_mb": _to_int(fields[3]),
                "memory_total_mb": _to_int(fields[4]),
                "temperature_c": _to_int(fields[5]),
            }
        )
    return gpus


def _to_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None
