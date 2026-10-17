"""A ``paho-mqtt`` client bridged to asyncio (T163, spec §26).

The concrete :class:`~app.devices.mqtt.transport.MqttClient` behind the optional
``iot`` extra. ``paho-mqtt`` is a *threaded* client with callbacks, while ULTRON
is asyncio throughout, so this adapter owns the seam: it starts paho's network
loop on its own thread, hands inbound messages to the event loop through a
thread-safe queue, and keeps publish off the loop with ``asyncio.to_thread``.

Everything above this file is broker-agnostic — the bridge only ever sees
:class:`MqttClient` — so swapping paho for ``aiomqtt`` later is a change here and
nowhere else. When the extra is absent, :func:`build_mqtt_client` returns
``None`` and the caller degrades (the WebSocket transport still serves devices);
nothing imports paho at module scope.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from app.config.settings import DeviceSettings
from app.core.errors import DeviceError
from app.devices.mqtt.availability import paho_available

__all__ = [
    "PahoMqttClient",
    "build_mqtt_client",
]

#: Seconds paho keeps an idle connection alive before its own keepalive ping.
_KEEPALIVE_SECONDS = 60

#: QoS for device traffic: 1 (at-least-once) — a lost heartbeat or command is a
#: silent liveness lie, and device frames are small, so the cost is acceptable.
_QOS = 1


class PahoMqttClient:
    """An :class:`MqttClient` over ``paho-mqtt`` (§26).

    Not constructed unless paho is importable (see :func:`build_mqtt_client`).
    """

    def __init__(self, settings: DeviceSettings) -> None:
        self._settings = settings
        self._prefix = settings.mqtt_topic_prefix
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[tuple[str, bytes]] | None = None
        self._client: Any = None

    async def start(self) -> None:
        """Connect to the broker, subscribe to device uplinks, start the loop."""
        import paho.mqtt.client as mqtt

        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self._settings.mqtt_client_id or None,
        )
        if self._settings.mqtt_username:
            client.username_pw_set(
                self._settings.mqtt_username, self._settings.mqtt_password or None
            )
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        self._client = client
        # ``connect`` blocks on DNS + TCP, so keep it off the event loop.
        await asyncio.to_thread(
            client.connect,
            self._settings.mqtt_broker_host,
            self._settings.mqtt_broker_port,
            _KEEPALIVE_SECONDS,
        )
        client.loop_start()

    def _on_connect(
        self,
        client: Any,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        """Subscribe to every device's uplink once the broker accepts us."""
        client.subscribe(f"{self._prefix}/devices/+/up", qos=_QOS)

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        """Hand a broker message to the event loop (called on paho's thread)."""
        if self._loop is None or self._queue is None:
            return
        item = (str(message.topic), bytes(message.payload))
        self._loop.call_soon_threadsafe(self._queue.put_nowait, item)

    async def publish(self, topic: str, payload: bytes) -> None:
        """Publish one payload; paho's publish is thread-safe but blocking."""
        if self._client is None:
            raise DeviceError("the MQTT client is not connected")
        await asyncio.to_thread(self._client.publish, topic, payload, _QOS, False)

    async def messages(self) -> AsyncIterator[tuple[str, bytes]]:
        """Yield ``(topic, payload)`` for every inbound message, forever."""
        queue = self._queue
        if queue is None:
            raise DeviceError("the MQTT client is not connected")
        while True:
            yield await queue.get()

    async def close(self) -> None:
        """Disconnect and stop paho's network thread."""
        client = self._client
        self._client = None
        if client is None:
            return
        await asyncio.to_thread(client.disconnect)
        await asyncio.to_thread(client.loop_stop)


def build_mqtt_client(settings: DeviceSettings) -> PahoMqttClient | None:
    """Build the MQTT client, or ``None`` when the ``iot`` extra is absent.

    Returning ``None`` (with :func:`paho_unavailable_reason` explaining why) lets
    a deployment that set ``MQTT_ENABLED`` without installing ``paho-mqtt`` keep
    running over the WebSocket transport and surface the misconfiguration in the
    logs, rather than failing to start (§64.11).
    """
    if not paho_available():
        return None
    return PahoMqttClient(settings)
