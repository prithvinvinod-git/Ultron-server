"""The ESP32 JSON-over-WebSocket transport (T162, spec §26/§64.9).

The device's side of the §26 architecture: an ESP32 dials **out** to the server
(§59.15/§64.6.4 — no inbound LAN listener, no port forwarding), presents its
device credential in a ``register`` frame, and then speaks the T160 protocol in
JSON — heartbeats up, commands down, results and events up.

**Why a WebSocket and not MQTT.** §26 leaves the transport open; the WebSocket is
the one that needs no broker, no extra process and no inbound port, which is what
makes "bring up a board" a single outbound connection. MQTT is the optional
adapter (T163) for deployments that already run a broker; both implement the same
:class:`~app.devices.manager.DeviceTransport`, so the registry does not know
which one a device used.

**Board-agnostic by construction** (§26): nothing here names an ESP32 model. The
module is named for the reference board because that is the firmware the T160
protocol was drawn against, but the transport is a generic JSON-over-WebSocket
device session — a second board speaks it unchanged.

**Keepalive** is the device's heartbeat plus the manager's sweep: the device
sends ``heartbeat`` on the interval the server told it in ``registered``, and
:meth:`DeviceManager.sweep` marks a silent device offline. The socket layer
(uvicorn) owns protocol-level ping/pong, so there is nothing to add here.

The reader is written against a tiny :class:`DeviceSocket` protocol rather than
Starlette's ``WebSocket`` so the whole session is unit-testable with a queue
double — ``tests/support/device_double.py`` — and no network.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from typing import Any, Protocol

from app.core.errors import InvalidInputError
from app.devices.manager import DeviceManager
from app.devices.protocol import (
    DeviceEventMessage,
    DeviceGoodbye,
    DeviceHeartbeat,
    DeviceHeartbeatAck,
    DeviceRegister,
    DeviceResult,
    parse_message,
)
from app.observability.logging import get_logger

__all__ = ["DeviceSocket", "WebSocketDeviceTransport", "run_device_session"]

_LOGGER = get_logger(__name__)


class DeviceSocket(Protocol):
    """The slice of a WebSocket the device session uses.

    Starlette's ``WebSocket`` satisfies this structurally; a test double
    implements it with a queue. Declaring it here keeps the session free of any
    web-framework import.
    """

    async def receive_json(self) -> Any:
        """Read and decode the next frame, or raise on disconnect."""

    async def send_json(self, data: Any) -> None:
        """Encode and send one frame."""

    async def close(self, code: int = 1000) -> None:
        """Close the socket. Idempotent."""


class WebSocketDeviceTransport:
    """A :class:`~app.devices.manager.DeviceTransport` over a device socket.

    The manager never sees the socket — only ``send``/``close`` — which is what
    keeps the registry testable with no socket at all.
    """

    def __init__(self, socket: DeviceSocket) -> None:
        self._socket = socket

    async def send(self, message: Mapping[str, Any]) -> None:
        await self._socket.send_json(dict(message))

    async def close(self) -> None:
        # Already closed is not a fault; closing twice is a no-op.
        with contextlib.suppress(RuntimeError):
            await self._socket.close()


async def run_device_session(socket: DeviceSocket, manager: DeviceManager) -> None:
    """Run one device's whole session over ``socket`` (§26).

    The first frame must be a ``register``; the manager answers ``registered``
    (accepted or refused) and, when accepted, the loop reads heartbeats, events
    and command results until the device says goodbye or the socket drops. Every
    exit path disconnects the device so its capabilities stop being offered
    (§64.10: discovery is live, not a config file).

    A malformed frame is logged and skipped rather than dropping the socket: a
    firmware bug must not turn one bad message into a lost connection (T160's
    contract). A malformed *registration*, however, ends the session — there is
    nothing to serve until a device identifies itself.
    """
    device_id: str | None = None
    try:
        raw = await socket.receive_json()
        try:
            first = parse_message(raw)
        except InvalidInputError:
            _LOGGER.warning(
                "device sent a malformed opening frame", extra={"event": "device.malformed_open"}
            )
            await _close(socket)
            return
        if not isinstance(first, DeviceRegister):
            _LOGGER.warning(
                "device did not register first", extra={"event": "device.no_register"}
            )
            await _close(socket)
            return

        device_id = first.device_id
        hello = await manager.register(first, WebSocketDeviceTransport(socket))
        await socket.send_json(hello.model_dump(mode="json"))
        if not hello.accepted:
            await _close(socket)
            return
        await _serve(socket, manager, device_id)
    finally:
        if device_id is not None:
            await manager.disconnect(device_id, reason="connection closed")


async def _serve(socket: DeviceSocket, manager: DeviceManager, device_id: str) -> None:
    """Read heartbeats, events and results until the device goes away (§26)."""
    while True:
        raw = await socket.receive_json()
        try:
            message = parse_message(raw)
        except InvalidInputError as exc:
            _LOGGER.warning(
                "device sent a malformed message",
                extra={"event": "device.malformed", "device_id": device_id, "detail": str(exc)},
            )
            continue
        if isinstance(message, DeviceHeartbeat):
            await manager.heartbeat(message)
            ack = DeviceHeartbeatAck(device_id=device_id)
            await socket.send_json(ack.model_dump(mode="json"))
        elif isinstance(message, DeviceEventMessage):
            await manager.report(message)
        elif isinstance(message, DeviceResult):
            await manager.deliver(message)
        elif isinstance(message, DeviceGoodbye):
            return
        else:  # pragma: no cover - parse_message only yields the models above
            _LOGGER.warning(
                "device sent a message the session cannot handle",
                extra={"event": "device.unsupported", "device_id": device_id},
            )


async def _close(socket: DeviceSocket) -> None:
    with contextlib.suppress(RuntimeError):
        await socket.close()
