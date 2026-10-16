"""Computer-node wire contract (T140/T146, spec §13/§64).

The JSON Schema in ``clients/protocol/node-protocol.schema.json`` is **normative**
— the Windows node is not written in Python, so the contract cannot live only in
the server's Pydantic classes. These tests are the lockstep between the two:

1. every schema ``$defs`` entry that names a message has exactly the server
   model's fields, required-set and bounds (`additionalProperties: false` matches
   the models' ``extra="forbid"``);
2. every enum in the schema matches its ``StrEnum`` member-for-member;
3. the node double's emitted frames validate against the schema, so the double a
   node is written from cannot drift either.

If someone changes a field in ``protocol.py`` and not in the schema, or the
reverse, one of these fails instead of the mismatch reaching the wire.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import jsonschema
import pytest
from pydantic import BaseModel

from app.agents.computer.protocol import (
    CAPABILITY_STRINGS,
    MessageType,
    NodeCapabilities,
    NodeCommand,
    NodeHeartbeat,
    NodeHeartbeatAck,
    NodeRegistration,
    NodeResult,
    NodeStatus,
    NodeType,
    ResultStatus,
    ServerHello,
)
from tests.support.node_double import FakeNode

pytestmark = pytest.mark.unit

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3] / "clients" / "protocol" / "node-protocol.schema.json"
)

#: Schema definition -> the server model that must mirror it.
_MODELS: dict[str, type[BaseModel]] = {
    "NodeRegistration": NodeRegistration,
    "ServerHello": ServerHello,
    "NodeHeartbeat": NodeHeartbeat,
    "NodeHeartbeatAck": NodeHeartbeatAck,
    "NodeCommand": NodeCommand,
    "NodeResult": NodeResult,
    "NodeCapabilities": NodeCapabilities,
}

_STR_ENUMS: dict[str, type[Any]] = {
    "NodeType": NodeType,
    "NodeStatus": NodeStatus,
    "ResultStatus": ResultStatus,
    "MessageType": MessageType,
}


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_SCHEMA_PATH.read_text(encoding="utf-8")))


def _validate(schema: dict[str, Any], instance: dict[str, Any]) -> None:
    jsonschema.validate(instance=instance, schema=schema)


# --------------------------------------------------------------------------- #
# Schema <-> models
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("definition", sorted(_MODELS))
def test_schema_properties_match_model_fields(schema: dict[str, Any], definition: str) -> None:
    """Every message's schema properties are exactly the model's fields."""
    model = _MODELS[definition]
    properties = set(schema["$defs"][definition]["properties"])
    assert properties == set(model.model_fields), definition


@pytest.mark.parametrize("definition", sorted(_MODELS))
def test_schema_required_matches_model_required(schema: dict[str, Any], definition: str) -> None:
    """The schema's ``required`` is the set of fields the model has no default for."""
    model = _MODELS[definition]
    expected = {name for name, field in model.model_fields.items() if field.is_required()}
    declared = set(schema["$defs"][definition].get("required", []))
    assert declared == expected, definition


@pytest.mark.parametrize("definition", sorted(_MODELS))
def test_message_objects_forbid_extra_properties(schema: dict[str, Any], definition: str) -> None:
    """``additionalProperties: false`` mirrors the models' ``extra="forbid"``."""
    assert schema["$defs"][definition]["additionalProperties"] is False, definition


@pytest.mark.parametrize("name", sorted(_STR_ENUMS))
def test_schema_enum_matches_str_enum(schema: dict[str, Any], name: str) -> None:
    enum_type = _STR_ENUMS[name]
    declared = set(schema["$defs"][name]["enum"])
    assert declared == {member.value for member in enum_type}, name


def test_capability_strings_match_schema(schema: dict[str, Any]) -> None:
    """The advisory capability list has one spelling in both places."""
    declared = set(schema["$defs"]["Capability"]["enum"])
    assert declared == set(CAPABILITY_STRINGS)


# --------------------------------------------------------------------------- #
# Server output validates against the normative schema
# --------------------------------------------------------------------------- #


def test_server_hello_validates_against_schema(schema: dict[str, Any]) -> None:
    hello = ServerHello(
        accepted=False,
        node_id="windows-1",
        reason="the node credential was rejected",
        heartbeat_interval=15,
        offline_after=45,
    )
    _validate(schema, hello.model_dump(mode="json"))


def test_node_command_validates_against_schema(schema: dict[str, Any]) -> None:
    command = NodeCommand(
        command_id=uuid.uuid4(),
        tool="computer.screenshot",
        arguments={"node": "windows-1"},
        principal="user:alice",
        deadline=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        correlation_id="corr-1",
        task_id="task-1",
    )
    _validate(schema, command.model_dump(mode="json"))


def test_node_capabilities_validates_against_schema(schema: dict[str, Any]) -> None:
    capabilities = NodeCapabilities(
        node_id="windows-1",
        node_type=NodeType.WINDOWS,
        name="Laptop",
        capabilities=["windows_ui", "screenshots"],
        status=NodeStatus.ONLINE,
        last_seen=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )
    _validate(schema, capabilities.model_dump(mode="json"))


# --------------------------------------------------------------------------- #
# Node double output validates against the normative schema
# --------------------------------------------------------------------------- #


def test_registration_frame_validates(schema: dict[str, Any]) -> None:
    node = FakeNode()
    _validate(schema, node.registration())


def test_registration_without_credential_is_still_valid(schema: dict[str, Any]) -> None:
    node = FakeNode()
    _validate(schema, node.registration(credential=""))


def test_heartbeat_frame_validates(schema: dict[str, Any]) -> None:
    node = FakeNode()
    _validate(schema, node.heartbeat(status="online", capabilities=["windows_ui"]))


def test_bare_heartbeat_frame_validates(schema: dict[str, Any]) -> None:
    node = FakeNode()
    _validate(schema, node.heartbeat())


def test_heartbeat_ack_validates(schema: dict[str, Any]) -> None:
    ack = NodeHeartbeatAck(node_id="windows-1")
    _validate(schema, ack.model_dump(mode="json"))


async def test_ok_result_frame_validates(schema: dict[str, Any]) -> None:
    node = FakeNode(handlers={"computer.list_windows": lambda _args: {"windows": ["a", "b"]}})
    command = NodeCommand(command_id=uuid.uuid4(), tool="computer.list_windows")
    result = await node.result_for(command.model_dump(mode="json"))
    _validate(schema, result)


async def test_error_result_frame_validates(schema: dict[str, Any]) -> None:
    node = FakeNode()
    command = NodeCommand(command_id=uuid.uuid4(), tool="computer.screenshot")
    result = await node.result_for(command.model_dump(mode="json"))
    assert result["status"] == "error"
    _validate(schema, result)


def test_goodbye_frame_validates(schema: dict[str, Any]) -> None:
    node = FakeNode()
    _validate(schema, node.goodbye("done"))


def test_frames_are_self_identifying(schema: dict[str, Any]) -> None:
    """Every frame's ``type`` is one the schema's MessageType names."""
    known = {member.value for member in MessageType}
    node = FakeNode()
    frames = [node.registration(), node.heartbeat(), node.goodbye()]
    assert {frame["type"] for frame in frames} <= known
