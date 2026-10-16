"""The computer-node agent family (T140–T145, spec §13/§42/§64).

The server side of §13's Computer Node architecture:

- :mod:`~app.agents.computer.protocol` — the wire contract (T140), mirrored by
  the JSON Schema documents in ``clients/protocol/``.
- :mod:`~app.agents.computer.auth` — node identity and credentials (T142).
- :mod:`~app.agents.computer.gateway` — registration, heartbeat, command
  dispatch and result correlation (T141).
- :mod:`~app.agents.computer.fallback` — the capability fallback chain (T145).
- :class:`~app.agents.computer.agent.ComputerAgent` — the worker that drives a
  model through the ``computer.*`` tools (T144).

It contains no Windows-specific logic: the node executes, the Core reasons
(§64.6.5). Nothing here imports a Windows-Use crate or runs a shell.
"""

from __future__ import annotations

from app.agents.computer.agent import ComputerAgent
from app.agents.computer.auth import NodeAuthenticator, NodeCredential, hash_token
from app.agents.computer.fallback import (
    DEFAULT_PRIORITY,
    FallbackAttempt,
    FallbackPlan,
    InteractionMethod,
    build_plan,
    methods_for_capabilities,
    select_method,
)
from app.agents.computer.gateway import ComputerNode, NodeGateway, NodeTransport
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

__all__ = [
    "CAPABILITY_STRINGS",
    "DEFAULT_PRIORITY",
    "ComputerAgent",
    "ComputerNode",
    "FallbackAttempt",
    "FallbackPlan",
    "InteractionMethod",
    "MessageType",
    "NodeAuthenticator",
    "NodeCapabilities",
    "NodeCommand",
    "NodeCredential",
    "NodeGateway",
    "NodeHeartbeat",
    "NodeHeartbeatAck",
    "NodeRegistration",
    "NodeResult",
    "NodeStatus",
    "NodeTransport",
    "NodeType",
    "ResultStatus",
    "ServerHello",
    "build_plan",
    "hash_token",
    "methods_for_capabilities",
    "select_method",
]
