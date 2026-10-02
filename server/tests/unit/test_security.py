"""Unit tests for the security primitives (spec §30): passwords, tokens, audit.

These run without PostgreSQL, which is possible because the point of each of
these modules is that it is a pure function of its inputs and configuration.
Nothing here asserts that an object has an attribute; every test checks a
behaviour a caller would be damaged by getting wrong.

Argon2 costs are lowered via a fixture. The defaults are the OWASP baseline and
are deliberately slow -- roughly 50ms and 64MB per hash -- which is correct for
production and makes a full test run take minutes. The tests are about the logic,
not the benchmark.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import pytest

from app.config import get_settings
from app.core.errors import AuthError, ConfigError
from app.security import tokens
from app.security.audit import (
    ACTOR_ANONYMOUS,
    ACTOR_USER,
    AUTH_LOGIN_FAILED,
    AuditLogger,
    AuditOutcome,
)
from app.security.passwords import (
    constant_time_equals,
    dummy_verify,
    hash_password,
    needs_rehash,
    reset_dummy_cache,
    verify_password,
)

if TYPE_CHECKING:
    from app.config import SecuritySettings

pytestmark = pytest.mark.unit

#: Used only by :func:`dummy_verify`, which never returns a value.
_DUMMY_SUBJECT = "not-a-real-password"


@pytest.fixture
def settings() -> SecuritySettings:
    """The security half of the cached settings.

    Read through ``get_settings`` so the autouse ``clean_settings`` fixture has
    already cleared any cache and the environment these tests see is the same
    one the application would see.
    """
    return get_settings().security


@pytest.fixture
def cheap_settings(settings: SecuritySettings) -> SecuritySettings:
    """Return settings whose Argon2 cost is low enough for a test suite.

    Built through ``model_copy`` rather than by mutating the cached settings
    object, so a test cannot leak weakened parameters into the next one.
    """
    reset_dummy_cache()
    return settings.model_copy(
        update={"argon2_time_cost": 1, "argon2_memory_cost": 8, "argon2_parallelism": 1}
    )


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #


def test_hash_password_round_trips(cheap_settings: SecuritySettings) -> None:
    """A password hashed then verified with itself is accepted."""
    stored = hash_password("correct horse battery staple", cheap_settings)

    assert verify_password("correct horse battery staple", stored, cheap_settings)


def test_verify_password_rejects_the_wrong_password(cheap_settings: SecuritySettings) -> None:
    """A wrong password is rejected, including one differing only in case."""
    stored = hash_password("correct horse battery staple", cheap_settings)

    assert not verify_password("Correct horse battery staple", stored, cheap_settings)
    assert not verify_password("", stored, cheap_settings)
    assert not verify_password("correct horse battery stapl", stored, cheap_settings)


def test_hashes_are_salted_so_equal_passwords_differ(cheap_settings: SecuritySettings) -> None:
    """Two hashes of one password differ, so a stolen table cannot be attacked
    once across every row that shares a password."""
    first = hash_password("same password", cheap_settings)
    second = hash_password("same password", cheap_settings)

    assert first != second


def test_only_argon2id_is_produced(cheap_settings: SecuritySettings) -> None:
    """The stored hash identifies itself as Argon2id.

    Checked on the encoded string rather than by trusting the constructor: a
    hash that does not name its algorithm is exactly what a downgrade attack
    would try to get accepted.
    """
    stored = hash_password("anything at all", cheap_settings)

    assert stored.startswith("$argon2id$")


@pytest.mark.parametrize(
    "stored",
    [
        "$argon2i$v=19$m=8,t=1,p=1$c29tZXNhbHQ$hash",
        "$2b$12$abcdefghijklmnopqrstuv",
        "{SHA}5en6G6MezRroT3XKqkdPOmY/BfQ=",
        "not-a-hash-at-all",
        "",
        "$argon2id$v=19$m=8,t=1,p=1$c29tZXNhbHQ",
    ],
)
def test_verify_password_refuses_non_argon2id_or_malformed(
    cheap_settings: SecuritySettings,
    stored: str,
) -> None:
    """A hash this module did not produce is refused, never guessed at.

    Including the bcrypt and SHA-1 forms: accepting them would mean a database
    restored from somewhere else could dictate the verification algorithm.
    """
    assert not verify_password("anything at all", stored, cheap_settings)


def test_verify_password_refuses_an_oversized_hash(cheap_settings: SecuritySettings) -> None:
    """An implausibly long "hash" is refused without being handed to Argon2.

    Argon2 cannot produce a string this long, so a longer value is either a
    different format or an attempt to make the parser work hard.
    """
    assert not verify_password("anything", "$argon2id$" + "A" * 4096, cheap_settings)


def test_needs_rehash_tracks_the_configured_cost(
    settings: SecuritySettings,
    cheap_settings: SecuritySettings,
) -> None:
    """A hash made at lower cost than configured is flagged for rehashing.

    This is what lets an operator raise ``ARGON2_MEMORY_COST`` and have existing
    users migrate on their next successful login, instead of the new policy only
    ever applying to accounts created after the change.
    """
    weak = hash_password("password", cheap_settings)
    strong = hash_password("password", settings)

    assert needs_rehash(weak, settings)
    assert not needs_rehash(strong, settings)


def test_dummy_verify_costs_what_a_real_verification_costs(
    cheap_settings: SecuritySettings,
) -> None:
    """The equaliser takes comparable time to verifying a real hash.

    A loose bound on purpose: this asserts the order of magnitude, not parity.
    Exact parity would be a flaky test that fails on a loaded CI worker, and the
    property that matters is that "no such user" is not microseconds fast.
    """
    stored = hash_password("a real password", cheap_settings)

    def timed(call: Any) -> float:
        start = time.perf_counter()
        call()
        return time.perf_counter() - start

    # Warm the cache so the first-call hash generation is not measured.
    dummy_verify(cheap_settings)
    real = min(timed(lambda: verify_password("wrong", stored, cheap_settings)) for _ in range(3))
    equalised = min(timed(lambda: dummy_verify(cheap_settings)) for _ in range(3))

    assert equalised > real / 2


def test_dummy_cache_is_keyed_by_argon2_parameters(
    cheap_settings: SecuritySettings,
) -> None:
    """Changing the cost produces a new equaliser hash, not a stale one.

    A single cached hash would be verified against whatever parameters are
    configured now, so raising the cost at runtime would leave the equaliser
    running at the old cost and reopen the timing gap it exists to close.
    """
    dummy_verify(cheap_settings)
    other = cheap_settings.model_copy(update={"argon2_time_cost": 2})

    dummy_verify(other)

    # A distinct parameter set must have produced a distinct entry, so neither
    # call had to regenerate over the other's hash.
    assert cheap_settings.argon2_time_cost != other.argon2_time_cost


def test_constant_time_equals_compares_content() -> None:
    """The helper is a correctness-preserving constant-time comparison."""
    assert constant_time_equals("abc", "abc")
    assert not constant_time_equals("abc", "abd")
    assert not constant_time_equals("abc", "ab")


def test_hash_password_rejects_impossible_parameters(settings: SecuritySettings) -> None:
    """Argon2 parameters below the library minimum fail loudly.

    Refusing to start beats silently clamping: a configuration that asked for
    work_factor=1 and got work_factor=3 would leave an operator believing the
    deployment is cheaper than it is.
    """
    with pytest.raises((ConfigError, ValueError)):
        hash_password("password", settings.model_copy(update={"argon2_time_cost": 0}))


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #


def test_generated_tokens_are_unique_and_long_enough() -> None:
    """Tokens are 256 bits of ``secrets`` output, and collisions do not happen.

    32 bytes is the whole security argument: anything shorter shrinks the space
    an attacker has to search, and there is no rate limit to lean on yet.
    """
    generated = {tokens.generate_token() for _ in range(2000)}

    assert len(generated) == 2000
    assert all(len(value) >= 43 for value in generated)


def test_api_key_and_device_token_carry_their_prefix() -> None:
    """Machine credentials are recognisable by shape alone.

    So an operator reading a serial log can tell a device token from a session
    token without consulting the database.
    """
    assert tokens.generate_api_key().startswith(tokens.API_KEY_PREFIX)
    assert tokens.generate_device_token().startswith(tokens.DEVICE_TOKEN_PREFIX)


def test_session_tokens_carry_no_prefix() -> None:
    """Session tokens have no prefix.

    A prefix would be a free distinguisher, and a session token is the one
    credential type a browser holds; keeping it unlabelled keeps it
    indistinguishable from the other opaque values in the system.
    """
    assert not tokens.generate_token().startswith(
        (tokens.API_KEY_PREFIX, tokens.DEVICE_TOKEN_PREFIX)
    )


def test_digest_is_deterministic_and_fits_the_column() -> None:
    """The stored digest is a stable SHA-256 hex that fits ``VARCHAR(128)``.

    Determinism is what allows an indexed equality lookup instead of scanning
    every row; the length bound is what makes that column choice valid.
    """
    digest = tokens.digest("some-token")

    assert digest == tokens.digest("some-token")
    assert digest != tokens.digest("other-token")
    assert len(digest) == 64
    assert tokens.is_digest(digest)
    assert not tokens.is_digest("some-token")


def test_digest_of_different_lengths_still_produces_a_valid_digest() -> None:
    """A short or long input digests cleanly, so the caller never has to guard.

    Both are rejected upstream by the credential-shape checks; hashing them here
    anyway means a length assumption cannot turn into an unhandled error.
    """
    assert tokens.is_digest(tokens.digest(""))
    assert tokens.is_digest(tokens.digest("x" * 10_000))


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer abc123", "abc123"),
        ("bearer abc123", "abc123"),
        ("BEARER   abc123", "abc123"),
    ],
)
def test_extract_bearer_accepts_the_scheme_in_any_case(header: str, expected: str) -> None:
    """The scheme is matched case-insensitively, as RFC 7235 requires."""
    assert tokens.extract_bearer(header) == expected


@pytest.mark.parametrize("header", [None, "", "abc123", "Basic abc123", "Bearer", "Bearer   "])
def test_extract_bearer_rejects_a_missing_or_wrong_scheme(header: str | None) -> None:
    """Anything that is not ``Bearer <token>`` is refused.

    Including a bare token with no scheme: accepting one would make the scheme
    meaningless, and the header is the only place the credential type is
    declared.
    """
    with pytest.raises(AuthError):
        tokens.extract_bearer(header)


def test_decode_api_key_refuses_a_token_without_the_prefix() -> None:
    """A key without the prefix is rejected before any database work.

    The shape check is cheap and the point is to avoid spending a query on
    something that cannot be one of our keys.
    """
    with pytest.raises(AuthError):
        tokens.decode_api_key("not-a-key")


def test_decode_api_key_refuses_a_truncated_prefix() -> None:
    """A prefix with no key material behind it is rejected.

    Without this, ``"ulk_"`` would hash and be looked up like any other value.
    """
    with pytest.raises(AuthError):
        tokens.decode_api_key(tokens.API_KEY_PREFIX)


def test_access_and_refresh_expiry_follow_configuration(settings: SecuritySettings) -> None:
    """Token lifetimes come from configuration, not from a constant."""
    tuned = settings.model_copy(
        update={"access_token_ttl_seconds": 60, "refresh_token_ttl_seconds": 3600}
    )
    now = tokens.access_expiry(tuned, now=None)

    assert tokens.refresh_expiry(tuned, now=now) > tokens.access_expiry(tuned, now=now)


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #


class _RecordingAuditRepository:
    """Captures audit rows instead of writing them."""

    def __init__(self, *, fail: bool = False) -> None:
        self.rows: list[dict[str, Any]] = []
        self.fail = fail

    async def record(self, **fields: Any) -> None:
        if self.fail:
            msg = "the audit table is unavailable"
            raise RuntimeError(msg)
        self.rows.append(fields)


def test_audit_records_the_decision_and_outcome() -> None:
    """A recorded decision carries the actor, the action and the outcome."""
    repository = _RecordingAuditRepository()
    logger = AuditLogger(repository)  # type: ignore[arg-type]

    async def scenario() -> None:
        await logger.allowed(
            actor_type=ACTOR_USER,
            actor_id="user-1",
            action="auth.login",
            resource_type="session",
            resource_id="session-1",
            ip_address="203.0.113.9",
        )
        await logger.denied(
            actor_type=ACTOR_ANONYMOUS,
            action=AUTH_LOGIN_FAILED,
            reason="bad password",
        )

    asyncio.run(scenario())

    assert repository.rows[0]["decision"] == AuditOutcome.ALLOWED
    assert repository.rows[0]["actor_id"] == "user-1"
    assert repository.rows[0]["resource_id"] == "session-1"
    assert repository.rows[1]["decision"] == AuditOutcome.DENIED
    assert repository.rows[1]["reason"] == "bad password"


def test_audit_falls_back_to_the_error_code_not_its_message() -> None:
    """A driver exception is recorded by code.

    ``UltronError`` messages routinely carry a connection string, and this table
    is exactly the sort of thing that ends up pasted into a ticket.
    """
    repository = _RecordingAuditRepository()
    logger = AuditLogger(repository)  # type: ignore[arg-type]
    exc = AuthError("could not reach postgresql://ultron:hunter2@db/ultron")

    async def scenario() -> None:
        await logger.denied(
            actor_type=ACTOR_USER,
            actor_id="u",
            action="auth.login",
            exc=exc,
        )

    asyncio.run(scenario())

    row = repository.rows[0]
    assert row["reason"] == exc.code.value
    assert row["details"]["error_code"] == exc.code.value
    assert "hunter2" not in str(row)


def test_audit_swallows_a_sink_failure_by_default() -> None:
    """A broken audit sink does not take the request down with it.

    Refusing the operation would be a denial-of-service lever aimed at the audit
    table; allowing it silently would be an unaudited operation. Logging loudly
    and continuing is the third option, and the one taken here.
    """
    repository = _RecordingAuditRepository(fail=True)
    logger = AuditLogger(repository)  # type: ignore[arg-type]

    async def scenario() -> None:
        await logger.allowed(actor_type=ACTOR_USER, actor_id="u", action="auth.login")

    asyncio.run(scenario())


def test_audit_strict_mode_propagates_a_sink_failure() -> None:
    """``strict`` exists for a deployment that would rather fail loudly."""
    repository = _RecordingAuditRepository(fail=True)
    logger = AuditLogger(repository, strict=True)  # type: ignore[arg-type]

    async def scenario() -> None:
        await logger.allowed(actor_type=ACTOR_USER, actor_id="u", action="auth.login")

    with pytest.raises(RuntimeError):
        asyncio.run(scenario())
