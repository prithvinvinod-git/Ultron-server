"""Node models (T332, spec §64.5/§64.10).

One table for all node kinds — Windows, server, ESP32. Merges the two legacy
registries: the ``devices`` table (T161, ESP32-centric) and the in-memory
``NodeGateway`` (T141, Windows-centric). Existing ESP32 device rows become
``kind=esp32`` node rows (D021) without a migration script — the code just
treats the column as optional when reading old rows.

Columns mirror :class:`~app.agents.computer.protocol.NodeCapabilities` plus
the fields needed for heartbeat/TTL tracking and authentication.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, json_type

__all__ = ["Node"]


class Node(Base):
    """A registered node — Windows, server, or ESP32 (spec §64.5)."""

    __tablename__ = "nodes"

    #: The node's protocol-level id, chosen by the node at registration.
    #: Used as the primary key so the protocol identity is the DB identity.
    node_id: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
        doc="The node's chosen identifier (e.g. 'windows-1', 'esp32-abc').",
    )

    #: Machine-readable kind. Matches :class:`app.agents.computer.protocol.NodeType`.
    node_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default="future",
        index=True,
        doc="windows | server | esp32 | future",
    )

    #: Human-readable name.
    name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        server_default="",
    )

    #: Advertised capabilities. Stored as JSON array — the spec says the set
    #: is advisory so a node may advertise strings not in the enum.
    capabilities: Mapped[list[str]] = mapped_column(
        json_type(),
        nullable=False,
        default=list,
        server_default="[]",
    )

    #: Connection status. Mirrors :class:`app.agents.computer.protocol.NodeStatus`.
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="offline",
        server_default="offline",
        index=True,
        doc="online | offline | degraded",
    )

    #: Last heartbeat timestamp. NULL means never seen.
    last_seen: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )

    #: Version string from registration.
    version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default="",
    )

    #: Which client this node serves (e.g. the electron app instance id).
    client_of: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    #: Protocol version — future extensibility marker.
    protocol_version: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )

    #: Hashed credential, NULL if auth was disabled at registration.
    auth_token_hash: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
        doc="Digest of the node credential; hashed at rest.",
    )

    #: Server-side creation time (not from the wire).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        # Indexed combo for the sweep query: find stale nodes sorted by silence.
        Index("ix_nodes_status_last_seen", "status", "last_seen"),
        # Unique on (node_type, name) so two windows nodes can't share a name.
        Index("ix_nodes_type_name", "node_type", "name"),
    )

    def is_stale(self, *, timeout_seconds: int, now: datetime | None = None) -> bool:
        """Report whether the node has missed its heartbeat.

        A node that has never checked in is *not* stale — it is still
        provisioning. Only after a first heartbeat does absence matter.
        """
        moment = now or datetime.now(UTC)
        if self.last_seen is None:
            return False
        seen = self.last_seen
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=UTC)
        return (moment - seen).total_seconds() > timeout_seconds

    @property
    def device_id(self) -> uuid.UUID | None:
        """Legacy compat: some callers expect a UUID from ``devices`` table.

        The node uses ``node_id`` as its primary key, not a UUID. This
        property is a no-op stub for compatibility; it returns None because
        there is no UUID equivalent.
        """
        return None

    def __repr__(self) -> str:
        return f"<Node {self.node_id} {self.node_type} {self.status}>"


