"""Device gateway for physical nodes such as ESP32 boards (Phase 10, spec §26).

The device subsystem is provider-independent and board-agnostic: a device
speaks the JSON protocol in :mod:`app.devices.protocol`, the gateway in
:mod:`app.devices.manager` owns the registry and liveness, and Core never names
a specific board (§26). Devices and computer nodes are one registry (D021), so
an ESP32 is an ``esp32`` record carrying the §26 device fields plus heartbeat
and capability data.
"""

from __future__ import annotations

from app.devices.manager import (
    DeviceConnection,
    DeviceManager,
    DeviceStore,
    DeviceTransport,
    NullDeviceStore,
)
from app.devices.protocol import (
    DEVICE_CAPABILITY_STRINGS,
    DEVICE_EVENT_TYPES,
    DeviceCapabilities,
    DeviceCommand,
    DeviceEventMessage,
    DeviceEventType,
    DeviceGoodbye,
    DeviceHeartbeat,
    DeviceHeartbeatAck,
    DeviceMessageType,
    DeviceRegister,
    DeviceRegistered,
    DeviceResult,
    DeviceResultStatus,
    DeviceStatus,
)

__all__ = [
    "DEVICE_CAPABILITY_STRINGS",
    "DEVICE_EVENT_TYPES",
    "DeviceCapabilities",
    "DeviceCommand",
    "DeviceConnection",
    "DeviceEventMessage",
    "DeviceEventType",
    "DeviceGoodbye",
    "DeviceHeartbeat",
    "DeviceHeartbeatAck",
    "DeviceManager",
    "DeviceMessageType",
    "DeviceRegister",
    "DeviceRegistered",
    "DeviceResult",
    "DeviceResultStatus",
    "DeviceStatus",
    "DeviceStore",
    "DeviceTransport",
    "NullDeviceStore",
    "core_event_for",
    "parse_message",
]
