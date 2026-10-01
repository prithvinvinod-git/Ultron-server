"""Device repositories (T015).

Three security-relevant behaviours.

**Authentication is by token hash, never by token.**
:func:`DeviceRepository.authenticate` takes the hash. A method that accepted a
plaintext token would put a bearer credential one careless log statement away
from being persisted.

**A disabled device is refused even with a valid token.** ``authenticate`` checks
the token *and* the status, because a credential that outlives the revocation of
the device it belongs to is exactly the case where the hash still matches and
access should not be granted.

**Refusals are indistinguishable.** A bad token, an unknown token, a disabled
device and an offline device all return ``None``. Distinguishing them would tell
an attacker which device identifiers exist.

One known limitation: ``DeviceStatus`` mixes connection state (``ONLINE`` /
``OFFLINE``) with administrative state (``DISABLED``). The spec does not define
the table's columns, and T014 settled on this column set, so the conflation
stands for now -- it means "revoked" and "not currently reachable" are
distinguished by an operator rather than enforced separately. Adding a separate
boolean is a T016 schema change, not a repository concern.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select

from app.core.errors import CapabilityNotImplementedError
from app.database.models import Device, DeviceEvent, DeviceStatus
from app.database.repositories.base import UuidRepository, apply_limit, apply_offset

#: Statuses in which a device may still be reached, for the authenticate check.
REACHABLE_STATUSES: tuple[DeviceStatus, ...] = (
    DeviceStatus.ONLINE,
    DeviceStatus.UNKNOWN,
)


class DeviceRepository(UuidRepository[Device]):
    """Devices."""

    model = Device

    async def authenticate(self, token_hash: str) -> Device | None:
        """Return the reachable device whose token hashes to ``token_hash``.

        Takes the hash, never the token. Returns ``None`` for a bad token, an
        unknown token, a disabled device and an offline device alike -- one
        indistinguishable answer, so the response cannot be used to learn which
        of those it was.
        """
        statement = select(Device).where(Device.auth_token_hash == token_hash)
        device = await self._fetch_one(statement)
        if device is None or device.status not in REACHABLE_STATUSES:
            return None
        return device

    async def list_online(self, *, limit: int | None = None) -> list[Device]:
        """Return devices currently marked online."""
        statement = select(Device).where(Device.status == DeviceStatus.ONLINE).order_by(Device.name)
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_by_capability(
        self, capability: str, *, limit: int | None = None
    ) -> list[Device]:
        """Return devices advertising one capability.

        Not implemented here, and it says so. ``capabilities`` is a JSON array, so
        the containment test has to happen in SQL, and the operator differs by
        dialect: JSONB containment on PostgreSQL, ``json_each`` on SQLite. A
        single-dialect guess would pass the unit tests and return the wrong rows
        in production.

        :class:`~app.core.errors.CapabilityNotImplementedError` is used rather
        than ``NotImplementedError`` so a caller that reaches this before the
        PostgreSQL work lands gets an Ultron error it can handle, not a bare
        builtin that escapes the normal error mapping.
        """
        raise CapabilityNotImplementedError(
            f"capability filtering for {capability!r} needs a dialect-specific "
            "JSONB containment query"
        )

    async def heartbeat(
        self,
        identifier: uuid.UUID | str,
        *,
        now: datetime | None = None,
        firmware_version: str | None = None,
    ) -> Device:
        """Record that a device is alive right now.

        Flips an offline device back to online, because a device that has just
        spoken to us is by definition reachable. Firmware is only overwritten
        when supplied, so a plain heartbeat does not blank a version someone
        else recorded.

        A disabled device is left disabled. A device that reports in while
        revoked has not been re-authorised, and letting a heartbeat undo a
        revocation would make ``disable`` meaningless.
        """
        device = await self.get_required(identifier)
        stamp = now or datetime.now(UTC)
        device.last_seen = stamp
        if device.status == DeviceStatus.OFFLINE:
            device.status = DeviceStatus.ONLINE
        if firmware_version is not None:
            device.firmware_version = firmware_version
        await self._session.flush()
        return device

    async def set_status(self, identifier: uuid.UUID | str, status: DeviceStatus) -> Device:
        """Set a device's status."""
        device = await self.get_required(identifier)
        device.status = status
        await self._session.flush()
        return device

    async def disable(self, identifier: uuid.UUID | str) -> Device:
        """Disable a device, invalidating its token immediately.

        Idempotent, and the status alone is what invalidates it -- there is no
        separate flag to leave inconsistent with the status.
        """
        device = await self.get_required(identifier)
        device.status = DeviceStatus.DISABLED
        await self._session.flush()
        return device

    async def enable(self, identifier: uuid.UUID | str) -> Device:
        """Re-enable a device, leaving its existing token in place.

        Re-enabling does not mint a new token. If a device was disabled because it
        was lost, the caller should rotate the credential separately; silently
        resurrecting the old one would undo the point of disabling it. The status
        goes to ``UNKNOWN`` rather than ``ONLINE`` because a re-enabled device has
        not yet been heard from, and claiming it is online would put it in the
        online listing before it has proved anything.
        """
        device = await self.get_required(identifier)
        if device.status == DeviceStatus.DISABLED:
            device.status = DeviceStatus.UNKNOWN
        await self._session.flush()
        return device

    async def list_stale(
        self,
        *,
        before: datetime,
        limit: int | None = None,
    ) -> list[Device]:
        """Return devices last seen before ``before``, oldest silence first.

        A required bound, because "which devices have gone quiet" has no useful
        unbounded answer. Disabled devices are included: knowing that a revoked
        device is still transmitting is exactly the signal this query exists to
        surface.
        """
        statement = (
            select(Device)
            .where(or_(Device.last_seen.is_(None), Device.last_seen < before))
            .order_by(Device.last_seen)
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def count_by_status(self) -> dict[str, int]:
        """Return a device count per status, for a health endpoint.

        One grouped query rather than one per enum member, so every count comes
        from a single consistent read.
        """
        statement = select(Device.status, func.count()).group_by(Device.status)
        result = await self._session.execute(statement)
        return {str(status): int(count) for status, count in result.all()}


class DeviceEventRepository(UuidRepository[DeviceEvent]):
    """Device events, appended in arrival order."""

    model = DeviceEvent

    async def append(
        self,
        *,
        device_id: uuid.UUID,
        event_type: str,
        sequence: int | None = None,
        payload: dict[str, object] | None = None,
        audio_path: str | None = None,
        received_at: datetime | None = None,
    ) -> DeviceEvent:
        """Append one device event, numbering it within the device.

        Per-device rather than global, because a device that reconnects may
        restart its own numbering. Two appends that race on the same number
        surface as a ``ConflictError`` from the flush, which the caller can
        resolve by deduplicating on the device's own sequence rather than by
        retrying blindly.
        """
        if sequence is None:
            statement = select(func.max(DeviceEvent.sequence)).where(
                DeviceEvent.device_id == device_id
            )
            result = await self._session.execute(statement)
            highest = result.scalar_one()
            sequence = 0 if highest is None else int(highest) + 1

        event = DeviceEvent(
            device_id=device_id,
            event_type=event_type,
            sequence=sequence,
            payload=payload,
            audio_path=audio_path,
            received_at=received_at or datetime.now(UTC),
        )
        return await self.add(event)

    async def list_for_device(
        self,
        device_id: uuid.UUID,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[DeviceEvent]:
        """Return a device's events in arrival order."""
        statement = (
            select(DeviceEvent)
            .where(DeviceEvent.device_id == device_id)
            .order_by(DeviceEvent.sequence)
        )
        return await self._fetch_all(apply_limit(apply_offset(statement, offset), limit))

    async def latest_for_device(self, device_id: uuid.UUID) -> DeviceEvent | None:
        """Return the most recent event, or ``None`` if there is none.

        A single-row query rather than a bounded list, so a caller checking "has
        this device said anything" does not fetch a page to read its last element.
        """
        statement = (
            select(DeviceEvent)
            .where(DeviceEvent.device_id == device_id)
            .order_by(DeviceEvent.sequence.desc())
            .limit(1)
        )
        return await self._fetch_one(statement)

    async def count_for_device(self, device_id: uuid.UUID) -> int:
        """Count a device's events, for a retention decision."""
        statement = (
            select(func.count()).select_from(DeviceEvent).where(DeviceEvent.device_id == device_id)
        )
        result = await self._session.execute(statement)
        return int(result.scalar_one() or 0)


__all__ = ["REACHABLE_STATUSES", "DeviceEventRepository", "DeviceRepository"]
