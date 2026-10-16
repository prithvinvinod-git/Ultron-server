"""Node routes (T141/T332, spec §13/§29/§64.10).

Two read endpoints and one WebSocket:

- ``GET /nodes`` — every registered node's registration, capabilities, status
  and ``last_seen`` (§64.10: discovery is exposed like the rest of the API).
- ``GET /nodes/{id}`` — one node, or ``404``/``NODE_NOT_FOUND``.
- ``WS /nodes/ws`` — the node's connection. A node dials *out* to the server
  (§59.15/§64.6.4: no inbound LAN listener), presents its node credential in the
  registration, and then speaks the T140 protocol: heartbeat up, commands down,
  results up.

**Why the WebSocket is not behind the user ``Auth`` dependency.** A node
credential authenticates *a node, never a user* (§64.13) — they are different
principals. The node presents the credential inside its ``register`` message,
and the gateway (:meth:`NodeGateway.register`) is what verifies it, with the
same typed refusal path a wrong token gets everywhere. Requiring a user token
first would mean running a server with a human session in front of a machine
identity, which is exactly the conflation §64.13 forbids.

The read endpoints *are* authenticated like every other route (§15/§29): read
access follows the ordinary permission check. Mutating node records (T332) is
elevated work and is not exposed here.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.agents.computer.protocol import (
    MessageType,
    NodeCapabilities,
    NodeHeartbeat,
    NodeHeartbeatAck,
    NodeRegistration,
    NodeResult,
)
from app.api.dependencies import Auth, Container
from app.observability.logging import get_logger

if TYPE_CHECKING:
    from app.agents.computer.gateway import NodeGateway

_LOGGER = get_logger(__name__)

router = APIRouter(prefix="/nodes", tags=["nodes"])


@router.get("", response_model=list[NodeCapabilities], summary="List registered nodes")
async def list_nodes(container: Container, principal: Auth) -> list[NodeCapabilities]:
    """Return every registered node, sorted by node id (§64.10)."""
    return [node.to_capabilities() for node in container.node_gateway.list_nodes()]


@router.get("/{node_id}", response_model=NodeCapabilities, summary="Describe one node")
async def get_node(node_id: str, container: Container, principal: Auth) -> NodeCapabilities:
    """Return one node's capabilities and status, or ``404``/``NODE_NOT_FOUND``."""
    return container.node_gateway.get(node_id).to_capabilities()


class _WebSocketTransport:
    """A :class:`~app.agents.computer.gateway.NodeTransport` over a WebSocket.

    The gateway never sees this class — only ``send``/``close`` — which is what
    keeps the gateway testable with no socket at all.
    """

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def send(self, message: dict[str, Any]) -> None:
        await self._websocket.send_json(message)

    async def close(self) -> None:
        # Already closed is not a fault; closing twice is a no-op.
        with contextlib.suppress(RuntimeError):
            await self._websocket.close()


@router.websocket("/ws")
async def node_socket(websocket: WebSocket) -> None:
    """Accept a node's outbound connection and run the T140 protocol.

    The first message must be a ``register``; the gateway answers with a
    ``ServerHello`` (accepted or refused) and, when accepted, the loop reads
    heartbeats and results while the gateway pushes commands down the same
    socket. Every exit path disconnects the node so its capabilities stop being
    offered (§64.10: discovery is live, not a config file).
    """
    await websocket.accept()
    container = getattr(websocket.app.state, "container", None)
    if container is None:
        await websocket.close(code=1011)
        return
    gateway = container.node_gateway
    transport = _WebSocketTransport(websocket)
    node_id: str | None = None
    try:
        raw = await websocket.receive_json()
        registration = NodeRegistration.model_validate(raw)
        node_id = registration.node_id
        hello = await gateway.register(registration, transport)
        await websocket.send_json(hello.model_dump(mode="json"))
        if not hello.accepted:
            await websocket.close()
            return
        await _serve(gateway, websocket, node_id)
    except (WebSocketDisconnect, RuntimeError):
        pass
    except ValidationError as exc:
        _LOGGER.warning(
            "node sent a malformed message",
            extra={"event": "node.malformed", "node_id": node_id, "errors": exc.error_count()},
        )
    finally:
        if node_id is not None:
            await gateway.disconnect(node_id, reason="connection closed")


async def _serve(gateway: NodeGateway, websocket: WebSocket, node_id: str) -> None:
    """Read heartbeats and results until the node goes away (§64.14)."""
    while True:
        raw = await websocket.receive_json()
        message_type = raw.get("type")
        if message_type == MessageType.HEARTBEAT:
            await gateway.heartbeat(NodeHeartbeat.model_validate(raw))
            ack = NodeHeartbeatAck(node_id=node_id)
            await websocket.send_json(ack.model_dump(mode="json"))
        elif message_type == MessageType.RESULT:
            await gateway.deliver(NodeResult.model_validate(raw))
        elif message_type == MessageType.GOODBYE:
            return
        else:
            _LOGGER.warning(
                "node sent an unsupported message",
                extra={"event": "node.unsupported", "node_id": node_id, "type": message_type},
            )


__all__ = ["router"]
