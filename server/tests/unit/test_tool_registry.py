"""Unit tests for the unified Tool Registry (T035).

§59.6's promise is that one registry backs every tool, and §16's first
pipeline stage is schema validation — so the tests pin the registry's
authority: names are unique or the register refuses, misses are 404s and not
KeyErrors, a broken schema fails at registration rather than at every call,
and validation can only ever run against the *registered* tool.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, cast

import pytest

from app.core.errors import (
    ConflictError,
    ErrorCode,
    ToolNotFoundError,
    ToolSchemaInvalidError,
)
from app.database.models import PermissionLevel
from app.tools.base import Reversibility, Tool
from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.unit


class EchoTool(Tool):
    name = "mock.echo"
    description = "Return the arguments unchanged."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "count": {"type": "integer", "minimum": 1},
        },
        "required": ["text"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 5.0
    reversibility = Reversibility.REVERSIBLE

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        return dict(arguments)


class GreetTool(Tool):
    name = "mock.greet"
    description = "Greet someone."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"who": {"type": "string"}},
        "required": ["who"],
    }
    permission_level = PermissionLevel.SAFE_ACTIONS
    timeout = 2.0
    reversibility = Reversibility.REVERSIBLE
    node_scope = "windows"

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        return f"hello {arguments['who']}"


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(EchoTool())
    reg.register(GreetTool())
    return reg


# --------------------------------------------------------------------------- #
# Register / lookup / list
# --------------------------------------------------------------------------- #
def test_register_then_lookup_returns_the_same_instance() -> None:
    registry = ToolRegistry()
    tool = EchoTool()
    registry.register(tool)
    assert registry.lookup("mock.echo") is tool


def test_duplicate_names_are_refused(registry: ToolRegistry) -> None:
    with pytest.raises(ConflictError) as excinfo:
        registry.register(EchoTool())
    assert excinfo.value.code is ErrorCode.CONFLICT
    assert excinfo.value.http_status == 409
    assert excinfo.value.details["tool"] == "mock.echo"


def test_an_unknown_name_is_a_404_not_a_keyerror(registry: ToolRegistry) -> None:
    with pytest.raises(ToolNotFoundError) as excinfo:
        registry.lookup("mock.missing")
    assert excinfo.value.code is ErrorCode.TOOL_NOT_FOUND
    assert excinfo.value.http_status == 404
    assert excinfo.value.tool == "mock.missing"


def test_membership(registry: ToolRegistry) -> None:
    assert "mock.echo" in registry
    assert "mock.missing" not in registry
    assert 42 not in registry


def test_list_is_sorted_by_name(registry: ToolRegistry) -> None:
    assert [tool.name for tool in registry.list()] == ["mock.echo", "mock.greet"]


def test_an_empty_registry_lists_nothing() -> None:
    assert ToolRegistry().list() == []


# --------------------------------------------------------------------------- #
# Describe (§59.6's record as data)
# --------------------------------------------------------------------------- #
def test_describe_carries_the_full_registry_record(registry: ToolRegistry) -> None:
    record = registry.describe("mock.greet")
    assert record == {
        "name": "mock.greet",
        "description": "Greet someone.",
        "input_schema": {
            "type": "object",
            "properties": {"who": {"type": "string"}},
            "required": ["who"],
        },
        "permission_level": "1",
        "timeout": 2.0,
        "reversibility": "reversible",
        "node_scope": "windows",
        "audit": False,
        "category": "mock",
    }


def test_describe_accepts_the_tool_instance(registry: ToolRegistry) -> None:
    assert registry.describe(EchoTool())["name"] == "mock.echo"


def test_describe_of_an_unknown_name_is_a_404(registry: ToolRegistry) -> None:
    with pytest.raises(ToolNotFoundError):
        registry.describe("mock.missing")


def test_the_described_schema_is_a_copy_not_the_registered_one(
    registry: ToolRegistry,
) -> None:
    """This dict travels into prompts; scribbling on it must not corrupt the tool."""
    record = registry.describe("mock.echo")
    record["input_schema"]["properties"]["text"]["type"] = "integer"
    fresh = registry.describe("mock.echo")
    assert fresh["input_schema"]["properties"]["text"]["type"] == "string"


# --------------------------------------------------------------------------- #
# Validate (§16 stage 1)
# --------------------------------------------------------------------------- #
def test_valid_arguments_come_back_as_a_plain_dict(registry: ToolRegistry) -> None:
    source: dict[str, Any] = {"text": "hi", "count": 2}
    result = registry.validate("mock.echo", source)
    assert result == source
    assert isinstance(result, dict)
    assert result is not source  # a copy, so later mutation cannot reach the call


def test_missing_required_property_is_reported_at_the_root(
    registry: ToolRegistry,
) -> None:
    with pytest.raises(ToolSchemaInvalidError) as excinfo:
        registry.validate("mock.echo", {})
    error = excinfo.value
    assert error.code is ErrorCode.TOOL_SCHEMA_INVALID
    assert error.http_status == 422
    assert error.details["errors"][0].startswith("<root>:")
    assert "required property" in error.details["errors"][0]


def test_every_violation_is_listed_with_its_path(registry: ToolRegistry) -> None:
    with pytest.raises(ToolSchemaInvalidError) as excinfo:
        registry.validate("mock.echo", {"text": 5, "count": 0})
    errors = excinfo.value.details["errors"]
    assert len(errors) == 2
    assert errors[0].startswith("count:")
    assert errors[1].startswith("text:")
    # The first is the retry hint the caller shows the model.
    assert "count" in excinfo.value.message


def test_additional_properties_are_refused_when_the_schema_says_so(
    registry: ToolRegistry,
) -> None:
    with pytest.raises(ToolSchemaInvalidError) as excinfo:
        registry.validate("mock.echo", {"text": "x", "surprise": True})
    message = excinfo.value.details["errors"][0]
    # jsonschema reports this violation at the object, not the offending key —
    # the key still has to appear, or the caller cannot correct the retry.
    assert message.startswith("<root>:")
    assert "surprise" in message


def test_validate_accepts_a_tool_instance_and_uses_the_registered_one(
    registry: ToolRegistry,
) -> None:
    stray = EchoTool()  # not the instance the registry holds
    assert registry.validate(stray, {"text": "hi"}) == {"text": "hi"}


def test_validate_refuses_a_tool_that_was_never_registered() -> None:
    registry = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        registry.validate(EchoTool(), {"text": "hi"})


# --------------------------------------------------------------------------- #
# Registration-time schema checks
# --------------------------------------------------------------------------- #
def test_a_tool_whose_schema_is_broken_fails_at_registration() -> None:
    class BrokenSchema(EchoTool):
        name = "mock.broken"
        input_schema: ClassVar[dict[str, Any]] = {"type": "banana"}

    registry = ToolRegistry()
    with pytest.raises(ToolSchemaInvalidError) as excinfo:
        registry.register(BrokenSchema())
    assert "not a valid JSON Schema" in excinfo.value.message


def test_a_non_object_schema_is_refused_before_jsonschema_sees_it() -> None:
    namespace: dict[str, Any] = {
        "name": "mock.listschema",
        "description": "d",
        "input_schema": ["not", "a", "schema"],
        "permission_level": PermissionLevel.READ_ONLY,
        "timeout": 1.0,
        "reversibility": Reversibility.IRREVERSIBLE,
    }

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        return None

    namespace["execute"] = execute
    registry = ToolRegistry()
    # type() builds the class at runtime; the guard under test runs at
    # definition, so there is nothing static for mypy to see here.
    fake = cast(Tool, type("ListSchema", (Tool,), namespace))
    with pytest.raises(ToolSchemaInvalidError) as excinfo:
        registry.register(fake)
    assert "must be a JSON object" in excinfo.value.message
