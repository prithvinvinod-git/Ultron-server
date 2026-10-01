"""Identity models: `users` and `sessions`.

The spec names both tables (lines 1121-1122) and specifies no columns for
either. Section 30 requires authentication, API keys for machine clients
(lines 1358-1359), and device authentication (line 1360); `.env.example` fixes
the Argon2 cost factors (lines 47-49), a 60-minute access token and 30-day
refresh token (lines 40-41), and an `ADMIN_USERNAME`/`ADMIN_PASSWORD` bootstrap
pair (lines 44-45). Those requirements are what these columns are derived from.

`password_hash` holds a PHC-format Argon2id string. The plaintext password is
never stored and never logged: `verify_password` returns a bool and raises
nothing that could carry the input, so a caller cannot accidentally interpolate
it into an error message.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    json_type,
)
from app.database.models.enums import AuditOutcome

if TYPE_CHECKING:
    from app.database.models.sessions import Session


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A human account.

    `is_superuser` is the escalation flag for the operator account created from
    `.env` on first boot. It is not implied by `is_admin`-style role strings
    because a single boolean cannot be granted piecemeal, and section 31 wants
    role checks to be explicit.
    """

    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(128))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    is_superuser: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_service_account: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        doc=(
            "True for machine clients authenticated by API key (spec section 30). "
            "A service account has no password and is barred from the "
            "interactive login flow."
        ),
    )
    api_key_hash: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        doc="Argon2id hash of the machine-client API key. The key itself is never stored.",
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sessions: Mapped[list[Session]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (Index("ix_users_active", "is_active", "is_superuser"),)

    def __repr__(self) -> str:
        return f"<User {self.username}>"


class AuditLogEntry(Base, UUIDPrimaryKeyMixin):
    """Audit trail for sensitive operations (spec section 30, line 1391).

    Deliberately **not** part of the ORM's `users` relationship graph: audit
    rows are append-only, are written on their own session, and are never
    cascade-deleted when an account is removed. Losing the record of what a user
    did at the moment the user is deleted is precisely the case the audit log
    exists to cover.

    `decision` uses `AuditOutcome` because the permission system has three
    verdicts (allow / deny / confirm) and a log that recorded only "denied"
    would hide every confirmation prompt -- the case an auditor most wants to
    see.
    """

    __tablename__ = "audit_logs"

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=None,
        index=True,
        doc="Set explicitly by the writer, not by the database clock, so that an "
        "audit row can record the time the operation was *attempted* rather than "
        "the time the row happened to be flushed.",
    )
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    decision: Mapped[AuditOutcome] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    details: Mapped[dict[str, object]] = mapped_column(
        json_type(), nullable=False, default=dict, server_default="{}"
    )

    __table_args__ = (
        Index("ix_audit_logs_actor_action", "actor_type", "actor_id", "action"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
    )

    @property
    def denied(self) -> bool:
        """True when the operation was refused."""
        return self.decision is AuditOutcome.DENIED

    def __repr__(self) -> str:
        return f"<AuditLogEntry {self.action} -> {self.decision}>"
