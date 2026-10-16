"""The ``computer.*`` tool set (T143, spec §13/§42/§64).

The model-facing surface over the computer-node gateway (T141). Every tool here
is a **structured command** (§64.6.2: "None of them is a general shell handed to
a model"): the arguments are a typed schema, and the gateway ships them to the
node, which executes them with its own Windows adapters. The server never
touches a Windows API, never imports a Windows-Use crate and never runs a shell
— which is exactly §51's Phase 8 gate ("Core has zero direct Windows-Use
dependency; no raw shell from server").

Each tool is a thin declaration plus one dispatch:

- **It declares a capability** (``windows_ui``, ``screenshots``,
  ``windows_apps``) and a §15 permission level. The pipeline (§16) decides
  whether the call is allowed; the node re-checks (§64.6.3 Path B). The tool
  itself enforces nothing beyond the schema — §66.7's "tools declare, the
  pipeline decides".
- **It resolves a target node and dispatches.** An explicit ``node`` argument
  names a node the caller wants; without one, the gateway picks the online node
  advertising the capability, deterministically (§64.11: the choice must be
  logged so "which machine did that run on" is answerable). An offline node
  becomes a typed ``NODE_UNAVAILABLE`` from :meth:`NodeGateway.dispatch` — never
  a hang, never a silent re-route (§64.11).

The high-sensitivity reads — a screenshot is the whole screen — are marked
``audit=True`` (§64.6.2: "screenshot/clipboard high-sensitivity + audited"), so
the pipeline records them beyond the permission check every call already gets.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from app.agents.computer.gateway import NodeGateway
from app.core.errors import InvalidInputError, NodeError
from app.database.models import PermissionLevel, VerificationOutcome
from app.observability.logging import get_logger
from app.tools.arguments import as_int, as_str
from app.tools.base import Reversibility, Tool

__all__ = ["build_computer_tools"]

_LOGGER = get_logger(__name__)

#: The optional target override every computer tool accepts. §64.11: a request
#: with a ``node`` is checked against that node's capabilities; without one the
#: router resolves by capability discovery. Named ``node`` to match the spec's
#: tool request shape (``{ "tool": ..., "node": "windows", ... }``).
_NODE_PROPERTY: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "description": "Target node id or kind (e.g. 'windows'). Omit to auto-resolve.",
}


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"node": _NODE_PROPERTY, **properties},
        "required": required or [],
        "additionalProperties": False,
    }


class _ComputerTool(Tool):
    """Family base: target resolution and dispatch over the node gateway.

    ``timeout``, ``node_scope`` and the dispatch plumbing are uniform across the
    family and declared here once; ``permission_level``, ``reversibility``,
    ``capability`` and the schema differ per tool, so each concrete tool declares
    its own. The class stays abstract (no ``execute``), so
    :meth:`Tool.__init_subclass__` lets the family base declare on the
    subclasses' behalf.
    """

    timeout = 30.0
    node_scope = "windows"

    #: The capability family this tool needs the node to advertise (§64.10).
    capability: ClassVar[str]

    def __init__(self, gateway: NodeGateway) -> None:
        self._gateway = gateway

    def _target(self, arguments: Mapping[str, Any]) -> str:
        """Resolve which node executes this call (§64.11), or raise typed.

        ``node`` names a node id *or* a node kind; a kind with exactly one online
        match resolves to it. Anything else — an unknown node, a node without
        the capability, no online node at all — is ``NODE_UNAVAILABLE`` with the
        capability named.
        """
        requested = arguments.get("node")
        if isinstance(requested, str) and requested:
            node = self._gateway.maybe(requested)
            if node is None:
                # A kind rather than an id: resolve among nodes of that kind.
                for candidate in self._gateway.list_nodes():
                    if candidate.node_type.value == requested and (
                        self.capability in candidate.capabilities
                    ):
                        node = candidate
                        break
            if node is None:
                raise NodeError(
                    requested, reason="no such node", capability=self.capability
                )
            if self.capability not in node.capabilities:
                raise NodeError(
                    requested,
                    reason="node does not advertise the capability",
                    capability=self.capability,
                )
            return node.node_id

        matches = self._gateway.find_by_capability(self.capability)
        if not matches:
            raise NodeError(
                "windows",
                reason="no online node advertises the capability",
                capability=self.capability,
            )
        chosen = matches[0]
        _LOGGER.info(
            "computer tool target resolved",
            extra={
                "event": "computer.target_resolved",
                "tool": self.name,
                "node_id": chosen.node_id,
                "capability": self.capability,
                "candidates": [node.node_id for node in matches],
            },
        )
        return chosen.node_id

    async def _dispatch(self, arguments: Mapping[str, Any]) -> Any:
        """Resolve the target and await the node's correlated result."""
        node_id = self._target(arguments)
        payload = {key: value for key, value in arguments.items() if key != "node"}
        return await self._gateway.dispatch(
            node_id,
            self.name,
            payload,
            capability=self.capability,
            limit=self.timeout,
        )


class ComputerListWindowsTool(_ComputerTool):
    """List the windows the node currently has open."""

    name = "computer.list_windows"
    description = (
        "List the open windows on the target computer: each window's id, "
        "application, title and whether it is focused."
    )
    input_schema: ClassVar[dict[str, Any]] = _schema({})
    permission_level = PermissionLevel.READ_ONLY
    reversibility = Reversibility.REVERSIBLE
    capability = "windows_ui"

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        return await self._dispatch(arguments)

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        windows = result.get("windows") if isinstance(result, Mapping) else None
        if isinstance(windows, list):
            return VerificationOutcome.SUCCESS
        return VerificationOutcome.UNVERIFIED


class ComputerScreenshotTool(_ComputerTool):
    """Capture a screenshot of a window or the whole screen."""

    name = "computer.screenshot"
    description = (
        "Capture a PNG screenshot on the target computer, of one window by id "
        "or of the whole primary screen, and return the file path."
    )
    input_schema: ClassVar[dict[str, Any]] = _schema(
        {
            "window_id": {
                "type": "string",
                "minLength": 1,
                "description": "Window to capture; omit for the whole screen.",
            },
            "name": {"type": "string", "minLength": 1, "maxLength": 80},
        }
    )
    permission_level = PermissionLevel.READ_ONLY
    reversibility = Reversibility.REVERSIBLE
    capability = "screenshots"
    #: A screenshot reads the whole screen; §64.6.2 marks it high-sensitivity.
    audit = True

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        return await self._dispatch(arguments)

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        path = result.get("path") if isinstance(result, Mapping) else None
        if isinstance(path, str) and path:
            return VerificationOutcome.SUCCESS
        return VerificationOutcome.FAILED


class ComputerClickTool(_ComputerTool):
    """Click at a screen coordinate, optionally within a window."""

    name = "computer.click"
    description = (
        "Click at pixel coordinates on the target computer, optionally scoped "
        "to a window by id. Uses the pointer; a UI-automation click is chosen "
        "by the fallback chain when available."
    )
    input_schema: ClassVar[dict[str, Any]] = _schema(
        {
            "x": {"type": "integer", "minimum": 0},
            "y": {"type": "integer", "minimum": 0},
            "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
            "clicks": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1},
            "window_id": {"type": "string", "minLength": 1},
        },
        required=["x", "y"],
    )
    permission_level = PermissionLevel.SAFE_ACTIONS
    reversibility = Reversibility.PARTIALLY_REVERSIBLE
    capability = "windows_ui"

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        as_int(arguments.get("x"), "x", minimum=0)
        as_int(arguments.get("y"), "y", minimum=0)
        return await self._dispatch(arguments)


class ComputerTypeTool(_ComputerTool):
    """Type text as keyboard input on the node."""

    name = "computer.type"
    description = (
        "Type a string as keyboard input on the target computer, optionally "
        "into a window by id. Use computer.keys for shortcuts and special keys."
    )
    input_schema: ClassVar[dict[str, Any]] = _schema(
        {
            "text": {"type": "string", "maxLength": 10_000},
            "window_id": {"type": "string", "minLength": 1},
            "interval_ms": {"type": "integer", "minimum": 0, "maximum": 5_000},
        },
        required=["text"],
    )
    permission_level = PermissionLevel.SAFE_ACTIONS
    reversibility = Reversibility.PARTIALLY_REVERSIBLE
    capability = "windows_ui"

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        as_str(arguments.get("text"), "text", allow_empty=True)
        return await self._dispatch(arguments)


class ComputerKeysTool(_ComputerTool):
    """Press a key or key combination (a shortcut)."""

    name = "computer.keys"
    description = (
        "Press a key combination on the target computer, e.g. 'ctrl+s' or "
        "'alt+f4', optionally scoped to a window by id."
    )
    input_schema: ClassVar[dict[str, Any]] = _schema(
        {
            "keys": {
                "oneOf": [
                    {"type": "string", "minLength": 1},
                    {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1},
                ],
                "description": "A combination string ('ctrl+shift+s') or a list of keys.",
            },
            "window_id": {"type": "string", "minLength": 1},
        },
        required=["keys"],
    )
    permission_level = PermissionLevel.SAFE_ACTIONS
    reversibility = Reversibility.PARTIALLY_REVERSIBLE
    capability = "windows_ui"

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        keys = arguments.get("keys")
        if isinstance(keys, str):
            if not keys:
                raise InvalidInputError("keys must not be empty")
        elif isinstance(keys, list):
            if not keys or not all(isinstance(item, str) and item for item in keys):
                raise InvalidInputError("keys list must contain non-empty strings")
        else:
            raise InvalidInputError("keys must be a string or a list of strings")
        return await self._dispatch(arguments)


class ComputerOpenApplicationTool(_ComputerTool):
    """Launch an application on the node."""

    name = "computer.open_application"
    description = "Launch an application by name on the target computer."
    input_schema: ClassVar[dict[str, Any]] = _schema(
        {
            "application": {"type": "string", "minLength": 1, "maxLength": 256},
            "arguments": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional launch arguments (not a shell line).",
            },
        },
        required=["application"],
    )
    permission_level = PermissionLevel.EXECUTE_PROGRAMS
    reversibility = Reversibility.REVERSIBLE
    capability = "windows_apps"

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        as_str(arguments.get("application"), "application")
        return await self._dispatch(arguments)


class ComputerCloseApplicationTool(_ComputerTool):
    """Close an application or window on the node."""

    name = "computer.close_application"
    description = (
        "Close an application on the target computer, by application name or "
        "by window id."
    )
    input_schema: ClassVar[dict[str, Any]] = _schema(
        {
            "application": {"type": "string", "minLength": 1, "maxLength": 256},
            "window_id": {"type": "string", "minLength": 1},
        }
    )
    permission_level = PermissionLevel.SAFE_ACTIONS
    reversibility = Reversibility.PARTIALLY_REVERSIBLE
    capability = "windows_apps"

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        application = arguments.get("application")
        window_id = arguments.get("window_id")
        if not application and not window_id:
            raise InvalidInputError("one of 'application' or 'window_id' is required")
        if application is not None:
            as_str(application, "application")
        return await self._dispatch(arguments)


def build_computer_tools(gateway: NodeGateway) -> tuple[Tool, ...]:
    """Construct the ``computer.*`` tools bound to ``gateway``.

    Included in the inventory only when the computer-node subsystem is enabled
    (``COMPUTER_NODES_ENABLED``), so a deployment that does not host nodes
    exposes no node tools at all rather than tools that always fail.
    """
    return (
        ComputerListWindowsTool(gateway),
        ComputerScreenshotTool(gateway),
        ComputerClickTool(gateway),
        ComputerTypeTool(gateway),
        ComputerKeysTool(gateway),
        ComputerOpenApplicationTool(gateway),
        ComputerCloseApplicationTool(gateway),
    )
