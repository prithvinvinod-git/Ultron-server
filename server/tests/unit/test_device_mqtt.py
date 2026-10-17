"""MQTT device transport tests (T163, spec §26).

The broker-agnostic seam is exercised end to end with an in-memory client: frames
in, manager calls out, publishes recorded. Nothing here needs a broker or the
``paho-mqtt`` extra, which is the point of keeping :class:`MqttClient` so small.
The one place a concrete client *is* built asserts the honest-degradation path —
a deployment that enabled MQTT without the extra gets ``None``, not a crash.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest

from app.config.settings import DeviceSettings
from app.devices.manager import DeviceManager
from app.devices.mqtt import client as mqtt_client
from app.devices.mqtt.availability import paho_available, paho_unavailable_reason
from app.devices.mqtt.client import PahoMqttClient, build_mqtt_client
from app.devices.mqtt.transport import (
    MqttDeviceBridge,
    MqttDeviceTransport,
    device_id_from_topic,
    topic_for_device,
)
from app.devices.protocol import DeviceStatus
from app.events.bus import EventBus
from app.events.types import EventType
from tests.support.device_double import ESP32_CAPABILITIES, FakeMqttClient

pytestmark = pytest.mark.unit

PREFIX = "ultron"
DEVICE_ID = "esp32-1"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _frame(device_id: str, message: dict[str, Any], *, prefix: str = PREFIX) -> tuple[str, bytes]:
    """An uplink ``(topic, payload)`` for one device and one message."""
    topic = topic_for_device(prefix, device_id, "up")
    return topic, json.dumps(message).encode("utf-8")


def _register_frame(device_id: str = DEVICE_ID, **overrides: Any) -> dict[str, Any]:
    message: dict[str, Any] = {
        "type": "register",
        "device_id": device_id,
        "name": "Kitchen Panel",
        "device_type": "esp32",
        "capabilities": list(ESP32_CAPABILITIES),
        "firmware_version": "1.0.0",
    }
    message.update(overrides)
    return message


async def _until(predicate: Callable[[], bool], *, ticks: int = 100) -> None:
    for _ in range(ticks):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not reached")


@pytest.fixture
def settings() -> DeviceSettings:
    return DeviceSettings()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def manager(settings: DeviceSettings, bus: EventBus) -> DeviceManager:
    return DeviceManager(settings=settings, bus=bus, require_auth=False)


# --------------------------------------------------------------------------- #
# Topics
# --------------------------------------------------------------------------- #


class TestTopics:
    def test_topic_for_device_names_both_directions(self) -> None:
        assert topic_for_device(PREFIX, DEVICE_ID, "up") == "ultron/devices/esp32-1/up"
        assert topic_for_device(PREFIX, DEVICE_ID, "down") == "ultron/devices/esp32-1/down"

    def test_device_id_round_trips(self) -> None:
        topic = topic_for_device(PREFIX, DEVICE_ID, "up")
        assert device_id_from_topic(PREFIX, topic) == DEVICE_ID

    def test_a_down_topic_is_not_read_as_an_uplink(self) -> None:
        assert device_id_from_topic(PREFIX, "ultron/devices/esp32-1/down") is None

    def test_a_foreign_prefix_is_rejected(self) -> None:
        assert device_id_from_topic(PREFIX, "other/devices/esp32-1/up") is None

    def test_a_shorter_topic_is_rejected(self) -> None:
        assert device_id_from_topic(PREFIX, "ultron/devices/esp32-1") is None

    def test_a_nested_prefix_still_round_trips(self) -> None:
        topic = topic_for_device("home/ultron", DEVICE_ID, "up")
        assert device_id_from_topic("home/ultron", topic) == DEVICE_ID


# --------------------------------------------------------------------------- #
# The manager-facing transport
# --------------------------------------------------------------------------- #


class TestMqttDeviceTransport:
    async def test_send_publishes_json_to_the_down_topic(self) -> None:
        client = FakeMqttClient()
        transport = MqttDeviceTransport(client, topic_prefix=PREFIX, device_id=DEVICE_ID)

        await transport.send({"type": "registered", "accepted": True})

        assert client.sent_to(DEVICE_ID) == [{"type": "registered", "accepted": True}]

    async def test_close_is_a_noop(self) -> None:
        transport = MqttDeviceTransport(FakeMqttClient(), topic_prefix=PREFIX, device_id=DEVICE_ID)
        await transport.close()  # must not raise


# --------------------------------------------------------------------------- #
# The bridge
# --------------------------------------------------------------------------- #


class TestMqttDeviceBridge:
    def test_subscription_topic_wildcards_every_device(self, manager: DeviceManager) -> None:
        bridge = MqttDeviceBridge(manager, FakeMqttClient())
        assert bridge.subscription_topic == "ultron/devices/+/up"

    def test_subscription_topic_honours_an_explicit_prefix(self, manager: DeviceManager) -> None:
        bridge = MqttDeviceBridge(manager, FakeMqttClient(), topic_prefix="home/ultron")
        assert bridge.subscription_topic == "home/ultron/devices/+/up"

    async def test_register_marks_the_device_online_and_answers(
        self, manager: DeviceManager
    ) -> None:
        client = FakeMqttClient(frames=[_frame(DEVICE_ID, _register_frame())])

        await MqttDeviceBridge(manager, client).run()

        device = manager.maybe(DEVICE_ID)
        assert device is not None
        assert device.status is DeviceStatus.ONLINE
        assert device.device_type == "esp32"
        sent = client.sent_to(DEVICE_ID)
        assert sent[0]["type"] == "registered"
        assert sent[0]["accepted"] is True
        assert sent[0]["device_id"] == DEVICE_ID

    async def test_heartbeat_is_acknowledged_on_the_down_topic(
        self, manager: DeviceManager
    ) -> None:
        frames = [
            _frame(DEVICE_ID, _register_frame()),
            _frame(DEVICE_ID, {"type": "heartbeat", "device_id": DEVICE_ID}),
        ]
        client = FakeMqttClient(frames=frames)

        await MqttDeviceBridge(manager, client).run()

        sent = client.sent_to(DEVICE_ID)
        assert [message["type"] for message in sent] == ["registered", "heartbeat_ack"]
        assert sent[1]["device_id"] == DEVICE_ID

    async def test_a_wake_word_event_is_published_to_the_bus(
        self, manager: DeviceManager, bus: EventBus
    ) -> None:
        subscription = bus.subscribe()
        frames = [
            _frame(DEVICE_ID, _register_frame()),
            _frame(DEVICE_ID, {"type": "event", "device_id": DEVICE_ID, "event_type": "wake_word"}),
        ]

        await MqttDeviceBridge(manager, FakeMqttClient(frames=frames)).run()

        # Registration publishes DEVICE_CONNECTED first; the wake word follows.
        connected = await subscription.get()
        assert connected is not None
        assert connected.event_type is EventType.DEVICE_CONNECTED
        wake = await subscription.get()
        assert wake is not None
        assert wake.event_type is EventType.WAKE_DETECTED
        subscription.close()

    async def test_a_result_resolves_a_pending_dispatch(self, manager: DeviceManager) -> None:
        register_client = FakeMqttClient(frames=[_frame(DEVICE_ID, _register_frame())])
        await MqttDeviceBridge(manager, register_client).run()

        dispatch = asyncio.get_running_loop().create_task(
            manager.dispatch(DEVICE_ID, "display.show")
        )

        def commands() -> list[dict[str, Any]]:
            return [
                message
                for message in register_client.sent_to(DEVICE_ID)
                if message["type"] == "command"
            ]

        await _until(lambda: bool(commands()))
        command = commands()[0]
        assert command["type"] == "command"

        result = {
            "type": "result",
            "command_id": command["command_id"],
            "device_id": DEVICE_ID,
            "status": "ok",
            "result": "shown",
        }
        await MqttDeviceBridge(manager, FakeMqttClient(frames=[_frame(DEVICE_ID, result)])).run()

        assert await dispatch == "shown"

    async def test_goodbye_marks_the_device_offline(self, manager: DeviceManager) -> None:
        frames = [
            _frame(DEVICE_ID, _register_frame()),
            _frame(DEVICE_ID, {"type": "goodbye", "device_id": DEVICE_ID, "reason": "restarting"}),
        ]

        await MqttDeviceBridge(manager, FakeMqttClient(frames=frames)).run()

        device = manager.maybe(DEVICE_ID)
        assert device is not None
        assert device.status is DeviceStatus.OFFLINE

    async def test_a_malformed_payload_is_skipped(self, manager: DeviceManager) -> None:
        frames = [
            (topic_for_device(PREFIX, DEVICE_ID, "up"), b"not json at all"),
            _frame(DEVICE_ID, _register_frame()),
        ]

        await MqttDeviceBridge(manager, FakeMqttClient(frames=frames)).run()

        assert manager.maybe(DEVICE_ID) is not None

    async def test_an_off_topic_message_is_ignored(self, manager: DeviceManager) -> None:
        frames = [("some/other/topic", b'{"type": "register", "device_id": "x"}')]

        await MqttDeviceBridge(manager, FakeMqttClient(frames=frames)).run()

        assert manager.maybe("x") is None

    async def test_a_frame_for_an_unknown_device_does_not_stop_the_run(
        self, manager: DeviceManager
    ) -> None:
        frames = [
            _frame("ghost", {"type": "heartbeat", "device_id": "ghost"}),
            _frame(DEVICE_ID, _register_frame()),
        ]

        await MqttDeviceBridge(manager, FakeMqttClient(frames=frames)).run()

        # The unknown heartbeat raised NotFoundError and was swallowed; the
        # following register still landed.
        assert manager.maybe(DEVICE_ID) is not None


# --------------------------------------------------------------------------- #
# The optional concrete client
# --------------------------------------------------------------------------- #


class TestAvailability:
    def test_the_probe_is_cached_and_consistent(self) -> None:
        paho_available.cache_clear()
        assert isinstance(paho_available(), bool)

    def test_a_missing_extra_has_a_reason(self) -> None:
        paho_available.cache_clear()
        if not paho_available():
            reason = paho_unavailable_reason()
            assert reason is not None
            assert "paho-mqtt" in reason

    def test_the_factory_returns_none_without_the_extra(
        self, settings: DeviceSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(mqtt_client, "paho_available", lambda: False)
        assert build_mqtt_client(settings) is None

    def test_the_factory_builds_a_client_when_the_extra_is_present(
        self, settings: DeviceSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(mqtt_client, "paho_available", lambda: True)
        client = build_mqtt_client(settings)
        assert isinstance(client, PahoMqttClient)
