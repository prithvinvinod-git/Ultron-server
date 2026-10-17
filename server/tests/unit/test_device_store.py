"""Device persistence tests (T165, spec §26).

The store is exercised against a private in-memory SQLite schema created from
``Base.metadata`` -- the real models, not a hand-built table list -- through the
same ``session_scope`` unit of work production uses. The integration tests then
drive a real :class:`DeviceManager` over the T161 device double and assert that
registering, heartbeating, reporting and going offline all reach the database,
and that a store failure never breaks the live gateway.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import DatabaseSettings
from app.config.settings import DeviceSettings
from app.database.models import Device, DeviceEvent, DeviceStatus
from app.database.repositories.devices import DeviceRepository
from app.database.session import Base, create_engine, create_session_factory, dispose, session_scope
from app.devices.manager import DeviceConnection, DeviceManager, DeviceStore, NullDeviceStore
from app.devices.protocol import DeviceEventMessage, DeviceHeartbeat, DeviceRegister
from app.devices.store import SqlDeviceStore
from app.events.bus import EventBus
from tests.support.device_double import FakeDevice

pytestmark = pytest.mark.unit

SQLITE_URL = "sqlite+aiosqlite://"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """An in-memory database with the production schema and a session factory."""
    engine = create_engine(DatabaseSettings(url=SQLITE_URL))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield create_session_factory(engine)
    finally:
        await dispose(engine)


@pytest.fixture
def store(factory: async_sessionmaker[AsyncSession]) -> SqlDeviceStore:
    return SqlDeviceStore(session_scope=lambda: session_scope(factory))


def _connection(
    device_id: str = "esp32-1",
    *,
    name: str = "Desk ESP32",
    device_type: str = "esp32",
    capabilities: tuple[str, ...] = ("display", "wake_word"),
    status: DeviceStatus = DeviceStatus.ONLINE,
    firmware_version: str | None = "1.0.0",
) -> DeviceConnection:
    device = FakeDevice(device_id=device_id)
    return DeviceConnection(
        device_id=device_id,
        name=name,
        device_type=device_type,
        capabilities=frozenset(capabilities),
        transport=device.transport,
        firmware_version=firmware_version,
        status=status,
    )


async def _rows(factory: async_sessionmaker[AsyncSession]) -> list[Device]:
    async with session_scope(factory) as session:
        return list((await session.execute(select(Device))).scalars().all())


async def _events(factory: async_sessionmaker[AsyncSession]) -> list[DeviceEvent]:
    async with session_scope(factory) as session:
        return list((await session.execute(select(DeviceEvent))).scalars().all())


# --------------------------------------------------------------------------- #
# Store, in isolation
# --------------------------------------------------------------------------- #


class TestSaveDevice:
    async def test_a_device_is_inserted_keyed_by_its_protocol_id(
        self, store: SqlDeviceStore, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await store.save_device(_connection())

        rows = await _rows(factory)
        assert len(rows) == 1
        assert rows[0].metadata_["device_id"] == "esp32-1"
        assert rows[0].name == "Desk ESP32"
        assert rows[0].device_type == "esp32"
        assert rows[0].capabilities == ["display", "wake_word"]
        assert rows[0].status == DeviceStatus.ONLINE

    async def test_a_second_save_updates_the_same_row(
        self, store: SqlDeviceStore, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await store.save_device(_connection())
        await store.save_device(
            _connection(name="Renamed", capabilities=("display",), firmware_version="2.0.0")
        )

        rows = await _rows(factory)
        assert len(rows) == 1
        assert rows[0].name == "Renamed"
        assert rows[0].capabilities == ["display"]
        assert rows[0].firmware_version == "2.0.0"

    async def test_find_by_external_id_round_trips(
        self, store: SqlDeviceStore, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await store.save_device(_connection())
        async with session_scope(factory) as session:
            found = await DeviceRepository(session).find_by_external_id("esp32-1")
            missing = await DeviceRepository(session).find_by_external_id("nope")
        assert found is not None
        assert missing is None


class TestMarkOffline:
    async def test_marking_offline_flips_the_status(
        self, store: SqlDeviceStore, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await store.save_device(_connection())
        await store.mark_offline("esp32-1", reason="heartbeat missed")

        rows = await _rows(factory)
        assert rows[0].status == DeviceStatus.OFFLINE
        assert rows[0].metadata_["last_offline_reason"] == "heartbeat missed"

    async def test_a_disabled_device_stays_disabled(
        self, store: SqlDeviceStore, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await store.save_device(_connection(status=DeviceStatus.DISABLED))
        await store.mark_offline("esp32-1", reason="heartbeat missed")

        rows = await _rows(factory)
        assert rows[0].status == DeviceStatus.DISABLED

    async def test_an_unknown_device_is_a_no_op(
        self, store: SqlDeviceStore, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await store.mark_offline("ghost", reason="heartbeat missed")
        assert await _rows(factory) == []


class TestRecordEvent:
    async def test_an_event_is_appended_to_device_events(
        self, store: SqlDeviceStore, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await store.save_device(_connection())
        await store.record_event(
            DeviceEventMessage(device_id="esp32-1", event_type="sensor_event", payload={"t": 21})
        )

        events = await _events(factory)
        assert len(events) == 1
        assert events[0].event_type == "sensor_event"
        assert events[0].payload == {"t": 21}

    async def test_events_are_numbered_within_the_device(
        self, store: SqlDeviceStore, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await store.save_device(_connection())
        for _ in range(3):
            event = DeviceEventMessage(device_id="esp32-1", event_type="button_press")
            await store.record_event(event)

        sequences = sorted(event.sequence for event in await _events(factory))
        assert sequences == [0, 1, 2]

    async def test_an_event_for_an_unknown_device_is_dropped(
        self, store: SqlDeviceStore, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await store.record_event(DeviceEventMessage(device_id="ghost", event_type="button_press"))
        assert await _events(factory) == []


class TestNullStore:
    async def test_the_null_store_is_a_no_op(self) -> None:
        store = NullDeviceStore()
        await store.save_device(_connection())
        await store.mark_offline("esp32-1", reason="x")
        await store.record_event(DeviceEventMessage(device_id="esp32-1", event_type="button_press"))


# --------------------------------------------------------------------------- #
# The manager, wired to a real store
# --------------------------------------------------------------------------- #


def _manager(store: DeviceStore | None) -> DeviceManager:
    return DeviceManager(
        settings=DeviceSettings(),
        bus=EventBus(),
        store=store,
        require_auth=False,
    )


async def _register(manager: DeviceManager, device: FakeDevice) -> None:
    device.bind(manager)
    await manager.register(DeviceRegister.model_validate(device.registration()), device.transport)


class TestManagerPersistence:
    async def test_registration_persists_the_device(
        self, store: SqlDeviceStore, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        manager = _manager(store)
        await _register(manager, FakeDevice(device_id="esp32-1"))

        rows = await _rows(factory)
        assert len(rows) == 1
        assert rows[0].status == DeviceStatus.ONLINE

    async def test_heartbeat_refreshes_the_row(
        self, store: SqlDeviceStore, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        manager = _manager(store)
        device = FakeDevice(device_id="esp32-1")
        await _register(manager, device)

        await manager.heartbeat(DeviceHeartbeat(device_id="esp32-1", firmware_version="9.9.9"))

        rows = await _rows(factory)
        assert rows[0].firmware_version == "9.9.9"

    async def test_a_reported_event_is_persisted(
        self, store: SqlDeviceStore, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        manager = _manager(store)
        device = FakeDevice(device_id="esp32-1")
        await _register(manager, device)

        message = device.event("sensor_event", payload={"t": 21})
        await manager.report(DeviceEventMessage.model_validate(message))

        events = await _events(factory)
        assert [event.event_type for event in events] == ["sensor_event"]

    async def test_disconnect_persists_offline(
        self, store: SqlDeviceStore, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        manager = _manager(store)
        await _register(manager, FakeDevice(device_id="esp32-1"))

        await manager.disconnect("esp32-1", reason="goodbye")

        rows = await _rows(factory)
        assert rows[0].status == DeviceStatus.OFFLINE

    async def test_a_store_failure_never_breaks_the_gateway(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        class ExplodingStore:
            """A store whose every write fails, to prove the gateway survives."""
            async def save_device(self, device: DeviceConnection) -> None:
                raise RuntimeError("database is down")

            async def mark_offline(self, device_id: str, *, reason: str) -> None:
                raise RuntimeError("database is down")

            async def record_event(self, event: DeviceEventMessage) -> None:
                raise RuntimeError("database is down")

        store: DeviceStore = ExplodingStore()
        manager = _manager(store)
        device = FakeDevice(device_id="esp32-1")

        registered = await manager.register(
            DeviceRegister.model_validate(device.registration()), device.transport
        )

        assert registered.accepted is True
        assert manager.maybe("esp32-1") is not None

    async def test_the_default_store_is_the_null_store(self) -> None:
        manager = _manager(None)
        assert isinstance(manager._store, NullDeviceStore)
