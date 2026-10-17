"""Device routes (T162, spec §26/§29).

Two read endpoints and one WebSocket:

- ``GET /devices`` — every registered device's §26 fields: ``device_id``,
  ``name``, ``device_type``, ``capabilities``, ``status``, ``last_seen`` and
  ``firmware_version``. Discovery is live, not a config file (§64.10).
- ``GET /devices/{id}`` — one device, or ``404``/``NOT_FOUND``.
- ``WS /devices/ws`` — a device's connection. It dials *out* to the server
  (§59.15/§64.6.4: no inbound LAN listener), presents its credential in the
  opening ``register``, and then speaks the T160 protocol: heartbeats, events
  and command results up; commands down.

**Why the WebSocket is not behind the user ``Auth`` dependency.** A device
credential authenticates *a device, never a user* (§30/§64.13) — different
principals. The device presents the credential inside ``register`` and the
manager is what verifies it, with the same typed refusal path a wrong token gets
everywhere. Requiring a user token first would put a human session in front of a
machine identity, the conflation §64.13 forbids.

The read endpoints *are* authenticated like every other route (§15/§29). The
session itself lives in :mod:`app.devices.esp32.transport`, so this module stays a
thin mount — the transport is exercised without a socket in the device tests.
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.dependencies import Auth, Container
from app.core.errors import InvalidInputError
from app.devices.esp32.transport import run_device_session
from app.devices.protocol import DeviceCapabilities
from app.observability.logging import get_logger

_LOGGER = get_logger(__name__)

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("", response_model=list[DeviceCapabilities], summary="List registered devices")
async def list_devices(container: Container, principal: Auth) -> list[DeviceCapabilities]:
    """Return every registered device, sorted by device id (§26)."""
    return [device.to_capabilities() for device in container.device_manager.list_devices()]


@router.get("/{device_id}", response_model=DeviceCapabilities, summary="Describe one device")
async def get_device(device_id: str, container: Container, principal: Auth) -> DeviceCapabilities:
    """Return one device's fields and status, or ``404``/``NOT_FOUND``."""
    return container.device_manager.get(device_id).to_capabilities()


@router.websocket("/ws")
async def device_socket(websocket: WebSocket) -> None:
    """Accept a device's outbound connection and run the T160 protocol (§26).

    When the device subsystem is disabled the socket is refused rather than
    half-served, so disabling devices is a real deployment switch and not just a
    note in the docs. Every exit path disconnects the device in the session's
    ``finally`` so its capabilities stop being offered (§64.10).
    """
    await websocket.accept()
    container = getattr(websocket.app.state, "container", None)
    if container is None:
        await websocket.close(code=1011)
        return
    manager = container.device_manager
    if not manager.settings.enabled:
        _LOGGER.warning("device connection refused: devices are disabled")
        await websocket.close(code=1008)
        return
    try:
        await run_device_session(websocket, manager)
    except (WebSocketDisconnect, RuntimeError):
        pass
    except InvalidInputError as exc:
        _LOGGER.warning(
            "device sent a frame the session rejected",
            extra={"event": "device.invalid_frame", "detail": str(exc)},
        )


__all__ = ["router"]
