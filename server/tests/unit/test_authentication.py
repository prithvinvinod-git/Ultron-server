"""Unit tests for the authentication service (T021, spec §30).

The service is exercised against in-memory repository doubles rather than a live
PostgreSQL, because what is being tested is the *decision* logic: which failure
produces which error, what gets audited, and when a whole family of sessions is
revoked. Those are questions about branching, and a mock answers them exactly;
transaction behaviour is a different layer and is tested there.

The doubles live in ``conftest.py`` because the route tests need them too, and a
second copy of a fake repository is a second thing to keep honest. See that file
for why each one mirrors the real repository's behaviour rather than just its
signature.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

from app.core.errors import AuthError, InvalidCredentialsError, PermissionDeniedError
from app.database.models import DeviceStatus
from app.security import tokens
from app.security.audit import (
    ACTOR_ANONYMOUS,
    ACTOR_DEVICE,
    ACTOR_SERVICE,
    ACTOR_USER,
    AUTH_API_KEY_ISSUED,
    AUTH_TOKEN_REUSE,
    AuditLogger,
    AuditOutcome,
)
from app.security.authentication import Authenticator, Principal
from app.security.passwords import verify_password
from tests.unit.conftest import (
    FIXED_NOW,
    FakeSessionRepository,
    FakeUserRepository,
)

if TYPE_CHECKING:
    from app.config import SecuritySettings

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Token authentication
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_authenticate_token_resolves_the_principal(
    authenticator: Any,
    make_user: Any,
) -> None:
    """A live session token becomes a user principal."""
    service, users, _, _ = authenticator
    user = make_user(is_superuser=True)
    users.users.append(user)
    issued = await service.login(user.username, "correct horse battery staple")

    principal = await service.authenticate_token(issued.access_token)

    assert principal.actor_type == ACTOR_USER
    assert principal.user_id == user.id
    assert principal.username == user.username
    assert principal.is_superuser is True
    assert principal.session_id == issued.session_id


@pytest.mark.asyncio
async def test_authenticate_token_records_last_use(authenticator: Any, make_user: Any) -> None:
    """Successful use updates ``last_used_at`` so stale sessions are visible."""
    service, users, sessions, _ = authenticator
    user = make_user()
    users.users.append(user)
    issued = await service.login(user.username, "correct horse battery staple")

    await service.authenticate_token(issued.access_token)

    assert sessions.sessions[0].last_used_at == FIXED_NOW


@pytest.mark.asyncio
async def test_authenticate_token_rejects_an_unknown_token(authenticator: Any) -> None:
    """A token this system never issued is refused."""
    service, *_ = authenticator

    with pytest.raises(AuthError):
        await service.authenticate_token(tokens.generate_token())


@pytest.mark.asyncio
async def test_authenticate_token_rejects_a_revoked_token(
    authenticator: Any,
    make_user: Any,
) -> None:
    """A revoked session stops working immediately, not at expiry."""
    service, users, _, _ = authenticator
    user = make_user()
    users.users.append(user)
    issued = await service.login(user.username, "correct horse battery staple")

    await service.logout(issued.access_token)

    with pytest.raises(AuthError):
        await service.authenticate_token(issued.access_token)


@pytest.mark.asyncio
async def test_authenticate_token_rejects_an_expired_token(
    authenticator: Any,
    make_user: Any,
) -> None:
    """An expired session is refused even though the row still exists."""
    service, users, sessions, _ = authenticator
    user = make_user()
    users.users.append(user)
    issued = await service.login(user.username, "correct horse battery staple")
    sessions.sessions[0].expires_at = FIXED_NOW - timedelta(seconds=1)

    with pytest.raises(AuthError):
        await service.authenticate_token(issued.access_token)


@pytest.mark.asyncio
async def test_authenticate_token_revokes_when_the_account_is_disabled(
    authenticator: Any,
    make_user: Any,
) -> None:
    """Disabling an account kills its live sessions rather than waiting them out.

    Leaving them valid until natural expiry would mean "disable this account"
    does not actually disable it for the length of the session TTL.
    """
    service, users, sessions, _ = authenticator
    user = make_user()
    users.users.append(user)
    issued = await service.login(user.username, "correct horse battery staple")
    user.is_active = False

    with pytest.raises(AuthError):
        await service.authenticate_token(issued.access_token)

    assert sessions.sessions[0].revoked_at is not None
    assert sessions.sessions[0].revoke_reason == "account is no longer active"


# --------------------------------------------------------------------------- #
# Refresh rotation and reuse
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_refresh_rotates_and_revokes_the_old_session(
    authenticator: Any,
    make_user: Any,
) -> None:
    """A refresh issues a new pair and retires the presented one."""
    service, users, sessions, _ = authenticator
    user = make_user()
    users.users.append(user)
    first = await service.login(user.username, "correct horse battery staple")

    second = await service.refresh(first.refresh_token)

    assert second.access_token != first.access_token
    assert second.refresh_token != first.refresh_token
    assert len(sessions.sessions) == 2
    original = sessions.sessions[0]
    assert original.revoked_at == FIXED_NOW
    assert original.revoke_reason == "rotated"
    assert sessions.sessions[1].rotated_from == original.id


@pytest.mark.asyncio
async def test_refresh_token_works_exactly_once(authenticator: Any, make_user: Any) -> None:
    """The superseded token is dead, so a copy of it is worthless."""
    service, users, _, _ = authenticator
    user = make_user()
    users.users.append(user)
    first = await service.login(user.username, "correct horse battery staple")
    await service.refresh(first.refresh_token)

    with pytest.raises(AuthError):
        await service.refresh(first.refresh_token)


@pytest.mark.asyncio
async def test_refresh_reuse_revokes_the_whole_family(
    authenticator: Any,
    audit_sink: Any,
    make_user: Any,
) -> None:
    """Replaying a consumed refresh token revokes every session for the user.

    Once an old token is presented there is no way to tell the legitimate holder
    from whoever replayed it, so the only safe move is to invalidate both.
    """
    service, users, sessions, _ = authenticator
    user = make_user()
    users.users.append(user)
    first = await service.login(user.username, "correct horse battery staple")
    second = await service.refresh(first.refresh_token)
    # A second, unrelated live session -- it must be caught by the family revoke.
    unrelated = await service.login(user.username, "correct horse battery staple")

    with pytest.raises(AuthError):
        await service.refresh(first.refresh_token)

    # Including the session the replayed token belonged to.
    assert all(session.revoked_at is not None for session in sessions.sessions)
    assert "refresh token reuse detected" in sessions.revocation_reasons
    # The audit trail names the specific event, not just a generic failure.
    assert AUTH_TOKEN_REUSE in audit_sink.actions()
    assert audit_sink.decisions().count(AuditOutcome.DENIED) >= 1
    # The rotated pair is collateral: the token the legitimate client holds dies
    # too, which is the intended cost of not knowing who replayed what.
    with pytest.raises(AuthError):
        await service.authenticate_token(second.access_token)
    with pytest.raises(AuthError):
        await service.authenticate_token(unrelated.access_token)


@pytest.mark.asyncio
async def test_refresh_reuse_revokes_the_family_outside_the_request_transaction(
    security: SecuritySettings,
    audit_sink: Any,
    make_user: Any,
) -> None:
    """The family revocation must survive the rollback that follows the refusal.

    Reuse detection ends by raising, and ``session_scope`` rolls back that raise.
    If the revocation shared the request transaction it would be undone by its
    own error path -- the audit row would survive while the attacker's
    rotated-out session stayed usable. This drives the wiring the dependency
    module uses: a ``revoke_family`` sink with its own storage, separate from the
    request session.
    """
    request_sessions = FakeSessionRepository()
    # Same rows, own connection: the durable sink reads and writes the same
    # table, it just commits on its own transaction.
    durable_sessions = FakeSessionRepository()
    durable_sessions.sessions = request_sessions.sessions
    users = FakeUserRepository()
    service = Authenticator(
        users=users,  # type: ignore[arg-type]
        sessions=request_sessions,  # type: ignore[arg-type]
        audit=AuditLogger(audit_sink),
        settings=security,
        now=lambda: FIXED_NOW,
        revoke_family=lambda user_id, reason: durable_sessions.revoke_all_for_user(
            user_id, reason=reason
        ),
    )
    user = make_user()
    users.users.append(user)
    first = await service.login(user.username, "correct horse battery staple")
    await service.refresh(first.refresh_token)

    with pytest.raises(AuthError):
        await service.refresh(first.refresh_token)

    assert all(s.revoked_at is not None for s in durable_sessions.sessions)
    assert "refresh token reuse detected" in durable_sessions.revocation_reasons
    # The request's own repository was never asked to revoke: that path would
    # have been rolled back by the raise it precedes.
    assert request_sessions.revocation_reasons == []
    assert AUTH_TOKEN_REUSE in audit_sink.actions()


@pytest.mark.asyncio
async def test_refresh_rejects_an_unknown_token(authenticator: Any) -> None:
    """An unissued refresh token is refused without touching any session."""
    service, _, sessions, _ = authenticator

    with pytest.raises(AuthError):
        await service.refresh(tokens.generate_token())

    assert sessions.sessions == []


@pytest.mark.asyncio
async def test_refresh_rejects_an_expired_refresh_token(
    authenticator: Any,
    make_user: Any,
) -> None:
    """An expired refresh token is refused.

    Distinguished from reuse: nothing is revoked here, because an expired token
    is a stale client rather than a stolen one, and revoking the family over it
    would let anyone holding an old token log the user out.
    """
    service, users, sessions, _ = authenticator
    user = make_user()
    users.users.append(user)
    issued = await service.login(user.username, "correct horse battery staple")
    sessions.sessions[0].refresh_expires_at = FIXED_NOW - timedelta(seconds=1)

    with pytest.raises(AuthError):
        await service.refresh(issued.refresh_token)

    assert sessions.revocation_reasons == []


# --------------------------------------------------------------------------- #
# Logout
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_logout_revokes_the_session(
    authenticator: Any,
    audit_sink: Any,
    make_user: Any,
) -> None:
    """Logout marks the session revoked and records why."""
    service, users, sessions, _ = authenticator
    user = make_user()
    users.users.append(user)
    issued = await service.login(user.username, "correct horse battery staple")

    await service.logout(issued.access_token)

    assert sessions.sessions[0].revoked_at == FIXED_NOW
    assert sessions.sessions[0].revoke_reason == "logout"
    assert any(row["action"] == "auth.logout" for row in audit_sink.rows)


@pytest.mark.asyncio
async def test_logout_is_idempotent(authenticator: Any, make_user: Any) -> None:
    """Logging out twice is not an error.

    It has to work after the token has already expired, which is exactly when a
    client reaches for logout.
    """
    service, users, sessions, _ = authenticator
    user = make_user()
    users.users.append(user)
    issued = await service.login(user.username, "correct horse battery staple")

    await service.logout(issued.access_token)
    await service.logout(issued.access_token)  # must not raise
    await service.logout(tokens.generate_token())  # unknown token, must not raise

    # The original reason survives: the first cause is the one that matters.
    assert sessions.sessions[0].revoke_reason == "logout"


# --------------------------------------------------------------------------- #
# API keys
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_issue_api_key_returns_the_key_once_and_stores_its_digest(
    authenticator: Any,
    audit_sink: Any,
    make_user: Any,
) -> None:
    """The plaintext key is returned once; only its digest is retained."""
    service, _, _, _ = authenticator
    user = make_user(is_service_account=True)

    key, returned = await service.issue_api_key(user)

    assert key.startswith(tokens.API_KEY_PREFIX)
    assert returned is user
    assert user.api_key_hash == tokens.digest(key)
    assert key not in user.api_key_hash
    assert user.is_service_account is True
    assert any(row["action"] == AUTH_API_KEY_ISSUED for row in audit_sink.rows)


@pytest.mark.asyncio
async def test_issue_api_key_leaves_the_owner_usable(
    authenticator: Any,
    make_user: Any,
) -> None:
    """Issuing a key does not destroy the row it was issued against.

    A digest that is overwritten in the wrong order, or cleared after being set,
    produces an account that looks fine and authenticates nobody.
    """
    service, users, _, _ = authenticator
    user = make_user()
    users.users.append(user)

    key, _ = await service.issue_api_key(user)

    resolved = await users.get_by_api_key_hash(tokens.digest(key))
    assert resolved is user


@pytest.mark.asyncio
async def test_authenticate_api_key_resolves_a_service_principal(
    authenticator: Any,
    make_user: Any,
) -> None:
    """A valid key yields a machine principal, not an interactive one."""
    service, users, _, _ = authenticator
    user = make_user(is_service_account=True)
    users.users.append(user)
    key, _ = await service.issue_api_key(user)

    principal = await service.authenticate_api_key(key)

    assert principal.actor_type == ACTOR_SERVICE
    assert principal.user_id == user.id
    assert principal.is_machine is True


@pytest.mark.asyncio
async def test_authenticate_api_key_rejects_a_disabled_account(
    authenticator: Any,
    make_user: Any,
) -> None:
    """A key belonging to a disabled account stops working."""
    service, users, _, _ = authenticator
    user = make_user(is_service_account=True)
    users.users.append(user)
    key, _ = await service.issue_api_key(user)
    user.is_active = False

    with pytest.raises(AuthError):
        await service.authenticate_api_key(key)


@pytest.mark.asyncio
async def test_revoke_api_key_makes_the_key_useless(
    authenticator: Any,
    make_user: Any,
) -> None:
    """Clearing the digest is what revokes a key; there is no key list to edit."""
    service, users, _, _ = authenticator
    user = make_user(is_service_account=True)
    users.users.append(user)
    key, _ = await service.issue_api_key(user)

    await service.revoke_api_key(user)

    assert user.api_key_hash is None
    with pytest.raises(AuthError):
        await service.authenticate_api_key(key)


# --------------------------------------------------------------------------- #
# Devices
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_authenticate_device_resolves_a_device_principal(
    authenticator: Any,
    device_factory: Any,
) -> None:
    """A valid device token yields a device principal with no user attached."""
    service, _, _, devices = authenticator
    token = tokens.generate_device_token()
    devices.devices.append(device_factory(auth_token_hash=tokens.digest(token)))

    principal = await service.authenticate_device(token)

    assert principal.actor_type == ACTOR_DEVICE
    assert principal.user_id is None
    assert principal.device_id is not None
    assert principal.username == "kitchen-speaker"
    assert principal.is_superuser is False


@pytest.mark.asyncio
async def test_authenticate_device_updates_last_seen(
    authenticator: Any,
    device_factory: Any,
) -> None:
    """A successful device authentication refreshes ``last_seen``."""
    service, _, _, devices = authenticator
    token = tokens.generate_device_token()
    device = device_factory(auth_token_hash=tokens.digest(token), last_seen=None)
    devices.devices.append(device)

    await service.authenticate_device(token)

    assert device.last_seen == FIXED_NOW


@pytest.mark.asyncio
async def test_authenticate_device_rejects_a_token_with_the_wrong_prefix(
    authenticator: Any,
) -> None:
    """A session token presented as a device token is refused on shape alone.

    Without the prefix check this would be hashed and looked up in the devices
    table, so a caller could aim any credential at any credential store.
    """
    service, *_ = authenticator

    with pytest.raises(AuthError):
        await service.authenticate_device(tokens.generate_token())


@pytest.mark.asyncio
async def test_authenticate_device_rejects_an_unknown_token(authenticator: Any) -> None:
    """An unknown device token is refused."""
    service, *_ = authenticator

    with pytest.raises(AuthError):
        await service.authenticate_device(tokens.generate_device_token())


@pytest.mark.asyncio
async def test_authenticate_device_rejects_an_offline_device(
    authenticator: Any,
    device_factory: Any,
) -> None:
    """An offline device cannot authenticate.

    The repository is expected to return ``None`` for an offline device; this
    pins that contract, because a service that re-implements the check would
    otherwise quietly accept one.
    """
    service, _, _, devices = authenticator
    token = tokens.generate_device_token()
    devices.devices.append(
        device_factory(auth_token_hash=tokens.digest(token), status=DeviceStatus.OFFLINE)
    )
    # ``FakeDeviceRepository.authenticate`` models the real one: an offline
    # device is not returned.
    assert await devices.authenticate(tokens.digest(token)) is None

    with pytest.raises(AuthError):
        await service.authenticate_device(token)


# --------------------------------------------------------------------------- #
# Principal
# --------------------------------------------------------------------------- #


def test_principal_actor_id_prefers_the_user_id() -> None:
    """The recorded actor is the user when there is one."""
    user_id = uuid.uuid4()
    principal = Principal(
        actor_type=ACTOR_USER,
        user_id=user_id,
        username="alice",
        is_superuser=False,
    )

    assert principal.actor_id == str(user_id)


def test_principal_actor_id_falls_back_to_the_device_then_the_username() -> None:
    """A device actor records its device id; an anonymous one its username."""
    device_id = uuid.uuid4()
    device = Principal(
        actor_type=ACTOR_DEVICE,
        user_id=None,
        username="speaker",
        is_superuser=False,
        device_id=device_id,
    )
    anonymous = Principal(
        actor_type=ACTOR_ANONYMOUS,
        user_id=None,
        username=None,
        is_superuser=False,
    )

    assert device.actor_id == str(device_id)
    assert anonymous.actor_id == "anonymous"


def test_principal_is_machine_only_for_service_and_device() -> None:
    """``is_machine`` distinguishes credentials with no human behind them."""
    service_principal = Principal(ACTOR_SERVICE, None, "bot", False)
    device_principal = Principal(ACTOR_DEVICE, None, "speaker", False)
    user_principal = Principal(ACTOR_USER, uuid.uuid4(), "alice", False)

    assert service_principal.is_machine is True
    assert device_principal.is_machine is True
    assert user_principal.is_machine is False


def test_require_superuser_allows_an_administrator() -> None:
    """An interactive superuser passes the guard."""
    service = Authenticator.__new__(Authenticator)  # no collaborators needed
    principal = Principal(ACTOR_USER, uuid.uuid4(), "root", True)

    service.require_superuser(principal)  # must not raise


def test_require_superuser_refuses_a_non_administrator() -> None:
    """A normal user is refused, and the refusal is a 403 not a 401.

    The distinction is the point: the caller *is* authenticated, the action is
    simply not permitted.
    """
    service = Authenticator.__new__(Authenticator)
    principal = Principal(ACTOR_USER, uuid.uuid4(), "alice", False)

    with pytest.raises(PermissionDeniedError) as caught:
        service.require_superuser(principal)

    assert caught.value.http_status == 403


def test_require_superuser_refuses_a_machine_credential() -> None:
    """An API key cannot be used to reach an administrative route.

    A service account may be flagged ``is_superuser`` if an operator set it so,
    and a bearer token for one is something an attacker may well have. The guard
    requires an interactive session, not merely a privileged flag.
    """
    service = Authenticator.__new__(Authenticator)
    principal = Principal(ACTOR_SERVICE, uuid.uuid4(), "bot", True)

    with pytest.raises(PermissionDeniedError):
        service.require_superuser(principal)


# --------------------------------------------------------------------------- #
# Password change
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_change_password_revokes_every_other_session(
    authenticator: Any,
    make_user: Any,
) -> None:
    """Changing a password kills the account's other sessions.

    A password change is the standard response to a suspected compromise; leaving
    the other sessions alive would leave the attacker in.
    """
    service, users, sessions, _ = authenticator
    user = make_user()
    users.users.append(user)
    await service.login(user.username, "correct horse battery staple")
    await service.login(user.username, "correct horse battery staple")

    await service.change_password(user, "correct horse battery staple", "a brand new passphrase")

    assert all(session.revoked_at is not None for session in sessions.sessions)
    assert "password changed" in sessions.revocation_reasons


@pytest.mark.asyncio
async def test_change_password_sets_a_verifiable_new_hash(
    authenticator: Any,
    make_user: Any,
    security: SecuritySettings,
) -> None:
    """The new hash verifies the new password and rejects the old one."""
    service, users, _, _ = authenticator
    user = make_user()
    users.users.append(user)

    await service.change_password(user, "correct horse battery staple", "a brand new passphrase")

    assert verify_password("a brand new passphrase", user.password_hash, security)
    assert not verify_password("correct horse battery staple", user.password_hash, security)


@pytest.mark.asyncio
async def test_change_password_requires_the_current_password(
    authenticator: Any,
    audit_sink: Any,
    make_user: Any,
) -> None:
    """A wrong current password is refused and audited, leaving the hash alone."""
    service, users, sessions, _ = authenticator
    user = make_user()
    users.users.append(user)
    original = user.password_hash

    with pytest.raises(InvalidCredentialsError):
        await service.change_password(user, "not the current password", "a brand new passphrase")

    assert user.password_hash == original
    assert sessions.revocation_reasons == []
    assert any(row["decision"] == AuditOutcome.DENIED for row in audit_sink.rows)
