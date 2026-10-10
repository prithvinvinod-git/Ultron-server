"""`mock.sleep` (T037): the timing and cancellation probe.

Sleeps a schema-bounded number of seconds, then returns. Its declared
timeout (0.5s) is deliberately *shorter* than the schema's maximum (30s):
a schema-valid call past the budget is how tests reach the pipeline's
`OperationTimeoutError` (504, retryable) without a real tool hanging, and
cancelling mid-sleep exercises the paired `TOOL_FAILED` with `CANCELLED`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, ClassVar

from app.database.models import PermissionLevel
from app.tools.base import Reversibility, Tool

__all__ = ["SleepTool"]


class SleepTool(Tool):
    """Sleep for N seconds, then return what it slept."""

    name = "mock.sleep"
    description = "Sleep for N seconds, then return; the timing probe."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"seconds": {"type": "number", "minimum": 0, "maximum": 30}},
        "required": ["seconds"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 0.5
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        seconds = float(arguments["seconds"])
        await asyncio.sleep(seconds)
        return {"slept": seconds}
