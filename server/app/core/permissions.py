"""The Permission Manager (T033): check every execution, audit every decision.

Spec §15: "Every tool declares its required level. Every execution is checked
by Permission Manager. Every execution is logged." §66.7 adds that *every*
decision — allow, deny, confirm-required — produces an audit row (§31), and
§64.13 fixes what that row carries: principal, client, node, tool, operation,
level, decision.

This is the Core-facing half of T033. The rules themselves are pure and live
in :mod:`app.security.permissions`; this class binds them to the three things
a rule alone cannot do:

1. **Resolve the grant.** A :class:`GrantStore` answers "what level does this
   principal hold for this (node, tool) scope", with an optional configured
   ``default_level`` behind it (the settings already reserve
   ``AGENT_DEFAULT_PERMISSION_LEVEL`` for exactly this fallback). Ungranted
   scopes fall back to the default, then to nothing — and nothing denies.

2. **Write the audit row for every decision.** Denials and confirmations are
   written ``durable`` — outside the caller's transaction — because both end
   by raising, and a row erased by the rollback of the refusal it records is
   the exact failure mode ``AuditLogger`` documents for failed logins: the
   trail stays empty precisely while the system is under attack. Allows stay
   transactional: if the work rolled back, the record of permission to do it
   should not outlive it.

3. **Turn a refusal into the right error.** ``check`` returns the decision
   for callers that want to handle it (a UI confirming before it even asks
   the server); ``enforce`` raises :class:`PermissionDeniedError` or
   :class:`ConfirmationRequiredError` — and always puts the target node in
   the error's details, because §64.12 requires a LEVEL 5 confirmation to
   *name the target node* rather than ask about an abstract operation.

The audit sink is a required constructor argument on purpose. A permission
manager that can be built without one is an invitation to ship a check that
nothing recorded — §15's third requirement would be satisfied only by
whenever somebody remembered.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from app.core.errors import ConfirmationRequiredError, PermissionDeniedError
from app.database.models import PermissionLevel
from app.security.audit import (
    PERMISSION_ALLOWED,
    PERMISSION_CONFIRM_REQUIRED,
    PERMISSION_DENIED,
    AuditLogger,
)
from app.security.permissions import (
    PermissionDecision,
    PermissionRequest,
    evaluate,
    scope_of,
)

__all__ = ["GrantStore", "PermissionManager"]


class GrantStore(Protocol):
    """Answers "what level does this principal hold for this scope?".

    The scope is (principal, node, tool) — §64.12's tuple minus the operation,
    which is carried by the request itself. Backed by the grants table when it
    exists (T353) and by a test double until then; the manager only ever
    needs this one lookup.
    """

    async def level_for(
        self, principal: str, *, node: str, tool: str
    ) -> PermissionLevel | None: ...


class PermissionManager:
    """Evaluates one request against §15's scale and audits the outcome."""

    def __init__(
        self,
        *,
        audit: AuditLogger,
        grants: GrantStore | None = None,
        default_level: PermissionLevel | None = None,
    ) -> None:
        """``audit`` is required — see the module docstring's third point."""
        self._audit = audit
        self._grants = grants
        self._default_level = default_level

    async def resolve(self, request: PermissionRequest) -> PermissionLevel | None:
        """The principal's level for this request's scope, or ``None``.

        Grant store first — a scoped grant is the precise answer — then the
        configured default, which is how an agent with no explicit grant still
        gets the read-only level its settings promise. ``None`` means the
        scope is ungranted, which policy treats as a denial.
        """
        if self._grants is not None:
            granted = await self._grants.level_for(
                request.principal, node=request.node, tool=request.tool
            )
            if granted is not None:
                return granted
        return self._default_level

    async def check(self, request: PermissionRequest) -> PermissionDecision:
        """Resolve, evaluate, audit — and return the decision without raising.

        The audit row is written for *every* outcome; see the class docstring
        for why denials and confirmations are durable and allows are not.
        """
        decision, _granted = await self._decide(request)
        return decision

    async def enforce(self, request: PermissionRequest) -> PermissionDecision:
        """Like :meth:`check`, but a refusal becomes the right error.

        Returns :attr:`PermissionDecision.ALLOWED` on success so a caller can
        chain; raises ``PermissionDeniedError`` (403) on denial and
        ``ConfirmationRequiredError`` (409) when a human must approve first.
        """
        decision, granted = await self._decide(request)
        if decision is PermissionDecision.ALLOWED:
            return decision
        error = (
            PermissionDeniedError
            if decision is PermissionDecision.DENIED
            else ConfirmationRequiredError
        )(
            request.operation,
            required_level=request.required_level.level,
            actual_level=granted.level if granted is not None else None,
        )
        # §64.12: a LEVEL 5 confirmation names the target node; carrying the
        # scope on every refusal costs nothing and makes the client's prompt
        # and the operator's ticket able to say where this would have run.
        error.details.update(
            {
                "node": request.node,
                "client": request.client,
                "tool": request.tool,
                "operation": request.operation,
                "target": request.target,
                "scope": list(scope_of(request)),
            }
        )
        raise error

    async def _decide(
        self, request: PermissionRequest
    ) -> tuple[PermissionDecision, PermissionLevel | None]:
        """Resolve, evaluate and audit once — shared by check and enforce."""
        granted = await self.resolve(request)
        decision = evaluate(replace(request, granted_level=granted))
        await self._record(request, granted, decision)
        return decision, granted

    async def _record(
        self,
        request: PermissionRequest,
        granted: PermissionLevel | None,
        decision: PermissionDecision,
    ) -> None:
        reason: str | None = None
        if decision is PermissionDecision.DENIED:
            reason = "no_grant_for_scope" if granted is None else "insufficient_level"
        elif decision is PermissionDecision.CONFIRM_REQUIRED:
            reason = "irreversible_operation" if request.irreversible else "confirmation_required"

        fields = {
            "actor_type": request.principal_type,
            "actor_id": request.principal,
            "action": {
                PermissionDecision.ALLOWED: PERMISSION_ALLOWED,
                PermissionDecision.DENIED: PERMISSION_DENIED,
                PermissionDecision.CONFIRM_REQUIRED: PERMISSION_CONFIRM_REQUIRED,
            }[decision],
            "resource_type": "tool",
            "resource_id": request.tool,
            "reason": reason,
            # §64.13's audit row: principal, client, node, tool, operation,
            # level, decision — the whole tuple, so "what was asked, where,
            # and at what level" is one row rather than a join.
            "details": {
                "principal": request.principal,
                "client": request.client,
                "node": request.node,
                "tool": request.tool,
                "operation": request.operation,
                "target": request.target,
                "required_level": request.required_level.name,
                "granted_level": granted.name if granted is not None else None,
                "decision": decision.value,
            },
            # Refusals end by raising (enforce), so their rows must outlive
            # the caller's rollback — the precedent set by failed logins.
            "durable": decision is not PermissionDecision.ALLOWED,
        }
        if decision is PermissionDecision.ALLOWED:
            await self._audit.allowed(**fields)
        elif decision is PermissionDecision.DENIED:
            await self._audit.denied(**fields)
        else:
            await self._audit.confirm_required(**fields)
