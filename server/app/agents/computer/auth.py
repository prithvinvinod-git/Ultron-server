"""Node identity and authentication (T142, spec §30/§64.13).

One identity model for every node kind (§64.13). A node presents a credential;
the server stores only its digest and compares in constant time; revocation
removes that node's access without touching any user session, because a node
credential authenticates *a node, never a user* (§64.13).

Why a standalone authenticator rather than reusing the user auth stack: §64.13
lists the distinctions explicitly — sessions and node identities are different
principals, recorded as such (`actor_type`), "the way §60.2 already separates a
Firebase UID from a ULTRON session". Reusing `app/security` would have collapsed
two principals into one and made "revoke the laptop" and "log the user out" the
same operation.

Storage is the `devices.auth_token_hash` column's pattern (`users.api_key_hash`):
a SHA-256 digest, never the token. SHA-256 and not a password hash because a
node token is a high-entropy random secret, not a human-chosen password — there
is nothing to slow a brute force against, and the comparison must be cheap
because it runs on every heartbeat.

**mTLS-ready** (the task line): the credential carries an optional certificate
fingerprint. Today verification is the bearer token; when the transport grows
TLS client certs, `NodeAuthenticator.verify` already has the fingerprint to bind
the token to the certificate, and the change is confined to that one comparison.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.core.errors import AuthError

__all__ = [
    "NodeAuthenticator",
    "NodeCredential",
    "hash_token",
]

#: Bytes of entropy in an issued token. 32 bytes (256 bits) is well past any
#: brute-force budget and short enough to travel in a header.
_TOKEN_BYTES = 32


def hash_token(token: str) -> str:
    """Return the storage digest of a node credential.

    SHA-256, deliberately: the token is high-entropy (`secrets.token_urlsafe`),
    so the digest's only job is to avoid storing the secret, not to resist a
    dictionary attack. ``usedforsecurity=True`` is the default and correct here
    (§30: this *is* a security use, unlike the semantic-memory fallback hash).
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class NodeCredential:
    """A node's credential and its metadata (§64.13). The token is shown once,
    at issue time, and only its digest is retained."""

    node_id: str
    token: str
    token_hash: str
    issued_at: datetime
    expires_at: datetime | None = None
    certificate_fingerprint: str | None = None
    revoked: bool = False

    def is_expired(self, *, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        moment = now or datetime.now(UTC)
        return moment >= self.expires_at


@dataclass(slots=True)
class NodeAuthenticator:
    """Issues, verifies and revokes node credentials (§30, §64.13).

    In-memory: the registry of connected nodes lives in the gateway's process,
    and a credential that outlives the process it authenticated has no meaning
    — a restarted server re-issues. (Persistence of node identities is T332's
    one-registry work, which unifies this with `devices`; nothing here prevents
    that, and the digest format already matches `devices.auth_token_hash`.)
    """

    token_ttl: int | None = None
    _by_node: dict[str, NodeCredential] = field(default_factory=dict)
    _by_hash: dict[str, NodeCredential] = field(default_factory=dict)

    def issue(
        self,
        node_id: str,
        *,
        certificate_fingerprint: str | None = None,
        now: datetime | None = None,
    ) -> NodeCredential:
        """Mint a fresh credential for ``node_id``, replacing any previous one.

        Replacement is the revocation for a compromised token: re-issuing a node
        is `issue` again, and the old digest stops matching. Returns the
        credential including the plaintext ``token`` — the caller must deliver
        it to the node immediately, because it is never recoverable afterwards.
        """
        moment = now or datetime.now(UTC)
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        expires_at = (
            moment + timedelta(seconds=self.token_ttl) if self.token_ttl else None
        )
        credential = NodeCredential(
            node_id=node_id,
            token=token,
            token_hash=hash_token(token),
            issued_at=moment,
            expires_at=expires_at,
            certificate_fingerprint=certificate_fingerprint,
        )
        self._evict(node_id)
        self._by_node[node_id] = credential
        self._by_hash[credential.token_hash] = credential
        return credential

    def verify(
        self,
        token: str,
        *,
        certificate_fingerprint: str | None = None,
        now: datetime | None = None,
    ) -> NodeCredential:
        """Return the credential a token authenticates, or raise ``AuthError``.

        Constant-time comparison (`hmac.compare_digest`) so a timing side channel
        cannot learn a prefix of the digest. A revoked, expired or unknown token
        is one answer — the same message — because telling them apart would let
        an attacker distinguish "wrong token" from "revoked node".
        """
        candidate = hash_token(token)
        credential = self._by_hash.get(candidate)
        if credential is None:
            # Spend the same comparison on a miss so the failure is not
            # measurably faster than a wrong-but-present digest.
            hmac.compare_digest(candidate, candidate)
            raise AuthError(
                "the node credential is not recognised",
                details={"reason": "unknown_credential"},
            )
        if not hmac.compare_digest(credential.token_hash, candidate):
            raise AuthError(
                "the node credential is not recognised",
                details={"reason": "unknown_credential"},
            )
        if credential.revoked:
            raise AuthError(
                "the node credential is not recognised",
                details={"reason": "revoked"},
            )
        if credential.is_expired(now=now):
            raise AuthError(
                "the node credential is not recognised",
                details={"reason": "expired"},
            )
        if (
            certificate_fingerprint is not None
            and credential.certificate_fingerprint is not None
            and not hmac.compare_digest(
                credential.certificate_fingerprint, certificate_fingerprint
            )
        ):
            raise AuthError(
                "the node credential is not recognised",
                details={"reason": "certificate_mismatch"},
            )
        return credential

    def revoke(self, node_id: str) -> bool:
        """Revoke one node's credential (§64.13: revocation works per node)."""
        credential = self._by_node.get(node_id)
        if credential is None:
            return False
        credential.revoked = True
        self._by_hash.pop(credential.token_hash, None)
        return True

    def credential_for(self, node_id: str) -> NodeCredential | None:
        return self._by_node.get(node_id)

    def _evict(self, node_id: str) -> None:
        previous = self._by_node.pop(node_id, None)
        if previous is not None:
            self._by_hash.pop(previous.token_hash, None)
