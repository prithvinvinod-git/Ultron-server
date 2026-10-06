"""The audit trail for sensitive operations (spec §31: "Create an audit log
for sensitive operations").

The schema and repository already existed with no caller, which is why §31 was
unsatisfied despite the table being complete. This module is that caller.

Two rules shape it:

**A denial is recorded, not just a success.** A log that only records what was
allowed cannot answer "what was this account trying to do?", which is usually
the question an operator actually has. ``record`` therefore takes the decision
explicitly and denial is a first-class outcome rather than a missing row.

**An audit failure must not become an outage.** If the append fails, the
sensitive operation it describes has usually already been decided, and raising
here would turn a broken audit sink into a denial of service. Failures are
logged through the structured logger -- which redacts -- and swallowed. The
``strict`` flag exists for the few callers that would rather fail closed.

The caller never constructs :class:`AuditLogEntry` directly: the repository's
``record`` enforces that ``actor_type``, ``action`` and ``decision`` are always
present, so an entry cannot say what happened without saying who decided and
how.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from app.database.models import AuditOutcome
from app.observability import get_logger, get_request_id

if TYPE_CHECKING:
    from app.core.errors import UltronError
    from app.database.repositories import AuditLogRepository

_LOGGER = get_logger(__name__)


class AuditSink(Protocol):
    """The one method :class:`AuditLogger` needs from a repository.

    Named separately from ``AuditLogRepository`` so the durable sink can be a
    short-lived object that owns its own connection rather than the
    request-scoped repository. Both satisfy it structurally.
    """

    async def record(self, **fields: Any) -> None: ...


#: Actor kinds. Kept as constants so a typo becomes an AttributeError at import
#: rather than a row that can never be filtered.
ACTOR_USER: str = "user"
ACTOR_SERVICE: str = "service_account"
ACTOR_DEVICE: str = "device"
ACTOR_SYSTEM: str = "system"
ACTOR_ANONYMOUS: str = "anonymous"

#: Action names used by the authentication subsystem. Prefixed so that a grep
#: for "auth." finds every authentication event in the table.
AUTH_LOGIN: str = "auth.login"
AUTH_LOGIN_FAILED: str = "auth.login_failed"
AUTH_LOGOUT: str = "auth.logout"
# Action names, not secrets. The S105 rule matches on the constant's name
# ("TOKEN", "PASSWORD") without looking at the value, which is right often enough
# to be worth keeping and wrong here; the noqa marks that as a reviewed decision
# rather than a blanket suppression.
AUTH_TOKEN_REFRESH: str = "auth.token_refresh"  # noqa: S105
AUTH_TOKEN_REUSE: str = "auth.token_reuse_detected"  # noqa: S105
AUTH_SESSION_REVOKED: str = "auth.session_revoked"
AUTH_API_KEY_ISSUED: str = "auth.api_key_issued"
AUTH_API_KEY_REVOKED: str = "auth.api_key_revoked"
AUTH_DEVICE_REGISTERED: str = "auth.device_registered"
AUTH_PASSWORD_CHANGE: str = "auth.password_change"  # noqa: S105

#: Action names for authorization decisions.
PERMISSION_DENIED: str = "permission.denied"
PERMISSION_ALLOWED: str = "permission.allowed"
PERMISSION_CONFIRM_REQUIRED: str = "permission.confirm_required"


class AuditLogger:
    """Writes audit entries, degrading safely when the sink is unavailable."""

    def __init__(
        self,
        repository: AuditLogRepository,
        *,
        strict: bool = False,
        durable: AuditSink | None = None,
    ) -> None:
        """``durable`` is an escape hatch for entries that must outlive a rollback.

        See :meth:`record`. Absent by default, which means the entry shares the
        caller's transaction and is discarded if the request fails.
        """
        self._repository = repository
        self._strict = strict
        self._durable = durable

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
        ip_address: str | None = None,
        details: dict[str, Any] | None = None,
        exc: UltronError | None = None,
        durable: bool = False,
    ) -> None:
        """Append one entry. Never raises unless ``strict`` is set.

        ``request_id`` is taken from the ambient correlation context rather than
        passed in, so an audit row and the log lines for the same request share
        an id and can actually be joined after the fact.

        When ``exc`` is given, its ``code`` is recorded instead of its message.
        A driver exception routinely carries a connection string, and this
        table is exactly the sort of thing that gets pasted into a ticket.

        **``durable=True`` routes the entry outside the caller's transaction.**
        By default an audit row shares the request's session, so a request that
        raises takes its audit rows down with it. That is correct for a success:
        if the work rolled back, the record of having done it should not survive.

        It is wrong for a *refusal*. A failed login is the case §15 cares about
        most and the one an operator most needs afterwards, yet it is precisely
        the case that ends by raising -- so the rollback erases the evidence and
        the failure trail stays empty while the endpoint is under attack. A
        durable entry is written on its own connection and committed
        independently; it is also the slower of the two, which is why it is
        opt-in rather than the default.

        Silently ignored when no durable sink was supplied, so a caller does not
        have to know whether one is configured. It degrades to the ordinary
        behaviour instead of raising.
        """
        payload = dict(details or {})
        if exc is not None:
            payload.setdefault("error_code", exc.code.value)
            if reason is None:
                reason = exc.code.value

        fields: dict[str, Any] = {
            "actor_type": actor_type,
            "action": action,
            "decision": decision,
            "actor_id": actor_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "reason": reason,
            "request_id": get_request_id(),
            "ip_address": ip_address,
            "details": payload,
        }

        sink = self._durable if durable else None
        if sink is not None:
            await self._write_durable(sink, fields)
            return

        try:
            await self._repository.record(**fields)
        except Exception:
            self._report_append_failure(actor_type=actor_type, action=action)

    async def _write_durable(self, sink: AuditSink, fields: dict[str, Any]) -> None:
        """Commit one entry independently of the caller's transaction.

        The failure handling matches :meth:`record`: a sink that has stopped
        accepting writes is logged at ERROR and swallowed, because the request it
        describes has usually already been decided and raising would turn a
        broken audit table into a denial of service.

        The sink is passed in rather than read from ``self`` so the check that it
        exists happens once, in the caller, where the fallback is decided.
        """
        try:
            await sink.record(**fields)
        except Exception:
            self._report_append_failure(
                actor_type=str(fields["actor_type"]),
                action=str(fields["action"]),
            )

    def _report_append_failure(self, *, actor_type: str, action: str) -> None:
        """Log a swallowed append failure, or re-raise when ``strict``.

        Deliberately broad, and the reason is the contract at module level. The
        repository translates driver failures to ``DatabaseError``, but a failure
        inside the append itself (a detached instance, a broken mapper, a
        serialization error in ``details``) would otherwise escape and turn an
        audit entry into an outage. An audit write that can be made to fail is a
        denial-of-service lever pointed at the table that records what everyone
        else did.

        This does not mean auditing is optional: the failure is logged at ERROR
        with the actor and action, so a sink that has stopped accepting writes is
        visible in the service's own logs rather than only as missing rows.
        """
        if self._strict:
            raise
        _LOGGER.error(
            "audit append failed",
            extra={
                "event": "audit.append_failed",
                "actor_type": actor_type,
                "action": action,
            },
        )

    async def allowed(self, **kwargs: Any) -> None:
        """Record a decision that was permitted."""
        await self.record(decision=AuditOutcome.ALLOWED, **kwargs)

    async def denied(self, **kwargs: Any) -> None:
        """Record a decision that was refused."""
        await self.record(decision=AuditOutcome.DENIED, **kwargs)

    async def confirm_required(self, **kwargs: Any) -> None:
        """Record a decision that is neither: it needs the user to confirm.

        A distinct outcome, because "ask the human" is not a failure and
        collapsing it into denied would hide every tool call that legitimately
        paused for confirmation.
        """
        await self.record(decision=AuditOutcome.CONFIRM_REQUIRED, **kwargs)


__all__ = [
    "ACTOR_ANONYMOUS",
    "ACTOR_DEVICE",
    "ACTOR_SERVICE",
    "ACTOR_SYSTEM",
    "ACTOR_USER",
    "AUTH_API_KEY_ISSUED",
    "AUTH_API_KEY_REVOKED",
    "AUTH_DEVICE_REGISTERED",
    "AUTH_LOGIN",
    "AUTH_LOGIN_FAILED",
    "AUTH_LOGOUT",
    "AUTH_PASSWORD_CHANGE",
    "AUTH_SESSION_REVOKED",
    "AUTH_TOKEN_REFRESH",
    "AUTH_TOKEN_REUSE",
    "PERMISSION_ALLOWED",
    "PERMISSION_CONFIRM_REQUIRED",
    "PERMISSION_DENIED",
    "AuditLogger",
]
