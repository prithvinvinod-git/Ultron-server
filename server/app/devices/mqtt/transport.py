"""The MQTT device transport adapter (T163, spec §26).

The optional second transport for the §26 device architecture. Where the
reference connection is a per-device WebSocket (T162), MQTT is a **shared broker
connection**: one client, many devices, each addressed by topic. Deployments that
already run a broker (Home Assistant, an ESP32 mesh gateway) prefer it; a
deployment without one uses the WebSocket and pays nothing for this module.

The adapter is split so the broker is a seam, not a dependency:
:class:`MqttClient` is the tiny surface the bridge needs, and
:class:`MqttDeviceBridge` translates the broker's frames into the *same*
:class:`~app.devices.manager.DeviceManager` calls the WebSocket session uses.
A device therefore behaves identically whichever transport it dialled in on, and
the whole bridge is unit-testable with an in-memory client and no broker.

**Topics** (all under ``MQTT_TOPIC_PREFIX``)::

    {prefix}/devices/{device_id}/up     device -> server   (register, heartbeat, event, result)
    {prefix}/devices/{device_id}/down   server -> device   (registered, heartbeat_ack, command)

The device id lives in the topic, so the bridge subscribes once to
``{prefix}/devices/+/up`` and reads the id off each topic — it never needs to
know which devices exist, exactly as the WebSocket route does not.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol, runtime_checkable

from app.core.errors import InvalidInputError, UltronError
from app.devices.manager import DeviceManager
from app.devices.protocol import (
    DeviceEventMessage,
    DeviceGoodbye,
    DeviceHeartbeat,
    DeviceHeartbeatAck,
    DeviceRegister,
    DeviceRegistered,
    DeviceResult,
    parse_message,
)
from app.observability.logging import get_logger

__all__ = [
    "MqttClient",
    "MqttDeviceBridge",
    "MqttDeviceTransport",
    "device_id_from_topic",
    "topic_for_device",
]

_LOGGER = get_logger(__name__)

#: The two directions under a device's topic subtree.
_UP = "up"
_DOWN = "down"


def topic_for_device(prefix: str, device_id: str, direction: str) -> str:
    """Build the MQTT topic for one device and direction.

    The ``+`` wildcard is only ever used in a *subscription* the bridge builds,
    never here, so a topic always names a concrete device.
    """
    return f"{prefix}/devices/{device_id}/{direction}"


def device_id_from_topic(prefix: str, topic: str) -> str | None:
    """Extract ``device_id`` from an ``up`` topic, or ``None`` if it does not fit.

    Returns ``None`` rather than raising so a stray broker message on a topic the
    bridge happens to be subscribed to is ignored, not fatal — a broker may carry
    other traffic and a shared prefix must not crash the bridge.
    """
    prefix_parts = prefix.strip("/").split("/")
    parts = topic.strip("/").split("/")
    if len(parts) != len(prefix_parts) + 3:
        return None
    if parts[: len(prefix_parts)] != prefix_parts:
        return None
    if parts[len(prefix_parts)] != "devices" or parts[-1] != _UP:
        return None
    return parts[-2]


@runtime_checkable
class MqttClient(Protocol):
    """The slice of a broker client the bridge uses.

    A concrete adapter over ``aiomqtt``/``paho`` implements this; the test double
    implements it with in-memory queues. Keeping it this small is why the bridge
    needs no broker to be exercised.
    """

    async def publish(self, topic: str, payload: bytes) -> None:
        """Publish one payload to one topic."""

    def messages(self) -> AsyncIterator[tuple[str, bytes]]:
        """Yield ``(topic, payload)`` for every inbound message, forever.

        An async generator: iterate until cancelled. The bridge owns the loop.
        """


class MqttDeviceTransport:
    """A :class:`~app.devices.manager.DeviceTransport` that publishes to a topic.

    ``close`` is a no-op: MQTT has no per-device connection to close — the broker
    owns the socket, and a device is "offline" by heartbeat expiry, not by us
    tearing anything down. That is the one behavioural difference from the
    WebSocket transport.
    """

    def __init__(self, client: MqttClient, *, topic_prefix: str, device_id: str) -> None:
        self._client = client
        self._topic = topic_for_device(topic_prefix, device_id, _DOWN)

    async def send(self, message: Mapping[str, Any]) -> None:
        payload = json.dumps(dict(message)).encode("utf-8")
        await self._client.publish(self._topic, payload)

    async def close(self) -> None:
        return None


class MqttDeviceBridge:
    """Translates broker frames into manager calls (spec §26).

    One bridge serves every device on the broker. It mirrors
    :func:`~app.devices.esp32.transport.run_device_session`'s handling — register,
    heartbeat, event, result, goodbye — but keys devices by the topic rather than
    by a socket, and holds a per-device :class:`MqttDeviceTransport` so the
    manager's outbound frames land on the right ``down`` topic.
    """

    def __init__(
        self,
        manager: DeviceManager,
        client: MqttClient,
        *,
        topic_prefix: str | None = None,
    ) -> None:
        self._manager = manager
        self._client = client
        self._prefix = topic_prefix or manager.settings.mqtt_topic_prefix
        self._transports: dict[str, MqttDeviceTransport] = {}

    @property
    def subscription_topic(self) -> str:
        """The single topic the bridge subscribes to: every device's ``up`` topic."""
        return f"{self._prefix}/devices/+/{_UP}"

    async def run(self) -> None:
        """Consume inbound frames until cancelled (spec §26).

        A malformed or off-topic message is logged and skipped; the bridge must
        survive a broker that carries other traffic or a firmware that emits one
        bad frame, exactly as the WebSocket session does.
        """
        async for topic, payload in self._client.messages():
            try:
                await self._handle(topic, payload)
            except asyncio.CancelledError:
                raise
            except UltronError as exc:
                _LOGGER.warning(
                    "mqtt device frame rejected",
                    extra={"event": "device.mqtt.rejected", "topic": topic, "detail": str(exc)},
                )
            except Exception:  # pragma: no cover - a bad frame must not kill the bridge
                _LOGGER.exception(
                    "mqtt device frame failed",
                    extra={"event": "device.mqtt.failed", "topic": topic},
                )

    async def _handle(self, topic: str, payload: bytes) -> None:
        device_id = device_id_from_topic(self._prefix, topic)
        if device_id is None:
            return  # not a device up-topic
        message = _decode(payload)
        if isinstance(message, DeviceRegister):
            await self._register(device_id, message)
        elif isinstance(message, DeviceHeartbeat):
            ack = await self._manager.heartbeat(message)
            await self._send(device_id, ack)
        elif isinstance(message, DeviceEventMessage):
            await self._manager.report(message)
        elif isinstance(message, DeviceResult):
            await self._manager.deliver(message)
        elif isinstance(message, DeviceGoodbye):
            await self._manager.disconnect(device_id, reason="device said goodbye")

    async def _register(self, device_id: str, registration: DeviceRegister) -> None:
        hello = await self._manager.register(registration, self._transport(device_id))
        await self._send(device_id, hello)

    async def _send(self, device_id: str, message: DeviceRegistered | DeviceHeartbeatAck) -> None:
        await self._transport(device_id).send(message.model_dump(mode="json"))

    def _transport(self, device_id: str) -> MqttDeviceTransport:
        transport = self._transports.get(device_id)
        if transport is None:
            transport = MqttDeviceTransport(
                self._client, topic_prefix=self._prefix, device_id=device_id
            )
            self._transports[device_id] = transport
        return transport


def _decode(payload: bytes) -> Any:
    """Decode one device payload into its typed model.

    Raises :class:`~app.core.errors.InvalidInputError` for non-JSON bytes or a
    frame that fails the protocol's validation, so the bridge's single ``except``
    treats them the same way the WebSocket session does.
    """
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidInputError("mqtt device payload is not JSON") from exc
    return parse_message(data)
