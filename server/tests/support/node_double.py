"""The computer-node test double (T146, spec §13/§64).

A node, on the wire, is just a peer that speaks the T140 protocol: it sends a
``register``, heartbeats, and answers ``command`` with a correlated ``result``.
This double enacts exactly that, in **plain JSON dicts**, so the suites exercise
the same boundary the real node crosses — the server's ``NodeResult`` model is
built from the double's JSON with ``model_validate``, which is precisely what
``app/api/routes/nodes.py`` does with a frame off the socket.

What it deliberately is *not*: a second implementation of the gateway. It holds
no dispatch table of its own; a ``command`` is answered by the ``handlers`` the
test supplied, and an unhandled tool is answered the way a real node answers one
it cannot run — ``status: "error"`` with ``error_code: "CAPABILITY_NOT_IMPLEMENTED"``.

Two escape hatches keep failure paths testable: ``auto_respond=False`` lets a
test withhold a result (to reach the gateway's timeout), and ``connection_error``
makes ``FakeTransport.send`` raise (to reach its connection-drop path).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.agents.computer.protocol import NodeResult

#: The capability families a Windows node advertises (spec §64.6.2).
WINDOWS_CAPABILITIES: tuple[str, ...] = (
    "filesystem",
    "powershell",
    "windows_apps",
    "windows_ui",
    "browser",
    "processes",
    "screenshots",
)

#: The error code a node returns for a tool it does not implement (§13: a
#: structured refusal, never an attempt to run something it cannot).
CAPABILITY_NOT_IMPLEMENTED = "CAPABILITY_NOT_IMPLEMENTED"


class FakeTransport:
    """A :class:`~app.agents.computer.gateway.NodeTransport` with no socket.

    ``send`` records the server's message and hands it to the node, so a
    dispatch resolves synchronously inside the gateway's own ``await send`` —
    no concurrency, no sleeps, no flakiness.
    """

    def __init__(self, node: FakeNode) -> None:
        self._node = node
        #: Every server→node message, in order.
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        #: When set, ``send`` raises this instead of delivering — the
        #: connection-dropped path.
        self.connection_error: type[Exception] | None = None

    async def send(self, message: Mapping[str, Any]) -> None:
        if self.connection_error is not None:
            raise self.connection_error("the node connection dropped")
        payload = dict(message)
        self.sent.append(payload)
        await self._node.on_server_message(payload)

    async def close(self) -> None:
        self.closed = True


class FakeNode:
    """A client-side node the gateway can register and dispatch to."""

    def __init__(
        self,
        node_id: str = "windows-node-1",
        *,
        node_type: str = "windows",
        name: str = "Test Windows Node",
        capabilities: Sequence[str] = WINDOWS_CAPABILITIES,
        version: str = "1.0.0",
        client_of: str | None = None,
        credential: str = "node-credential",
        handlers: Mapping[str, Callable[[dict[str, Any]], Any]] | None = None,
        auto_respond: bool = True,
    ) -> None:
        self.node_id = node_id
        self.node_type = node_type
        self.name = name
        self.capabilities = list(capabilities)
        self.version = version
        self.client_of = client_of
        self.credential = credential
        self.handlers: dict[str, Callable[[dict[str, Any]], Any]] = dict(handlers or {})
        self.auto_respond = auto_respond
        self.transport = FakeTransport(self)
        #: Every node→server message the node built, in order.
        self.emitted: list[dict[str, Any]] = []
        #: Results built but withheld when ``auto_respond`` is False.
        self.withheld: list[dict[str, Any]] = []
        self.gateway: Any | None = None

    # -------------------------------------------------------------- #
    # Outbound messages the node sends (§64.14)
    # -------------------------------------------------------------- #
    def bind(self, gateway: Any) -> FakeNode:
        """Attach the gateway this node delivers results to."""
        self.gateway = gateway
        return self

    def registration(self, *, credential: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "register",
            "node_id": self.node_id,
            "node_type": self.node_type,
            "name": self.name,
            "capabilities": list(self.capabilities),
            "version": self.version,
            "client_of": self.client_of,
            "credential": self.credential if credential is None else credential,
        }
        self.emitted.append(payload)
        return payload

    def heartbeat(
        self,
        *,
        status: str | None = None,
        capabilities: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": "heartbeat", "node_id": self.node_id}
        if status is not None:
            payload["status"] = status
        if capabilities is not None:
            payload["capabilities"] = list(capabilities)
        self.emitted.append(payload)
        return payload

    def goodbye(self, reason: str = "shutting down") -> dict[str, Any]:
        payload: dict[str, Any] = {"type": "goodbye", "node_id": self.node_id, "reason": reason}
        self.emitted.append(payload)
        return payload

    # -------------------------------------------------------------- #
    # Inbound handling (§64.11)
    # -------------------------------------------------------------- #
    async def on_server_message(self, message: dict[str, Any]) -> None:
        """React to one server→node message (``command`` is the only one that replies)."""
        if message.get("type") != "command":
            return
        result = await self.result_for(message)
        self.emitted.append(result)
        if not self.auto_respond:
            self.withheld.append(result)
            return
        await self._deliver(result)

    async def result_for(self, command: Mapping[str, Any]) -> dict[str, Any]:
        """Build the JSON result for one command, running the test's handler."""
        command_id = command.get("command_id")
        tool = str(command.get("tool", ""))
        handler = self.handlers.get(tool)
        if handler is None:
            return self._result(
                command_id,
                status="error",
                error_code=CAPABILITY_NOT_IMPLEMENTED,
                error=f"this node does not implement '{tool}'",
            )
        try:
            value = handler(dict(command.get("arguments") or {}))
            if inspect.isawaitable(value):
                value = await value
        except Exception as error:  # a handler that raises is a tool failure
            return self._result(
                command_id,
                status="error",
                error_code="TOOL_FAILED",
                error=str(error),
            )
        return self._result(command_id, status="ok", result=value)

    async def release(self) -> None:
        """Deliver results withheld while ``auto_respond`` was False."""
        while self.withheld:
            await self._deliver(self.withheld.pop(0))

    async def _deliver(self, result: dict[str, Any]) -> None:
        if self.gateway is not None:
            # Exactly the parse the WebSocket route performs on a real frame.
            await self.gateway.deliver(NodeResult.model_validate(result))

    def _result(
        self,
        command_id: Any,
        *,
        status: str,
        result: Any = None,
        error_code: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "result",
            "command_id": command_id,
            "node_id": self.node_id,
            "status": status,
        }
        if result is not None:
            payload["result"] = result
        if error_code is not None:
            payload["error_code"] = error_code
        if error is not None:
            payload["error"] = error
        return payload


__all__ = [
    "CAPABILITY_NOT_IMPLEMENTED",
    "WINDOWS_CAPABILITIES",
    "FakeNode",
    "FakeTransport",
]
