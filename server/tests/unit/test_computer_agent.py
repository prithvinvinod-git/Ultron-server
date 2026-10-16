"""Computer agent tests (T144/T147, spec §13/§42/§66.10).

The agent is a model-driven worker: a ``FakeProvider`` scripts the model turns, a
real ``ToolExecutor`` runs the calls through §16's pipeline, and the ``computer.*``
tools (T143) dispatch to the T146 node double over a real gateway. That makes the
whole path real — model → ``computer.list_windows`` → gateway → node → report —
with no Windows, no socket and no browser, while the assertions cover the pieces
only the agent owns: the fallback plan it builds from the target's capabilities,
the report it returns, its progress events and its failure discipline.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.computer import ComputerAgent
from app.agents.computer.gateway import NodeGateway
from app.agents.computer.protocol import NodeRegistration
from app.agents.loop import definitions_from_registry
from app.config import ModelRouterSettings
from app.config.settings import ComputerNodeSettings
from app.core.errors import AgentError, CapabilityNotImplementedError, InvalidInputError
from app.core.permissions import PermissionManager
from app.database.models import PermissionLevel
from app.events.bus import EventBus
from app.models.base import FinishReason, ModelResponse, ToolCall
from app.models.fake import FakeProvider
from app.models.router import ModelRouter
from app.security.audit import AuditLogger
from app.tools.computer import build_computer_tools
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from tests.support.node_double import FakeNode

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Fixtures and doubles
# --------------------------------------------------------------------------- #


class RecordingBus(EventBus):
    """A real bus that also keeps every published event, for assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, event_type: Any, payload: Any = None, **kwargs: Any) -> Any:
        self.published.append((str(event_type), dict(payload or {})))
        return await super().publish(event_type, payload, **kwargs)


@pytest.fixture
def gateway() -> NodeGateway:
    return NodeGateway(settings=ComputerNodeSettings(), require_auth=False)


async def _register(gateway: NodeGateway, node: FakeNode) -> FakeNode:
    node.bind(gateway)
    registration = NodeRegistration.model_validate(node.registration(credential=""))
    await gateway.register(registration, node.transport)
    return node


def _windows_node(capabilities: tuple[str, ...] = ("windows_ui", "screenshots")) -> FakeNode:
    return FakeNode(
        capabilities=capabilities,
        handlers={
            "computer.list_windows": lambda _a: {"windows": [{"title": "Notepad"}]},
            "computer.screenshot": lambda _a: {"path": "C:/shot.png"},
            "computer.open_application": lambda _a: {"opened": "notepad"},
        },
    )


def _registry(gateway: NodeGateway) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in build_computer_tools(gateway):
        registry.register(tool)
    return registry


def _executor(registry: ToolRegistry, audit_sink: Any) -> ToolExecutor:
    return ToolExecutor(
        registry=registry,
        permissions=PermissionManager(
            audit=AuditLogger(audit_sink),
            default_level=PermissionLevel.DESTRUCTIVE,
        ),
        events=EventBus(),
    )


def _tool_call(name: str, arguments: dict[str, Any], call_id: str = "c1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


def _tool_response(*calls: ToolCall) -> ModelResponse:
    return ModelResponse(
        model="llama3",
        content="",
        tool_calls=tuple(calls),
        finish_reason=FinishReason.TOOL_CALLS,
        provider="fake",
    )


def _answer(text: str = "done") -> ModelResponse:
    return ModelResponse(
        model="llama3",
        content=text,
        finish_reason=FinishReason.STOP,
        provider="fake",
    )


def _provider(*responses: ModelResponse) -> FakeProvider:
    return FakeProvider(name="ollama", local=True, responses=responses)


def _router(provider: FakeProvider) -> ModelRouter:
    return ModelRouter(
        [provider],
        settings=ModelRouterSettings(default="ollama:llama3", fallback_chain=[]),
    )


def _agent(
    gateway: NodeGateway,
    *,
    provider: FakeProvider,
    audit_sink: Any,
    bus: EventBus | None = None,
    **kwargs: Any,
) -> ComputerAgent:
    registry = _registry(gateway)
    return ComputerAgent(
        router=_router(provider),
        executor=_executor(registry, audit_sink),
        tools=definitions_from_registry(registry),
        gateway=gateway,
        bus=bus,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Happy path: model -> computer.list_windows -> node -> report
# --------------------------------------------------------------------------- #


async def test_agent_drives_the_loop_through_the_computer_tools(
    gateway: NodeGateway, audit_sink: Any
) -> None:
    node = await _register(gateway, _windows_node())
    provider = _provider(
        _tool_response(_tool_call("computer.list_windows", {})),
        _answer("Notepad is open"),
    )
    agent = _agent(gateway, provider=provider, audit_sink=audit_sink, name="computer")

    report = await agent.run({"prompt": "what is open?", "task_id": "task-1"})

    assert report["agent"] == "computer.agent"
    assert report["answer"] == "Notepad is open"
    assert report["completed"] is True
    assert report["stop_reason"] == "completed"
    assert report["iterations"] == 2
    assert report["tool_calls"] == 1
    assert report["goal"] == "what is open?"
    assert report["task_id"] == "task-1"
    # The tool reached the node as a structured command, not a shell line.
    assert node.transport.sent[-1]["tool"] == "computer.list_windows"


async def test_agent_advertises_only_the_computer_tools(
    gateway: NodeGateway, audit_sink: Any
) -> None:
    provider = _provider(_answer())
    agent = _agent(gateway, provider=provider, audit_sink=audit_sink)

    await agent.run({"prompt": "hi"})

    assert provider.last_request is not None
    names = {definition.name for definition in provider.last_request.tools}
    assert names and all(name.startswith("computer.") for name in names)


async def test_agent_falls_back_to_its_own_id_for_the_task(
    gateway: NodeGateway, audit_sink: Any
) -> None:
    provider = _provider(_answer())
    agent = _agent(gateway, provider=provider, audit_sink=audit_sink)

    report = await agent.run({"prompt": "hi"})

    assert report["task_id"] == str(agent.agent_id)


# --------------------------------------------------------------------------- #
# Fallback plan (§66.10)
# --------------------------------------------------------------------------- #


async def test_agent_plans_from_the_target_capabilities(
    gateway: NodeGateway, audit_sink: Any
) -> None:
    node = await _register(gateway, _windows_node(capabilities=("windows_apps",)))
    provider = _provider(_answer())
    agent = _agent(
        gateway, provider=provider, audit_sink=audit_sink, target=node.node_id
    )

    report = await agent.run({"prompt": "open notepad"})

    # windows_apps implies the application_api method, the most reliable lead.
    assert report["target"] == node.node_id
    assert report["fallback"]["selected"] == "application_api"


async def test_agent_without_a_gateway_plans_the_full_chain(
    gateway: NodeGateway, audit_sink: Any
) -> None:
    provider = _provider(_answer())
    agent = ComputerAgent(
        router=_router(provider),
        executor=_executor(_registry(gateway), audit_sink),
        tools=definitions_from_registry(_registry(gateway)),
    )

    report = await agent.run({"prompt": "do something"})

    assert report["fallback"]["selected"] == "application_api"
    methods = [attempt["method"] for attempt in report["fallback"]["attempts"]]
    assert methods == [
        "application_api",
        "dom",
        "ui_automation",
        "keyboard_shortcut",
        "mouse",
        "vision",
    ]


async def test_an_explicit_methods_override_wins(
    gateway: NodeGateway, audit_sink: Any
) -> None:
    provider = _provider(_answer())
    agent = _agent(gateway, provider=provider, audit_sink=audit_sink)

    report = await agent.run({"prompt": "look", "methods": ["vision"]})

    assert report["fallback"]["selected"] == "vision"


async def test_a_required_method_that_is_unavailable_is_refused(
    gateway: NodeGateway, audit_sink: Any
) -> None:
    node = await _register(gateway, _windows_node(capabilities=("windows_apps",)))
    provider = _provider(_answer())
    agent = _agent(
        gateway,
        provider=provider,
        audit_sink=audit_sink,
        target=node.node_id,
        require_method="dom",
    )

    with pytest.raises(CapabilityNotImplementedError):
        await agent.run({"prompt": "go"})


# --------------------------------------------------------------------------- #
# Progress events
# --------------------------------------------------------------------------- #


async def test_agent_publishes_plan_and_report_progress(
    gateway: NodeGateway, audit_sink: Any
) -> None:
    bus = RecordingBus()
    provider = _provider(
        _tool_response(_tool_call("computer.list_windows", {})),
        _answer("done"),
    )
    agent = _agent(gateway, provider=provider, audit_sink=audit_sink, bus=bus)

    await agent.run({"prompt": "go"})

    phases = [payload["phase"] for _, payload in bus.published if "phase" in payload]
    assert phases == ["plan", "report"]
    assert all(kind == "TASK_PROGRESS" for kind, _ in bus.published)


# --------------------------------------------------------------------------- #
# Bounded loop and preconditions
# --------------------------------------------------------------------------- #


async def test_agent_reports_a_loop_that_hit_its_tool_ceiling(
    gateway: NodeGateway, audit_sink: Any
) -> None:
    provider = _provider(
        _tool_response(
            _tool_call("computer.list_windows", {}, "c1"),
            _tool_call("computer.list_windows", {}, "c2"),
        ),
    )
    agent = _agent(gateway, provider=provider, audit_sink=audit_sink, max_tool_calls=1)

    report = await agent.run({"prompt": "go"})

    assert report["completed"] is False
    assert report["stop_reason"] == "max_tool_calls"
    assert report["tool_calls"] == 1


async def test_agent_requires_a_prompt(gateway: NodeGateway, audit_sink: Any) -> None:
    agent = _agent(gateway, provider=_provider(_answer()), audit_sink=audit_sink)
    with pytest.raises(InvalidInputError):
        await agent.run({})


def test_agent_refuses_no_tools(gateway: NodeGateway, audit_sink: Any) -> None:
    registry = _registry(gateway)
    with pytest.raises(ValueError, match="at least one computer tool"):
        ComputerAgent(
            router=_router(_provider(_answer())),
            executor=_executor(registry, audit_sink),
            tools=[],
        )


def test_agent_declares_its_type() -> None:
    assert ComputerAgent.agent_type == "computer.agent"
    assert ComputerAgent.description


# --------------------------------------------------------------------------- #
# Failure translation
# --------------------------------------------------------------------------- #


async def test_agent_passes_a_typed_error_through_untouched(
    gateway: NodeGateway, audit_sink: Any
) -> None:
    agent = _agent(gateway, provider=_provider(_answer()), audit_sink=audit_sink)
    with pytest.raises(InvalidInputError):
        await agent.run({"prompt": "   "})


async def test_agent_wraps_an_unexpected_error(
    gateway: NodeGateway, audit_sink: Any
) -> None:
    provider = _provider()
    provider.queue_error(ValueError("boom"))
    agent = _agent(gateway, provider=provider, audit_sink=audit_sink)

    with pytest.raises(AgentError) as excinfo:
        await agent.run({"prompt": "go"})
    assert isinstance(excinfo.value.cause, ValueError)


# --------------------------------------------------------------------------- #
# Identity binding (§15/§16)
# --------------------------------------------------------------------------- #


async def test_agent_attributes_tool_calls_to_itself(
    gateway: NodeGateway, audit_sink: Any
) -> None:
    await _register(gateway, _windows_node())
    provider = _provider(
        _tool_response(_tool_call("computer.list_windows", {})),
        _answer("done"),
    )
    agent = _agent(gateway, provider=provider, audit_sink=audit_sink)

    await agent.run({"prompt": "go", "task_id": "task-9"})

    rows = [row for row in audit_sink.rows if row.get("resource_type") == "tool"]
    assert rows
    assert {str(row["actor_id"]) for row in rows} == {str(agent.agent_id)}
    assert {row["resource_id"] for row in rows} == {"computer.list_windows"}
