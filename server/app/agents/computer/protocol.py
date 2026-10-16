"""The computer-node wire protocol (T140, spec §13/§42/§64).

One contract, spoken by the server gateway (T141) and the Windows node
(`clients/desktop-node/`, T146). The JSON Schema documents in
``clients/protocol/`` are the *normative* form — the node is not written in
Python, so the contract cannot live only in Python classes — and the Pydantic
models here are the server's typed view of those same documents. A contract
test (``tests/unit/test_node_protocol.py``) asserts the two agree field-for-field,
so a change to one that forgets the other fails the suite rather than the wire.

The protocol is deliberately small and message-oriented:

```text
node -> server   register       { node_id, node_type, capabilities, ... }
server -> node   registered     { accepted, heartbeat_interval, ... }
node -> server   heartbeat      { node_id, capabilities?, status? }
server -> node   heartbeat_ack  { server_time }
server -> node   command        { command_id, tool, arguments, ... }
node -> server   result         { command_id, status, result?, error?, ... }
either           goodbye        { reason }
```

Two rules from §59.15/§64.13 shape every field:

- **The connection is outbound from the node.** The node dials the server; the
  server never dials into the LAN. Nothing here assumes a listening socket on
  the node.
- **A node credential authenticates a node, never a user.** ``auth`` carries a
  node credential; the principal that *asked* for a command rides on the
  command instead, and the two are separate principals (§64.13).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CAPABILITY_STRINGS",
    "MessageType",
    "NodeCapabilities",
    "NodeCommand",
    "NodeHeartbeat",
    "NodeHeartbeatAck",
    "NodeRegistration",
    "NodeResult",
    "NodeStatus",
    "NodeType",
    "ResultStatus",
    "ServerHello",
]


class NodeType(StrEnum):
    """§64.5's node inventory. The set is closed; `future` is the escape hatch
    §64.2 asks for so a new kind can register without a schema change."""

    WINDOWS = "windows"
    SERVER = "server"
    ESP32 = "esp32"
    FUTURE = "future"


class NodeStatus(StrEnum):
    """§64.10's status vocabulary, verbatim: `online | offline | degraded`."""

    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"


class ResultStatus(StrEnum):
    """The outcome of one command, from the node."""

    OK = "ok"
    ERROR = "error"


class MessageType(StrEnum):
    """The ``type`` discriminator on every wire message."""

    REGISTER = "register"
    REGISTERED = "registered"
    HEARTBEAT = "heartbeat"
    HEARTBEAT_ACK = "heartbeat_ack"
    COMMAND = "command"
    RESULT = "result"
    GOODBYE = "goodbye"


#: The capability *families* a node may advertise (§64.6.2 Windows, §64.7
#: server, §64.9 ESP32). Like §66.9's capability vocabulary this is advisory:
#: a node may advertise a string not in this list, and the registry stores
#: whatever was advertised. The set exists so the common names have one
#: spelling and the ESP32 display can map them to icons.
CAPABILITY_STRINGS: tuple[str, ...] = (
    # Windows (§64.6.2)
    "filesystem",
    "powershell",
    "windows_apps",
    "windows_ui",
    "browser",
    "git",
    "processes",
    "screenshots",
    "notifications",
    "clipboard",
    # Server (§64.7)
    "linux_apps",
    "linux_shell",
    "server_management",
    "background_agents",
    "scheduler",
    "services",
    # ESP32 (§64.9)
    "display",
    "microphone",
    "speaker",
    "buttons",
    "sensors",
    "physical_controls",
)


class NodeRegistration(BaseModel):
    """The first message a node sends: who it is and what it can do."""

    model_config = ConfigDict(extra="forbid")

    type: MessageType = MessageType.REGISTER
    node_id: str = Field(min_length=1, max_length=128)
    node_type: NodeType
    name: str = Field(default="", max_length=128)
    capabilities: list[str] = Field(default_factory=list)
    client_of: str | None = Field(default=None, max_length=64)
    version: str = Field(default="", max_length=64)
    firmware_version: str | None = Field(default=None, max_length=64)
    #: The node credential §64.13 issues. Opaque to the model; hashed at rest.
    credential: str | None = Field(default=None, max_length=512)


class ServerHello(BaseModel):
    """The server's answer to a registration: accepted or not, and the rules."""

    model_config = ConfigDict(extra="forbid")

    type: MessageType = MessageType.REGISTERED
    accepted: bool
    node_id: str
    reason: str | None = None
    heartbeat_interval: int = Field(ge=1)
    offline_after: int = Field(ge=1)
    server_time: datetime = Field(default_factory=lambda: datetime.now(UTC))


class NodeHeartbeat(BaseModel):
    """A periodic liveness message; may refresh the advertised capabilities."""

    model_config = ConfigDict(extra="forbid")

    type: MessageType = MessageType.HEARTBEAT
    node_id: str = Field(min_length=1, max_length=128)
    status: NodeStatus | None = None
    capabilities: list[str] | None = None


class NodeHeartbeatAck(BaseModel):
    """The server's answer to a heartbeat: liveness acknowledged, with its clock."""

    model_config = ConfigDict(extra="forbid")

    type: MessageType = MessageType.HEARTBEAT_ACK
    node_id: str
    server_time: datetime = Field(default_factory=lambda: datetime.now(UTC))


class NodeCommand(BaseModel):
    """One structured command (§13: never an arbitrary shell string)."""

    model_config = ConfigDict(extra="forbid")

    type: MessageType = MessageType.COMMAND
    command_id: UUID
    tool: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    #: Who asked, for the node-side permission re-check (§64.6.3 Path B).
    principal: str | None = Field(default=None, max_length=128)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    #: Absolute deadline; the node should abandon the work once past it.
    deadline: datetime | None = None
    correlation_id: str | None = Field(default=None, max_length=128)
    task_id: str | None = Field(default=None, max_length=64)


class NodeResult(BaseModel):
    """The node's answer to one command, correlated by ``command_id``."""

    model_config = ConfigDict(extra="forbid")

    type: MessageType = MessageType.RESULT
    command_id: UUID
    node_id: str = Field(min_length=1, max_length=128)
    status: ResultStatus
    result: Any = None
    error_code: str | None = Field(default=None, max_length=64)
    error: str | None = Field(default=None, max_length=2048)
    duration_ms: int | None = Field(default=None, ge=0)


class NodeCapabilities(BaseModel):
    """The capability/state view of one node — the shape `GET /nodes` returns."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    node_type: NodeType
    name: str
    capabilities: list[str]
    status: NodeStatus
    last_seen: datetime | None = None
    connected_at: datetime | None = None
    version: str = ""
    client_of: str | None = None
