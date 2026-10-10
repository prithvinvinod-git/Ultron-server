"""Unit tests for the deterministic mock tools (T037).

Four probes that let later suites exercise the pipeline without a real
capability: a no-op round-trip, a typed failure, a bounded delay (whose
declared timeout is deliberately shorter than its schema maximum), and a
level-2 write whose effect is visible outside the pipeline. The tests pin
each tool's declared job — level, reversibility, schema — and prove the
two interesting ones reach `ConfirmationRequiredError` and
`OperationTimeoutError` through the *real* executor, not local doubles.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from time import perf_counter
from typing import Any

import pytest

from app.core.errors import (
    ConfirmationRequiredError,
    ErrorCode,
    OperationTimeoutError,
    ToolError,
    ToolSchemaInvalidError,
)
from app.core.permissions import PermissionManager
from app.database.models import PermissionLevel, VerificationOutcome
from app.events.bus import EventBus
from app.security.audit import AuditLogger
from app.tools.executor import ToolExecutor, ToolResult
from app.tools.mock.echo import EchoTool
from app.tools.mock.fail import FailTool
from app.tools.mock.sleep import SleepTool
from app.tools.mock.write_state import WriteStateTool
from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_state() -> Iterator[None]:
    WriteStateTool.state.clear()
    yield
    WriteStateTool.state.clear()


def make_pipeline(
    audit_sink: Any,
    *,
    default_level: PermissionLevel = PermissionLevel.DESTRUCTIVE,
) -> tuple[ToolExecutor, EventBus, ToolRegistry]:
    registry = ToolRegistry()
    for tool in (EchoTool(), FailTool(), SleepTool(), WriteStateTool()):
        registry.register(tool)
    bus = EventBus()
    executor = ToolExecutor(
        registry=registry,
        permissions=PermissionManager(
            audit=AuditLogger(audit_sink),
            default_level=default_level,
        ),
        events=bus,
    )
    return executor, bus, registry


async def run(
    executor: ToolExecutor,
    tool: str,
    arguments: Mapping[str, Any],
    **kwargs: Any,
) -> ToolResult:
    return await executor.run(tool, arguments, principal="alice", **kwargs)


# --------------------------------------------------------------------------- #
# Declarations — the registry records each probe's job
# --------------------------------------------------------------------------- #
async def test_all_four_register_under_the_mock_category(
    audit_sink: Any,
) -> None:
    _, _, registry = make_pipeline(audit_sink)

    assert [tool.name for tool in registry.list()] == [
        "mock.echo",
        "mock.fail",
        "mock.sleep",
        "mock.write_state",
    ]
    categories = {tool.name: registry.describe(tool.name)["category"] for tool in registry.list()}
    assert categories == dict.fromkeys(categories, "mock")


async def test_declarations_match_each_probes_job(audit_sink: Any) -> None:
    _, _, registry = make_pipeline(audit_sink)

    for name in ("mock.echo", "mock.fail", "mock.sleep"):
        record = registry.describe(name)
        assert record["permission_level"] == PermissionLevel.READ_ONLY.value
        assert record["reversibility"] == "reversible"
        assert record["timeout"] > 0

    write = registry.describe("mock.write_state")
    assert write["permission_level"] == PermissionLevel.MODIFY_PROJECT.value
    assert write["reversibility"] == "reversible"

    # The timing probe's contract: the budget is shorter than the schema's
    # largest sleep, so a schema-valid call can still hit the timeout.
    assert registry.describe("mock.sleep")["timeout"] < 30


# --------------------------------------------------------------------------- #
# mock.echo
# --------------------------------------------------------------------------- #
async def test_echo_round_trips_through_the_pipeline(audit_sink: Any) -> None:
    executor, _, _ = make_pipeline(audit_sink)

    result = await run(executor, "mock.echo", {"text": "hi", "n": 3})

    assert isinstance(result, ToolResult)
    assert result.output == {"text": "hi", "n": 3}
    assert result.verification is VerificationOutcome.UNVERIFIED


async def test_echo_returns_a_fresh_dict_not_its_input() -> None:
    data: dict[str, Any] = {"a": 1}

    out = await EchoTool().execute(data)
    out["a"] = 99

    assert data == {"a": 1}


# --------------------------------------------------------------------------- #
# mock.fail
# --------------------------------------------------------------------------- #
async def test_fail_raises_a_typed_error_with_the_callers_message() -> None:
    with pytest.raises(ToolError) as excinfo:
        await FailTool().execute({"message": "nope"})

    assert excinfo.value.code is ErrorCode.TOOL_EXECUTION_FAILED
    assert excinfo.value.http_status == 500
    assert excinfo.value.message == "nope"
    assert excinfo.value.tool == "mock.fail"


async def test_fail_through_the_pipeline_keeps_the_default_message(
    audit_sink: Any,
) -> None:
    executor, _, _ = make_pipeline(audit_sink)

    with pytest.raises(ToolError) as excinfo:
        await run(executor, "mock.fail", {})

    assert "deterministic failure" in excinfo.value.message


# --------------------------------------------------------------------------- #
# mock.sleep
# --------------------------------------------------------------------------- #
async def test_sleep_returns_after_its_delay() -> None:
    started = perf_counter()

    out = await SleepTool().execute({"seconds": 0.05})

    assert out == {"slept": 0.05}
    assert perf_counter() - started >= 0.04


async def test_sleep_past_the_declared_budget_is_the_retryable_timeout(
    audit_sink: Any,
) -> None:
    executor, _, _ = make_pipeline(audit_sink)

    # Schema-valid (1s is within the 30s maximum) but past the 0.5s budget.
    with pytest.raises(OperationTimeoutError) as excinfo:
        await run(executor, "mock.sleep", {"seconds": 1.0})

    assert excinfo.value.http_status == 504
    assert excinfo.value.retryable is True
    assert excinfo.value.code is ErrorCode.TIMEOUT
    assert excinfo.value.details["operation"] == "mock.sleep"


# --------------------------------------------------------------------------- #
# mock.write_state
# --------------------------------------------------------------------------- #
async def test_write_state_requires_confirmation_at_level_2(
    audit_sink: Any,
) -> None:
    executor, _, _ = make_pipeline(audit_sink)

    with pytest.raises(ConfirmationRequiredError):
        await run(
            executor,
            "mock.write_state",
            {"key": "plan", "value": "attack at dawn"},
        )

    assert WriteStateTool.state == {}


async def test_write_state_writes_when_confirmed(audit_sink: Any) -> None:
    executor, _, _ = make_pipeline(audit_sink)

    result = await run(
        executor,
        "mock.write_state",
        {"key": "plan", "value": "attack at dawn"},
        confirmed=True,
    )

    assert result.output == {
        "key": "plan",
        "value": "attack at dawn",
        "state": {"plan": "attack at dawn"},
    }
    # The effect is visible outside the pipeline (17's verify reads here).
    assert WriteStateTool.state == {"plan": "attack at dawn"}


async def test_write_state_schema_requires_key_and_value(
    audit_sink: Any,
) -> None:
    _, _, registry = make_pipeline(audit_sink)

    with pytest.raises(ToolSchemaInvalidError) as excinfo:
        registry.validate("mock.write_state", {"key": "orphan"})

    assert excinfo.value.http_status == 422
    assert any("value" in error for error in excinfo.value.details["errors"])
