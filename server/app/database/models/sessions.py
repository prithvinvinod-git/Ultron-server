"""Session model: `sessions`.

The spec names the table (line 1122) and specifies nothing about it. Section 30
requires sessions and tokens (line 1358); `.env.example` sets
`ACCESS_TOKEN_EXPIRE_MINUTES=60` and `REFRESH_TOKEN_EXPIRE_DAYS=30`
(lines 40-41).

Two decisions worth stating:

1.  **Only hashes are stored.** `token_hash` and `refresh_token_hash` hold
    digests. A stolen database therefore does not yield usable tokens, which is
    the same reasoning applied to `users.api_key_hash`.

2.  **Refresh rotation with reuse detection.** `rotated_from` links a replacement
    session to the one it supersedes, and `revoked_at` is set on the old row
    rather than deleting it. If a revoked token is ever presented again, the
    chain shows a rotated session being reused, which is the signal to revoke
    the whole family. Deleting the row would erase exactly that evidence.

`is_valid()` returns a bool and never raises, so it is safe to call on a session
whose expiry has passed or whose revocation is unrecorded.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.database.models.users import User


class Session(Base, UUIDPrimaryKeyMixin):
    """One authenticated session."""

    __tablename__ = "sessions"

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    refresh_token_hash: Mapped[str | None] = mapped_column(String(128), unique=True)
    rotated_from: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        doc="The session this one replaced during refresh rotation. SET NULL so "
        "that pruning an old session cannot cascade into its successor.",
    )
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(128))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="sessions", lazy="selectin")

    __table_args__ = (Index("ix_sessions_user_active", "user_id", "revoked_at", "expires_at"),)

    def is_valid(self, *, now: datetime | None = None) -> bool:
        """Report whether the access token is currently usable.

        `now` is injectable so the check is testable without freezing time.
        """
        moment = now or datetime.now(UTC)
        if self.revoked_at is not None:
            return False
        return self.expires_at > moment

    def is_refreshable(self, *, now: datetime | None = None) -> bool:
        """Report whether the refresh token may still be exchanged."""
        moment = now or datetime.now(UTC)
        if self.revoked_at is not None:
            return False
        if self.refresh_expires_at is None:
            return False
        return self.refresh_expires_at > moment

    def revoke(self, *, reason: str = "manual", now: datetime | None = None) -> None:
        """Revoke the session, recording why.

        Idempotent: revoking an already-revoked session leaves the original
        reason and timestamp alone, so the first cause of revocation is the one
        that gets reported.
        """
        if self.revoked_at is not None:
            return
        self.revoked_at = now or datetime.now(UTC)
        self.revoke_reason = reason

    def __repr__(self) -> str:
        state = "revoked" if self.revoked_at is not None else "active"
        return f"<Session {self.user_id} {state}>"
