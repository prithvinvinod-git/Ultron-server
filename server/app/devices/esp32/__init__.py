"""The reference device transport: ESP32 JSON over WebSocket (T162, spec §26).

The §26 device architecture's reference connection — an ESP32 (or any board
speaking the T160 protocol) dials out, registers, heartbeats, reports events and
answers commands over a WebSocket. :mod:`app.devices.esp32.transport` holds the
session (:func:`run_device_session`) and the manager-facing socket transport
(:class:`WebSocketDeviceTransport`); the ``/devices`` route is the thin mount.

Nothing here hard-codes a board (T167's gate): the session is generic
JSON-over-WebSocket, and the package is named for the reference device only
because that is the firmware the protocol was drawn against. A second board
reuses it unchanged.
"""

from __future__ import annotations

from app.devices.esp32.transport import (
    DeviceSocket,
    WebSocketDeviceTransport,
    run_device_session,
)

__all__ = [
    "DeviceSocket",
    "WebSocketDeviceTransport",
    "run_device_session",
]
