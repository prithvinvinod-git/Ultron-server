"""Permission policy: LEVEL 0-5, scope, and the confirmation rule (T033).

Spec §15 defines the only permission scale in the project — six levels from
read-only to destructive — and three requirements: every tool declares its
level, every execution is checked, every execution is logged. §64.12 adds the
dimensions a decision is evaluated over and the explicit confirmation rule;
§66.7 confirms one engine and adds "everything irreversible is confirmed".

This module is the **pure** half of that: request in, decision out, no I/O.
The half that resolves grants, writes the audit row and raises the errors
lives in :mod:`app.core.permissions` (the Permission Manager), which keeps
this file testable without a database and keeps the rules from being buried
inside a component that has one.

Three rules the code exists to hold:

**Tools declare, the engine decides** (§66.7). A tool never carries its own
permission logic; it states a required level and the policy answers. Nothing
in this module can raise a tool's privilege — a higher ``granted_level`` than
``required_level`` still evaluates against the *required* level, because the
risk belongs to the operation, not to whoever is running it.

**Confirmation follows the level, not the caller** (§64.12): levels 0-1 never
ask, level 2 may be pre-authorized per workspace and level 3 per operation,
levels 4-5 ask every single time, and anything marked irreversible asks even
when the level would not (§66.17). ``confirmed`` is an explicit yes already
obtained for *this invocation* — the one thing that answers a confirmation.

**No grant is a denial.** §64.12 scopes a grant to
``(principal, node, tool, operation)``: a LEVEL 3 grant on ``server`` grants
nothing on ``windows``. An unresolvable scope is therefore a deny, never a
silent fall-through to "allowed because nothing said no".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.database.models import AuditOutcome, PermissionLevel
from app.security.audit import ACTOR_USER

__all__ = [
    "PermissionDecision",
    "PermissionRequest",
    "confirmation_required",
    "evaluate",
    "scope_of",
]


class PermissionDecision(StrEnum):
    """The three outcomes of one permission evaluation (§66.7).

    The values are *identical* to :class:`app.database.models.AuditOutcome`'s,
    so a decision can be written to — and re-read from — the audit table with
    no translation table in between. ``outcome`` exists so the type checker
    still sees the conversion at the seam between the two spellings.
    """

    ALLOWED = "allowed"
    DENIED = "denied"
    CONFIRM_REQUIRED = "confirm_required"

    @property
    def outcome(self) -> AuditOutcome:
        """The audit-table spelling of this decision."""
        return AuditOutcome(self.value)


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """One decision's inputs — §64.12's dimensions, plus the confirmation flags.

    ``granted_level`` is an *input*, not something this module looks up: the
    Permission Manager resolves it from the grant store for
    ``(principal, node, tool)`` before evaluating, which is what keeps policy
    free of storage. ``None`` means no grant exists for that scope and
    evaluates as a denial.
    """

    principal: str
    node: str
    tool: str
    operation: str
    required_level: PermissionLevel
    #: Which interface asked (Electron, PWA, voice, device) — §64.12.
    client: str = "api"
    #: The workspace / path / service / device the action touches — §64.12.
    target: str | None = None
    #: Actor kind for the audit row: user, service_account, device, system.
    principal_type: str = ACTOR_USER
    granted_level: PermissionLevel | None = None
    #: LEVEL 2 may skip confirmation when pre-authorized for this workspace.
    workspace_authorized: bool = False
    #: LEVEL 3 may skip confirmation when pre-authorized for this operation.
    operation_authorized: bool = False
    #: An explicit yes already obtained for this invocation.
    confirmed: bool = False
    #: §66.17: irreversible operations confirm regardless of level.
    irreversible: bool = False


def scope_of(request: PermissionRequest) -> tuple[str, str, str, str]:
    """The grant scope §64.12 evaluates over: (principal, node, tool, operation)."""
    return (request.principal, request.node, request.tool, request.operation)


def confirmation_required(request: PermissionRequest) -> bool:
    """Whether this invocation needs an explicit human yes (§64.12, §66.17).

    Evaluated against the *required* level — the operation's risk — because a
    level-2 write stays a level-2 write no matter how privileged the principal
    asking for it happens to be.
    """
    if request.irreversible:
        # §66.17: irreversible asks even where the scale alone would not.
        return True
    level = request.required_level.level
    if level <= 1:
        return False
    if level == 2:
        # CONTROLLED writes (§59.3): confirm unless this workspace is trusted.
        return not request.workspace_authorized
    if level == 3:
        # Program execution: confirm unless this exact operation is trusted.
        return not request.operation_authorized
    # LEVEL 4 and 5 ask per invocation, every time — pre-authorization cannot
    # answer a question that §64.12 requires to be asked again each time.
    return True


def evaluate(request: PermissionRequest) -> PermissionDecision:
    """Evaluate one request against §15's scale and §64.12's confirmation rule.

    Denial outranks confirmation: asking a human to approve something the
    principal is not granted would launder a refusal into a delay.
    """
    if request.granted_level is None:
        # No grant for this (principal, node, tool) scope — §64.12's rule that
        # a grant on one node grants nothing on another.
        return PermissionDecision.DENIED
    if not request.granted_level.allows(request.required_level):
        return PermissionDecision.DENIED
    if request.confirmed:
        # The explicit yes for this invocation — the answer to any confirmation.
        return PermissionDecision.ALLOWED
    if confirmation_required(request):
        return PermissionDecision.CONFIRM_REQUIRED
    return PermissionDecision.ALLOWED
