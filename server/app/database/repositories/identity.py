"""Identity repositories: users, sessions, and the audit trail (T015).

The audit log is here rather than in its own module because writing an audit row
and writing the thing being audited belong together: a caller that forgets one
will forget the other too, and putting them side by side makes that visible at
review time.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.database.models import AuditLogEntry, AuditOutcome, Session, User
from app.database.repositories.base import UuidRepository, apply_limit, apply_offset


class UserRepository(UuidRepository[User]):
    """Users. Lookup by username and by API key."""

    model = User

    async def get_by_username(self, username: str) -> User | None:
        """Return the user with this username, or ``None``."""
        statement = select(User).where(User.username == username)
        return await self._fetch_one(statement)

    async def get_by_api_key_hash(self, key_hash: str) -> User | None:
        """Return the service account whose API key hashes to ``key_hash``.

        Takes the *hash*, never the key. A repository that accepted a plaintext
        key would be one careless log statement away from persisting a bearer
        credential.
        """
        statement = select(User).where(
            User.api_key_hash == key_hash,
            User.is_service_account.is_(True),
            User.is_active.is_(True),
        )
        return await self._fetch_one(statement)

    async def list_active(self, *, limit: int | None = None) -> list[User]:
        """Return active, non-service accounts, alphabetically."""
        statement = (
            select(User)
            .where(User.is_active.is_(True), User.is_service_account.is_(False))
            .order_by(User.username)
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_for_owner(
        self,
        owner_id: uuid.UUID,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[User]:
        """Return one user's active sessions' owner view: their own account row."""
        statement = (
            select(User)
            .where(User.id == owner_id, User.is_active.is_(True))
            .order_by(User.username)
        )
        return await self._fetch_all(apply_limit(apply_offset(statement, offset), limit))


class SessionRepository(UuidRepository[Session]):
    """Sessions. Lookup by token hash, never by the token itself."""

    model = Session

    async def get_by_token_hash(self, token_hash: str) -> Session | None:
        """Return the session whose access token hashes to ``token_hash``."""
        statement = select(Session).where(Session.token_hash == token_hash)
        return await self._fetch_one(statement)

    async def get_by_refresh_token_hash(self, refresh_hash: str) -> Session | None:
        """Return the session whose refresh token hashes to ``refresh_hash``.

        Used to detect refresh-token reuse. A hit on an *already revoked* session
        is the interesting case: it means a token that should have died is still
        in circulation, so the caller must revoke the whole rotation family
        rather than issue another token.
        """
        statement = select(Session).where(Session.refresh_token_hash == refresh_hash)
        return await self._fetch_one(statement)

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Session]:
        """Return a user's sessions, newest first."""
        statement = (
            select(Session).where(Session.user_id == user_id).order_by(Session.created_at.desc())
        )
        return await self._fetch_all(apply_limit(apply_offset(statement, offset), limit))

    async def list_active_for_user(self, user_id: uuid.UUID) -> list[Session]:
        """Return a user's unrevoked, unexpired sessions.

        Filtering on ``expires_at`` here rather than in the caller means "active"
        has one definition. A caller re-deriving expiry with its own clock
        comparison is how a list of live sessions ends up including an expired
        one.
        """
        statement = select(Session).where(
            Session.user_id == user_id,
            Session.revoked_at.is_(None),
            Session.expires_at > datetime.now(UTC),
        )
        return await self._fetch_all(apply_limit(statement))

    async def revoke_all_for_user(
        self,
        user_id: uuid.UUID,
        *,
        reason: str = "password_change",
        now: datetime | None = None,
    ) -> int:
        """Revoke every unrevoked session belonging to ``user_id``.

        Returns how many were revoked. Iterates rows rather than issuing one
        ``UPDATE ... WHERE revoked_at IS NULL``, because revocation must record
        its reason and timestamp, and :meth:`Session.revoke` is idempotent by
        design -- the first cause of revocation is the one that gets reported,
        which is the behaviour you want when a user changes their password.
        """
        statement = select(Session).where(
            Session.user_id == user_id,
            Session.revoked_at.is_(None),
        )
        result = await self._session.execute(statement)
        sessions = list(result.scalars().all())
        for session in sessions:
            session.revoke(reason=reason, now=now)
        await self._session.flush()
        return len(sessions)


class AuditLogRepository(UuidRepository[AuditLogEntry]):
    """The audit trail.

    Append-only by intent: there is no update, and the inherited per-row
    ``delete`` exists only to satisfy the base class. §30 asks for an audit log
    of sensitive operations; a log that can be edited after the fact does not
    audit.
    """

    model = AuditLogEntry

    async def record(
        self,
        *,
        actor_type: str,
        action: str,
        decision: AuditOutcome,
        actor_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
        details: dict[str, object] | None = None,
        occurred_at: datetime | None = None,
    ) -> AuditLogEntry:
        """Append one audit entry.

        ``actor_type``, ``action`` and ``decision`` are mandatory, so an entry
        cannot record *that* something happened without recording who decided
        and how. ``occurred_at`` defaults to now in this frame of reference
        rather than to the database clock, so the recorded time is the time the
        decision was made.
        """
        entry = AuditLogEntry(
            occurred_at=occurred_at or datetime.now(UTC),
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            decision=decision,
            reason=reason,
            request_id=request_id,
            ip_address=ip_address,
            details=details or {},
        )
        return await self.add(entry)

    async def list_for_actor(
        self,
        actor_type: str,
        actor_id: str,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[AuditLogEntry]:
        """Return one actor's decisions, newest first."""
        statement = (
            select(AuditLogEntry)
            .where(AuditLogEntry.actor_type == actor_type, AuditLogEntry.actor_id == actor_id)
            .order_by(AuditLogEntry.occurred_at.desc())
        )
        return await self._fetch_all(apply_limit(apply_offset(statement, offset), limit))

    async def list_for_resource(
        self,
        resource_type: str,
        resource_id: str,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[AuditLogEntry]:
        """Return every decision about one resource, newest first."""
        statement = (
            select(AuditLogEntry)
            .where(
                AuditLogEntry.resource_type == resource_type,
                AuditLogEntry.resource_id == resource_id,
            )
            .order_by(AuditLogEntry.occurred_at.desc())
        )
        return await self._fetch_all(apply_limit(apply_offset(statement, offset), limit))

    async def list_denied(self, *, limit: int | None = None) -> list[AuditLogEntry]:
        """Return refusals, newest first.

        Kept separate from the general listing because "what was refused" is the
        first question an operator asks, and it should not require a filter the
        caller could forget.
        """
        statement = (
            select(AuditLogEntry)
            .where(AuditLogEntry.decision == AuditOutcome.DENIED)
            .order_by(AuditLogEntry.occurred_at.desc())
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_awaiting_confirmation(
        self,
        *,
        limit: int | None = None,
    ) -> list[AuditLogEntry]:
        """Return decisions that were parked awaiting a human.

        A confirmation that was never resolved is a half-finished operation.
        Surfacing it as its own query means a stuck confirmation is visible
        rather than buried in a general listing.
        """
        statement = (
            select(AuditLogEntry)
            .where(AuditLogEntry.decision == AuditOutcome.CONFIRM_REQUIRED)
            .order_by(AuditLogEntry.occurred_at)
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_for_request(
        self, request_id: str, *, limit: int | None = None
    ) -> list[AuditLogEntry]:
        """Return every decision recorded under one request id.

        This is how a single user action is reconstructed across the services it
        touched, without a join on anything else.
        """
        statement = (
            select(AuditLogEntry)
            .where(AuditLogEntry.request_id == request_id)
            .order_by(AuditLogEntry.occurred_at)
        )
        return await self._fetch_all(apply_limit(statement, limit))


__all__ = ["AuditLogRepository", "SessionRepository", "UserRepository"]
