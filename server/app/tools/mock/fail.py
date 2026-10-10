"""`mock.fail` (T037): the deterministic failure probe.

§59.6 requires tools to raise *typed* errors, so this mock obeys the same
contract: it raises `ToolError` (500, `TOOL_EXECUTION_FAILED`) carrying the
caller's message. Tests use it to pin the pipeline's failure path — the
`TOOL_STARTED` → `TOOL_FAILED` pairing, the error's `tool` field, the
durable outcome — without waiting for a real capability to break.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from app.core.errors import ToolError
from app.database.models import PermissionLevel
from app.tools.base import Reversibility, Tool

__all__ = ["FailTool"]


class FailTool(Tool):
    """Always fail with a typed tool error."""

    name = "mock.fail"
    description = "Always fail with a typed tool error; the failure probe."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 5.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        raise ToolError(
            self.name,
            str(arguments.get("message", "mock.fail: deterministic failure")),
        )
