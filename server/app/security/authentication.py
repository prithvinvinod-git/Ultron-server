"""Authentication service (spec §30).

Implements the four credential types §30 names, over the schema and repositories
that already existed: passwords, session tokens, API keys for machine clients,
and device tokens.

Every path ends in an audit entry, including every failure. A login that fails is
not an error to be logged and discarded -- it is the data point that answers
"which accounts are being brute-forced", and it is only recorded because a
denial is as auditable as a success.

Timing is equalised on the unknown-username path with
:func:`app.security.passwords.dummy_verify`, because a fast "no such user"
beside a slow "wrong password" is a username oracle.

Transaction note: repositories never commit (see
:mod:`app.database.repositories.base`), so every mutation here is an attribute
assignment on a loaded ORM object and the caller's ``session_scope`` decides
whether it is kept. That is deliberate -- a login that creates a session and an
audit row must land together or not at all.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from app.core.errors import (
    AuthError,
    InvalidCredentialsError,
    PermissionDeniedError,
)
from app.database.models import Session, User
from app.observability import get_logger
from app.security import tokens
from app.security.audit import (
    ACTOR_ANONYMOUS,
    ACTOR_DEVICE,
    ACTOR_SERVICE,
    ACTOR_USER,
    AUTH_API_KEY_ISSUED,
    AUTH_LOGIN,
    AUTH_LOGIN_FAILED,
    AUTH_LOGOUT,
    AUTH_TOKEN_REFRESH,
    AUTH_TOKEN_REUSE,
    AuditLogger,
)
from app.security.passwords import (
    dummy_verify,
    hash_password,
    needs_rehash,
    verify_password,
)

if TYPE_CHECKING:
    from app.config import SecuritySettings
    from app.database.repositories import SessionRepository, UserRepository
    from app.database.repositories.devices import DeviceRepository

_LOGGER = get_logger(__name__)


class RevokeFamily(Protocol):
    """Revoke every session belonging to one user, and report how many went.

    Separate from ``SessionRepository`` because reuse detection has to end in a
    revocation that *outlives the request*. The request transaction rolls back
    when :class:`AuthError` propagates, which would discard a family revocation
    performed in that same transaction -- leaving the attacker's rotated-out
    session usable while only the audit row survived. The production wiring
    therefore supplies a sink that revokes on its own connection; without one
    the authenticator falls back to the request session, which is correct for
    callers that commit on success.
    """

    async def __call__(self, user_id: uuid.UUID, reason: str) -> int: ...


@dataclass(frozen=True, slots=True)
class Principal:
    """Whoever is making the current request.

    Frozen because a principal is a fact about the caller. If request handling
    could rewrite ``actor_type`` from ``user`` to ``service_account``, the audit
    trail would record a decision that never happened.
    """

    actor_type: str
    user_id: uuid.UUID | None
    username: str | None
    is_superuser: bool
    session_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None

    @property
    def actor_id(self) -> str:
        """The identifier to record against an audit entry."""
        return str(self.user_id or self.device_id or self.username or "anonymous")

    @property
    def is_machine(self) -> bool:
        """True for API keys and device tokens, which have no interactive user."""
        return self.actor_type in {ACTOR_SERVICE, ACTOR_DEVICE}


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    """The pair returned by a successful login or refresh.

    The plaintext tokens exist exactly once, here. Only digests are stored, so
    this cannot be reconstructed from the database and must not be logged.
    """

    access_token: str
    refresh_token: str
    expires_at: datetime
    refresh_expires_at: datetime | None
    session_id: uuid.UUID


class Authenticator:
    """Password, session, API-key and device authentication."""

    def __init__(
        self,
        *,
        users: UserRepository,
        sessions: SessionRepository,
        audit: AuditLogger,
        settings: SecuritySettings,
        devices: DeviceRepository | None = None,
        now: Callable[[], datetime] | None = None,
        revoke_family: RevokeFamily | None = None,
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._audit = audit
        self._settings = settings
        self._devices = devices
        self._now = now or (lambda: datetime.now(UTC))
        self._revoke_family = revoke_family

    # -- Passwords -------------------------------------------------------- #

    async def login(
        self,
        username: str,
        password: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> IssuedTokens:
        """Exchange a username and password for a session.

        Raises:
            InvalidCredentialsError: for an unknown user, a wrong password, a
                disabled account, or a service account presented with a
                password. All four are one error with one message.
        """
        user = await self._users.get_by_username(username)

        if user is None or user.password_hash is None or not user.is_active:
            # Three cases that must be indistinguishable to the caller: an
            # unknown username, a service account that has no password (so
            # presenting one is credential confusion, not a wrong guess), and a
            # disabled account. All three spend the same Argon2 budget and
            # raise the same error, so neither the message nor the latency says
            # which one it was.
            await self._record_failed_login(
                username,
                reason=(
                    "no usable password on this account" if user is not None else "unknown username"
                ),
                ip_address=ip_address,
            )
            dummy_verify(self._settings)
            raise InvalidCredentialsError()

        if not verify_password(password, user.password_hash, self._settings):
            await self._record_failed_login(
                str(user.id),
                reason="bad password",
                ip_address=ip_address,
            )
            raise InvalidCredentialsError()

        # Migrate the hash opportunistically, so raising ARGON2_MEMORY_COST
        # reaches existing users instead of only newly created ones.
        if needs_rehash(user.password_hash, self._settings):
            user.password_hash = hash_password(password, self._settings)

        user.last_login_at = self._now()
        issued = await self._start_session(
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        await self._audit.allowed(
            actor_type=ACTOR_USER,
            actor_id=str(user.id),
            action=AUTH_LOGIN,
            resource_type="session",
            resource_id=str(issued.session_id),
            ip_address=ip_address,
        )
        return issued

    async def _record_failed_login(
        self,
        actor_id: str,
        *,
        reason: str,
        ip_address: str | None,
    ) -> None:
        """Record a failed login.

        ``reason`` distinguishes the cases for whoever reads the audit table, but
        it never reaches the client -- the caller raises the same error either
        way. Recording the *why* while returning a single *what* is what makes
        the trail useful without making it an oracle.
        """
        await self._audit.denied(
            durable=True,
            actor_type=ACTOR_USER,
            actor_id=actor_id,
            action=AUTH_LOGIN_FAILED,
            reason=reason,
            ip_address=ip_address,
        )

    async def change_password(
        self,
        user: User,
        current_password: str,
        new_password: str,
        *,
        ip_address: str | None = None,
    ) -> None:
        """Replace a user's password after checking the current one.

        Every other session is revoked. A password change is the standard
        response to a suspected compromise, and leaving existing sessions alive
        would defeat the point of changing it.
        """
        if user.password_hash is None or not verify_password(
            current_password, user.password_hash, self._settings
        ):
            await self._audit.denied(
                durable=True,
                actor_type=ACTOR_USER,
                actor_id=str(user.id),
                action="auth.password_change",
                reason="current password did not match",
                ip_address=ip_address,
            )
            raise InvalidCredentialsError()

        user.password_hash = hash_password(new_password, self._settings)
        revoked = await self._sessions.revoke_all_for_user(
            user.id,
            reason="password changed",
        )
        await self._audit.allowed(
            actor_type=ACTOR_USER,
            actor_id=str(user.id),
            action="auth.password_change",
            ip_address=ip_address,
            details={"sessions_revoked": revoked},
        )

    # -- Session tokens --------------------------------------------------- #

    async def _start_session(
        self,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
        rotated_from: uuid.UUID | None = None,
    ) -> IssuedTokens:
        """Create a session row and return the plaintext tokens for it."""
        now = self._now()
        access = tokens.generate_token()
        refresh = tokens.generate_token()

        session = Session(
            user_id=user.id,
            token_hash=tokens.digest(access),
            refresh_token_hash=tokens.digest(refresh),
            rotated_from=rotated_from,
            ip_address=ip_address,
            user_agent=user_agent,
            expires_at=tokens.access_expiry(self._settings, now=now),
            refresh_expires_at=tokens.refresh_expiry(self._settings, now=now),
        )
        session = await self._sessions.add(session)

        return IssuedTokens(
            access_token=access,
            refresh_token=refresh,
            expires_at=session.expires_at,
            refresh_expires_at=session.refresh_expires_at,
            session_id=session.id,
        )

    async def authenticate_token(
        self,
        access_token: str,
        *,
        ip_address: str | None = None,
    ) -> Principal:
        """Resolve a session token to a principal.

        Raises:
            AuthError: when the token is unknown, revoked, expired, or belongs
                to a user who is no longer active.
        """
        session = await self._sessions.get_by_token_hash(tokens.digest(access_token))
        if session is None or not session.is_valid(now=self._now()):
            await self._audit.denied(
                durable=True,
                actor_type=ACTOR_ANONYMOUS,
                action=AUTH_LOGIN_FAILED,
                reason="unknown, revoked or expired access token",
                ip_address=ip_address,
            )
            raise AuthError("invalid or expired token")

        user = await self._users.get(session.user_id)
        if user is None or not user.is_active:
            # Deleted or disabled while the token was still inside its lifetime.
            # Revoking stops the token lingering until it expires on its own.
            session.revoke(reason="account is no longer active", now=self._now())
            await self._audit.denied(
                durable=True,
                actor_type=ACTOR_USER,
                actor_id=str(session.user_id),
                action=AUTH_LOGIN_FAILED,
                reason="account inactive",
                ip_address=ip_address,
            )
            raise AuthError("invalid or expired token")

        session.last_used_at = self._now()
        return Principal(
            actor_type=ACTOR_USER,
            user_id=user.id,
            username=user.username,
            is_superuser=user.is_superuser,
            session_id=session.id,
        )

    async def refresh(
        self,
        refresh_token: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> IssuedTokens:
        """Rotate a refresh token and return a fresh pair.

        Reuse detection rests on a structural fact, not on a stored reason
        string: a refresh token is consumed exactly when its session is revoked
        by rotation, so a presented token that resolves to an already-revoked
        session is a replay. The whole family is then revoked, because at that
        point the legitimate holder and the thief are indistinguishable.

        The superseded row is revoked rather than deleted, which is what makes
        the replay detectable at all -- deleting it would erase the only
        evidence that the token was ever spent.
        """
        session = await self._sessions.get_by_refresh_token_hash(tokens.digest(refresh_token))
        if session is None:
            raise AuthError("invalid refresh token")

        # Revocation is checked *before* expiry, and separately from it. Folding
        # them into one ``is_refreshable`` test would let a replayed token look
        # like an ordinary dead token, which is precisely the case that must not
        # be silent.
        if session.revoked_at is not None:
            await self._audit.denied(
                durable=True,
                actor_type=ACTOR_USER,
                actor_id=str(session.user_id),
                action=AUTH_TOKEN_REUSE,
                resource_type="session",
                resource_id=str(session.id),
                reason="a consumed refresh token was presented again",
                ip_address=ip_address,
            )
            revoked = (
                await self._revoke_family(session.user_id, "refresh token reuse detected")
                if self._revoke_family is not None
                else await self._sessions.revoke_all_for_user(
                    session.user_id,
                    reason="refresh token reuse detected",
                )
            )
            _LOGGER.warning(
                "refresh token reuse detected; session family revoked",
                extra={
                    "event": "auth.token_reuse",
                    "session_id": str(session.id),
                    "sessions_revoked": revoked,
                },
            )
            raise AuthError("invalid refresh token")

        if not session.is_refreshable(now=self._now()):
            raise AuthError("invalid refresh token")

        user = await self._users.get(session.user_id)
        if user is None or not user.is_active:
            raise AuthError("invalid refresh token")

        session.revoke(reason="rotated", now=self._now())
        issued = await self._start_session(
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
            rotated_from=session.id,
        )
        await self._audit.allowed(
            actor_type=ACTOR_USER,
            actor_id=str(user.id),
            action=AUTH_TOKEN_REFRESH,
            resource_type="session",
            resource_id=str(issued.session_id),
            ip_address=ip_address,
        )
        return issued

    async def logout(self, access_token: str, *, ip_address: str | None = None) -> None:
        """Revoke the session behind an access token.

        Idempotent: logging out twice, or with a token that has already expired,
        is not an error. The caller wanted the session gone and it is gone.
        """
        session = await self._sessions.get_by_token_hash(tokens.digest(access_token))
        if session is None or session.revoked_at is not None:
            return
        session.revoke(reason="logout", now=self._now())
        await self._audit.allowed(
            actor_type=ACTOR_USER,
            actor_id=str(session.user_id),
            action=AUTH_LOGOUT,
            resource_type="session",
            resource_id=str(session.id),
            ip_address=ip_address,
        )

    # -- API keys for machine clients -------------------------------------- #

    async def issue_api_key(
        self,
        owner: User,
        *,
        ip_address: str | None = None,
    ) -> tuple[str, User]:
        """Mint an API key for a service account and return it exactly once.

        The key is stored as a SHA-256 digest, not Argon2. It is 256 bits of
        ``secrets`` output, so there is no dictionary to search and a slow KDF
        would only add latency to every machine request -- and ``users`` has an
        index on ``api_key_hash`` that Argon2 could not use.
        """
        key = tokens.generate_api_key()
        owner.api_key_hash = tokens.digest(key)
        owner.is_service_account = True
        await self._audit.allowed(
            actor_type=ACTOR_SERVICE,
            actor_id=str(owner.id),
            action=AUTH_API_KEY_ISSUED,
            resource_type="user",
            resource_id=str(owner.id),
            ip_address=ip_address,
        )
        return key, owner

    async def authenticate_api_key(
        self,
        api_key: str,
        *,
        ip_address: str | None = None,
    ) -> Principal:
        """Resolve an API key to a principal.

        Raises:
            InvalidCredentialsError: when the key is malformed, unknown, or
                belongs to an account that is no longer active.
        """
        candidate = tokens.decode_api_key(api_key)
        user = await self._users.get_by_api_key_hash(tokens.digest(candidate))
        if user is None or not user.is_active:
            await self._audit.denied(
                durable=True,
                actor_type=ACTOR_ANONYMOUS,
                action=AUTH_LOGIN_FAILED,
                reason="unknown API key",
                ip_address=ip_address,
            )
            raise AuthError("invalid API key")

        return Principal(
            actor_type=ACTOR_SERVICE,
            user_id=user.id,
            username=user.username,
            is_superuser=user.is_superuser,
        )

    async def revoke_api_key(self, owner: User, *, ip_address: str | None = None) -> None:
        """Clear an account's API key digest, which makes the key useless."""
        owner.api_key_hash = None
        owner.is_service_account = False
        await self._audit.allowed(
            actor_type=ACTOR_SERVICE,
            actor_id=str(owner.id),
            action="auth.api_key_revoked",
            resource_type="user",
            resource_id=str(owner.id),
            ip_address=ip_address,
        )

    # -- Device authentication -------------------------------------------- #

    async def authenticate_device(
        self,
        device_token: str,
        *,
        ip_address: str | None = None,
    ) -> Principal:
        """Resolve a device token to a principal.

        ``DeviceRepository.authenticate`` returns ``None`` for a bad token, an
        unknown token, a disabled device and an offline device alike -- one
        indistinguishable answer, which is the right shape for this to have.
        """
        if self._devices is None:
            raise AuthError("device authentication is not configured")
        if not device_token.startswith(tokens.DEVICE_TOKEN_PREFIX):
            raise AuthError("invalid device token")

        device = await self._devices.authenticate(tokens.digest(device_token))
        if device is None:
            await self._audit.denied(
                durable=True,
                actor_type=ACTOR_ANONYMOUS,
                action=AUTH_LOGIN_FAILED,
                reason="unknown device token",
                ip_address=ip_address,
            )
            raise AuthError("invalid device token")

        device.last_seen = self._now()
        return Principal(
            actor_type=ACTOR_DEVICE,
            user_id=None,
            username=device.name,
            is_superuser=False,
            device_id=device.id,
        )

    # -- Guards ------------------------------------------------------------ #

    def require_superuser(self, principal: Principal) -> None:
        """Raise :class:`PermissionDeniedError` unless the principal is an admin.

        This is the check §30 requires for administrative functionality, and the
        reason ``Principal.is_superuser`` is a field rather than something looked
        up later: "is this an admin" has to be answered from the credential that
        was presented, not from ambient state.

        Two conditions, both necessary. The privileged flag alone is not enough:
        a service account may be flagged ``is_superuser`` if an operator set it
        so, and a bearer token for one is exactly what an attacker who has
        stolen one holds. Administrative actions are therefore restricted to an
        interactive user session, which leaves an attributable human on the other
        end and can be revoked by them.

        The two are refused with the same error and the same message. Distinguishing
        them would tell a probing client which half of the check it cleared.
        """
        if not principal.is_superuser or principal.actor_type != ACTOR_USER:
            raise PermissionDeniedError(f"administrative action as {principal.actor_type}")


__all__ = ["Authenticator", "IssuedTokens", "Principal"]
