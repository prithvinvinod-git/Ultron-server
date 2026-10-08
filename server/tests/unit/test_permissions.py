"""Unit tests for the permission policy and Permission Manager (T033).

Two halves, two classes of risk:

**The policy** (``app.security.permissions``) is pure, so the tests pin the
rules §15/§64.12/§66.17 actually state — confirmation by level, denial before
confirmation, risk judged by the operation rather than the caller — as a
truth table rather than prose.

**The manager** (``app.core.permissions``) is where rules meet I/O, so the
tests pin the two behaviours prose cannot keep honest: *every* decision lands
an audit row carrying §64.13's full tuple, and a refusal raises the right
error with the target node in its details.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.errors import ConfirmationRequiredError, PermissionDeniedError
from app.core.permissions import PermissionManager
from app.database.models import AuditOutcome, PermissionLevel
from app.security.audit import (
    ACTOR_SYSTEM,
    PERMISSION_ALLOWED,
    PERMISSION_CONFIRM_REQUIRED,
    PERMISSION_DENIED,
    AuditLogger,
)
from app.security.permissions import (
    PermissionDecision,
    PermissionRequest,
    confirmation_required,
    evaluate,
    scope_of,
)
from tests.unit.conftest import RecordingAuditRepository

pytestmark = pytest.mark.unit


def make_request(**overrides: Any) -> PermissionRequest:
    """A LEVEL 0 read on the server node; override any dimension per test."""
    base: dict[str, Any] = {
        "principal": "alice",
        "node": "server",
        "tool": "fs.read",
        "operation": "read",
        "required_level": PermissionLevel.READ_ONLY,
    }
    base.update(overrides)
    return PermissionRequest(**base)


class FakeGrants:
    """Grant store double: a table of (principal, node, tool) → level."""

    def __init__(
        self,
        table: dict[tuple[str, str, str], PermissionLevel] | None = None,
    ) -> None:
        self.table = table or {}
        self.calls: list[tuple[str, str, str]] = []

    async def level_for(self, principal: str, *, node: str, tool: str) -> PermissionLevel | None:
        self.calls.append((principal, node, tool))
        return self.table.get((principal, node, tool))


def make_manager(
    audit_sink: Any,
    grants: FakeGrants | None = None,
    default_level: PermissionLevel | None = None,
    *,
    durable: Any = None,
) -> PermissionManager:
    return PermissionManager(
        audit=AuditLogger(audit_sink, durable=durable),
        grants=grants,
        default_level=default_level,
    )


# --------------------------------------------------------------------------- #
# Policy: the §64.12 confirmation truth table
# --------------------------------------------------------------------------- #
def test_decisions_speak_the_audit_vocabulary() -> None:
    """A decision is written to and read from the table with no translation."""
    assert PermissionDecision.ALLOWED.outcome is AuditOutcome.ALLOWED
    assert PermissionDecision.DENIED.outcome is AuditOutcome.DENIED
    assert PermissionDecision.CONFIRM_REQUIRED.outcome is AuditOutcome.CONFIRM_REQUIRED


def test_scope_is_the_four_dimension_tuple() -> None:
    request = make_request(operation="commit")
    assert scope_of(request) == ("alice", "server", "fs.read", "commit")


def test_no_grant_for_the_scope_is_a_denial() -> None:
    """§64.12: a grant on one node grants nothing on another."""
    request = make_request(required_level=PermissionLevel.READ_ONLY)
    assert evaluate(request) is PermissionDecision.DENIED


def test_an_insufficient_grant_is_a_denial() -> None:
    request = make_request(
        required_level=PermissionLevel.SYSTEM_CONFIG,
        granted_level=PermissionLevel.EXECUTE_PROGRAMS,
    )
    assert evaluate(request) is PermissionDecision.DENIED


def test_read_only_and_safe_actions_never_ask() -> None:
    for level in (PermissionLevel.READ_ONLY, PermissionLevel.SAFE_ACTIONS):
        request = make_request(required_level=level, granted_level=level)
        assert evaluate(request) is PermissionDecision.ALLOWED


def test_level_2_confirms_unless_the_workspace_is_pre_authorized() -> None:
    untrusted = make_request(
        required_level=PermissionLevel.MODIFY_PROJECT,
        granted_level=PermissionLevel.MODIFY_PROJECT,
    )
    trusted = make_request(
        required_level=PermissionLevel.MODIFY_PROJECT,
        granted_level=PermissionLevel.MODIFY_PROJECT,
        workspace_authorized=True,
    )
    assert evaluate(untrusted) is PermissionDecision.CONFIRM_REQUIRED
    assert evaluate(trusted) is PermissionDecision.ALLOWED


def test_level_3_confirms_unless_the_operation_is_pre_authorized() -> None:
    untrusted = make_request(
        required_level=PermissionLevel.EXECUTE_PROGRAMS,
        granted_level=PermissionLevel.EXECUTE_PROGRAMS,
    )
    trusted = make_request(
        required_level=PermissionLevel.EXECUTE_PROGRAMS,
        granted_level=PermissionLevel.EXECUTE_PROGRAMS,
        operation_authorized=True,
    )
    assert evaluate(untrusted) is PermissionDecision.CONFIRM_REQUIRED
    assert evaluate(trusted) is PermissionDecision.ALLOWED


def test_level_4_confirms_every_invocation_despite_pre_authorization() -> None:
    """§64.12: levels 4-5 ask every time; pre-authorization cannot answer."""
    request = make_request(
        required_level=PermissionLevel.SYSTEM_CONFIG,
        granted_level=PermissionLevel.SYSTEM_CONFIG,
        workspace_authorized=True,
        operation_authorized=True,
    )
    assert confirmation_required(request) is True
    assert evaluate(request) is PermissionDecision.CONFIRM_REQUIRED


def test_level_5_confirms_until_this_invocation_is_confirmed() -> None:
    base = {
        "required_level": PermissionLevel.DESTRUCTIVE,
        "granted_level": PermissionLevel.DESTRUCTIVE,
    }
    assert evaluate(make_request(**base)) is PermissionDecision.CONFIRM_REQUIRED
    assert evaluate(make_request(**base, confirmed=True)) is PermissionDecision.ALLOWED


def test_irreversible_confirms_even_at_a_safe_level() -> None:
    """§66.17: everything marked irreversible asks, whatever the level."""
    request = make_request(
        required_level=PermissionLevel.SAFE_ACTIONS,
        granted_level=PermissionLevel.SAFE_ACTIONS,
        irreversible=True,
    )
    assert confirmation_required(request) is True
    assert evaluate(request) is PermissionDecision.CONFIRM_REQUIRED


def test_risk_follows_the_operation_not_the_callers_privilege() -> None:
    """An over-scoped principal does not make a LEVEL 4 tool less risky."""
    request = make_request(
        required_level=PermissionLevel.SYSTEM_CONFIG,
        granted_level=PermissionLevel.DESTRUCTIVE,
        operation_authorized=True,
        workspace_authorized=True,
    )
    assert evaluate(request) is PermissionDecision.CONFIRM_REQUIRED


def test_denial_outranks_confirmation() -> None:
    """Asking a human to approve an ungranted action would launder a refusal."""
    request = make_request(
        required_level=PermissionLevel.DESTRUCTIVE,
        granted_level=PermissionLevel.SAFE_ACTIONS,
        confirmed=True,
        irreversible=True,
    )
    assert evaluate(request) is PermissionDecision.DENIED


# --------------------------------------------------------------------------- #
# Manager: grant resolution
# --------------------------------------------------------------------------- #
async def test_a_scoped_grant_wins_over_the_default() -> None:
    grants = FakeGrants({("alice", "server", "fs.write"): PermissionLevel.MODIFY_PROJECT})
    manager = make_manager(
        RecordingAuditRepository(),
        grants=grants,
        default_level=PermissionLevel.READ_ONLY,
    )
    request = make_request(tool="fs.write")

    assert await manager.resolve(request) is PermissionLevel.MODIFY_PROJECT
    assert grants.calls == [("alice", "server", "fs.write")]


async def test_a_miss_falls_back_to_the_configured_default_then_nothing() -> None:
    with_default = make_manager(
        RecordingAuditRepository(),
        grants=FakeGrants(),
        default_level=PermissionLevel.READ_ONLY,
    )
    bare = make_manager(RecordingAuditRepository())
    request = make_request()

    assert await with_default.resolve(request) is PermissionLevel.READ_ONLY
    assert await bare.resolve(request) is None


# --------------------------------------------------------------------------- #
# Manager: every decision is audited (§64.13's tuple)
# --------------------------------------------------------------------------- #
async def test_an_allow_is_audited_with_the_full_tuple(audit_sink: Any) -> None:
    manager = make_manager(audit_sink, default_level=PermissionLevel.READ_ONLY)
    decision = await manager.check(make_request(client="pwa"))

    assert decision is PermissionDecision.ALLOWED
    assert audit_sink.actions() == [PERMISSION_ALLOWED]
    row = audit_sink.rows[0]
    assert row["actor_id"] == "alice"
    assert row["resource_type"] == "tool"
    assert row["resource_id"] == "fs.read"
    assert row["decision"] is AuditOutcome.ALLOWED
    assert row["details"] == {
        "principal": "alice",
        "client": "pwa",
        "node": "server",
        "tool": "fs.read",
        "operation": "read",
        "target": None,
        "required_level": "READ_ONLY",
        "granted_level": "READ_ONLY",
        "decision": "allowed",
    }


async def test_a_denial_is_audited_with_a_reason(audit_sink: Any) -> None:
    manager = make_manager(audit_sink, grants=FakeGrants())
    decision = await manager.check(make_request(required_level=PermissionLevel.EXECUTE_PROGRAMS))

    assert decision is PermissionDecision.DENIED
    assert audit_sink.actions() == [PERMISSION_DENIED]
    row = audit_sink.rows[0]
    assert row["decision"] is AuditOutcome.DENIED
    assert row["reason"] == "no_grant_for_scope"
    assert row["details"]["granted_level"] is None


async def test_an_insufficient_grant_is_distinguished_from_no_grant(
    audit_sink: Any,
) -> None:
    grants = FakeGrants({("alice", "server", "fs.read"): PermissionLevel.SAFE_ACTIONS})
    manager = make_manager(audit_sink, grants=grants)
    await manager.check(make_request(required_level=PermissionLevel.SYSTEM_CONFIG))

    assert audit_sink.rows[0]["reason"] == "insufficient_level"
    assert audit_sink.rows[0]["details"]["granted_level"] == "SAFE_ACTIONS"


async def test_a_confirmation_is_audited_as_its_own_outcome(audit_sink: Any) -> None:
    manager = make_manager(audit_sink, default_level=PermissionLevel.MODIFY_PROJECT)
    decision = await manager.check(make_request(required_level=PermissionLevel.MODIFY_PROJECT))

    assert decision is PermissionDecision.CONFIRM_REQUIRED
    assert audit_sink.actions() == [PERMISSION_CONFIRM_REQUIRED]
    assert audit_sink.rows[0]["decision"] is AuditOutcome.CONFIRM_REQUIRED
    assert audit_sink.rows[0]["reason"] == "confirmation_required"


async def test_irreversible_confirmation_is_labelled_as_such(
    audit_sink: Any,
) -> None:
    manager = make_manager(audit_sink, default_level=PermissionLevel.SAFE_ACTIONS)
    await manager.check(
        make_request(required_level=PermissionLevel.SAFE_ACTIONS, irreversible=True)
    )
    assert audit_sink.rows[0]["reason"] == "irreversible_operation"


async def test_the_principal_type_reaches_the_audit_row(audit_sink: Any) -> None:
    manager = make_manager(audit_sink, default_level=PermissionLevel.READ_ONLY)
    await manager.check(make_request(principal_type=ACTOR_SYSTEM, principal="core"))
    assert audit_sink.rows[0]["actor_type"] == ACTOR_SYSTEM
    assert audit_sink.rows[0]["actor_id"] == "core"


async def test_a_refusal_is_routed_to_the_durable_sink() -> None:
    """§64/audit contract: refusal rows outlive the rollback that follows a raise."""
    normal = RecordingAuditRepository()
    durable = RecordingAuditRepository()
    manager = make_manager(normal, grants=FakeGrants(), durable=durable)

    with pytest.raises(PermissionDeniedError):
        await manager.enforce(make_request(required_level=PermissionLevel.SYSTEM_CONFIG))

    assert normal.rows == []
    assert durable.actions() == [PERMISSION_DENIED]


async def test_an_allow_shares_the_callers_transaction(audit_sink: Any) -> None:
    """The durable sink is for refusals only; a rolled-back allow leaves nothing."""
    durable = RecordingAuditRepository()
    manager = make_manager(audit_sink, default_level=PermissionLevel.READ_ONLY, durable=durable)
    await manager.enforce(make_request())

    assert durable.rows == []
    assert audit_sink.actions() == [PERMISSION_ALLOWED]


# --------------------------------------------------------------------------- #
# Manager: enforce turns a refusal into the right error
# --------------------------------------------------------------------------- #
async def test_enforce_passes_an_allow_through(audit_sink: Any) -> None:
    manager = make_manager(audit_sink, default_level=PermissionLevel.READ_ONLY)
    assert await manager.enforce(make_request()) is PermissionDecision.ALLOWED


async def test_enforce_raises_a_403_with_the_scope_in_details(
    audit_sink: Any,
) -> None:
    manager = make_manager(audit_sink, grants=FakeGrants())
    with pytest.raises(PermissionDeniedError) as excinfo:
        await manager.enforce(make_request(required_level=PermissionLevel.SYSTEM_CONFIG))

    error = excinfo.value
    assert error.code.value == "PERMISSION_DENIED"
    assert error.http_status == 403
    assert error.details["node"] == "server"
    assert error.details["tool"] == "fs.read"
    assert error.details["scope"] == ["alice", "server", "fs.read", "read"]
    assert error.details["required_level"] == 4
    # Ungranted scope: the error omits actual_level entirely rather than
    # reporting a level that does not exist (errors.py omits None).
    assert "actual_level" not in error.details
    assert audit_sink.actions() == [PERMISSION_DENIED]


async def test_enforce_raises_409_naming_the_target_node(audit_sink: Any) -> None:
    """§64.12: a LEVEL 5 confirmation names the target node it would run on."""
    manager = make_manager(audit_sink, default_level=PermissionLevel.DESTRUCTIVE)
    with pytest.raises(ConfirmationRequiredError) as excinfo:
        await manager.enforce(
            make_request(node="windows", required_level=PermissionLevel.DESTRUCTIVE)
        )

    error = excinfo.value
    assert error.code.value == "CONFIRMATION_REQUIRED"
    assert error.http_status == 409
    assert error.details["node"] == "windows"
    assert error.details["required_level"] == 5
    assert error.details["actual_level"] == 5
    # The refusal was audited before the raise, durably:
    assert audit_sink.actions() == [PERMISSION_CONFIRM_REQUIRED]
