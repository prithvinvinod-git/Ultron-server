"""The device test double (T161, spec §26).

A device, on the wire, is a peer that speaks the T160 protocol: it sends a
``register``, heartbeats, reports ``event``s, and answers ``command`` with a
correlated ``result``. This double enacts exactly that in **plain JSON dicts**,
so the suite crosses the same boundary the ESP32 transport will — the manager's
``DeviceResult`` is built from the double's JSON with ``model_validate``, which
is what ``app/api/routes/devices.py`` will do with a frame off the socket (T162).

It is not a second implementation of the manager: a ``command`` is answered by
the ``handlers`` the test supplied, and an unhandled action is answered the way a
device answers one it cannot run — ``status: "error"`` with
``error_code: "CAPABILITY_NOT_IMPLEMENTED"``.

Two escape hatches keep failure paths testable: ``auto_respond=False`` withholds
a result (to reach the manager's timeout), and ``connection_error`` makes
``FakeDeviceTransport.send`` raise (to reach its connection-drop path).
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any

from app.devices.protocol import DeviceResult

#: The capability families the reference ESP32 advertises (§64.9). Advisory: the
#: registry stores whatever a device advertises, not just these.
ESP32_CAPABILITIES: tuple[str, ...] = (
    "display",
    "microphone",
    "speaker",
    "buttons",
    "sensors",
    "physical_controls",
)

#: The error code a device returns for an action it does not implement.
CAPABILITY_NOT_IMPLEMENTED = "CAPABILITY_NOT_IMPLEMENTED"

#: Sentinel a test pushes to make ``FakeDeviceSocket.receive_json`` raise, the
#: way Starlette's ``WebSocket.receive_json`` raises ``WebSocketDisconnect``.
_CLOSE = object()


class ConnectionClosedError(Exception):
    """Raised on receive after a ``_CLOSE`` sentinel, like a dropped socket."""


class FakeDeviceSocket:
    """A :class:`~app.devices.esp32.transport.DeviceSocket` with no network.

    Frames pushed with :meth:`push` are handed to ``receive_json`` in order;
    frames the server sends land on the :meth:`next_sent` queue. This drives
    ``run_device_session`` end to end without a WebSocket.
    """

    def __init__(self) -> None:
        self._incoming: asyncio.Queue[Any] = asyncio.Queue()
        self._outgoing: asyncio.Queue[Any] = asyncio.Queue()
        self.closed = False
        self.close_codes: list[int] = []

    async def push(self, frame: Any) -> None:
        await self._incoming.put(frame)

    async def close_from_peer(self) -> None:
        """Simulate the device dropping the socket."""
        await self._incoming.put(_CLOSE)

    async def receive_json(self) -> Any:
        frame = await self._incoming.get()
        if frame is _CLOSE:
            raise ConnectionClosedError()
        return frame

    async def send_json(self, data: Any) -> None:
        await self._outgoing.put(data)

    async def next_sent(self, within: float = 1.0) -> Any:
        return await asyncio.wait_for(self._outgoing.get(), timeout=within)

    async def close(self, code: int = 1000) -> None:
        self.closed = True
        self.close_codes.append(code)


class FakeDeviceTransport:
    """A :class:`~app.devices.manager.DeviceTransport` with no socket.

    ``send`` records the server's message and hands it to the device, so a
    dispatch resolves synchronously inside the manager's own ``await send`` —
    no concurrency, no sleeps, no flakiness.
    """

    def __init__(self, device: FakeDevice) -> None:
        self._device = device
        #: Every server->device message, in order.
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        #: When set, ``send`` raises this instead of delivering — the
        #: connection-dropped path.
        self.connection_error: type[Exception] | None = None

    async def send(self, message: Mapping[str, Any]) -> None:
        if self.connection_error is not None:
            raise self.connection_error("the device connection dropped")
        payload = dict(message)
        self.sent.append(payload)
        await self._device.on_server_message(payload)

    async def close(self) -> None:
        self.closed = True


class FakeDevice:
    """A client-side device the manager can register and dispatch to."""

    def __init__(
        self,
        device_id: str = "esp32-1",
        *,
        device_type: str = "esp32",
        name: str = "Test ESP32",
        capabilities: Sequence[str] = ESP32_CAPABILITIES,
        firmware_version: str = "1.0.0",
        credential: str | None = "device-credential",
        handlers: Mapping[str, Callable[[dict[str, Any]], Any]] | None = None,
        auto_respond: bool = True,
    ) -> None:
        self.device_id = device_id
        self.device_type = device_type
        self.name = name
        self.capabilities = list(capabilities)
        self.firmware_version = firmware_version
        self.credential = credential
        self.handlers: dict[str, Callable[[dict[str, Any]], Any]] = dict(handlers or {})
        self.auto_respond = auto_respond
        self.transport = FakeDeviceTransport(self)
        #: Every device->server message the device built, in order.
        self.emitted: list[dict[str, Any]] = []
        #: Results built but withheld when ``auto_respond`` is False.
        self.withheld: list[dict[str, Any]] = []
        self.manager: Any | None = None

    # -------------------------------------------------------------- #
    # Outbound messages the device sends (§26)
    # -------------------------------------------------------------- #
    def bind(self, manager: Any) -> FakeDevice:
        """Attach the manager this device delivers results to."""
        self.manager = manager
        return self

    def registration(self, *, credential: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "register",
            "device_id": self.device_id,
            "name": self.name,
            "device_type": self.device_type,
            "capabilities": list(self.capabilities),
            "firmware_version": self.firmware_version,
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
        payload: dict[str, Any] = {"type": "heartbeat", "device_id": self.device_id}
        if status is not None:
            payload["status"] = status
        if capabilities is not None:
            payload["capabilities"] = list(capabilities)
        self.emitted.append(payload)
        return payload

    def event(self, event_type: str, *, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        message: dict[str, Any] = {
            "type": "event",
            "device_id": self.device_id,
            "event_type": event_type,
        }
        if payload is not None:
            message["payload"] = dict(payload)
        self.emitted.append(message)
        return message

    def goodbye(self, reason: str = "shutting down") -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "goodbye",
            "device_id": self.device_id,
            "reason": reason,
        }
        self.emitted.append(payload)
        return payload

    # -------------------------------------------------------------- #
    # Inbound handling (§26 command/result)
    # -------------------------------------------------------------- #
    async def on_server_message(self, message: dict[str, Any]) -> None:
        """React to one server->device message (``command`` is the one that replies)."""
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
        action = str(command.get("action", ""))
        handler = self.handlers.get(action)
        if handler is None:
            return self._result(
                command_id,
                status="error",
                error_code=CAPABILITY_NOT_IMPLEMENTED,
                error=f"this device does not implement '{action}'",
            )
        try:
            value = handler(dict(command.get("arguments") or {}))
            if inspect.isawaitable(value):
                value = await value
        except Exception as error:  # a handler that raises is a device failure
            return self._result(
                command_id,
                status="error",
                error_code="DEVICE_ACTION_FAILED",
                error=str(error),
            )
        return self._result(command_id, status="ok", result=value)

    async def release(self) -> None:
        """Deliver results withheld while ``auto_respond`` was False."""
        while self.withheld:
            await self._deliver(self.withheld.pop(0))

    async def _deliver(self, result: dict[str, Any]) -> None:
        if self.manager is not None:
            # Exactly the parse the WebSocket route performs on a real frame.
            await self.manager.deliver(DeviceResult.model_validate(result))

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
            "device_id": self.device_id,
            "status": status,
        }
        if result is not None:
            payload["result"] = result
        if error_code is not None:
            payload["error_code"] = error_code
        if error is not None:
            payload["error"] = error
        return payload


class FakeMqttClient:
    """An :class:`~app.devices.mqtt.transport.MqttClient` with no broker (T163).

    Two modes. With ``frames`` the uplink is a **finite** async iterator, so
    ``MqttDeviceBridge.run`` returns once the frames are drained and a test can
    assert deterministically with no sleeps or task juggling. Without ``frames``
    the uplink blocks on a queue fed by :meth:`push`, for container-style use.

    Publishes are decoded and kept with their topic, so a test asserts that the
    server's answer landed on the right device's ``down`` topic.
    """

    def __init__(
        self,
        *,
        topic_prefix: str = "ultron",
        frames: Sequence[tuple[str, bytes]] | None = None,
    ) -> None:
        self.topic_prefix = topic_prefix
        self._frames = list(frames) if frames is not None else None
        self._queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()
        #: Every ``(topic, decoded payload)`` the server published, in order.
        self.published: list[tuple[str, Any]] = []

    async def publish(self, topic: str, payload: bytes) -> None:
        self.published.append((topic, json.loads(payload)))

    async def messages(self) -> AsyncIterator[tuple[str, bytes]]:
        if self._frames is not None:
            for frame in self._frames:
                yield frame
            return
        while True:
            yield await self._queue.get()

    async def push(self, topic: str, frame: Any) -> None:
        """Queue one uplink payload; ``frame`` is JSON-encoded when not bytes."""
        payload = frame if isinstance(frame, bytes) else json.dumps(frame).encode("utf-8")
        await self._queue.put((topic, payload))

    def up_topic(self, device_id: str) -> str:
        return f"{self.topic_prefix}/devices/{device_id}/up"

    def down_topic(self, device_id: str) -> str:
        return f"{self.topic_prefix}/devices/{device_id}/down"

    def sent_to(self, device_id: str) -> list[Any]:
        """Every payload published to ``device_id``'s down topic, in order."""
        topic = self.down_topic(device_id)
        return [payload for published_topic, payload in self.published if published_topic == topic]


__all__ = [
    "CAPABILITY_NOT_IMPLEMENTED",
    "ESP32_CAPABILITIES",
    "ConnectionClosedError",
    "FakeDevice",
    "FakeDeviceSocket",
    "FakeDeviceTransport",
    "FakeMqttClient",
]
