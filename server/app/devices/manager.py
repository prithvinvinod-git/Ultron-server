"""The device gateway: registry, liveness and command routing (T161, spec §26).

The server side of the §26 device architecture. Devices register, heartbeat,
report events and answer structured commands. It parallels
:class:`~app.agents.computer.gateway.NodeGateway` deliberately -- the same
transport-agnostic shape, the same ``register``/``heartbeat``/``command`` ->
``result`` handshake -- and shares the same identity model, because §26 is
amended to make device records and node records **one registry** (D021): a
device credential authenticates a device, never a user (§30, §64.13).

**The manager is transport-agnostic.** A device's connection is anything that
can ``await send(message)`` and be closed: the ESP32 WebSocket transport (T162),
an MQTT adapter (T163), or a test double. Keeping the socket out of here is why
Core never names a board (§26) and why the whole manager is testable with no
hardware and no network.

What it owns, and what it deliberately does not:

- **Registry, status and ``last_seen``** -- a device announces its capabilities
  and refreshes them; :meth:`DeviceManager.sweep` marks a silent device offline
  so discovery stays live rather than a config file (§64.10).
- **Events** -- an inbound :class:`~app.devices.protocol.DeviceEventMessage` is
  recorded and, when it has a canonical counterpart, published: ``wake_word`` ->
  ``WAKE_DETECTED`` (§25), ``device_online``/``device_offline`` ->
  ``DEVICE_CONNECTED``/``DEVICE_DISCONNECTED`` (§19). ``button_press`` /
  ``sensor_event`` / ``audio_stream`` have no canonical event and are surfaced to
  the caller (the IoT agent, T164) rather than mapped to a catch-all.
- **Command/result correlation** -- each command carries a ``command_id`` and a
  pending future; a matching :class:`~app.devices.protocol.DeviceResult`
  resolves it, and a stray result is reported, not dropped.
- **Typed unavailability** -- a dispatch to an offline or unknown device, or a
  device missing the capability the caller needs, raises
  :class:`~app.core.errors.DeviceError`; a device that answers with a failure
  raises ``ToolError``.

It does **not** perform permission checks: §15's permission decision is the
pipeline's, and a dispatch carries the principal only so the audit row can name
it. Persistence is a **seam** (T165): the manager calls an optional
:class:`DeviceStore` on registration, heartbeat, the transition offline and a
reported event, so the live view is made durable without the manager importing
SQLAlchemy. A store write that fails is logged and swallowed (§64.11) — a device
is never dropped because the database was momentarily unreachable.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Coroutine, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from app.agents.computer.auth import NodeAuthenticator
from app.config.settings import DeviceSettings
from app.core.errors import DeviceError, NotFoundError, ToolError
from app.devices.protocol import (
    DeviceCapabilities,
    DeviceCommand,
    DeviceEventMessage,
    DeviceHeartbeat,
    DeviceHeartbeatAck,
    DeviceRegister,
    DeviceRegistered,
    DeviceResult,
    DeviceResultStatus,
    DeviceStatus,
    core_event_for,
)
from app.events.bus import EventBus
from app.events.types import EventType
from app.observability.logging import get_logger

__all__ = ["DeviceConnection", "DeviceManager", "DeviceStore", "DeviceTransport", "NullDeviceStore"]

_LOGGER = get_logger(__name__)


@runtime_checkable
class DeviceTransport(Protocol):
    """The minimum a device connection must provide.

    The ESP32 WebSocket wraps its ``send_json``/``close``; the test double
    implements it with a queue. The manager never assumes more.
    """

    async def send(self, message: Mapping[str, Any]) -> None:
        """Deliver one wire message to the device."""

    async def close(self) -> None:
        """Close the connection. Idempotent."""


@runtime_checkable
class DeviceStore(Protocol):
    """Durable view of the device layer (T165, §26).

    The manager calls this on registration, heartbeat, offline transition and a
    reported event. :class:`~app.devices.store.SqlDeviceStore` writes it through
    the repositories; :class:`NullDeviceStore` is the default so the manager is
    usable with no database at all. The protocol lives here, beside
    :class:`DeviceTransport`, so both seams the manager codes to are in one place
    and ``store.py`` depends on the manager, never the reverse.
    """

    async def save_device(self, device: DeviceConnection) -> None:
        """Insert or update the row for a registered or refreshed device."""

    async def mark_offline(self, device_id: str, *, reason: str) -> None:
        """Mark the device's row offline, leaving a disabled row disabled."""

    async def record_event(self, event: DeviceEventMessage) -> None:
        """Append one reported event to ``device_events``."""


class NullDeviceStore:
    """A store that does nothing, the manager's honest default (§64.11)."""

    async def save_device(self, device: DeviceConnection) -> None:
        return None

    async def mark_offline(self, device_id: str, *, reason: str) -> None:
        return None

    async def record_event(self, event: DeviceEventMessage) -> None:
        return None


@dataclass(slots=True)
class DeviceConnection:
    """One registered device's live record (spec §26's field list)."""

    device_id: str
    name: str
    device_type: str
    capabilities: frozenset[str]
    transport: DeviceTransport
    firmware_version: str | None = None
    status: DeviceStatus = DeviceStatus.ONLINE
    connected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
    #: Events reported, for the health snapshot.
    events_received: int = 0
    commands_sent: int = 0
    commands_failed: int = 0

    def is_stale(self, *, offline_after: int, now: datetime | None = None) -> bool:
        moment = now or datetime.now(UTC)
        return (moment - self.last_seen).total_seconds() > offline_after

    def to_capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            device_id=self.device_id,
            name=self.name,
            device_type=self.device_type,
            capabilities=sorted(self.capabilities),
            status=self.status,
            last_seen=self.last_seen,
            connected_at=self.connected_at,
            firmware_version=self.firmware_version,
        )


@dataclass
class _Pending:
    future: asyncio.Future[DeviceResult]
    action: str
    command_id: uuid.UUID
    created_at: datetime


class DeviceManager:
    """Registers devices, correlates commands, tracks availability (§26)."""

    def __init__(
        self,
        *,
        settings: DeviceSettings | None = None,
        authenticator: NodeAuthenticator | None = None,
        bus: EventBus | None = None,
        store: DeviceStore | None = None,
        command_timeout: float = 30.0,
        require_auth: bool | None = None,
    ) -> None:
        self._settings = settings or DeviceSettings()
        self._auth = authenticator or NodeAuthenticator()
        self._bus = bus
        self._store: DeviceStore = store or NullDeviceStore()
        self._command_timeout = command_timeout
        self._require_auth = self._settings.require_auth if require_auth is None else require_auth
        self._devices: dict[str, DeviceConnection] = {}
        self._pending: dict[uuid.UUID, _Pending] = {}
        self._monitor: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def settings(self) -> DeviceSettings:
        return self._settings

    # ------------------------------------------------------------------ #
    # Credentials (§26 with §30, §64.13)
    # ------------------------------------------------------------------ #
    def issue_credential(self, device_id: str) -> str:
        """Mint and return a device credential token. Shown once (§30)."""
        return self._auth.issue(device_id).token

    def revoke_credential(self, device_id: str) -> bool:
        return self._auth.revoke(device_id)

    # ------------------------------------------------------------------ #
    # Registration and heartbeat (§26)
    # ------------------------------------------------------------------ #
    async def register(
        self, registration: DeviceRegister, transport: DeviceTransport
    ) -> DeviceRegistered:
        """Register (or re-register) a device and answer with the server's rules.

        Authentication is by the device credential (§26/§30). When auth is
        required and the credential is absent or invalid, the device is refused
        with ``accepted=False`` -- the ``DeviceRegistered`` frame is the
        protocol's refusal channel, because a registration failure is a normal
        handshake outcome a firmware can report, not a server fault.
        """
        if self._require_auth:
            if not registration.credential:
                return self._refuse(registration.device_id, "a device credential is required")
            try:
                self._auth.verify(registration.credential)
            except Exception:
                return self._refuse(registration.device_id, "the device credential was rejected")

        existing = self._devices.get(registration.device_id)
        if existing is None and len(self._devices) >= self._settings.max_connections:
            return self._refuse(registration.device_id, "the device registry is full")

        capabilities = frozenset(registration.capabilities)
        if existing is not None:
            # A reconnecting device: replace the transport, keep accounting.
            await self._close_transport(existing.transport, registration.device_id)
            existing.transport = transport
            existing.capabilities = capabilities
            existing.name = registration.name or existing.name
            existing.device_type = registration.device_type or existing.device_type
            existing.firmware_version = registration.firmware_version or existing.firmware_version
            existing.status = DeviceStatus.ONLINE
            existing.last_seen = datetime.now(UTC)
            device = existing
        else:
            device = DeviceConnection(
                device_id=registration.device_id,
                name=registration.name or registration.device_id,
                device_type=registration.device_type,
                capabilities=capabilities,
                transport=transport,
                firmware_version=registration.firmware_version,
            )
            self._devices[registration.device_id] = device
            await self._publish_connected(device)

        await self._persist("save_device", self._store.save_device(device))
        _LOGGER.info(
            "device registered",
            extra={
                "event": "device.registered",
                "device_id": device.device_id,
                "device_type": device.device_type,
                "capabilities": sorted(device.capabilities),
            },
        )
        return DeviceRegistered(
            accepted=True,
            device_id=device.device_id,
            heartbeat_interval=self._heartbeat_interval(),
            offline_after=self._settings.heartbeat_timeout,
        )

    async def heartbeat(self, heartbeat: DeviceHeartbeat) -> DeviceHeartbeatAck:
        """Record a liveness signal; refresh status and capabilities if given.

        A heartbeat for an unknown device is a typed ``NotFoundError`` rather
        than a silent create: a device that was never registered must go through
        ``register`` so its capabilities and identity are established first.
        """
        device = self._devices.get(heartbeat.device_id)
        if device is None:
            raise NotFoundError("device", heartbeat.device_id)
        was_offline = device.status is DeviceStatus.OFFLINE
        device.last_seen = datetime.now(UTC)
        if heartbeat.status is not None and heartbeat.status is not DeviceStatus.DISABLED:
            device.status = heartbeat.status
        elif was_offline:
            device.status = DeviceStatus.ONLINE
        if heartbeat.firmware_version is not None:
            device.firmware_version = heartbeat.firmware_version
        if heartbeat.capabilities is not None:
            device.capabilities = frozenset(heartbeat.capabilities)
        if was_offline:
            await self._publish_connected(device)
        await self._persist("save_device", self._store.save_device(device))
        return DeviceHeartbeatAck(device_id=device.device_id)

    # ------------------------------------------------------------------ #
    # Events (§26)
    # ------------------------------------------------------------------ #
    async def report(self, event: DeviceEventMessage) -> bool:
        """Record one device event and publish its canonical counterpart.

        Returns whether a canonical core event was published: ``wake_word``,
        ``device_online`` and ``device_offline`` have one; ``button_press``,
        ``sensor_event`` and ``audio_stream`` do not, and the caller (the IoT
        agent, T164) handles them directly. A ``device_online``/``device_offline``
        event also moves the device's status, so liveness does not depend on a
        separate heartbeat.
        """
        device = self._devices.get(event.device_id)
        if device is None:
            raise NotFoundError("device", event.device_id)
        device.last_seen = datetime.now(UTC)
        device.events_received += 1
        await self._persist("record_event", self._store.record_event(event))

        core_event = core_event_for(event.event_type)
        if core_event is EventType.DEVICE_CONNECTED:
            device.status = DeviceStatus.ONLINE
        elif core_event is EventType.DEVICE_DISCONNECTED:
            await self._mark_offline(device, reason="device reported offline")
            return True

        if core_event is None:
            return False
        await self._publish_event(core_event, device, event)
        return True

    # ------------------------------------------------------------------ #
    # Dispatch and correlation (§26 command/response)
    # ------------------------------------------------------------------ #
    async def dispatch(
        self,
        device_id: str,
        action: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        principal: str | None = None,
        limit: float | None = None,
        correlation_id: str | None = None,
        capability: str | None = None,
    ) -> Any:
        """Send one structured command and await its correlated result.

        Raises :class:`DeviceError` when the device is unknown, offline, missing
        the capability the caller needs, or does not answer within ``limit``
        seconds. Raises :class:`ToolError` when the device answered but reported
        a failure -- the command reached the device and failed there.
        """
        if self._closed:
            raise DeviceError(f"the device gateway is shutting down; '{action}' not sent")
        device = self._devices.get(device_id)
        if device is None:
            raise DeviceError(f"no such device: {device_id!r}")
        if device.status is DeviceStatus.OFFLINE:
            raise DeviceError(f"device {device_id!r} is offline")
        if capability is not None and capability not in device.capabilities:
            raise DeviceError(f"device {device_id!r} does not advertise capability {capability!r}")

        within = limit if limit is not None else self._command_timeout
        command = DeviceCommand(
            command_id=uuid.uuid4(),
            device_id=device_id,
            action=action,
            arguments=dict(arguments or {}),
            principal=principal,
            deadline=_deadline(within),
            correlation_id=correlation_id,
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[DeviceResult] = loop.create_future()
        self._pending[command.command_id] = _Pending(
            future=future,
            action=action,
            command_id=command.command_id,
            created_at=datetime.now(UTC),
        )
        device.commands_sent += 1
        try:
            await device.transport.send(command.model_dump(mode="json"))
            result = await asyncio.wait_for(future, timeout=within)
        except TimeoutError as exc:
            device.commands_failed += 1
            raise DeviceError(f"device {device_id!r} did not answer within {within:g}s") from exc
        except (OSError, ConnectionError) as exc:
            device.commands_failed += 1
            raise DeviceError(f"the device {device_id!r} connection dropped") from exc
        finally:
            self._pending.pop(command.command_id, None)

        if result.status is DeviceResultStatus.ERROR:
            device.commands_failed += 1
            raise ToolError(
                action,
                result.error or f"device '{device_id}' reported a failure",
                details={"device": device_id, "error_code": result.error_code},
            )
        return result.result

    async def deliver(self, result: DeviceResult) -> bool:
        """Resolve the pending command for ``result.command_id``.

        Returns whether a waiter was found. An unknown or late result is logged
        and dropped rather than raising: a device cannot be trusted to be
        well-behaved, and a stray result must not kill the connection reader.
        """
        pending = self._pending.get(result.command_id)
        if pending is None or pending.future.done():
            _LOGGER.warning(
                "unmatched device result",
                extra={
                    "event": "device.result_unmatched",
                    "device_id": result.device_id,
                    "command_id": str(result.command_id),
                },
            )
            return False
        pending.future.set_result(result)
        return True

    # ------------------------------------------------------------------ #
    # Availability
    # ------------------------------------------------------------------ #
    async def disconnect(self, device_id: str, *, reason: str = "") -> bool:
        """Mark a device offline and fail any waiters (spec §26/§19).

        The record is **retained**, not removed, exactly as the node gateway
        retains an offline node: ``GET /devices`` must keep showing a board that
        is merely unreachable (§64.10), with its capabilities and ``last_seen``
        intact, and a reconnect updates the same row rather than creating a
        second one.
        """
        device = self._devices.get(device_id)
        if device is None:
            return False
        await self._mark_offline(device, reason=reason)
        return True

    async def sweep(self, *, now: datetime | None = None) -> list[str]:
        """Mark every device past its heartbeat window offline. Returns their ids.

        Exposed (not only used by the monitor task) so availability is testable
        deterministically by advancing a clock rather than sleeping.
        """
        moment = now or datetime.now(UTC)
        offline: list[str] = []
        for device in list(self._devices.values()):
            if device.status is DeviceStatus.ONLINE and device.is_stale(
                offline_after=self._settings.heartbeat_timeout, now=moment
            ):
                await self._mark_offline(device, reason="heartbeat missed")
                offline.append(device.device_id)
        return offline

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def get(self, device_id: str) -> DeviceConnection:
        device = self._devices.get(device_id)
        if device is None:
            raise NotFoundError("device", device_id)
        return device

    def maybe(self, device_id: str) -> DeviceConnection | None:
        return self._devices.get(device_id)

    def list_devices(self) -> list[DeviceConnection]:
        return [self._devices[key] for key in sorted(self._devices)]

    def find_by_capability(
        self, capability: str, *, online_only: bool = True
    ) -> list[DeviceConnection]:
        """Devices advertising ``capability``, online-first then by id."""
        matches = [
            device
            for device in self._devices.values()
            if capability in device.capabilities
            and (not online_only or device.status is not DeviceStatus.OFFLINE)
        ]
        matches.sort(key=lambda d: (d.status is DeviceStatus.OFFLINE, d.device_id))
        return matches

    @property
    def online_count(self) -> int:
        return sum(1 for device in self._devices.values() if device.status is DeviceStatus.ONLINE)

    def snapshot(self) -> dict[str, Any]:
        return {
            "devices": len(self._devices),
            "online": self.online_count,
            "pending": len(self._pending),
            "by_type": _count_types(self._devices.values()),
        }

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        """Start the heartbeat monitor. Idempotent."""
        if self._monitor is None or self._monitor.done():
            self._monitor = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        """Stop monitoring and close every connection. Idempotent."""
        self._closed = True
        if self._monitor is not None:
            self._monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor
            self._monitor = None
        for command_id, pending in list(self._pending.items()):
            if not pending.future.done():
                pending.future.cancel()
            self._pending.pop(command_id, None)
        for device in list(self._devices.values()):
            await self._close_transport(device.transport, device.device_id)
        self._devices.clear()

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _heartbeat_interval(self) -> int:
        # Half the timeout, so one missed interval does not immediately offline
        # a device -- the same relationship the node gateway uses.
        return max(self._settings.heartbeat_timeout // 2, 1)

    async def _monitor_loop(self) -> None:
        interval = max(self._heartbeat_interval(), 1)
        while not self._closed:
            await asyncio.sleep(interval)
            try:
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - monitor must survive
                _LOGGER.exception("device heartbeat monitor iteration failed")

    async def _mark_offline(self, device: DeviceConnection, *, reason: str) -> None:
        if device.status is DeviceStatus.OFFLINE:
            return
        device.status = DeviceStatus.OFFLINE
        for command_id, pending in list(self._pending.items()):
            if not pending.future.done():
                pending.future.set_exception(
                    DeviceError(f"device {device.device_id!r} went offline")
                )
                self._pending.pop(command_id, None)
        await self._close_transport(device.transport, device.device_id)
        await self._persist(
            "mark_offline", self._store.mark_offline(device.device_id, reason=reason)
        )
        await self._publish_event(
            EventType.DEVICE_DISCONNECTED, device, None, extra={"reason": reason}
        )

    async def _persist(self, action: str, operation: Coroutine[Any, Any, None]) -> None:
        """Run one store call, logging rather than raising on failure (§64.11).

        The live registry is authoritative; a store that is briefly unreachable
        must not drop a device or fail a heartbeat. The failure is logged with
        the action so persistence gaps are visible, not silent.
        """
        try:
            await operation
        except Exception:
            _LOGGER.warning(
                "device persistence failed",
                extra={"event": "device.store_failed", "action": action},
            )

    async def _close_transport(self, transport: DeviceTransport, device_id: str) -> None:
        try:
            await transport.close()
        except Exception:  # pragma: no cover - a dead socket is expected
            _LOGGER.debug("device transport close failed", extra={"device_id": device_id})

    def _refuse(self, device_id: str, reason: str) -> DeviceRegistered:
        _LOGGER.warning(
            "device registration refused",
            extra={"event": "device.register_refused", "device_id": device_id, "reason": reason},
        )
        return DeviceRegistered(
            accepted=False,
            device_id=device_id,
            reason=reason,
            heartbeat_interval=self._heartbeat_interval(),
            offline_after=self._settings.heartbeat_timeout,
        )

    async def _publish_connected(self, device: DeviceConnection) -> None:
        await self._publish_event(EventType.DEVICE_CONNECTED, device, None)

    async def _publish_event(
        self,
        event_type: EventType,
        device: DeviceConnection,
        event: DeviceEventMessage | None,
        *,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        if self._bus is None:
            return
        payload: dict[str, Any] = {
            "device_id": device.device_id,
            "device_type": device.device_type,
            "name": device.name,
            "capabilities": sorted(device.capabilities),
        }
        if event is not None:
            payload["event_type"] = event.event_type
            if event.payload is not None:
                payload["data"] = event.payload
        if extra:
            payload.update(extra)
        await self._bus.publish(event_type, payload, device_id=device.device_id)


def _deadline(seconds: float) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=seconds)


def _count_types(devices: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for device in devices:
        key = device.device_type or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts
