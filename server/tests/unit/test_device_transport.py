"""ESP32 transport / device session tests (T162, spec §26).

The session is the device's half of the gateway: ``register`` -> ``registered``,
heartbeat -> ``heartbeat_ack``, events up and commands down with a correlated
result. These tests drive :func:`run_device_session` over the queue-backed
:class:`~tests.support.device_double.FakeDeviceSocket`, so the whole handshake is
asserted with no socket, no hardware and no sleeps beyond an awaited frame.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.config.settings import DeviceSettings
from app.devices.esp32.transport import run_device_session
from app.devices.manager import DeviceManager
from app.devices.protocol import DeviceResult, DeviceResultStatus, DeviceStatus
from app.events.bus import EventBus
from app.events.types import EventType
from tests.support.device_double import (
    ConnectionClosedError,
    FakeDevice,
    FakeDeviceSocket,
)

pytestmark = pytest.mark.unit


async def _start(
    manager: DeviceManager, socket: FakeDeviceSocket
) -> asyncio.Task[None]:
    return asyncio.create_task(run_device_session(socket, manager))


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def manager(bus: EventBus) -> DeviceManager:
    return DeviceManager(settings=DeviceSettings(), bus=bus, require_auth=False)


# --------------------------------------------------------------------------- #
# Handshake (§26)
# --------------------------------------------------------------------------- #


class TestHandshake:
    async def test_register_then_heartbeat_round_trip(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice()
        socket = FakeDeviceSocket()
        task = await _start(manager, socket)

        await socket.push(device.registration())
        hello = await socket.next_sent()
        assert hello["type"] == "registered"
        assert hello["accepted"] is True
        assert hello["device_id"] == device.device_id
        assert hello["offline_after"] == manager.settings.heartbeat_timeout
        assert manager.get(device.device_id).status is DeviceStatus.ONLINE

        await socket.push(device.heartbeat(status="online"))
        ack = await socket.next_sent()
        assert ack["type"] == "heartbeat_ack"
        assert ack["device_id"] == device.device_id

        await socket.push(device.goodbye())
        await task
        assert socket.closed is True  # disconnect closes the transport
        assert manager.get(device.device_id).status is DeviceStatus.OFFLINE  # retained

    async def test_a_malformed_opening_frame_closes_the_socket(
        self, manager: DeviceManager
    ) -> None:
        socket = FakeDeviceSocket()
        await socket.push({"type": "register"})  # missing device_id
        await run_device_session(socket, manager)
        assert socket.closed is True

    async def test_a_non_register_opening_frame_closes_the_socket(
        self, manager: DeviceManager
    ) -> None:
        socket = FakeDeviceSocket()
        await socket.push({"type": "heartbeat", "device_id": "esp32-1"})
        await run_device_session(socket, manager)
        assert socket.closed is True

    async def test_a_refused_registration_closes_the_socket(self, bus: EventBus) -> None:
        manager = DeviceManager(settings=DeviceSettings(), bus=bus, require_auth=True)
        device = FakeDevice(credential="not-a-real-token")
        socket = FakeDeviceSocket()
        await socket.push(device.registration())
        await run_device_session(socket, manager)
        hello = await socket.next_sent()
        assert hello["accepted"] is False
        assert hello["reason"]
        assert socket.closed is True
        assert manager.online_count == 0

    async def test_a_peer_disconnect_disconnects_the_device(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice()
        socket = FakeDeviceSocket()
        task = await _start(manager, socket)
        await socket.push(device.registration())
        await socket.next_sent()
        await socket.close_from_peer()
        with pytest.raises(ConnectionClosedError):
            await task
        assert manager.get(device.device_id).status is DeviceStatus.OFFLINE


# --------------------------------------------------------------------------- #
# Events and robustness (§26)
# --------------------------------------------------------------------------- #


class TestSessionLoop:
    async def test_a_wake_word_event_publishes_wake_detected(
        self, manager: DeviceManager, bus: EventBus
    ) -> None:
        device = FakeDevice()
        socket = FakeDeviceSocket()
        task = await _start(manager, socket)
        await socket.push(device.registration())
        await socket.next_sent()

        subscription = bus.subscribe()
        await socket.push(device.event("wake_word"))
        envelope = await asyncio.wait_for(subscription.get(), timeout=1.0)
        assert envelope is not None
        assert envelope.event_type is EventType.WAKE_DETECTED

        await socket.push(device.goodbye())
        await task

    async def test_a_malformed_frame_is_skipped_and_the_session_continues(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice()
        socket = FakeDeviceSocket()
        task = await _start(manager, socket)
        await socket.push(device.registration())
        await socket.next_sent()

        await socket.push({"type": "heartbeat"})  # missing device_id -> invalid
        await socket.push(device.heartbeat())  # the session is still alive
        ack = await socket.next_sent()
        assert ack["type"] == "heartbeat_ack"

        await socket.push(device.goodbye())
        await task


# --------------------------------------------------------------------------- #
# Command / response (§26)
# --------------------------------------------------------------------------- #


class TestCommandRoundTrip:
    async def test_a_command_goes_down_and_its_result_resolves_the_dispatch(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice()
        socket = FakeDeviceSocket()
        task = await _start(manager, socket)
        await socket.push(device.registration())
        await socket.next_sent()

        dispatch = asyncio.create_task(
            manager.dispatch(device.device_id, "set_led", {"state": "on"})
        )
        command = await socket.next_sent()
        assert command["type"] == "command"
        assert command["action"] == "set_led"
        assert command["arguments"] == {"state": "on"}

        await socket.push(
            DeviceResult(
                command_id=command["command_id"],
                device_id=device.device_id,
                status=DeviceResultStatus.OK,
                result={"ok": True},
            ).model_dump(mode="json")
        )
        assert await asyncio.wait_for(dispatch, timeout=1.0) == {"ok": True}

        await socket.push(device.goodbye())
        await task

    async def test_a_result_with_no_pending_command_is_ignored(
        self, manager: DeviceManager
    ) -> None:
        device = FakeDevice()
        socket = FakeDeviceSocket()
        task = await _start(manager, socket)
        await socket.push(device.registration())
        await socket.next_sent()

        await socket.push(
            DeviceResult(
                command_id=uuid.uuid4(),
                device_id=device.device_id,
                status=DeviceResultStatus.OK,
                result="stale",
            ).model_dump(mode="json")
        )
        await socket.push(device.heartbeat())
        ack = await socket.next_sent()
        assert ack["type"] == "heartbeat_ack"  # the reader survived the stray result

        await socket.push(device.goodbye())
        await task
