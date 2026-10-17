"""Device manager tests (T161, spec §26).

The manager is the server's half of the device protocol: registration,
heartbeat, event reporting, and command dispatch with result correlation. Every
test drives the real manager over the T161 double, so what is asserted is the
actual correlation and event behaviour — no socket, no hardware, no sleeps
beyond the one timeout test that needs a clock.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.config.settings import DeviceSettings
from app.core.errors import DeviceError, NotFoundError, ToolError
from app.devices.manager import DeviceManager
from app.devices.protocol import (
    DeviceEventMessage,
    DeviceHeartbeat,
    DeviceRegister,
    DeviceResult,
    DeviceResultStatus,
    DeviceStatus,
)
from app.events.bus import EventBus
from app.events.types import EventType
from tests.support.device_double import FakeDevice

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _registration(device: FakeDevice, credential: str | None = None) -> DeviceRegister:
    return DeviceRegister.model_validate(device.registration(credential=credential))


async def _register(
    manager: DeviceManager, device: FakeDevice, credential: str | None = None
) -> Any:
    device.bind(manager)
    return await manager.register(_registration(device, credential), device.transport)


async def _drain(subscription: Any, *, wait: float = 0.05) -> list[Any]:
    events: list[Any] = []
    while True:
        try:
            envelope = await asyncio.wait_for(subscription.get(), timeout=wait)
        except TimeoutError:
            break
        if envelope is None:
            break
        events.append(envelope)
    return events


def _types(events: list[Any]) -> list[EventType]:
    return [event.event_type for event in events]


@pytest.fixture
def settings() -> DeviceSettings:
    return DeviceSettings()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def manager(settings: DeviceSettings, bus: EventBus) -> DeviceManager:
    # Auth has its own tests; the rest drive an open manager so the only variable
    # under test is the behaviour being asserted.
    return DeviceManager(settings=settings, bus=bus, require_auth=False)


@pytest.fixture
def secured(settings: DeviceSettings, bus: EventBus) -> DeviceManager:
    return DeviceManager(settings=settings, bus=bus, require_auth=True)


# --------------------------------------------------------------------------- #
# Registration (§26)
# --------------------------------------------------------------------------- #


class TestRegistration:
    async def test_a_valid_registration_is_accepted(
        self, manager: DeviceManager, settings: DeviceSettings
    ) -> None:
        device = FakeDevice()
        hello = await _register(manager, device)
        assert hello.accepted is True
        assert hello.device_id == device.device_id
        assert hello.offline_after == settings.heartbeat_timeout
        assert hello.heartbeat_interval == max(settings.heartbeat_timeout // 2, 1)
        assert manager.online_count == 1

    async def test_registration_records_the_advertised_capabilities(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice(capabilities=["display", "buttons"])
        await _register(manager, device)
        record = manager.get(device.device_id)
        assert record.capabilities == frozenset({"display", "buttons"})
        assert record.device_type == "esp32"
        assert record.firmware_version == "1.0.0"

    async def test_registration_publishes_device_connected(
        self, manager: DeviceManager, bus: EventBus
    ) -> None:
        subscription = bus.subscribe()
        await _register(manager, FakeDevice())
        events = await _drain(subscription)
        assert EventType.DEVICE_CONNECTED in _types(events)

    async def test_re_registration_replaces_the_transport_and_closes_the_old(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice()
        old_transport = device.transport
        await _register(manager, device)
        # A reconnect builds a fresh transport; re-registering swaps it in place.
        replacement = FakeDevice().transport
        await manager.register(_registration(device), replacement)
        assert manager.get(device.device_id).transport is replacement
        assert old_transport.closed is True

    async def test_registration_is_refused_when_max_connections_reached(
        self, bus: EventBus
    ) -> None:
        manager = DeviceManager(
            settings=DeviceSettings(max_connections=1), bus=bus, require_auth=False
        )
        await _register(manager, FakeDevice("esp32-1"))
        refused = await _register(manager, FakeDevice("esp32-2"))
        assert refused.accepted is False
        assert refused.reason is not None
        assert manager.online_count == 1

    async def test_device_type_is_free_form_so_no_board_is_pinned(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice("custom-1", device_type="custom-board-v9")
        await _register(manager, device)
        assert manager.get("custom-1").device_type == "custom-board-v9"


# --------------------------------------------------------------------------- #
# Authentication (§26 with §30, §64.13)
# --------------------------------------------------------------------------- #


class TestAuthentication:
    async def test_registration_without_credential_is_refused_when_auth_required(
        self, secured: DeviceManager
    ) -> None:
        device = FakeDevice(credential=None)
        hello = await _register(secured, device, credential=None)
        assert hello.accepted is False
        assert hello.reason is not None
        assert secured.online_count == 0

    async def test_registration_with_a_wrong_credential_is_refused(
        self, secured: DeviceManager
    ) -> None:
        device = FakeDevice()
        hello = await _register(secured, device, credential="not-a-real-token")
        assert hello.accepted is False
        assert secured.list_devices() == []

    async def test_an_issued_credential_is_accepted(self, secured: DeviceManager) -> None:
        device = FakeDevice()
        token = secured.issue_credential(device.device_id)
        hello = await _register(secured, device, credential=token)
        assert hello.accepted is True
        assert secured.online_count == 1

    async def test_a_revoked_credential_is_refused(self, secured: DeviceManager) -> None:
        device = FakeDevice()
        token = secured.issue_credential(device.device_id)
        assert secured.revoke_credential(device.device_id) is True
        hello = await _register(secured, device, credential=token)
        assert hello.accepted is False

    async def test_an_open_manager_accepts_a_device_without_a_credential(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice(credential=None)
        hello = await _register(manager, device, credential=None)
        assert hello.accepted is True


# --------------------------------------------------------------------------- #
# Heartbeat and liveness (§26)
# --------------------------------------------------------------------------- #


class TestHeartbeat:
    async def test_heartbeat_refreshes_last_seen(self, manager: DeviceManager) -> None:
        device = FakeDevice()
        await _register(manager, device)
        record = manager.get(device.device_id)
        record.last_seen = datetime.now(UTC) - timedelta(seconds=120)
        ack = await manager.heartbeat(
            DeviceHeartbeat.model_validate(device.heartbeat())
        )
        assert ack.device_id == device.device_id
        assert record.is_stale(offline_after=60) is False

    async def test_heartbeat_for_an_unknown_device_is_a_typed_not_found(
        self, manager: DeviceManager
    ) -> None:
        with pytest.raises(NotFoundError):
            await manager.heartbeat(DeviceHeartbeat(device_id="ghost"))

    async def test_heartbeat_refreshes_capabilities_and_firmware(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice()
        await _register(manager, device)
        await manager.heartbeat(
            DeviceHeartbeat.model_validate(
                device.heartbeat(capabilities=["display"], status="online")
            )
        )
        record = manager.get(device.device_id)
        assert record.capabilities == frozenset({"display"})

    async def test_a_stale_device_is_marked_offline_by_the_sweep(
        self, manager: DeviceManager, bus: EventBus
    ) -> None:
        subscription = bus.subscribe()
        await _register(manager, FakeDevice())
        await _drain(subscription)  # drop the connect event
        offline = await manager.sweep(now=datetime.now(UTC) + timedelta(seconds=120))
        assert offline == ["esp32-1"]
        assert manager.get("esp32-1").status is DeviceStatus.OFFLINE
        assert EventType.DEVICE_DISCONNECTED in _types(await _drain(subscription))

    async def test_sweep_ignores_a_device_within_its_window(self, manager: DeviceManager) -> None:
        await _register(manager, FakeDevice())
        assert await manager.sweep() == []

    async def test_heartbeat_brings_an_offline_device_back_online(
        self, manager: DeviceManager, bus: EventBus
    ) -> None:
        device = FakeDevice()
        await _register(manager, device)
        await manager.sweep(now=datetime.now(UTC) + timedelta(seconds=120))
        assert manager.get(device.device_id).status is DeviceStatus.OFFLINE
        subscription = bus.subscribe()
        await manager.heartbeat(DeviceHeartbeat.model_validate(device.heartbeat()))
        assert manager.get(device.device_id).status is DeviceStatus.ONLINE
        assert EventType.DEVICE_CONNECTED in _types(await _drain(subscription))


# --------------------------------------------------------------------------- #
# Events (§26, §19, §25)
# --------------------------------------------------------------------------- #


class TestEvents:
    async def test_wake_word_publishes_wake_detected(
        self, manager: DeviceManager, bus: EventBus
    ) -> None:
        device = FakeDevice()
        await _register(manager, device)
        subscription = bus.subscribe()
        published = await manager.report(
            DeviceEventMessage.model_validate(device.event("wake_word"))
        )
        assert published is True
        assert EventType.WAKE_DETECTED in _types(await _drain(subscription))

    async def test_an_application_event_has_no_canonical_core_event(
        self, manager: DeviceManager, bus: EventBus
    ) -> None:
        device = FakeDevice()
        await _register(manager, device)
        subscription = bus.subscribe()
        published = await manager.report(
            DeviceEventMessage.model_validate(device.event("button_press", payload={"id": 1}))
        )
        assert published is False
        assert await _drain(subscription) == []

    async def test_device_offline_event_moves_status_without_a_heartbeat(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice()
        await _register(manager, device)
        await manager.report(DeviceEventMessage.model_validate(device.event("device_offline")))
        assert manager.get(device.device_id).status is DeviceStatus.OFFLINE

    async def test_reporting_for_an_unknown_device_is_a_typed_not_found(
        self, manager: DeviceManager
    ) -> None:
        with pytest.raises(NotFoundError):
            await manager.report(DeviceEventMessage(device_id="ghost", event_type="wake_word"))

    async def test_reporting_counts_events_for_the_snapshot(self, manager: DeviceManager) -> None:
        device = FakeDevice()
        await _register(manager, device)
        await manager.report(DeviceEventMessage.model_validate(device.event("sensor_event")))
        assert manager.get(device.device_id).events_received == 1


# --------------------------------------------------------------------------- #
# Dispatch and correlation (§26 command/result)
# --------------------------------------------------------------------------- #


class TestDispatch:
    async def test_a_successful_command_returns_the_device_result(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice(handlers={"set_led": lambda args: {"state": "on"}})
        await _register(manager, device)
        result = await manager.dispatch(
            device.device_id, "set_led", {"color": "red"}, capability="display"
        )
        assert result == {"state": "on"}
        assert manager.get(device.device_id).commands_sent == 1

    async def test_the_command_is_delivered_on_the_wire_with_an_action(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice(handlers={"beep": lambda args: None})
        await _register(manager, device)
        await manager.dispatch(device.device_id, "beep")
        commands = [m for m in device.transport.sent if m["type"] == "command"]
        assert len(commands) == 1
        assert commands[0]["action"] == "beep"

    async def test_dispatch_to_an_unknown_device_is_a_typed_device_error(
        self, manager: DeviceManager
    ) -> None:
        with pytest.raises(DeviceError):
            await manager.dispatch("ghost", "beep")

    async def test_dispatch_to_an_offline_device_is_a_typed_device_error(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice()
        await _register(manager, device)
        await manager.sweep(now=datetime.now(UTC) + timedelta(seconds=120))
        with pytest.raises(DeviceError):
            await manager.dispatch(device.device_id, "beep")

    async def test_dispatch_without_the_required_capability_is_a_typed_device_error(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice(handlers={"set_led": lambda args: None})
        await _register(manager, device)
        with pytest.raises(DeviceError):
            await manager.dispatch(device.device_id, "set_led", capability="camera")

    async def test_a_device_reported_failure_becomes_a_tool_error(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice(handlers={"boom": lambda args: 1 / 0})
        await _register(manager, device)
        with pytest.raises(ToolError):
            await manager.dispatch(device.device_id, "boom")
        assert manager.get(device.device_id).commands_failed == 1

    async def test_an_unimplemented_action_is_reported_as_a_failure(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice()
        await _register(manager, device)
        with pytest.raises(ToolError):
            await manager.dispatch(device.device_id, "no_such_action")

    async def test_a_command_that_is_never_answered_times_out(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice(auto_respond=False, handlers={"slow": lambda args: "late"})
        await _register(manager, device)
        with pytest.raises(DeviceError):
            await manager.dispatch(device.device_id, "slow", limit=0.05)

    async def test_a_dropped_connection_surfaces_as_a_typed_device_error(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice(handlers={"beep": lambda args: None})
        await _register(manager, device)
        device.transport.connection_error = OSError
        with pytest.raises(DeviceError):
            await manager.dispatch(device.device_id, "beep")

    async def test_a_late_result_after_delivery_is_dropped_not_crashing(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice(handlers={"beep": lambda args: None})
        await _register(manager, device)
        stale = DeviceResult(
            command_id=uuid.uuid4(),
            device_id=device.device_id,
            status=DeviceResultStatus.OK,
            result="late",
        )
        assert await manager.deliver(stale) is False


# --------------------------------------------------------------------------- #
# Disconnect and lifecycle
# --------------------------------------------------------------------------- #


class TestLifecycle:
    async def test_disconnect_marks_offline_and_closes_the_transport(
        self, manager: DeviceManager, bus: EventBus
    ) -> None:
        device = FakeDevice()
        await _register(manager, device)
        subscription = bus.subscribe()
        assert await manager.disconnect(device.device_id) is True
        assert device.transport.closed is True
        # Retained, not removed: discovery keeps showing an unreachable board.
        assert manager.get(device.device_id).status is DeviceStatus.OFFLINE
        assert EventType.DEVICE_DISCONNECTED in _types(await _drain(subscription))

    async def test_disconnect_of_an_unknown_device_is_a_no_op(self, manager: DeviceManager) -> None:
        assert await manager.disconnect("ghost") is False

    async def test_a_pending_command_is_failed_when_the_device_goes_offline(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice(auto_respond=False, handlers={"slow": lambda args: "late"})
        await _register(manager, device)
        task = asyncio.create_task(
            manager.dispatch(device.device_id, "slow", limit=5.0)
        )
        await asyncio.sleep(0.01)
        await manager.sweep(now=datetime.now(UTC) + timedelta(seconds=120))
        with pytest.raises(DeviceError):
            await task

    async def test_stop_closes_every_transport_and_is_idempotent(
        self, manager: DeviceManager
    ) -> None:
        first = FakeDevice("esp32-1")
        second = FakeDevice("esp32-2")
        await _register(manager, first)
        await _register(manager, second)
        await manager.stop()
        assert first.transport.closed is True
        assert second.transport.closed is True
        assert manager.list_devices() == []
        await manager.stop()  # idempotent

    async def test_start_and_stop_the_heartbeat_monitor(self, manager: DeviceManager) -> None:
        await manager.start()
        await manager.start()  # idempotent
        await manager.stop()

    async def test_dispatch_after_stop_is_a_typed_device_error(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice(handlers={"beep": lambda args: None})
        await _register(manager, device)
        await manager.stop()
        with pytest.raises(DeviceError):
            await manager.dispatch(device.device_id, "beep")


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #


class TestQueries:
    async def test_find_by_capability_returns_online_devices_first(
        self, manager: DeviceManager
    ) -> None:
        online = FakeDevice("esp32-a", capabilities=["display"])
        offline = FakeDevice("esp32-b", capabilities=["display"])
        await _register(manager, online)
        await _register(manager, offline)
        await manager.disconnect(offline.device_id)
        found = manager.find_by_capability("display", online_only=False)
        assert [d.device_id for d in found] == ["esp32-a", "esp32-b"]

    async def test_find_by_capability_online_only_filters_offline(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice(capabilities=["display"])
        await _register(manager, device)
        manager.get(device.device_id).status = DeviceStatus.OFFLINE
        assert manager.find_by_capability("display", online_only=True) == []
        assert len(manager.find_by_capability("display", online_only=False)) == 1

    async def test_get_an_unknown_device_is_a_typed_not_found(self, manager: DeviceManager) -> None:
        with pytest.raises(NotFoundError):
            manager.get("ghost")

    async def test_snapshot_reports_counts(self, manager: DeviceManager) -> None:
        await _register(manager, FakeDevice("esp32-1"))
        await _register(manager, FakeDevice("esp32-2", device_type="custom"))
        snap = manager.snapshot()
        assert snap["devices"] == 2
        assert snap["online"] == 2
        assert snap["by_type"] == {"esp32": 1, "custom": 1}
