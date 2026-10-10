"""`mock.write_state` (T037): the controlled-write probe.

Level 2 (`MODIFY_PROJECT`) on purpose — it is how tests reach
`ConfirmationRequiredError` (409) and the confirmed path with a
*registered* tool rather than a local double, using the same
`PermissionManager` the real tools will face.

The state is one class-level dict shared by every instance (tests clear it
in a fixture), because the point of the tool is that its effect is visible
*outside* the pipeline: §17's `verify`, §66.16's history, and plain test
assertions all read the same place. The output carries a snapshot of the
whole state so a single call shows what the write changed.

Declared `reversible` honestly: it stores an in-memory value, and storing
the previous value back restores it. A mock that claimed irreversibility
would be lying about the only thing it does — and §66.17 confirmations are
too important to practise on a fiction.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from app.database.models import PermissionLevel
from app.tools.base import Reversibility, Tool

__all__ = ["WriteStateTool"]


class WriteStateTool(Tool):
    """Write one key into shared mock state and report the new state."""

    name = "mock.write_state"
    description = "Write a key into shared mock state; the controlled-write probe."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"key": {"type": "string"}, "value": {}},
        "required": ["key", "value"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.MODIFY_PROJECT
    timeout = 5.0
    reversibility = Reversibility.REVERSIBLE

    #: Shared by every instance on purpose — see the module docstring.
    state: ClassVar[dict[str, Any]] = {}

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        key = str(arguments["key"])
        value = arguments["value"]
        self.state[key] = value
        return {"key": key, "value": value, "state": dict(self.state)}
