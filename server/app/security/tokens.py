"""Session and API-key tokens (spec §30: session/token architecture).

Design decision: opaque random tokens stored as digests, not JWTs.

The spec asks for a "session/token architecture" (line 1358) and does not
mandate a token format. Two things in this codebase argue against JWTs:

1.  ``sessions`` carries ``revoked_at``, ``revoke_reason``, ``rotated_from`` and
    ``last_used_at``, and its docstring states the reason explicitly: "If a
    revoked token is ever presented again, the chain shows a rotated session
    being reused, which is the signal to revoke the whole family. Deleting the
    row would erase exactly that evidence." A signed JWT cannot be revoked
    before it expires, so this mechanism only works for opaque tokens.
2.  PostgreSQL is the authoritative store (spec §2). Every authenticated request
    therefore reads one indexed row, which is the price of being able to kill a
    session the instant it is compromised.

``SecuritySettings.jwt_secret`` and the ``PyJWT`` dependency are consequently
*not* used by this scheme. They are left in place because they are already
documented in ``.env.example``, but nothing treats them as load-bearing, and a
deployment that leaves ``JWT_SECRET`` at its placeholder is not exposed by this
code path. That is stated here so the unused setting is not mistaken for a
control that is working.

Hashing: session tokens use SHA-256, not Argon2. These are 256-bit random
values, not user-chosen passwords, so there is no dictionary to search and the
deliberate slowness of Argon2 would only add CPU to every authenticated request.
User-chosen secrets -- passwords, API keys and device tokens, which a human may
have picked from something memorable -- are hashed with Argon2id in
:mod:`app.security.passwords` instead.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from app.core.errors import AuthError

if TYPE_CHECKING:
    from app.config import SecuritySettings

#: Bytes of entropy in a session token and in an API key. 256 bits is the
#: point at which brute force stops being a strategy and the value is kept at
#: two full words so it is not "reduced for performance".
TOKEN_BYTES: Final = 32

#: Session tokens carry no prefix. They are only ever transmitted in an
#: Authorization header, where the scheme already identifies them.
API_KEY_PREFIX: Final = "ulk_"

#: Device tokens are provisioned by an operator and typed by a person, so they
#: are prefixed to survive being read aloud or copied out of a serial log.
#: A public prefix, not a secret. Its whole purpose is to be recognisable at a
#: glance in a log or a serial monitor.
DEVICE_TOKEN_PREFIX: Final = "udt_"  # noqa: S105

#: token_hash is a 64-character SHA-256 hex digest; the column is VARCHAR(128).
_MAX_DIGEST_LENGTH: Final = 128


def generate_token() -> str:
    """Return a fresh opaque token with 256 bits of entropy."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def generate_api_key() -> str:
    """Return a fresh API key, prefixed so it is recognisable in a log.

    The prefix is public. It carries no entropy and exists so that a key
    pasted into a support ticket can be identified, and so a key committed by
    mistake is obviously a key.
    """
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(TOKEN_BYTES)}"


def generate_device_token() -> str:
    """Return a fresh device token, prefixed for the same reason."""
    return f"{DEVICE_TOKEN_PREFIX}{secrets.token_urlsafe(TOKEN_BYTES)}"


def digest(value: str) -> str:
    """Return the stored form of a token or key."""
    return hashlib.sha256(value.encode()).hexdigest()


def is_digest(value: str) -> bool:
    """Return whether ``value`` looks like a stored digest.

    Guards the paths that accept a header value and must decide whether it is
    already a digest, so a caller cannot skip the hash step by sending one.
    """
    return len(value) == _MAX_DIGEST_LENGTH // 2 and all(c in "0123456789abcdef" for c in value)


def access_expiry(settings: SecuritySettings, *, now: datetime | None = None) -> datetime:
    """When an access token issued now stops being valid."""
    moment = now or datetime.now(UTC)
    return moment + timedelta(minutes=settings.access_token_expire_minutes)


def refresh_expiry(settings: SecuritySettings, *, now: datetime | None = None) -> datetime:
    """When a refresh token issued now stops being usable for rotation."""
    moment = now or datetime.now(UTC)
    return moment + timedelta(days=settings.refresh_token_expire_days)


def extract_bearer(header: str | None) -> str:
    """Return the credential from an ``Authorization`` header.

    Raises:
        AuthError: when the header is missing, malformed, or uses a scheme other
            than Bearer. All three answer identically so a client cannot learn
            which of them it got wrong, and so no credential is echoed back.
    """
    if not header:
        raise AuthError("missing Authorization header")
    scheme, _, credential = header.partition(" ")
    if scheme.lower() != "bearer" or not credential.strip():
        raise AuthError("expected an Authorization header of the form 'Bearer <token>'")
    return credential.strip()


def decode_api_key(candidate: str) -> str:
    """Return ``candidate`` if it is shaped like an API key.

    Raises:
        AuthError: otherwise. A key without the prefix cannot be one this system
            issued, so it is rejected before any database work is done.
    """
    if not candidate.startswith(API_KEY_PREFIX) or len(candidate) <= len(API_KEY_PREFIX) + 16:
        raise AuthError("malformed API key")
    return candidate


def claim_payload(
    *,
    subject: str,
    session_id: str,
    actor: str,
    issued_at: datetime,
    expires_at: datetime,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the body of an access token.

    Returned rather than encoded here so the caller can store whatever it needs
    and so the same claims can be logged in tests. The token itself is
    ``digest`` of a random value; this dictionary is the *record* of what that
    token stood for.
    """
    return {
        "sub": subject,
        "sid": session_id,
        "actor": actor,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        **(extra or {}),
    }
