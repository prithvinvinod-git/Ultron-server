"""Password hashing (spec §30: secure password handling).

Only Argon2id is offered. bcrypt and PBKDF2 are not "kept for compatibility"
here: there is no legacy installation to keep compatible with, and every
additional accepted algorithm is a way for a downgrade attack to succeed. A
hash that does not identify itself as Argon2id is rejected rather than guessed
at.

The parameters come from :class:`SecuritySettings`, so a deployment can raise
the cost without a code change, and the defaults are the OWASP 2024 baseline
for Argon2id.
"""

from __future__ import annotations

import contextlib
import hmac
from typing import TYPE_CHECKING

from argon2 import PasswordHasher
from argon2.exceptions import (
    HashingError,
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

from app.core.errors import ConfigError

if TYPE_CHECKING:
    from app.config import SecuritySettings

#: The one prefix this module will accept. Argon2 hashes carry their algorithm,
#: cost and salt in the encoded string, so the prefix is checked rather than
#: inferred from parameters that are not ours.
_REQUIRED_PREFIX = "$argon2id$"

#: Argon2 cannot produce a hash longer than this, so a longer "hash" is either a
#: different format or an attack payload rather than a password hash.
_MAX_HASH_LENGTH = 256


def _hasher(settings: SecuritySettings) -> PasswordHasher:
    """Build a hasher from configuration."""
    return PasswordHasher(
        time_cost=settings.argon2_time_cost,
        memory_cost=settings.argon2_memory_cost,
        parallelism=settings.argon2_parallelism,
        hash_len=32,
        salt_len=16,
    )


def hash_password(password: str, settings: SecuritySettings) -> str:
    """Return an Argon2id PHC string for ``password``.

    Raises:
        ConfigError: if the configured parameters are rejected by Argon2. This
            is a startup-time misconfiguration, not a user error.
    """
    try:
        return _hasher(settings).hash(password)
    except HashingError as error:
        raise ConfigError(
            "password hashing is misconfigured: "
            f"{settings.argon2_time_cost=}, {settings.argon2_memory_cost=}, "
            f"{settings.argon2_parallelism=}"
        ) from error


def verify_password(password: str, stored_hash: str, settings: SecuritySettings) -> bool:
    """Return whether ``password`` matches ``stored_hash``.

    A malformed or empty stored hash returns ``False`` rather than raising. The
    caller is answering "is this the right password", and "I cannot tell" must
    not become "yes" or a 500.

    The comparison inside Argon2 is constant-time, and :func:`hmac.compare_digest`
    is not needed on top of it. What *is* needed is a dummy verification for an
    account with no password, because otherwise the response time of "no such
    user" and "wrong password" differ and the endpoint becomes an oracle for
    which usernames exist.
    """
    if not stored_hash or len(stored_hash) > _MAX_HASH_LENGTH:
        return False
    if not stored_hash.startswith(_REQUIRED_PREFIX):
        return False
    try:
        return _hasher(settings).verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


#: Throwaway Argon2 hashes, keyed by the cost parameters they were generated
#: with. The key matters: a single cached hash would be verified against
#: whatever parameters are configured *now*, so raising ``ARGON2_MEMORY_COST``
#: at runtime would leave the equaliser running at the old cost and reintroduce
#: the timing gap it exists to close. Keying it means the first unknown-username
#: request after a parameter change pays one extra hash -- once -- and then the
#: two paths are equal again.
_DUMMY_HASHES: dict[tuple[int, int, int], str] = {}


def _dummy_key(settings: SecuritySettings) -> tuple[int, int, int]:
    """Return the cache key for a set of Argon2 cost parameters."""
    return (
        settings.argon2_time_cost,
        settings.argon2_memory_cost,
        settings.argon2_parallelism,
    )


def dummy_verify(settings: SecuritySettings) -> None:
    """Burn the time a real verification would, against a throwaway hash.

    Called on the "user not found" path so that the two responses cost the
    same. Without it, "no such user" returns in microseconds while "wrong
    password" takes the full Argon2 budget, and the difference is a reliable
    oracle for which usernames exist.

    The hash is generated rather than written into the source. A hardcoded
    literal would have to keep matching the configured cost parameters, and it
    only works at all while the library happens to accept it -- a stricter
    parser would make it fail instantly and reintroduce the very leak this is
    meant to remove.
    """
    key = _dummy_key(settings)
    cached = _DUMMY_HASHES.get(key)
    if cached is None:
        cached = _hasher(settings).hash("ultron-timing-equalizer")
        _DUMMY_HASHES[key] = cached

    # The verification is expected to fail -- that is the point of it. The
    # exceptions are suppressed because the outcome carries no information;
    # only the time it took is being spent.
    with contextlib.suppress(VerifyMismatchError, VerificationError, InvalidHashError):
        _hasher(settings).verify(cached, "not-a-real-password")


def reset_dummy_cache() -> None:
    """Discard the cached equaliser hashes.

    For tests that assert on cache behaviour. Production never calls this: the
    cache is only ever populated on demand, so it is self-managing.
    """
    _DUMMY_HASHES.clear()


def needs_rehash(stored_hash: str, settings: SecuritySettings) -> bool:
    """Return whether ``stored_hash`` should be replaced on next login.

    Argon2 encodes its own parameters, so this asks whether the stored hash was
    made with weaker settings than the ones now configured. It is how raising
    ``ARGON2_MEMORY_COST`` migrates existing passwords, instead of leaving every
    password on the old cost indefinitely.
    """
    if not stored_hash.startswith(_REQUIRED_PREFIX):
        return True
    try:
        return _hasher(settings).check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


def constant_time_equals(left: str, right: str) -> bool:
    """Compare two strings without leaking their contents through timing."""
    return hmac.compare_digest(left.encode(), right.encode())
