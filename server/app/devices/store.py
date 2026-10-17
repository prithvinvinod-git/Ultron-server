"""Device state persistence: the ``devices`` row and ``device_events`` (T165, §26).

The :class:`~app.devices.manager.DeviceManager` is the live, in-process view of
the device layer; this module is what makes that view durable. It is a **seam**:
the manager calls a tiny :class:`~app.devices.manager.DeviceStore` protocol on
the events it already handles, and the concrete :class:`SqlDeviceStore` writes
through the repositories inside the shared ``session_scope`` unit of work. The
manager never imports SQLAlchemy, so it stays testable with a no-op store and a
device never depends on the database being up to be routed.

Two decisions worth stating:

**The protocol id is the join.** §26 gives a device a free-string ``device_id``
the firmware chooses; the ``devices`` table's key is a UUID (T014). Rather than
mint a second identity, the wire id is stored under ``metadata["device_id"]``
and looked up through a portable JSON scalar query
(:meth:`DeviceRepository.find_by_external_id`). That is forward-compatible with
T332, which gives the unified node registry a first-class ``node_id`` column.

**Persistence never breaks the gateway.** A device that registered, sent a
heartbeat or reported an event must not be dropped because a write failed. Every
store call is issued through :meth:`DeviceManager._persist`, which logs a
``device.store_failed`` diagnostic and continues — the live registry stays
authoritative, and the failure is visible rather than swallowed silently (§64.11).
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Device, DeviceStatus
from app.database.repositories.devices import DeviceEventRepository, DeviceRepository
from app.devices.manager import DeviceConnection
from app.devices.protocol import DeviceEventMessage
from app.observability.logging import get_logger

__all__ = ["DEVICE_ID_KEY", "SessionScope", "SqlDeviceStore"]

_LOGGER = get_logger(__name__)

#: Something that yields a session when entered (``Container.session_scope``).
SessionScope = Callable[[], AbstractAsyncContextManager[AsyncSession]]

#: The metadata key holding a device's protocol id (§26's free-string identity).
DEVICE_ID_KEY = "device_id"


class SqlDeviceStore:
    """Write device state and events through the repositories (T165, §26).

    Each call is one ``session_scope`` unit of work: a read-then-write for the
    device row, an append for an event. The store is deliberately stateless — it
    holds only the session scope — so several managers, or a manager and the API,
    share one durable view.
    """

    def __init__(self, *, session_scope: SessionScope) -> None:
        self._session_scope = session_scope

    async def save_device(self, device: DeviceConnection) -> None:
        """Upsert the ``devices`` row keyed by the protocol ``device_id``."""
        async with self._session_scope() as session:
            repository = DeviceRepository(session)
            row = await repository.find_by_external_id(device.device_id)
            if row is None:
                await repository.add(
                    Device(
                        name=device.name,
                        device_type=device.device_type or None,
                        capabilities=sorted(device.capabilities),
                        status=device.status,
                        last_seen=device.last_seen,
                        firmware_version=device.firmware_version,
                        metadata_={DEVICE_ID_KEY: device.device_id},
                    )
                )
                return
            row.name = device.name
            row.device_type = device.device_type or None
            row.capabilities = sorted(device.capabilities)
            row.status = device.status
            row.last_seen = device.last_seen
            row.firmware_version = device.firmware_version
            row.metadata_ = {**row.metadata_, DEVICE_ID_KEY: device.device_id}

    async def mark_offline(self, device_id: str, *, reason: str) -> None:
        """Flip the row offline, unless it was administratively disabled.

        A revoked device stays ``DISABLED``: a heartbeat or a sweep must not undo
        a revocation, the same rule the repository's ``heartbeat`` follows.
        """
        async with self._session_scope() as session:
            repository = DeviceRepository(session)
            row = await repository.find_by_external_id(device_id)
            if row is None:
                return
            if row.status != DeviceStatus.DISABLED:
                row.status = DeviceStatus.OFFLINE
            row.metadata_ = {**row.metadata_, "last_offline_reason": reason}

    async def record_event(self, event: DeviceEventMessage) -> None:
        """Append one event, auto-numbering it when the device sent no sequence."""
        async with self._session_scope() as session:
            row = await DeviceRepository(session).find_by_external_id(event.device_id)
            if row is None:
                _LOGGER.debug(
                    "dropping event for an unknown device",
                    extra={"event": "device.event_unstored", "device_id": event.device_id},
                )
                return
            await DeviceEventRepository(session).append(
                device_id=row.id,
                event_type=event.event_type,
                sequence=event.sequence or None,
                payload=event.payload,
                audio_path=event.audio_path,
                received_at=event.received_at,
            )
