"""The computer-node gateway (T141, spec §13/§42/§64).

The server side of the Computer Node architecture: nodes register, heartbeat,
receive structured commands and return correlated results. §13's diagram
(``ULTRON SERVER → Computer Agent → Computer Node Gateway → Windows laptop``)
is this module plus the agent (T144); §64.11's routing rules are its dispatch
contract.

**The gateway is transport-agnostic.** A node's connection is anything that can
``await send(message)`` and be closed — a WebSocket today (`app/api/routes/
nodes.py`), a test double in the suite. Keeping the socket out of here is what
makes the whole gateway testable with no Windows, no network and no browser, and
it is why §64.6.5's "the node executes tools, the Core reasons" holds: this
module never touches an OS.

What it owns, and what it deliberately does not:

- **Registration and heartbeat** — a node announces its capabilities and refreshes
  them; discovery is live, not a config file (§64.10). A node that stops
  heartbeating is marked offline by :meth:`sweep`, and its capabilities stop
  being offered without an edit.
- **Command/result correlation** — each command carries a ``command_id`` and a
  pending future; a matching :class:`NodeResult` resolves it. A result for an
  unknown or already-answered id is reported, not silently dropped.
- **Typed unavailability** — §64.11's ``NODE_UNAVAILABLE``: a dispatch to an
  offline node, a node that lacks the capability, or a node that never answers
  raises :class:`~app.core.errors.NodeError` with the node and capability named.
- **Events** — ``NODE_ONLINE``/``NODE_OFFLINE`` and their ``WINDOWS_``/
  ``SERVER_``/``ESP32_`` forms, plus ``NODE_CAPABILITIES_UPDATED``, on the one
  bus (§64.15). Every node event carries ``node_id`` in its payload.

It does **not** perform permission checks: §64.12 makes a permission decision a
``(principal, node, tool, operation)`` tuple owned by the pipeline (§15/§16).
A dispatch carries the principal so the audit row and the node-side re-check
(§64.6.3 Path B) can name it, but the pipeline is what decides.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from app.agents.computer.auth import NodeAuthenticator
from app.agents.computer.protocol import (
    NodeCapabilities,
    NodeCommand,
    NodeHeartbeat,
    NodeRegistration,
    NodeResult,
    NodeStatus,
    NodeType,
    ResultStatus,
    ServerHello,
)
from app.config.settings import ComputerNodeSettings
from app.core.errors import NodeError, NodeNotFoundError, ToolError
from app.events.bus import EventBus
from app.events.types import EventType
from app.observability.logging import get_logger

__all__ = ["ComputerNode", "NodeGateway", "NodeTransport"]

_LOGGER = get_logger(__name__)

#: Which `NODE_*` event a node type announces, per §64.14/§64.15.
_TYPE_EVENT: dict[NodeType, tuple[EventType, EventType]] = {
    NodeType.WINDOWS: (EventType.WINDOWS_ONLINE, EventType.WINDOWS_OFFLINE),
    NodeType.SERVER: (EventType.SERVER_ONLINE, EventType.SERVER_OFFLINE),
    NodeType.ESP32: (EventType.ESP32_ONLINE, EventType.ESP32_OFFLINE),
}


@runtime_checkable
class NodeTransport(Protocol):
    """The minimum a node connection must provide.

    A WebSocket implements this by wrapping its ``send_json``/``close``; the
    test double implements it with a queue. The gateway never assumes more.
    """

    async def send(self, message: Mapping[str, Any]) -> None:
        """Deliver one wire message to the node."""

    async def close(self) -> None:
        """Close the connection. Idempotent."""


@dataclass(slots=True)
class ComputerNode:
    """One registered node's live record (§64.10's conceptual record)."""

    node_id: str
    node_type: NodeType
    name: str
    capabilities: frozenset[str]
    transport: NodeTransport
    status: NodeStatus = NodeStatus.ONLINE
    connected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
    version: str = ""
    client_of: str | None = None
    #: Accounted dispatches, for the health snapshot.
    commands_sent: int = 0
    commands_failed: int = 0

    def is_stale(self, *, offline_after: int, now: datetime | None = None) -> bool:
        moment = now or datetime.now(UTC)
        return (moment - self.last_seen).total_seconds() > offline_after

    def to_capabilities(self) -> NodeCapabilities:
        return NodeCapabilities(
            node_id=self.node_id,
            node_type=self.node_type,
            name=self.name,
            capabilities=sorted(self.capabilities),
            status=self.status,
            last_seen=self.last_seen,
            connected_at=self.connected_at,
            version=self.version,
            client_of=self.client_of,
        )


@dataclass
class _Pending:
    future: asyncio.Future[NodeResult]
    tool: str
    command_id: uuid.UUID
    created_at: datetime


class NodeGateway:
    """Registers nodes, correlates commands, tracks availability (§13/§64)."""

    def __init__(
        self,
        *,
        settings: ComputerNodeSettings | None = None,
        authenticator: NodeAuthenticator | None = None,
        bus: EventBus | None = None,
        command_timeout: float = 30.0,
        max_nodes: int = 64,
        require_auth: bool = True,
    ) -> None:
        self._settings = settings or ComputerNodeSettings()
        self._auth = authenticator or NodeAuthenticator(token_ttl=self._settings.token_ttl)
        self._bus = bus
        self._command_timeout = command_timeout
        self._max_nodes = max_nodes
        self._require_auth = require_auth
        self._nodes: dict[str, ComputerNode] = {}
        self._pending: dict[uuid.UUID, _Pending] = {}
        self._monitor: asyncio.Task[None] | None = None
        self._closed = False

    # ------------------------------------------------------------------ #
    # Credentials (§64.13)
    # ------------------------------------------------------------------ #
    def issue_credential(self, node_id: str) -> str:
        """Mint and return a node credential token. Shown once (§30)."""
        return self._auth.issue(node_id).token

    def revoke_credential(self, node_id: str) -> bool:
        return self._auth.revoke(node_id)

    # ------------------------------------------------------------------ #
    # Registration and heartbeat (§64.10, §64.14)
    # ------------------------------------------------------------------ #
    async def register(
        self, registration: NodeRegistration, transport: NodeTransport
    ) -> ServerHello:
        """Register (or re-register) a node and answer with the server's rules.

        Authentication is by the node credential (§64.13). When the gateway
        requires auth and the credential is absent or invalid, the node is
        refused with ``accepted=False`` — the ``ServerHello`` is the protocol's
        refusal channel, because a registration failure is a normal handshake
        outcome the node can report, not a server fault.
        """
        if self._require_auth:
            if not registration.credential:
                return self._refuse(registration.node_id, "a node credential is required")
            try:
                self._auth.verify(registration.credential)
            except Exception:
                return self._refuse(registration.node_id, "the node credential was rejected")

        existing = self._nodes.get(registration.node_id)
        if existing is None and len(self._nodes) >= self._max_nodes:
            return self._refuse(registration.node_id, "the node registry is full")

        capabilities = frozenset(registration.capabilities)
        if existing is not None:
            # A reconnecting node: replace the transport, keep accounting.
            await self._close_transport(existing.transport, registration.node_id)
            existing.transport = transport
            existing.capabilities = capabilities
            existing.name = registration.name or existing.name
            existing.version = registration.version or existing.version
            existing.client_of = registration.client_of
            existing.status = NodeStatus.ONLINE
            existing.last_seen = datetime.now(UTC)
            node = existing
            await self._publish_capabilities(node)
        else:
            node = ComputerNode(
                node_id=registration.node_id,
                node_type=registration.node_type,
                name=registration.name or registration.node_id,
                capabilities=capabilities,
                transport=transport,
                version=registration.version,
                client_of=registration.client_of,
            )
            self._nodes[registration.node_id] = node
            await self._publish_online(node)

        _LOGGER.info(
            "node registered",
            extra={
                "event": "node.registered",
                "node_id": node.node_id,
                "node_type": node.node_type.value,
                "capabilities": sorted(node.capabilities),
            },
        )
        return ServerHello(
            accepted=True,
            node_id=node.node_id,
            heartbeat_interval=self._settings.heartbeat,
            offline_after=self._settings.offline_after,
        )

    async def heartbeat(self, heartbeat: NodeHeartbeat) -> None:
        """Record a liveness signal; refresh capabilities if they changed."""
        node = self._nodes.get(heartbeat.node_id)
        if node is None:
            raise NodeNotFoundError("node", heartbeat.node_id)
        was_offline = node.status is NodeStatus.OFFLINE
        node.last_seen = datetime.now(UTC)
        if heartbeat.status is not None:
            node.status = heartbeat.status
        elif was_offline:
            node.status = NodeStatus.ONLINE
        if heartbeat.capabilities is not None:
            updated = frozenset(heartbeat.capabilities)
            if updated != node.capabilities:
                node.capabilities = updated
                await self._publish_capabilities(node)
        if was_offline:
            await self._publish_online(node)

    # ------------------------------------------------------------------ #
    # Dispatch and correlation (§64.11)
    # ------------------------------------------------------------------ #
    async def dispatch(
        self,
        node_id: str,
        tool: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        principal: str | None = None,
        limit: float | None = None,
        correlation_id: str | None = None,
        task_id: str | None = None,
        capability: str | None = None,
    ) -> Any:
        """Send one structured command and await its correlated result.

        Raises :class:`NodeError` (typed ``NODE_UNAVAILABLE``) when the node is
        unknown, offline, missing the capability the caller needs, or does not
        answer within ``limit`` seconds (§64.11: never a hang). Raises
        :class:`ToolError` when the node answered but reported a failure — the
        command reached the machine and failed there, which is a tool failure,
        not an unavailable node.
        """
        if self._closed:
            raise NodeError(node_id, reason="the gateway is shutting down")
        node = self._nodes.get(node_id)
        if node is None:
            raise NodeError(node_id, reason="no such node", capability=capability)
        if node.status is NodeStatus.OFFLINE:
            raise NodeError(node_id, reason="node is offline", capability=capability)
        if capability is not None and capability not in node.capabilities:
            raise NodeError(
                node_id,
                reason="node does not advertise the capability",
                capability=capability,
            )

        within = limit if limit is not None else self._command_timeout
        command = NodeCommand(
            command_id=uuid.uuid4(),
            tool=tool,
            arguments=dict(arguments or {}),
            principal=principal,
            deadline=_deadline(within),
            correlation_id=correlation_id,
            task_id=task_id,
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[NodeResult] = loop.create_future()
        self._pending[command.command_id] = _Pending(
            future=future, tool=tool, command_id=command.command_id, created_at=datetime.now(UTC)
        )
        node.commands_sent += 1
        try:
            await node.transport.send(command.model_dump(mode="json"))
            result = await asyncio.wait_for(future, timeout=within)
        except TimeoutError as exc:
            node.commands_failed += 1
            raise NodeError(
                node_id, reason=f"no result within {within:g}s", capability=capability
            ) from exc
        except (OSError, ConnectionError) as exc:
            node.commands_failed += 1
            raise NodeError(
                node_id, reason="the node connection dropped", capability=capability
            ) from exc
        finally:
            self._pending.pop(command.command_id, None)

        if result.status is ResultStatus.ERROR:
            node.commands_failed += 1
            raise ToolError(
                tool,
                result.error or f"node '{node_id}' reported a failure",
                details={"node": node_id, "error_code": result.error_code},
            )
        return result.result

    async def deliver(self, result: NodeResult) -> bool:
        """Resolve the pending command for ``result.command_id``.

        Returns whether a waiter was found. An unknown or late result is logged
        and dropped rather than raising — a node cannot be trusted to be
        well-behaved, and a stray result must not kill the connection reader.
        """
        pending = self._pending.get(result.command_id)
        if pending is None or pending.future.done():
            _LOGGER.warning(
                "unmatched node result",
                extra={
                    "event": "node.result_unmatched",
                    "node_id": result.node_id,
                    "command_id": str(result.command_id),
                },
            )
            return False
        pending.future.set_result(result)
        return True

    # ------------------------------------------------------------------ #
    # Availability (§64.14)
    # ------------------------------------------------------------------ #
    async def disconnect(self, node_id: str, *, reason: str = "") -> bool:
        """Mark a node offline and fail any waiters (§64.14)."""
        node = self._nodes.get(node_id)
        if node is None:
            return False
        await self._mark_offline(node, reason=reason)
        return True

    async def sweep(self, *, now: datetime | None = None) -> list[str]:
        """Mark every node past its heartbeat window offline. Returns their ids.

        Exposed (not only used by the monitor task) so availability is testable
        deterministically by advancing a clock rather than sleeping.
        """
        moment = now or datetime.now(UTC)
        offline: list[str] = []
        for node in list(self._nodes.values()):
            if node.status is NodeStatus.ONLINE and node.is_stale(
                offline_after=self._settings.offline_after, now=moment
            ):
                await self._mark_offline(node, reason="heartbeat missed")
                offline.append(node.node_id)
        return offline

    # ------------------------------------------------------------------ #
    # Queries (§64.10's GET /nodes)
    # ------------------------------------------------------------------ #
    def get(self, node_id: str) -> ComputerNode:
        node = self._nodes.get(node_id)
        if node is None:
            raise NodeNotFoundError("node", node_id)
        return node

    def maybe(self, node_id: str) -> ComputerNode | None:
        return self._nodes.get(node_id)

    def list_nodes(self) -> list[ComputerNode]:
        return [self._nodes[key] for key in sorted(self._nodes)]

    def find_by_capability(
        self, capability: str, *, online_only: bool = True
    ) -> list[ComputerNode]:
        """Nodes advertising ``capability``, online-first then by id (§64.11)."""
        matches = [
            node
            for node in self._nodes.values()
            if capability in node.capabilities
            and (not online_only or node.status is not NodeStatus.OFFLINE)
        ]
        matches.sort(key=lambda n: (n.status is NodeStatus.OFFLINE, n.node_id))
        return matches

    @property
    def online_count(self) -> int:
        return sum(1 for node in self._nodes.values() if node.status is NodeStatus.ONLINE)

    def snapshot(self) -> dict[str, Any]:
        return {
            "nodes": len(self._nodes),
            "online": self.online_count,
            "pending": len(self._pending),
            "by_type": _count_types(self._nodes.values()),
        }

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        """Start the heartbeat monitor (§64.14). Idempotent."""
        if self._monitor is None or self._monitor.done():
            self._monitor = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        """Stop monitoring and close every connection. Idempotent."""
        self._closed = True
        if self._monitor is not None:
            self._monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor
            self._monitor = None
        for command_id, pending in list(self._pending.items()):
            if not pending.future.done():
                pending.future.cancel()
            self._pending.pop(command_id, None)
        for node in list(self._nodes.values()):
            await self._close_transport(node.transport, node.node_id)
        self._nodes.clear()

    async def _monitor_loop(self) -> None:
        interval = max(self._settings.heartbeat, 1)
        while not self._closed:
            await asyncio.sleep(interval)
            try:
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - monitor must survive
                _LOGGER.exception("node heartbeat monitor iteration failed")

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    async def _mark_offline(self, node: ComputerNode, *, reason: str) -> None:
        if node.status is NodeStatus.OFFLINE:
            return
        node.status = NodeStatus.OFFLINE
        # Fail any command still waiting on this node so a caller never hangs.
        for command_id, pending in list(self._pending.items()):
            if not pending.future.done():
                pending.future.set_exception(
                    NodeError(node.node_id, reason=reason or "node went offline")
                )
                self._pending.pop(command_id, None)
        await self._close_transport(node.transport, node.node_id)
        await self._publish_offline(node, reason=reason)

    async def _close_transport(self, transport: NodeTransport, node_id: str) -> None:
        try:
            await transport.close()
        except Exception:  # pragma: no cover - a dead socket is expected
            _LOGGER.debug("node transport close failed", extra={"node_id": node_id})

    def _refuse(self, node_id: str, reason: str) -> ServerHello:
        _LOGGER.warning(
            "node registration refused",
            extra={"event": "node.register_refused", "node_id": node_id, "reason": reason},
        )
        return ServerHello(
            accepted=False,
            node_id=node_id,
            reason=reason,
            heartbeat_interval=self._settings.heartbeat,
            offline_after=self._settings.offline_after,
        )

    async def _publish_online(self, node: ComputerNode) -> None:
        await self._publish_lifecycle(EventType.NODE_ONLINE, node)
        specific = _TYPE_EVENT.get(node.node_type)
        if specific is not None:
            await self._publish_lifecycle(specific[0], node)

    async def _publish_offline(self, node: ComputerNode, *, reason: str) -> None:
        await self._publish_lifecycle(EventType.NODE_OFFLINE, node, extra={"reason": reason})
        specific = _TYPE_EVENT.get(node.node_type)
        if specific is not None:
            await self._publish_lifecycle(specific[1], node, extra={"reason": reason})

    async def _publish_capabilities(self, node: ComputerNode) -> None:
        await self._publish_lifecycle(EventType.NODE_CAPABILITIES_UPDATED, node)

    async def _publish_lifecycle(
        self, event_type: EventType, node: ComputerNode, *, extra: Mapping[str, Any] | None = None
    ) -> None:
        if self._bus is None:
            return
        payload: dict[str, Any] = {
            "node_id": node.node_id,
            "node_type": node.node_type.value,
            "name": node.name,
            "capabilities": sorted(node.capabilities),
        }
        if extra:
            payload.update(extra)
        await self._bus.publish(event_type, payload, persist=False)


def _deadline(seconds: float) -> datetime:
    return datetime.fromtimestamp(datetime.now(UTC).timestamp() + seconds, tz=UTC)


def _count_types(nodes: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in nodes:
        key = node.node_type.value
        counts[key] = counts.get(key, 0) + 1
    return counts
