"""Unit tests for the tool execution pipeline (T036).

§16 fixes the order — schema → permission → policy → execute → verify →
event → result — and §66.7 fixes the split: tools declare, the pipeline
decides. These tests pin that a call cannot skip a stage: bad arguments
never reach `execute`, a denial neither runs nor emits, `TOOL_STARTED` is
published only once execution is genuinely about to begin (and always
pairs with `COMPLETED` or `FAILED`), timeouts become the retryable 504
rather than the generic 500, and no event payload ever carries the
arguments (§59.6).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, ClassVar

import pytest

from app.core.errors import (
    CommandNotAllowedError,
    ConfirmationRequiredError,
    ErrorCode,
    OperationTimeoutError,
    PermissionDeniedError,
    ToolError,
    ToolNotFoundError,
    ToolSchemaInvalidError,
    ToolVerificationError,
)
from app.core.permissions import PermissionManager
from app.database.models import AuditOutcome, PermissionLevel, VerificationOutcome
from app.events.bus import EventBus, EventEnvelope, Subscription
from app.events.types import EventType
from app.security.audit import PERMISSION_DENIED, AuditLogger
from app.tools.base import Reversibility, Tool
from app.tools.executor import PolicyCheck, ToolExecutor, ToolResult
from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Tool doubles
# --------------------------------------------------------------------------- #
class RecordingTool(Tool):
    name = "mock.recording"
    description = "Record one call and echo the arguments."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.READ_ONLY
    timeout = 5.0
    reversibility = Reversibility.REVERSIBLE

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        self.calls.append(dict(arguments))
        return dict(arguments)


class NodeRecordingTool(RecordingTool):
    name = "mock.node"
    node_scope = "windows"


class WriteTool(RecordingTool):
    name = "mock.write"
    permission_level = PermissionLevel.MODIFY_PROJECT


class DestructTool(RecordingTool):
    name = "mock.destruct"
    reversibility = Reversibility.IRREVERSIBLE


class SlowTool(RecordingTool):
    name = "mock.slow"
    timeout = 0.05

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        await asyncio.sleep(30)
        return "never"


class ForeverTool(RecordingTool):
    name = "mock.forever"

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        await self.release.wait()
        return "never"


class ExplodingTool(RecordingTool):
    name = "mock.explode"

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        raise ValueError("boom")


class TypedFailTool(RecordingTool):
    name = "mock.typed"

    async def execute(self, arguments: Mapping[str, Any]) -> Any:
        raise CommandNotAllowedError(self.name, "not on the allow-list")


class VerifyingTool(RecordingTool):
    name = "mock.verify"

    def __init__(self) -> None:
        super().__init__()
        self.verify_outcome = VerificationOutcome.UNVERIFIED

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        return self.verify_outcome


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #
def make_executor(
    audit_sink: Any,
    *tools: Tool,
    default_level: PermissionLevel = PermissionLevel.DESTRUCTIVE,
    policy: PolicyCheck | None = None,
) -> tuple[ToolExecutor, EventBus, ToolRegistry]:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    bus = EventBus()
    executor = ToolExecutor(
        registry=registry,
        permissions=PermissionManager(
            audit=AuditLogger(audit_sink),
            default_level=default_level,
        ),
        events=bus,
        policy=policy,
    )
    return executor, bus, registry


async def drain(subscription: Subscription, *, quiet: float = 0.15) -> list[EventEnvelope]:
    """Collect events until the quiet period passes; [] means none arrived."""
    events: list[EventEnvelope] = []
    while True:
        try:
            envelope = await asyncio.wait_for(subscription.get(), timeout=quiet)
        except TimeoutError:
            return events
        if envelope is None:
            return events
        events.append(envelope)


async def next_event(subscription: Subscription) -> EventEnvelope:
    envelope = await asyncio.wait_for(subscription.get(), timeout=1)
    assert envelope is not None
    return envelope


async def run(
    executor: ToolExecutor,
    tool: str | Tool = "mock.recording",
    arguments: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> ToolResult:
    return await executor.run(
        tool,
        arguments if arguments is not None else {"text": "hi"},
        principal="alice",
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Stage 1 — schema: nothing downstream runs on bad input
# --------------------------------------------------------------------------- #
async def test_invalid_arguments_fail_schema_before_execution(
    audit_sink: Any,
) -> None:
    tool = RecordingTool()
    executor, bus, _ = make_executor(audit_sink, tool)
    subscription = bus.subscribe()

    with pytest.raises(ToolSchemaInvalidError) as excinfo:
        await run(executor, arguments={"wrong": True})

    assert excinfo.value.http_status == 422
    assert excinfo.value.code is ErrorCode.TOOL_SCHEMA_INVALID
    assert excinfo.value.details["errors"]
    assert tool.calls == []
    assert await drain(subscription) == []


async def test_unknown_tool_is_a_404_with_no_events(
    audit_sink: Any,
) -> None:
    executor, bus, _ = make_executor(audit_sink, RecordingTool())
    subscription = bus.subscribe()

    with pytest.raises(ToolNotFoundError) as excinfo:
        await run(executor, tool="mock.missing")

    assert excinfo.value.http_status == 404
    assert await drain(subscription) == []


# --------------------------------------------------------------------------- #
# Stage 2 — permission: refusal means no execution, no tool events
# --------------------------------------------------------------------------- #
async def test_denial_raises_403_audited_and_without_emitting(
    audit_sink: Any,
) -> None:
    tool = WriteTool()
    executor, bus, _ = make_executor(audit_sink, tool, default_level=PermissionLevel.READ_ONLY)
    subscription = bus.subscribe()

    with pytest.raises(PermissionDeniedError) as excinfo:
        await run(executor, tool="mock.write")

    assert excinfo.value.http_status == 403
    assert excinfo.value.code is ErrorCode.PERMISSION_DENIED
    assert excinfo.value.details["node"] == "server"
    assert excinfo.value.details["scope"] == [
        "alice",
        "server",
        "mock.write",
        "mock.write",
    ]
    assert audit_sink.actions() == [PERMISSION_DENIED]
    assert audit_sink.decisions() == [AuditOutcome.DENIED]
    assert tool.calls == []
    assert await drain(subscription) == []


async def test_confirmation_required_raises_409_without_executing(
    audit_sink: Any,
) -> None:
    tool = WriteTool()
    executor, bus, _ = make_executor(audit_sink, tool, default_level=PermissionLevel.MODIFY_PROJECT)
    subscription = bus.subscribe()

    with pytest.raises(ConfirmationRequiredError) as excinfo:
        await run(executor, tool="mock.write")

    assert excinfo.value.http_status == 409
    assert audit_sink.decisions() == [AuditOutcome.CONFIRM_REQUIRED]
    assert tool.calls == []
    assert await drain(subscription) == []


async def test_confirmed_invocation_of_a_level_2_tool_runs(
    audit_sink: Any,
) -> None:
    tool = WriteTool()
    executor, _, _ = make_executor(audit_sink, tool, default_level=PermissionLevel.MODIFY_PROJECT)

    result = await run(executor, tool="mock.write", confirmed=True)

    assert result.output == {"text": "hi"}
    assert tool.calls == [{"text": "hi"}]


async def test_workspace_pre_authorization_answers_level_2(
    audit_sink: Any,
) -> None:
    tool = WriteTool()
    executor, _, _ = make_executor(audit_sink, tool, default_level=PermissionLevel.MODIFY_PROJECT)

    result = await run(executor, tool="mock.write", workspace_authorized=True)

    assert isinstance(result, ToolResult)
    assert tool.calls == [{"text": "hi"}]


async def test_irreversible_tool_asks_even_at_the_lowest_level(
    audit_sink: Any,
) -> None:
    """§66.17: the declaration, not the level, decides the strong confirmation."""
    tool = DestructTool()
    executor, _, _ = make_executor(audit_sink, tool)  # granted DESTRUCTIVE

    with pytest.raises(ConfirmationRequiredError):
        await run(executor, tool="mock.destruct")

    assert tool.calls == []

    result = await run(executor, tool="mock.destruct", confirmed=True)
    assert isinstance(result, ToolResult)
    assert tool.calls == [{"text": "hi"}]


# --------------------------------------------------------------------------- #
# Stage 3 — policy: the injectable veto
# --------------------------------------------------------------------------- #
async def test_policy_veto_refuses_before_execution_or_events(
    audit_sink: Any,
) -> None:
    tool = RecordingTool()

    async def veto(_tool: Tool, _arguments: Mapping[str, Any]) -> None:
        raise CommandNotAllowedError(tool.name, "workspace policy says no")

    executor, bus, _ = make_executor(audit_sink, tool, policy=veto)
    subscription = bus.subscribe()

    with pytest.raises(CommandNotAllowedError):
        await run(executor)

    assert tool.calls == []
    assert await drain(subscription) == []
    # Permission ran (and allowed) before policy refused.
    assert AuditOutcome.ALLOWED in audit_sink.decisions()


async def test_policy_receives_the_registered_tool_and_validated_arguments(
    audit_sink: Any,
) -> None:
    registered = RecordingTool()
    seen: list[tuple[Tool, Mapping[str, Any]]] = []

    async def record(tool: Tool, arguments: Mapping[str, Any]) -> None:
        seen.append((tool, arguments))

    executor, _, registry = make_executor(audit_sink, registered, policy=record)

    await run(executor)

    assert len(seen) == 1
    assert seen[0][0] is registry.lookup("mock.recording")
    assert seen[0][0] is registered
    assert dict(seen[0][1]) == {"text": "hi"}


# --------------------------------------------------------------------------- #
# Stages 4–7 — execute, verify, event, result
# --------------------------------------------------------------------------- #
async def test_happy_path_returns_a_full_result(
    audit_sink: Any,
) -> None:
    tool = RecordingTool()
    executor, _, _ = make_executor(audit_sink, tool)

    result = await run(executor)

    assert isinstance(result, ToolResult)
    assert result.tool == "mock.recording"
    assert result.output == {"text": "hi"}
    assert result.verification is VerificationOutcome.UNVERIFIED
    assert result.node == "server"
    assert result.duration_ms >= 0
    assert tool.calls == [{"text": "hi"}]


async def test_node_scoped_tool_reports_its_node(
    audit_sink: Any,
) -> None:
    executor, bus, _ = make_executor(audit_sink, NodeRecordingTool())
    subscription = bus.subscribe()

    result = await run(executor, tool="mock.node")
    started = await next_event(subscription)

    assert result.node == "windows"
    assert started.payload["node"] == "windows"


async def test_started_is_published_before_execute_runs(
    audit_sink: Any,
) -> None:
    """The STARTED event brackets execution: it is readable *inside* execute."""

    class GatedTool(RecordingTool):
        name = "mock.gated"
        seen: EventType | str | None = None

        async def execute(self, arguments: Mapping[str, Any]) -> Any:
            envelope = await asyncio.wait_for(subscription.get(), timeout=1)
            assert envelope is not None
            self.seen = envelope.event_type
            self.calls.append(dict(arguments))
            return dict(arguments)

    tool = GatedTool()
    executor, bus, _ = make_executor(audit_sink, tool)
    subscription = bus.subscribe()

    await run(executor, tool="mock.gated")

    assert tool.seen == EventType.TOOL_STARTED


async def test_events_pair_strictly_and_payloads_carry_no_arguments(
    audit_sink: Any,
) -> None:
    executor, bus, _ = make_executor(audit_sink, RecordingTool())
    subscription = bus.subscribe()

    await run(executor, arguments={"text": "secret thought"})

    started = await next_event(subscription)
    completed = await next_event(subscription)

    assert started.event_type == EventType.TOOL_STARTED
    assert completed.event_type == EventType.TOOL_COMPLETED
    assert started.persist is False  # the start is not the durable record
    assert completed.persist is True  # the outcome is (§66.16 history)
    assert set(started.payload) == {"tool", "principal", "client", "node"}
    assert set(completed.payload) == {
        "tool",
        "principal",
        "client",
        "node",
        "duration_ms",
        "verification",
    }
    assert completed.payload["verification"] == VerificationOutcome.UNVERIFIED
    assert await drain(subscription) == []


async def test_timeout_is_the_retryable_504_with_a_failed_pair(
    audit_sink: Any,
) -> None:
    executor, bus, _ = make_executor(audit_sink, SlowTool())
    subscription = bus.subscribe()

    with pytest.raises(OperationTimeoutError) as excinfo:
        await run(executor, tool="mock.slow")

    assert excinfo.value.http_status == 504
    assert excinfo.value.retryable is True
    assert excinfo.value.code is ErrorCode.TIMEOUT

    started = await next_event(subscription)
    failed = await next_event(subscription)
    assert started.event_type == EventType.TOOL_STARTED
    assert failed.event_type == EventType.TOOL_FAILED
    assert failed.payload["error_code"] == ErrorCode.TIMEOUT
    assert failed.persist is True


async def test_a_plain_exception_becomes_tool_error_with_cause(
    audit_sink: Any,
) -> None:
    executor, bus, _ = make_executor(audit_sink, ExplodingTool())
    subscription = bus.subscribe()

    with pytest.raises(ToolError) as excinfo:
        await run(executor, tool="mock.explode")

    assert excinfo.value.code is ErrorCode.TOOL_EXECUTION_FAILED
    assert excinfo.value.http_status == 500
    assert isinstance(excinfo.value.cause, ValueError)

    started = await next_event(subscription)
    failed = await next_event(subscription)
    assert started.event_type == EventType.TOOL_STARTED
    assert failed.event_type == EventType.TOOL_FAILED
    assert failed.payload["error_code"] == ErrorCode.TOOL_EXECUTION_FAILED


async def test_a_typed_error_from_a_tool_propagates_unchanged(
    audit_sink: Any,
) -> None:
    executor, bus, _ = make_executor(audit_sink, TypedFailTool())
    subscription = bus.subscribe()

    with pytest.raises(CommandNotAllowedError) as excinfo:
        await run(executor, tool="mock.typed")

    assert excinfo.value.http_status == 403
    assert excinfo.value.code is ErrorCode.COMMAND_NOT_ALLOWED

    failed = await next_event(subscription)
    assert failed.event_type == EventType.TOOL_STARTED  # bracket still holds
    failed = await next_event(subscription)
    assert failed.event_type == EventType.TOOL_FAILED
    assert failed.payload["error_code"] == ErrorCode.COMMAND_NOT_ALLOWED


async def test_failed_verification_is_reported_after_execution(
    audit_sink: Any,
) -> None:
    tool = VerifyingTool()
    tool.verify_outcome = VerificationOutcome.FAILED
    executor, bus, _ = make_executor(audit_sink, tool)
    subscription = bus.subscribe()

    with pytest.raises(ToolVerificationError) as excinfo:
        await run(executor, tool="mock.verify")

    # The effect already happened - the failure is after the fact (§17).
    assert excinfo.value.code is ErrorCode.TOOL_VERIFICATION_FAILED
    assert tool.calls == [{"text": "hi"}]

    started = await next_event(subscription)
    failed = await next_event(subscription)
    assert started.event_type == EventType.TOOL_STARTED
    assert failed.event_type == EventType.TOOL_FAILED
    assert failed.payload["error_code"] == ErrorCode.TOOL_VERIFICATION_FAILED


async def test_partial_verification_passes_through_to_the_result(
    audit_sink: Any,
) -> None:
    tool = VerifyingTool()
    tool.verify_outcome = VerificationOutcome.PARTIAL
    executor, bus, _ = make_executor(audit_sink, tool)
    subscription = bus.subscribe()

    result = await run(executor, tool="mock.verify")
    started = await next_event(subscription)
    completed = await next_event(subscription)

    assert result.verification is VerificationOutcome.PARTIAL
    assert completed.event_type == EventType.TOOL_COMPLETED
    assert completed.payload["verification"] == VerificationOutcome.PARTIAL
    assert started.event_type == EventType.TOOL_STARTED


async def test_cancellation_propagates_and_still_closes_the_pair(
    audit_sink: Any,
) -> None:
    executor, bus, _ = make_executor(audit_sink, ForeverTool())
    subscription = bus.subscribe()

    task = asyncio.create_task(run(executor, tool="mock.forever"))
    started = await next_event(subscription)
    assert started.event_type == EventType.TOOL_STARTED

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    failed = await next_event(subscription)
    assert failed.event_type == EventType.TOOL_FAILED
    assert failed.payload["error_code"] == ErrorCode.CANCELLED


async def test_a_closed_bus_does_not_block_execution(
    audit_sink: Any,
) -> None:
    tool = RecordingTool()
    executor, bus, _ = make_executor(audit_sink, tool)
    bus.close()

    result = await run(executor)

    assert result.output == {"text": "hi"}
    assert tool.calls == [{"text": "hi"}]


async def test_run_by_instance_executes_the_registered_instance(
    audit_sink: Any,
) -> None:
    registered = RecordingTool()
    stranger = RecordingTool()
    executor, _, _ = make_executor(audit_sink, registered)

    await run(executor, tool=stranger)

    assert registered.calls == [{"text": "hi"}]
    assert stranger.calls == []
