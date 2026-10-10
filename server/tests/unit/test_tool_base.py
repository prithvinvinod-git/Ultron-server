"""Unit tests for the Tool ABC (T034).

The ABC's job is to make bad tools impossible to *register*, so the tests are
mostly about the definition-time guards: a concrete tool that ships without
its §14/§59.6/§66.17 declarations must fail at import, not at first call —
and a tool that claims success it never checked (§17) must not be able to do
that through the default ``verify``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

import pytest

from app.database.models import PermissionLevel, VerificationOutcome
from app.tools.base import Reversibility, Tool

pytestmark = pytest.mark.unit


class EchoTool(Tool):
    """The smallest legal concrete tool: every declaration, one async call."""

    name = "mock.echo"
    description = "Return the arguments unchanged."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 5.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        return arguments


def test_a_tool_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        Tool()  # type: ignore[abstract]


def test_a_concrete_tool_exposes_its_declarations() -> None:
    tool = EchoTool()
    assert tool.name == "mock.echo"
    assert tool.description
    assert tool.input_schema["type"] == "object"
    assert tool.permission_level is PermissionLevel.READ_ONLY
    assert tool.timeout == 5.0
    assert tool.reversibility is Reversibility.REVERSIBLE
    # §64.11 default: no node declared means the cloud server.
    assert EchoTool.node_scope is None
    assert EchoTool.audit is False


async def test_execute_receives_the_validated_arguments() -> None:
    assert await EchoTool().execute({"text": "hi"}) == {"text": "hi"}


async def test_the_default_verify_claims_nothing() -> None:
    """§17: never assume success — the default is UNVERIFIED, not SUCCESS."""
    outcome = await EchoTool().verify({"text": "hi"}, {"text": "hi"})
    assert outcome is VerificationOutcome.UNVERIFIED


async def test_a_tool_that_can_check_overrides_verify() -> None:
    class CheckingTool(EchoTool):
        async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
            return (
                VerificationOutcome.SUCCESS if result == arguments else VerificationOutcome.FAILED
            )

    tool = CheckingTool()
    assert await tool.verify({"x": 1}, {"x": 1}) is VerificationOutcome.SUCCESS
    assert await tool.verify({"x": 2}, {"x": 1}) is VerificationOutcome.FAILED


# --------------------------------------------------------------------------- #
# Definition-time guards
# --------------------------------------------------------------------------- #
def test_a_tool_without_execute_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError, match="execute"):

        class NoExecute(EchoTool):
            execute = None  # type: ignore[assignment]


def test_an_abstract_intermediate_may_still_be_undeclared() -> None:
    """A work-in-progress ABC is not registrable, so its declarations are due later.

    It stays abstract simply by not implementing ``execute``.
    """

    class WorkInProgress(Tool):
        pass

    assert WorkInProgress.__abstractmethods__ == {"execute"}


@pytest.mark.parametrize(
    "field",
    ["name", "description", "input_schema", "permission_level", "timeout", "reversibility"],
)
def test_a_concrete_tool_missing_a_required_declaration_fails_to_define(
    field: str,
) -> None:
    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        return None

    namespace: dict[str, Any] = {
        "name": "test.subject",
        "description": "d",
        "input_schema": {},
        "permission_level": PermissionLevel.READ_ONLY,
        "timeout": 1.0,
        "reversibility": Reversibility.IRREVERSIBLE,
        "execute": execute,
    }
    namespace.pop(field)

    with pytest.raises(TypeError, match=field):
        type(f"Missing{field}", (Tool,), namespace)


def test_timeout_must_be_a_positive_bounded_number() -> None:
    for bad in (0, -1.0):
        with pytest.raises(TypeError, match="timeout"):

            class BadTimeout(EchoTool):
                timeout = bad


def test_permission_level_must_be_on_the_one_scale() -> None:
    with pytest.raises(TypeError, match="permission_level"):

        class BadLevel(EchoTool):
            permission_level = 3  # type: ignore[assignment]


def test_reversibility_must_be_declared() -> None:
    with pytest.raises(TypeError, match="reversibility"):

        class BadReversibility(EchoTool):
            reversibility = "reversible"  # type: ignore[assignment]


def test_execute_must_be_async() -> None:
    """A sync execute passes most type checkers and explodes on the first await."""

    with pytest.raises(TypeError, match="async"):

        class SyncTool(EchoTool):
            def execute(self, arguments: Mapping[str, Any]) -> Any:
                return dict(arguments)


def test_a_family_base_may_declare_for_its_family() -> None:
    """A lineage may centralise a declaration — what it may not do is omit one."""

    class GitToolBase(Tool):
        name = "git.base"
        description = "Shared git declarations."
        input_schema: ClassVar[dict[str, Any]] = {"type": "object"}
        permission_level = PermissionLevel.SAFE_ACTIONS
        timeout = 30.0
        reversibility = Reversibility.REVERSIBLE

        async def execute(self, arguments: Mapping[str, Any]) -> Any:
            return arguments

    class GitStatus(GitToolBase):
        name = "git.status"

    assert GitStatus().name == "git.status"
    assert GitStatus().timeout == 30.0
