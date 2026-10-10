"""`mock.echo` (T037): the pipeline's no-op probe.

Returns its arguments unchanged so a test can prove a call travelled the
whole §16 pipeline — schema validation, permission, policy, events — and
came back with exactly what went in. It does no I/O, so its answer can
never lie about the world.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from app.database.models import PermissionLevel
from app.tools.base import Reversibility, Tool

__all__ = ["EchoTool"]


class EchoTool(Tool):
    """Return the validated arguments as a fresh dict."""

    name = "mock.echo"
    description = "Return the arguments unchanged; the pipeline's no-op probe."
    input_schema: ClassVar[dict[str, Any]] = {"type": "object"}
    permission_level = PermissionLevel.READ_ONLY
    timeout = 5.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        return dict(arguments)
