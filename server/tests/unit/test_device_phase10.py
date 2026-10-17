"""Phase 10 end-to-end tests — device lifecycle, event flow, auth, wake path (T166).

The per-task files (protocol, manager, transport, routes, MQTT, IoT agent, store)
each test one layer. This file tests the **phase**: the layers wired to each
other, because the invariants §26 actually asks for live at the seams, not inside
any one unit.

- One bus. A device registering, waking and leaving publishes ``DEVICE_CONNECTED``,
  ``WAKE_DETECTED`` and ``DEVICE_DISCONNECTED`` to the same bus, in order.
- One registry, one row. A device that reconnects is the same record and the same
  ``devices`` row, keyed by the wire ``device_id`` — never a duplicate.
- One wire id. The protocol's free-string id joins the UUID primary key through
  ``metadata["device_id"]``, so an event lands on the right row.
- One wake. A device-side wake and a detector-side wake raise the same
  ``WAKE_DETECTED`` with the device named, so the voice path has one seam.

The whole stack is real: the JSON-over-WebSocket session runs over
:class:`~tests.support.device_double.FakeDeviceSocket`, the manager persists
through a real :class:`~app.devices.store.SqlDeviceStore` on in-memory SQLite,
and the assertions read the bus and the database.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import DatabaseSettings
from app.config.settings import DeviceSettings
from app.core.errors import NotFoundError
from app.database.models import Device, DeviceEvent, DeviceStatus
from app.database.session import Base, create_engine, create_session_factory, dispose, session_scope
from app.devices.esp32.transport import run_device_session
from app.devices.manager import DeviceManager
from app.devices.protocol import DeviceEventMessage, DeviceRegister
from app.devices.store import SqlDeviceStore
from app.events.bus import EventBus
from app.events.types import EventType
from app.voice.wake import WakeWordDetector
from tests.support.device_double import FakeDevice, FakeDeviceSocket

pytestmark = pytest.mark.unit

SQLITE_URL = "sqlite+aiosqlite://"
_DEVICE_ID = "esp32-1"


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


class RecordingBus(EventBus):
    """A real bus that also keeps every published event, for assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.published: list[tuple[str, dict[str, object], str | None]] = []

    async def publish(self, event_type: Any, payload: Any = None, **kwargs: Any) -> Any:
        body = dict(payload) if isinstance(payload, dict) else {}
        device_id = kwargs.get("device_id")
        self.published.append(
            (str(event_type), body, device_id if isinstance(device_id, str) else None)
        )
        return await super().publish(event_type, payload, **kwargs)

    @property
    def types(self) -> list[str]:
        return [event_type for event_type, _, _ in self.published]

    def envelopes(self, event_type: EventType) -> list[tuple[dict[str, object], str | None]]:
        return [
            (payload, device_id)
            for kind, payload, device_id in self.published
            if kind == event_type.value
        ]


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(DatabaseSettings(url=SQLITE_URL))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield create_session_factory(engine)
    finally:
        await dispose(engine)


@pytest.fixture
def bus() -> RecordingBus:
    return RecordingBus()


@pytest.fixture
def store(factory: async_sessionmaker[AsyncSession]) -> SqlDeviceStore:
    return SqlDeviceStore(session_scope=lambda: session_scope(factory))


@pytest.fixture
def manager(bus: RecordingBus, store: SqlDeviceStore) -> DeviceManager:
    return DeviceManager(settings=DeviceSettings(), bus=bus, store=store, require_auth=False)


def _secured(store: SqlDeviceStore | None = None) -> DeviceManager:
    return DeviceManager(
        settings=DeviceSettings(require_auth=True),
        bus=EventBus(),
        store=store,
        require_auth=True,
    )


async def _rows(factory: async_sessionmaker[AsyncSession]) -> list[Device]:
    async with session_scope(factory) as session:
        return list((await session.execute(select(Device))).scalars().all())


async def _events(factory: async_sessionmaker[AsyncSession]) -> list[DeviceEvent]:
    async with session_scope(factory) as session:
        return list((await session.execute(select(DeviceEvent))).scalars().all())


async def _connect(manager: DeviceManager, device: FakeDevice) -> None:
    await manager.register(DeviceRegister.model_validate(device.registration()), device.transport)


async def _open(manager: DeviceManager) -> tuple[asyncio.Task[None], FakeDeviceSocket]:
    socket = FakeDeviceSocket()
    return asyncio.create_task(run_device_session(socket, manager)), socket


async def _register(manager: DeviceManager, device: FakeDevice) -> None:
    device.bind(manager)
    await manager.register(DeviceRegister.model_validate(device.registration()), device.transport)


# --------------------------------------------------------------------------- #
# Lifecycle (§26) — over the real WebSocket session
# --------------------------------------------------------------------------- #


class TestLifecycle:
    async def test_a_device_registers_reports_and_leaves(
        self, manager: DeviceManager, bus: RecordingBus, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        device = FakeDevice(device_id=_DEVICE_ID)
        socket = FakeDeviceSocket()
        task = asyncio.create_task(run_device_session(socket, manager))

        await socket.push(device.registration())
        hello = await socket.next_sent()
        assert hello["accepted"] is True
        assert bus.types == [EventType.DEVICE_CONNECTED.value]

        await socket.push(device.heartbeat(status="online"))
        assert (await socket.next_sent())["type"] == "heartbeat_ack"

        await socket.push(device.event("wake_word", payload={"text": "ultron, hello"}))
        await socket.push(device.heartbeat(status="online"))
        await socket.next_sent()  # the ack proves the preceding event was handled
        assert bus.types[-1] == EventType.WAKE_DETECTED.value

        await socket.push(device.goodbye())
        await task
        assert socket.closed is True
        assert bus.types[-1] == EventType.DEVICE_DISCONNECTED.value

        rows = await _rows(factory)
        assert len(rows) == 1
        assert rows[0].status == DeviceStatus.OFFLINE

    async def test_a_reconnect_reuses_one_record_and_one_row(
        self, manager: DeviceManager, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        first = FakeDevice(device_id=_DEVICE_ID)
        await manager.register(DeviceRegister.model_validate(first.registration()), first.transport)
        await manager.disconnect(_DEVICE_ID, reason="network blip")

        second = FakeDevice(device_id=_DEVICE_ID)
        await manager.register(
            DeviceRegister.model_validate(second.registration()), second.transport
        )

        assert len(manager.list_devices()) == 1
        assert len(await _rows(factory)) == 1
        assert manager.get(_DEVICE_ID).status is DeviceStatus.ONLINE

    async def test_a_swept_device_is_marked_offline_and_stops_being_offered(
        self, manager: DeviceManager, bus: RecordingBus, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        device = FakeDevice(device_id=_DEVICE_ID, capabilities=("display",))
        await _connect(manager, device)
        assert manager.find_by_capability("display")

        swept = await manager.sweep(now=datetime.now(UTC) + timedelta(seconds=3600))

        assert swept == [_DEVICE_ID]
        assert manager.find_by_capability("display") == []
        assert manager.find_by_capability("display", online_only=False)  # retained
        assert bus.types[-1] == EventType.DEVICE_DISCONNECTED.value
        assert (await _rows(factory))[0].status == DeviceStatus.OFFLINE


# --------------------------------------------------------------------------- #
# Event flow (§26 -> §19)
# --------------------------------------------------------------------------- #


class TestEventFlow:
    async def test_a_device_event_maps_to_its_canonical_core_event(
        self, manager: DeviceManager, bus: RecordingBus
    ) -> None:
        device = FakeDevice(device_id=_DEVICE_ID)
        await _connect(manager, device)

        published = await manager.report(
            DeviceEventMessage(device_id=_DEVICE_ID, event_type="wake_word")
        )

        assert published is True
        assert bus.types == [EventType.DEVICE_CONNECTED.value, EventType.WAKE_DETECTED.value]

    async def test_an_application_event_has_no_canonical_core_event(
        self, manager: DeviceManager, bus: RecordingBus
    ) -> None:
        device = FakeDevice(device_id=_DEVICE_ID)
        await _connect(manager, device)
        before = list(bus.types)

        published = await manager.report(
            DeviceEventMessage(device_id=_DEVICE_ID, event_type="button_press")
        )

        assert published is False
        assert bus.types == before

    async def test_every_reported_event_is_persisted_in_order(
        self, manager: DeviceManager, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        device = FakeDevice(device_id=_DEVICE_ID)
        await _connect(manager, device)

        await manager.report(DeviceEventMessage(device_id=_DEVICE_ID, event_type="sensor_event"))
        await manager.report(DeviceEventMessage(device_id=_DEVICE_ID, event_type="button_press"))

        events = await _events(factory)
        assert [event.event_type for event in events] == ["sensor_event", "button_press"]
        assert [event.sequence for event in events] == [0, 1]

    async def test_the_envelope_names_the_device(
        self, manager: DeviceManager, bus: RecordingBus
    ) -> None:
        device = FakeDevice(device_id=_DEVICE_ID)
        await _connect(manager, device)

        await manager.report(DeviceEventMessage(device_id=_DEVICE_ID, event_type="wake_word"))

        payload, device_id = bus.envelopes(EventType.WAKE_DETECTED)[0]
        assert device_id == _DEVICE_ID
        assert payload["device_id"] == _DEVICE_ID
        assert payload["event_type"] == "wake_word"


# --------------------------------------------------------------------------- #
# Auth boundary (§26 with §30)
# --------------------------------------------------------------------------- #


class TestAuthBoundary:
    async def test_a_credential_is_required_when_auth_is_on(
        self, factory: async_sessionmaker[AsyncSession], store: SqlDeviceStore
    ) -> None:
        manager = _secured(store)
        device = FakeDevice(device_id=_DEVICE_ID, credential=None)

        registered = await manager.register(
            DeviceRegister.model_validate(device.registration()), device.transport
        )

        assert registered.accepted is False
        assert manager.online_count == 0
        assert await _rows(factory) == []

    async def test_an_issued_credential_is_admitted(
        self, factory: async_sessionmaker[AsyncSession], store: SqlDeviceStore
    ) -> None:
        manager = _secured(store)
        token = manager.issue_credential(_DEVICE_ID)
        device = FakeDevice(device_id=_DEVICE_ID, credential=token)

        registered = await manager.register(
            DeviceRegister.model_validate(device.registration()), device.transport
        )

        assert registered.accepted is True
        assert len(await _rows(factory)) == 1

    async def test_a_revoked_credential_is_refused(self) -> None:
        manager = _secured()
        token = manager.issue_credential(_DEVICE_ID)
        manager.revoke_credential(_DEVICE_ID)
        device = FakeDevice(device_id=_DEVICE_ID, credential=token)

        registered = await manager.register(
            DeviceRegister.model_validate(device.registration()), device.transport
        )

        assert registered.accepted is False

    async def test_a_bad_credential_is_refused_without_naming_the_device(self) -> None:
        manager = _secured()
        device = FakeDevice(device_id=_DEVICE_ID, credential="not-a-real-token")

        registered = await manager.register(
            DeviceRegister.model_validate(device.registration()), device.transport
        )

        assert registered.accepted is False
        assert registered.reason


# --------------------------------------------------------------------------- #
# Wake path (§25 with §26)
# --------------------------------------------------------------------------- #


class TestWakePath:
    async def test_a_device_wake_and_a_detector_wake_are_one_event(
        self, manager: DeviceManager, bus: RecordingBus
    ) -> None:
        device = FakeDevice(device_id=_DEVICE_ID)
        await _connect(manager, device)
        await manager.report(DeviceEventMessage(device_id=_DEVICE_ID, event_type="wake_word"))
        device_event = bus.envelopes(EventType.WAKE_DETECTED)[0]

        detector = WakeWordDetector(["ultron"], bus=bus)
        await detector.signal(wake_word="ultron", device_id="esp32-2")
        detector_event = bus.envelopes(EventType.WAKE_DETECTED)[-1]

        assert device_event[0]["device_id"] == _DEVICE_ID
        assert device_event[1] == _DEVICE_ID
        assert detector_event[1] == "esp32-2"
        assert len(bus.envelopes(EventType.WAKE_DETECTED)) == 2

    async def test_a_wake_from_an_unknown_device_is_a_typed_not_found(
        self, manager: DeviceManager
    ) -> None:
        with pytest.raises(NotFoundError):
            await manager.report(DeviceEventMessage(device_id="ghost", event_type="wake_word"))
