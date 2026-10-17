"""The device wire protocol (T160, spec §26).

The device gateway (T161) and the ESP32 JSON transport (T162) speak this one
contract. Spec §26 fixes the *device fields* -- ``device_id``, ``name``,
``type``, ``capabilities``, ``status``, ``last_seen``, ``firmware_version`` --
and the *events* a device may report:

```text
wake_word      button_press    sensor_event
audio_stream   device_online   device_offline
```

**No board is named anywhere here** (§26: "Do not hard-code one ESP32 board
into the Core"). ``device_type`` is a free string a firmware chooses; the
capability list is advertised, not enumerated; and the event vocabulary is the
spec's, so a second board speaks the same protocol without a Core change.

The shape mirrors the computer-node protocol (``app/agents/computer/protocol.py``)
deliberately: the same ``type`` discriminator, the same ``register`` ->
``registered`` / ``heartbeat`` -> ``heartbeat_ack`` / ``command`` -> ``result``
handshake, and the same two rules -- the connection is **outbound from the
device** (the server never dials into the LAN), and a **device credential
authenticates a device, never a user** (§26 with §30, §64.13).

The Pydantic models are the server's typed view of the wire. :func:`parse_message`
turns a decoded JSON object into the right model using its ``type``, so the
transport has a single, validated entry point.

``device_online`` / ``device_offline`` are mapped to the canonical
``DEVICE_CONNECTED`` / ``DEVICE_DISCONNECTED`` core events (§19) and
``wake_word`` to ``WAKE_DETECTED`` (§25); the other three are application
events with no canonical counterpart and are surfaced to the IoT agent instead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.errors import InvalidInputError
from app.database.models.enums import DeviceStatus
from app.events.types import EventType

__all__ = [
    "CORE_EVENT_FOR_DEVICE_EVENT",
    "DEVICE_CAPABILITY_STRINGS",
    "DEVICE_EVENT_TYPES",
    "DeviceCapabilities",
    "DeviceCommand",
    "DeviceEventMessage",
    "DeviceEventType",
    "DeviceGoodbye",
    "DeviceHeartbeat",
    "DeviceHeartbeatAck",
    "DeviceMessageType",
    "DeviceRegister",
    "DeviceRegistered",
    "DeviceResult",
    "DeviceResultStatus",
    "DeviceStatus",
    "core_event_for",
    "parse_message",
]


class DeviceEventType(StrEnum):
    """The six events spec §26 enumerates, verbatim and lower-case."""

    WAKE_WORD = "wake_word"
    BUTTON_PRESS = "button_press"
    SENSOR_EVENT = "sensor_event"
    AUDIO_STREAM = "audio_stream"
    DEVICE_ONLINE = "device_online"
    DEVICE_OFFLINE = "device_offline"


class DeviceMessageType(StrEnum):
    """The ``type`` discriminator on every device wire message."""

    REGISTER = "register"
    REGISTERED = "registered"
    HEARTBEAT = "heartbeat"
    HEARTBEAT_ACK = "heartbeat_ack"
    EVENT = "event"
    COMMAND = "command"
    RESULT = "result"
    GOODBYE = "goodbye"


class DeviceResultStatus(StrEnum):
    """The outcome of one command, from the device."""

    OK = "ok"
    ERROR = "error"


#: The event names, as a tuple, for iteration and validation without an enum
#: round-trip. Kept in the spec's spelling so a payload matches the document.
DEVICE_EVENT_TYPES: tuple[str, ...] = tuple(event.value for event in DeviceEventType)

#: Capability *families* a device may advertise (§64.9). Advisory, like the node
#: vocabulary: a device may advertise a string not in this list and the registry
#: stores whatever it advertised. The set exists so the common names have one
#: spelling for the ESP32 display to map to icons.
DEVICE_CAPABILITY_STRINGS: tuple[str, ...] = (
    "display",
    "microphone",
    "speaker",
    "buttons",
    "sensors",
    "physical_controls",
)

#: Device event -> the canonical core event it is published as (§19, §25).
#: ``button_press`` / ``sensor_event`` / ``audio_stream`` have no canonical event
#: and are left to the IoT agent, so they are absent rather than mapped to a
#: catch-all.
CORE_EVENT_FOR_DEVICE_EVENT: dict[str, EventType] = {
    DeviceEventType.WAKE_WORD.value: EventType.WAKE_DETECTED,
    DeviceEventType.DEVICE_ONLINE.value: EventType.DEVICE_CONNECTED,
    DeviceEventType.DEVICE_OFFLINE.value: EventType.DEVICE_DISCONNECTED,
}


def core_event_for(event_type: str) -> EventType | None:
    """Return the canonical core event for a device event, or ``None``.

    Case-insensitive so a firmware that sends ``WAKE_WORD`` still maps: the spec
    spells device events in lower case (line 1293) but its event lists are upper
    case elsewhere (§19), and the same fact should not depend on which list a
    firmware author read.
    """
    return CORE_EVENT_FOR_DEVICE_EVENT.get(event_type.strip().lower())


class DeviceRegister(BaseModel):
    """The first message a device sends: who it is and what it can do."""

    model_config = ConfigDict(extra="forbid")

    type: DeviceMessageType = DeviceMessageType.REGISTER
    device_id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="", max_length=128)
    #: Free-form board/firmware string; never an enum, so no board is pinned (§26).
    device_type: str = Field(default="", max_length=64)
    capabilities: list[str] = Field(default_factory=list)
    firmware_version: str | None = Field(default=None, max_length=64)
    #: The device credential (§26/§30). Opaque here; hashed at rest by T161.
    credential: str | None = Field(default=None, max_length=512)
    protocol_version: str = Field(default="1", max_length=16)


class DeviceRegistered(BaseModel):
    """The server's answer to a registration: accepted or not, and the rules."""

    model_config = ConfigDict(extra="forbid")

    type: DeviceMessageType = DeviceMessageType.REGISTERED
    accepted: bool
    device_id: str
    reason: str | None = None
    heartbeat_interval: int = Field(ge=1)
    offline_after: int = Field(ge=1)
    server_time: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DeviceHeartbeat(BaseModel):
    """A periodic liveness message; may refresh status and capabilities."""

    model_config = ConfigDict(extra="forbid")

    type: DeviceMessageType = DeviceMessageType.HEARTBEAT
    device_id: str = Field(min_length=1, max_length=128)
    status: DeviceStatus | None = None
    capabilities: list[str] | None = None
    firmware_version: str | None = Field(default=None, max_length=64)


class DeviceHeartbeatAck(BaseModel):
    """The server's answer to a heartbeat: liveness acknowledged, with its clock."""

    model_config = ConfigDict(extra="forbid")

    type: DeviceMessageType = DeviceMessageType.HEARTBEAT_ACK
    device_id: str
    server_time: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DeviceEventMessage(BaseModel):
    """One event a device reports (§26)."""

    model_config = ConfigDict(extra="forbid")

    type: DeviceMessageType = DeviceMessageType.EVENT
    device_id: str = Field(min_length=1, max_length=128)
    #: One of :data:`DEVICE_EVENT_TYPES`, or an extension string a firmware adds.
    event_type: str = Field(min_length=1, max_length=64)
    sequence: int = Field(default=0, ge=0)
    payload: dict[str, Any] | None = None
    #: Filesystem path for an ``audio_stream`` event -- a path, not a blob
    #: (spec §36 keeps uploads on the shared volume), filled by the transport
    #: once it has stored the audio, not sent by the device.
    audio_path: str | None = Field(default=None, max_length=1024)
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DeviceCommand(BaseModel):
    """One structured command to a device (§13: never an arbitrary shell string)."""

    model_config = ConfigDict(extra="forbid")

    type: DeviceMessageType = DeviceMessageType.COMMAND
    command_id: UUID
    device_id: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    #: Who asked, for the device-side permission re-check (mirrors §64.6.3).
    principal: str | None = Field(default=None, max_length=128)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    #: Absolute deadline; the device should abandon the work once past it.
    deadline: datetime | None = None
    correlation_id: str | None = Field(default=None, max_length=128)


class DeviceResult(BaseModel):
    """The device's answer to one command, correlated by ``command_id``."""

    model_config = ConfigDict(extra="forbid")

    type: DeviceMessageType = DeviceMessageType.RESULT
    command_id: UUID
    device_id: str = Field(min_length=1, max_length=128)
    status: DeviceResultStatus
    result: Any = None
    error_code: str | None = Field(default=None, max_length=64)
    error: str | None = Field(default=None, max_length=2048)
    duration_ms: int | None = Field(default=None, ge=0)


class DeviceGoodbye(BaseModel):
    """A clean shutdown, either direction."""

    model_config = ConfigDict(extra="forbid")

    type: DeviceMessageType = DeviceMessageType.GOODBYE
    device_id: str | None = Field(default=None, max_length=128)
    reason: str | None = Field(default=None, max_length=256)


class DeviceCapabilities(BaseModel):
    """The capability/state view of one device -- the shape a listing returns."""

    model_config = ConfigDict(extra="forbid")

    device_id: str
    name: str
    device_type: str
    capabilities: list[str]
    status: DeviceStatus
    last_seen: datetime | None = None
    connected_at: datetime | None = None
    firmware_version: str | None = None


#: Every inbound message model, keyed by its ``type`` discriminator.
_MESSAGE_MODELS: dict[str, type[BaseModel]] = {
    DeviceMessageType.REGISTER.value: DeviceRegister,
    DeviceMessageType.HEARTBEAT.value: DeviceHeartbeat,
    DeviceMessageType.EVENT.value: DeviceEventMessage,
    DeviceMessageType.RESULT.value: DeviceResult,
    DeviceMessageType.GOODBYE.value: DeviceGoodbye,
}

#: The outbound models a device may receive, for the transport's own sends.
_SERVER_MESSAGE_MODELS: dict[str, type[BaseModel]] = {
    DeviceMessageType.REGISTERED.value: DeviceRegistered,
    DeviceMessageType.HEARTBEAT_ACK.value: DeviceHeartbeatAck,
    DeviceMessageType.COMMAND.value: DeviceCommand,
    DeviceMessageType.GOODBYE.value: DeviceGoodbye,
}


def parse_message(data: Any) -> BaseModel:
    """Validate a decoded device frame into its typed model.

    A single entry point so the transport never re-implements dispatch: it
    decodes JSON, calls this, and handles the returned model. Raises
    :class:`~app.core.errors.InvalidInputError` for anything that is not a
    mapping, has no recognised ``type``, or fails model validation -- a malformed
    frame is the peer's problem and a typed 422 is the honest answer, never a
    crash that drops the socket.
    """
    if not isinstance(data, dict):
        raise InvalidInputError("device message must be a JSON object")
    kind = data.get("type")
    model = _MESSAGE_MODELS.get(kind) if isinstance(kind, str) else None
    if model is None:
        raise InvalidInputError(f"unknown device message type: {kind!r}")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise InvalidInputError(
            f"invalid {kind} message", details={"errors": exc.error_count()}
        ) from exc
