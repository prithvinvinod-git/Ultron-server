"""Device wire contract (T160, spec §26).

The device protocol is the server's typed view of the ESP32 JSON transport
(T162). These tests pin the parts the transport and gateway rely on:

1. :func:`parse_message` is the one entry point -- it dispatches on ``type`` and
   raises :class:`~app.core.errors.InvalidInputError` (never a crash) for
   anything malformed;
2. the §26 device event vocabulary is exactly the six names the spec lists, and
   the canonical-event mapping is total over it;
3. the envelope carries the §26 fields, and ``device_type`` is free-form so no
   board is hard-coded into Core.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core.errors import InvalidInputError
from app.devices.protocol import (
    DEVICE_EVENT_TYPES,
    DeviceEventMessage,
    DeviceGoodbye,
    DeviceHeartbeat,
    DeviceHeartbeatAck,
    DeviceMessageType,
    DeviceRegister,
    DeviceRegistered,
    DeviceResult,
    DeviceResultStatus,
    DeviceStatus,
    core_event_for,
    parse_message,
)
from app.events.types import EventType

pytestmark = pytest.mark.unit


# -- §26 vocabulary -----------------------------------------------------------


def test_device_event_types_are_the_six_the_spec_lists() -> None:
    assert DEVICE_EVENT_TYPES == (
        "wake_word",
        "button_press",
        "sensor_event",
        "audio_stream",
        "device_online",
        "device_offline",
    )


def test_core_event_mapping_covers_online_offline_and_wake() -> None:
    assert core_event_for("wake_word") is EventType.WAKE_DETECTED
    assert core_event_for("device_online") is EventType.DEVICE_CONNECTED
    assert core_event_for("device_offline") is EventType.DEVICE_DISCONNECTED


def test_core_event_mapping_is_case_insensitive() -> None:
    assert core_event_for("WAKE_WORD") is EventType.WAKE_DETECTED
    assert core_event_for("  Device_Offline  ") is EventType.DEVICE_DISCONNECTED


def test_application_events_have_no_canonical_core_event() -> None:
    assert core_event_for("button_press") is None
    assert core_event_for("sensor_event") is None
    assert core_event_for("audio_stream") is None
    assert core_event_for("not_a_real_event") is None


# -- the envelope carries the §26 fields, no board pinned ---------------------


def test_register_carries_the_spec_fields_and_free_form_type() -> None:
    frame = DeviceRegister(
        device_id="esp32-kitchen",
        name="Kitchen panel",
        device_type="esp32-s3-devkitc-1",
        capabilities=["display", "microphone", "buttons"],
        firmware_version="1.2.3",
    )
    assert frame.type is DeviceMessageType.REGISTER
    assert frame.device_type == "esp32-s3-devkitc-1"
    assert frame.protocol_version == "1"


def test_register_accepts_an_arbitrary_board_string() -> None:
    frame = DeviceRegister(device_id="d1", device_type="some-future-board-v7")
    assert frame.device_type == "some-future-board-v7"


def test_event_accepts_an_extension_event_type() -> None:
    frame = DeviceEventMessage(device_id="d1", event_type="temperature_high")
    assert frame.event_type == "temperature_high"


def test_heartbeat_status_uses_the_registry_status_enum() -> None:
    frame = DeviceHeartbeat(device_id="d1", status=DeviceStatus.ONLINE)
    assert frame.status is DeviceStatus.ONLINE


# -- parse_message dispatch ---------------------------------------------------


def test_parse_register() -> None:
    model = parse_message({"type": "register", "device_id": "d1", "capabilities": ["display"]})
    assert isinstance(model, DeviceRegister)
    assert model.capabilities == ["display"]


def test_parse_heartbeat() -> None:
    model = parse_message({"type": "heartbeat", "device_id": "d1", "status": "online"})
    assert isinstance(model, DeviceHeartbeat)
    assert model.status is DeviceStatus.ONLINE


def test_parse_event() -> None:
    model = parse_message(
        {"type": "event", "device_id": "d1", "event_type": "button_press", "sequence": 4}
    )
    assert isinstance(model, DeviceEventMessage)
    assert model.event_type == "button_press"
    assert model.sequence == 4


def test_parse_result() -> None:
    command_id = uuid.uuid4()
    model = parse_message(
        {
            "type": "result",
            "command_id": str(command_id),
            "device_id": "d1",
            "status": "ok",
        }
    )
    assert isinstance(model, DeviceResult)
    assert model.command_id == command_id
    assert model.status is DeviceResultStatus.OK


def test_parse_goodbye() -> None:
    model = parse_message({"type": "goodbye", "reason": "shutdown"})
    assert isinstance(model, DeviceGoodbye)


# -- malformed input is a typed error, never a crash --------------------------


def test_parse_rejects_a_non_object() -> None:
    with pytest.raises(InvalidInputError):
        parse_message(["not", "an", "object"])


def test_parse_rejects_an_unknown_type() -> None:
    with pytest.raises(InvalidInputError):
        parse_message({"type": "teleport", "device_id": "d1"})


def test_parse_rejects_a_missing_type() -> None:
    with pytest.raises(InvalidInputError):
        parse_message({"device_id": "d1"})


def test_parse_rejects_a_missing_required_field() -> None:
    with pytest.raises(InvalidInputError):
        parse_message({"type": "register"})


def test_parse_rejects_an_unknown_field() -> None:
    with pytest.raises(InvalidInputError):
        parse_message({"type": "register", "device_id": "d1", "surprise": True})


def test_parse_rejects_a_bad_status_value() -> None:
    with pytest.raises(InvalidInputError):
        parse_message({"type": "heartbeat", "device_id": "d1", "status": "on fire"})


# -- models are round-trippable (the transport serializes what it parses) ------


def test_server_frames_round_trip() -> None:
    registered = DeviceRegistered(
        accepted=True,
        device_id="d1",
        heartbeat_interval=30,
        offline_after=60,
        server_time=datetime(2026, 1, 1, tzinfo=UTC),
    )
    dumped = registered.model_dump(mode="json")
    # A server frame is re-validated by a device with the same models.
    assert DeviceRegistered.model_validate(dumped) == registered

    ack = DeviceHeartbeatAck(device_id="d1", server_time=datetime(2026, 1, 1, tzinfo=UTC))
    assert DeviceHeartbeatAck.model_validate(ack.model_dump(mode="json")) == ack


def test_registered_requires_positive_intervals() -> None:
    with pytest.raises(ValidationError):
        DeviceRegistered(accepted=True, device_id="d1", heartbeat_interval=0, offline_after=60)
