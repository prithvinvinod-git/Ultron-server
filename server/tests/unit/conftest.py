"""Shared test doubles for the authentication subsystem (T021, spec §30).

The doubles and the fixtures that assemble them live here rather than in a test
module, because both the service tests (``test_authentication.py``) and the
route tests (``test_auth_routes.py``) need them. Importing fixtures from a
sibling test module works but is not something pytest documents, and a second
copy of a fake repository is a second thing to keep honest.

Each double mirrors the *behaviour* of the real repository, not just its
signature. That matters more than it sounds: the offline-device test once failed
because a fake matched on the token hash alone while the real repository also
required the device to be reachable. A double that is more permissive than the
thing it stands in for will eventually contradict it, and the failure will read
as a bug in production code.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from app.config import get_settings
from app.database.models import Device, DeviceStatus, Session, User
from app.database.repositories.devices import REACHABLE_STATUSES
from app.security.audit import AuditLogger, AuditOutcome
from app.security.authentication import Authenticator
from app.security.passwords import hash_password

if TYPE_CHECKING:
    from app.config import SecuritySettings

#: Every authentication test uses the same instant, so an assertion about a
#: timestamp cannot pass or fail depending on when the suite ran.
FIXED_NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Repository doubles
# --------------------------------------------------------------------------- #


class FakeUserRepository:
    """Indexed user lookups over a list, mirroring the real signatures."""

    def __init__(self, users: list[User] | None = None) -> None:
        self.users: list[User] = users or []
        self.commits = 0

    async def get(self, identifier: uuid.UUID | str) -> User | None:
        target = identifier if isinstance(identifier, uuid.UUID) else uuid.UUID(str(identifier))
        return next((u for u in self.users if u.id == target), None)

    async def get_by_username(self, username: str) -> User | None:
        return next((u for u in self.users if u.username == username), None)

    async def get_by_api_key_hash(self, key_hash: str) -> User | None:
        return next((u for u in self.users if u.api_key_hash == key_hash), None)


class FakeSessionRepository:
    """Session lookups plus a revocation counter.

    ``revoke_all_for_user`` returns an ``int`` because the real repository does,
    and the service logs that count; a double returning a list would let a
    ``len()`` slip through type checking and fail at runtime.
    """

    def __init__(self) -> None:
        self.sessions: list[Session] = []
        self.revocation_reasons: list[str] = []
        self.added: list[Session] = []

    async def add(self, instance: Session) -> Session:
        # A real flush assigns the client-minted default id.
        if instance.id is None:
            instance.id = uuid.uuid4()
        self.sessions.append(instance)
        self.added.append(instance)
        return instance

    async def get_by_token_hash(self, token_hash: str) -> Session | None:
        return next((s for s in self.sessions if s.token_hash == token_hash), None)

    async def get_by_refresh_token_hash(self, refresh_hash: str) -> Session | None:
        return next(
            (s for s in self.sessions if s.refresh_token_hash == refresh_hash),
            None,
        )

    async def revoke_all_for_user(
        self,
        user_id: uuid.UUID,
        *,
        reason: str = "manual",
        now: datetime | None = None,
    ) -> int:
        self.revocation_reasons.append(reason)
        moment = now or FIXED_NOW
        count = 0
        for session in self.sessions:
            if session.user_id == user_id and session.revoked_at is None:
                session.revoke(reason=reason, now=moment)
                count += 1
        return count


class FakeDeviceRepository:
    """A device repository that enforces the same reachability rule as the real one.

    An earlier version of this double matched on the token hash alone, and the
    offline-device test then failed against correct production code. The status
    filter is the whole point of ``DeviceRepository.authenticate``: a device that
    has been revoked or quarantined must not authenticate even with a valid token,
    so a double that ignores it can only ever disagree with the repository it
    stands in for.
    """

    def __init__(self, devices: list[Device] | None = None) -> None:
        self.devices = devices or []

    async def authenticate(self, token_hash: str) -> Device | None:
        return next(
            (
                d
                for d in self.devices
                if d.auth_token_hash == token_hash and d.status in REACHABLE_STATUSES
            ),
            None,
        )


class RecordingAuditRepository:
    """Captures audit rows in memory and offers assertions over them."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record(self, **fields: Any) -> None:
        self.rows.append(fields)

    def actions(self) -> list[str]:
        return [str(row["action"]) for row in self.rows]

    def decisions(self) -> list[AuditOutcome]:
        return [row["decision"] for row in self.rows]


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def security() -> SecuritySettings:
    """Security settings with an Argon2 cost a test suite can afford.

    The production defaults are the OWASP baseline, which is deliberately
    expensive: running them a few hundred times per suite would dominate the run
    time. This weakens only the cost, not the algorithm, so the tests still
    exercise real Argon2id rather than a stubbed hasher.
    """
    settings = get_settings().security
    return settings.model_copy(
        update={
            "argon2_time_cost": 1,
            "argon2_memory_cost": 8,
            "argon2_parallelism": 1,
            "access_token_ttl_seconds": 900,
            "refresh_token_ttl_seconds": 86_400,
        }
    )


@pytest.fixture
def make_user(security: SecuritySettings) -> Any:
    """Build a persisted-looking user with a usable password."""

    def factory(
        username: str = "alice",
        password: str = "correct horse battery staple",
        **overrides: Any,
    ) -> User:
        user = User(
            id=uuid.uuid4(),
            username=username,
            email=f"{username}@example.test",
            password_hash=hash_password(password, security),
            is_active=True,
            is_superuser=False,
            is_service_account=False,
        )
        for name, value in overrides.items():
            setattr(user, name, value)
        return user

    return factory


@pytest.fixture
def audit_sink() -> RecordingAuditRepository:
    return RecordingAuditRepository()


@pytest.fixture
def authenticator(
    security: SecuritySettings,
    audit_sink: RecordingAuditRepository,
) -> Iterator[
    tuple[
        Authenticator,
        FakeUserRepository,
        FakeSessionRepository,
        FakeDeviceRepository,
    ]
]:
    """Assemble the authenticator over doubles, with a frozen clock."""
    users = FakeUserRepository()
    sessions = FakeSessionRepository()
    devices = FakeDeviceRepository()
    service = Authenticator(
        users=users,  # type: ignore[arg-type]
        sessions=sessions,  # type: ignore[arg-type]
        devices=devices,  # type: ignore[arg-type]
        audit=AuditLogger(audit_sink),  # type: ignore[arg-type]
        settings=security,
        now=lambda: FIXED_NOW,
    )
    yield service, users, sessions, devices


@pytest.fixture
def device_factory() -> Any:
    """Build a device row with a reachable status unless told otherwise."""

    def factory(**overrides: Any) -> Device:
        defaults: dict[str, Any] = {
            "id": uuid.uuid4(),
            "name": "kitchen-speaker",
            "device_type": "speaker",
            "status": DeviceStatus.ONLINE,
            "last_seen": FIXED_NOW,
        }
        defaults.update(overrides)
        return Device(**defaults)

    return factory
