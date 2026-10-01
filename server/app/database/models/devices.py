"""Device models: `devices` and `device_events`.

The spec names both tables (lines 1131-1132) and gives two **different** field
lists for `devices`:

*   Section 26 (lines 1205-1213): `device_id`, `name`, `type`, `capabilities`,
    `status`, `last_seen`, `firmware_version` -- seven fields, no auth.
*   Appendix (lines 3029-3034): `device_id`, `capabilities`, `status`,
    `last_seen`, `firmware`, `authentication` -- six fields, auth instead of
    name/type.

The union is taken. Section 30 requires device authentication (line 1360) and
Phase 10 lists it again (line 2142), so `auth_token_hash` is kept and the
appendix's `firmware` is satisfied by naming the column `firmware_version` per
the main spec. Dropping `name` or `type` would lose data the primary spec
demands, so both are retained.

`auth_token_hash` holds a digest, never a token, for the same reason
`users.api_key_hash` does.

`capabilities` is JSONB: the spec requires an abstraction over device types
("Do not hard-code one ESP32 board into the Core", line 1227) and never
enumerates capabilities, so an enum would defeat the requirement.

`device_events` mirrors the `events` table's approach -- `event_type` is a free
string because the spec lists device event names as `wake_word`, `button_press`,
`sensor_event`, `audio_stream`, `device_online`, `device_offline` (lines
1218-1223) in lowercase prose form while the main event list is uppercase, and
because the same list will grow.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, UUIDPrimaryKeyMixin, json_type
from app.database.models.enums import DeviceStatus


class Device(Base, UUIDPrimaryKeyMixin):
    """A registered device (ESP32 or any gateway-attached client)."""

    __tablename__ = "devices"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    device_type: Mapped[str | None] = mapped_column(
        "type",
        String(64),
        index=True,
        doc="Column is 'type' per the spec (line 1208); the attribute is "
        "`device_type` because `type` shadows the builtin in the class body.",
    )
    capabilities: Mapped[list[str]] = mapped_column(
        json_type(),
        nullable=False,
        default=list,
        server_default="[]",
    )
    status: Mapped[DeviceStatus] = mapped_column(
        String(32),
        nullable=False,
        default=DeviceStatus.UNKNOWN,
        server_default=DeviceStatus.UNKNOWN.value,
        index=True,
    )
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    firmware_version: Mapped[str | None] = mapped_column(String(64))
    auth_token_hash: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        doc="Digest of the device credential. NULL for a device registered but "
        "not yet provisioned.",
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        json_type(),
        nullable=False,
        default=dict,
        server_default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    events: Mapped[list[DeviceEvent]] = relationship(
        back_populates="device",
        cascade="all, delete-orphan",
        lazy="raise_on_sql",
    )

    __table_args__ = (Index("ix_devices_status_last_seen", "status", "last_seen"),)

    @property
    def device_id(self) -> uuid.UUID:
        """The spec's name for this row's identifier."""
        return self.id

    def is_stale(self, *, timeout_seconds: int, now: datetime | None = None) -> bool:
        """Report whether the device has missed its heartbeat.

        `DEVICES_HEARTBEAT_TIMEOUT` defaults to 60s (`.env.example` line 263).
        A device that has never checked in is *not* stale -- absence of a first
        heartbeat means it is still provisioning, and calling that a timeout
        would flap every newly registered device straight to offline.
        """
        moment = now or datetime.now(UTC)
        if self.last_seen is None:
            return False
        seen = self.last_seen
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=UTC)
        return (moment - seen).total_seconds() > timeout_seconds

    def __repr__(self) -> str:
        return f"<Device {self.name} {self.status}>"


class DeviceEvent(Base, UUIDPrimaryKeyMixin):
    """One event reported by a device."""

    __tablename__ = "device_events"

    device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(json_type())
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    audio_path: Mapped[str | None] = mapped_column(
        String(1024),
        doc="Filesystem path for an audio_stream event. A path and not a blob: "
        "spec section 36 keeps uploads on the shared volume.",
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    device: Mapped[Device] = relationship(back_populates="events", lazy="raise_on_sql")

    __table_args__ = (Index("ix_device_events_device_received", "device_id", "received_at"),)

    @property
    def is_offline_signal(self) -> bool:
        """True for any spelling the spec uses to mean the device went offline.

        The spec gives three different names for the same fact: `device_offline`
        in the device event list (line 1223), `DEVICE_OFFLINE` in the bus list
        (line 2957), and `DEVICE_DISCONNECTED` in the main event list (line 985).
        A gateway may send any of them, so all three are recognised here
        (case-insensitively, since the spec is not consistent about casing) rather
        than forcing each integration to know which list its name came from.
        """
        return self.event_type.lower() in {
            "device_offline",
            "device_disconnected",
            "disconnected",
        }

    def __repr__(self) -> str:
        return f"<DeviceEvent {self.device_id} {self.event_type}>"
