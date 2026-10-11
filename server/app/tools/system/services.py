"""Service-status tools (T095/T232, spec §59.5).

``system.services``, ``system.ollama`` and ``system.docker`` are read-only
inspections: they report what is running, reachable and loaded without starting,
stopping or deploying anything. Every call is bounded and fails soft — an
unavailable probe returns ``{"available": false}`` rather than aborting, because
the point of a status tool is to say so.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, ClassVar

import psutil

from app.config.settings import OllamaSettings
from app.core.errors import OperationTimeoutError
from app.database.models import PermissionLevel
from app.tools.arguments import as_int
from app.tools.base import Reversibility, Tool
from app.tools.system._exec import find_program, run_command

_MAX_SERVICES = 200


class ServicesTool(Tool):
    """System services and their state."""

    name = "system.services"
    description = "List system services and their current state."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}},
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 20.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        limit = min(as_int(arguments.get("limit"), "limit", default=100, minimum=1), 500)
        if hasattr(psutil, "win_service_iter"):
            return await asyncio.to_thread(_windows_services, limit)
        return await _posix_services(limit)


class OllamaStatusTool(Tool):
    """Ollama reachability and installed models."""

    name = "system.ollama"
    description = "Check whether the configured Ollama host is reachable and list its models."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 15.0
    reversibility = Reversibility.REVERSIBLE

    def __init__(self, settings: OllamaSettings) -> None:
        self._settings = settings

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        import httpx

        url = f"{self._settings.url}/api/tags"
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.connect_timeout,
                headers=self._settings.auth_headers(),
            ) as client:
                response = await client.get(url)
        except httpx.HTTPError:
            return {"available": False, "url": self._settings.url, "models": []}
        if response.status_code != 200:
            return {
                "available": False,
                "url": self._settings.url,
                "status_code": response.status_code,
                "models": [],
            }
        payload = response.json()
        models = [
            str(item.get("name", ""))
            for item in payload.get("models", [])
            if isinstance(item, dict)
        ]
        return {"available": True, "url": self._settings.url, "models": models}


class DockerStatusTool(Tool):
    """Docker daemon reachability and container listing."""

    name = "system.docker"
    description = "Report Docker daemon availability and the containers it is running."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"all": {"type": "boolean", "default": True}},
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 20.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        program = find_program("docker")
        if program is None:
            return {"available": False, "reason": "docker not found", "containers": []}
        argv = [
            program,
            "ps",
            "--no-trunc",
            "--format",
            "{{.ID}}\t{{.Image}}\t{{.Status}}\t{{.Names}}",
        ]
        if bool(arguments.get("all", True)):
            argv.insert(2, "--all")
        code, out, err = await run_command(argv, timeout_seconds=15.0)
        if code != 0:
            return {
                "available": False,
                "reason": err.strip() or "docker ps failed",
                "containers": [],
            }
        return {"available": True, "containers": _parse_container_lines(out)}


def _windows_services(limit: int) -> dict[str, Any]:
    services: list[dict[str, Any]] = []
    for service in psutil.win_service_iter():
        if len(services) >= min(limit, _MAX_SERVICES):
            break
        try:
            info = service.as_dict()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        services.append(
            {
                "name": info.get("name"),
                "display_name": info.get("display_name"),
                "status": info.get("status"),
                "start_type": info.get("start_type"),
            }
        )
    return {"count": len(services), "services": services}


async def _posix_services(limit: int) -> dict[str, Any]:
    program = find_program("systemctl")
    if program is None:
        return {"count": 0, "services": [], "reason": "systemctl not found"}
    try:
        code, out, err = await run_command(
            [program, "list-units", "--type=service", "--no-pager", "--plain", "--no-legend"],
            timeout_seconds=15.0,
        )
    except OperationTimeoutError:
        raise
    if code != 0:
        return {"count": 0, "services": [], "reason": err.strip() or "systemctl failed"}
    services: list[dict[str, str]] = []
    for line in out.splitlines()[: min(limit, _MAX_SERVICES)]:
        fields = line.split(None, 4)
        if len(fields) >= 4:
            services.append(
                {"unit": fields[0], "load": fields[1], "active": fields[2], "sub": fields[3]}
            )
    return {"count": len(services), "services": services}


def _parse_container_lines(output: str) -> list[dict[str, str]]:
    containers: list[dict[str, str]] = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) < 4:
            continue
        containers.append(
            {"id": fields[0], "image": fields[1], "status": fields[2], "names": fields[3]}
        )
    return containers


__all__ = ["DockerStatusTool", "OllamaStatusTool", "ServicesTool"]
