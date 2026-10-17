"""The optional MQTT device transport (T163, spec §26).

The second way a device may reach the Core. Where the ESP32 transport (T162) is
a per-device WebSocket, this is one shared broker connection addressed by topic;
both hand the *same* frames to the *same*
:class:`~app.devices.manager.DeviceManager`, so a device behaves identically on
either.

:mod:`app.devices.mqtt.transport` holds the broker-agnostic seam — the
:class:`MqttClient` protocol, the :class:`MqttDeviceTransport`, and the
:class:`MqttDeviceBridge` that routes frames. :mod:`app.devices.mqtt.availability`
and :mod:`app.devices.mqtt.client` are the optional ``paho-mqtt`` concrete
client, imported lazily so ULTRON runs without the ``iot`` extra installed.
"""

from __future__ import annotations

from app.devices.mqtt.availability import paho_available, paho_unavailable_reason
from app.devices.mqtt.client import PahoMqttClient, build_mqtt_client
from app.devices.mqtt.transport import (
    MqttClient,
    MqttDeviceBridge,
    MqttDeviceTransport,
    device_id_from_topic,
    topic_for_device,
)

__all__ = [
    "MqttClient",
    "MqttDeviceBridge",
    "MqttDeviceTransport",
    "PahoMqttClient",
    "build_mqtt_client",
    "device_id_from_topic",
    "paho_available",
    "paho_unavailable_reason",
    "topic_for_device",
]
