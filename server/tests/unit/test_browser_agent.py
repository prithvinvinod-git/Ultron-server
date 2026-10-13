"""T113 tests: the browser agent (spec §11).

The agent is a model-driven worker, so it is exercised end to end without a
network or a real browser: a ``FakeProvider`` (T065) scripts the model turns, a
real ``ToolExecutor`` runs the calls through §16's pipeline, and the ``browser.*``
tools (T112) act on the in-memory runtime fakes from T111
(:mod:`tests.unit.test_browser_session`). That makes the flow real — model →
``browser.open`` → per-agent session → report — while the session key the agent
binds is exactly what the isolation assertion checks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.agents.browser import BrowserAgent
from app.agents.loop import definitions_from_registry
from app.config import ModelRouterSettings
from app.config.settings import BrowserSettings
from app.core.errors import AgentError, InvalidInputError
from app.core.permissions import PermissionManager
from app.database.models import PermissionLevel
from app.events.bus import EventBus
from app.models.base import FinishReason, ModelResponse, ToolCall
from app.models.fake import FakeProvider
from app.models.router import ModelRouter
from app.security.audit import AuditLogger
from app.tools.browser import session as session_module, tools as tools_module
from app.tools.browser.session import BrowserSessionManager
from app.tools.browser.tools import build_browser_tools
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from tests.unit import test_browser_session as tbs

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Fixtures + doubles
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def playwright_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the availability probe report success so the tools acquire sessions.

    The SSRF guard's DNS lookup is bypassed too: it is exercised where it lives
    (T112's ``browser.open`` tests), and a browsing agent is not a network test.
    """
    monkeypatch.setattr(session_module, "playwright_unavailable_reason", lambda: None)
    monkeypatch.setattr(
        tools_module,
        "assert_url_allowed",
        lambda url, *, allow_private_networks=False: url,
    )


class RecordingBus(EventBus):
    """A real bus that also keeps every published event, for assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, event_type: Any, payload: Any = None, **kwargs: Any) -> Any:
        self.published.append((str(event_type), dict(payload or {})))
        return await super().publish(event_type, payload, **kwargs)


def _settings(tmp_path: Path, **overrides: Any) -> BrowserSettings:
    base: dict[str, Any] = {
        "enabled": True,
        "screenshot_dir": str(tmp_path / "shots"),
        "download_dir": str(tmp_path / "downloads"),
    }
    base.update(overrides)
    return BrowserSettings(**base)


def _registry(tmp_path: Path, runtime: Any) -> tuple[ToolRegistry, BrowserSessionManager]:
    manager = BrowserSessionManager(_settings(tmp_path), runtime=runtime)
    registry = ToolRegistry()
    for tool in build_browser_tools(manager):
        registry.register(tool)
    return registry, manager


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
    tmp_path: Path,
    *,
    provider: FakeProvider,
    runtime: Any,
    bus: EventBus | None = None,
    **kwargs: Any,
) -> tuple[BrowserAgent, BrowserSessionManager]:
    registry, manager = _registry(tmp_path, runtime)
    agent = BrowserAgent(
        router=_router(provider),
        executor=_executor(registry, kwargs.pop("audit_sink", None)),
        tools=definitions_from_registry(registry),
        sessions=manager,
        bus=bus,
        **kwargs,
    )
    return agent, manager


# --------------------------------------------------------------------------- #
# Happy path: model -> browser.open -> report
# --------------------------------------------------------------------------- #
async def test_agent_drives_the_loop_through_the_browser_tools(
    tmp_path: Path, audit_sink: Any
) -> None:
    provider = _provider(
        _tool_response(_tool_call("browser.open", {"url": "https://example.com"})),
        _answer("Found it"),
    )
    agent, manager = _agent(
        tmp_path,
        provider=provider,
        runtime=tbs.FakeRuntime(),
        audit_sink=audit_sink,
        name="browser",
    )

    report = await agent.run({"prompt": "open example.com", "task_id": "task-1"})

    assert report["agent"] == "browser.agent"
    assert report["answer"] == "Found it"
    assert report["completed"] is True
    assert report["stop_reason"] == "completed"
    assert report["iterations"] == 2
    assert report["tool_calls"] == 1
    assert report["goal"] == "open example.com"
    assert report["task_id"] == "task-1"
    # The tool ran against the agent's own session (the agent id is the key).
    session = manager.get(str(agent.agent_id))
    assert session is not None
    assert session.url == "https://example.com"


async def test_agent_advertises_only_the_browser_tools(
    tmp_path: Path, audit_sink: Any
) -> None:
    provider = _provider(_answer())
    agent, _ = _agent(tmp_path, provider=provider, runtime=tbs.FakeRuntime(), audit_sink=audit_sink)

    await agent.run({"prompt": "hi"})

    assert provider.last_request is not None
    names = {definition.name for definition in provider.last_request.tools}
    assert names and all(name.startswith("browser.") for name in names)


async def test_agent_falls_back_to_its_own_id_for_the_task(
    tmp_path: Path, audit_sink: Any
) -> None:
    provider = _provider(_answer())
    agent, _ = _agent(
        tmp_path, provider=provider, runtime=tbs.FakeRuntime(), audit_sink=audit_sink
    )

    report = await agent.run({"prompt": "hi"})

    assert report["task_id"] == str(agent.agent_id)


# --------------------------------------------------------------------------- #
# Isolation: two agents, two sessions (§11)
# --------------------------------------------------------------------------- #
async def test_two_agents_get_isolated_sessions(tmp_path: Path, audit_sink: Any) -> None:
    runtime = tbs.FakeRuntime()
    registry, manager = _registry(tmp_path, runtime)
    executor = _executor(registry, audit_sink)
    definitions = definitions_from_registry(registry)

    def build(provider: FakeProvider) -> BrowserAgent:
        return BrowserAgent(
            router=_router(provider),
            executor=executor,
            tools=definitions,
            sessions=manager,
        )

    first = build(
        _provider(
            _tool_response(_tool_call("browser.open", {"url": "https://a.example"})),
            _answer("a"),
        )
    )
    second = build(
        _provider(
            _tool_response(_tool_call("browser.open", {"url": "https://b.example"})),
            _answer("b"),
        )
    )

    await first.run({"prompt": "go a"})
    await second.run({"prompt": "go b"})

    assert set(manager.agents()) == {str(first.agent_id), str(second.agent_id)}
    first_session = manager.get(str(first.agent_id))
    second_session = manager.get(str(second.agent_id))
    assert first_session is not None
    assert second_session is not None
    assert first_session.context_id != second_session.context_id
    assert first_session.url == "https://a.example"
    assert second_session.url == "https://b.example"


# --------------------------------------------------------------------------- #
# Progress events
# --------------------------------------------------------------------------- #
async def test_agent_publishes_browse_and_report_progress(
    tmp_path: Path, audit_sink: Any
) -> None:
    bus = RecordingBus()
    provider = _provider(
        _tool_response(_tool_call("browser.open", {"url": "https://example.com"})),
        _answer("done"),
    )
    agent, _ = _agent(
        tmp_path, provider=provider, runtime=tbs.FakeRuntime(), audit_sink=audit_sink, bus=bus
    )

    await agent.run({"prompt": "go"})

    phases = [payload["phase"] for _, payload in bus.published if "phase" in payload]
    assert phases == ["browse", "report"]
    assert all(kind == "TASK_PROGRESS" for kind, _ in bus.published)


# --------------------------------------------------------------------------- #
# Bounded loop
# --------------------------------------------------------------------------- #
async def test_agent_reports_a_loop_that_hit_its_tool_ceiling(
    tmp_path: Path, audit_sink: Any
) -> None:
    provider = _provider(
        _tool_response(
            _tool_call("browser.inspect", {}, "c1"),
            _tool_call("browser.inspect", {}, "c2"),
        ),
    )
    agent, _ = _agent(
        tmp_path,
        provider=provider,
        runtime=tbs.FakeRuntime(),
        audit_sink=audit_sink,
        max_tool_calls=1,
    )

    report = await agent.run({"prompt": "go"})

    assert report["completed"] is False
    assert report["stop_reason"] == "max_tool_calls"
    assert report["tool_calls"] == 1


# --------------------------------------------------------------------------- #
# Preconditions and construction guards
# --------------------------------------------------------------------------- #
async def test_agent_requires_a_prompt(tmp_path: Path, audit_sink: Any) -> None:
    agent, _ = _agent(
        tmp_path, provider=_provider(_answer()), runtime=tbs.FakeRuntime(), audit_sink=audit_sink
    )
    with pytest.raises(InvalidInputError):
        await agent.run({})


def test_agent_refuses_no_tools(tmp_path: Path) -> None:
    registry, _ = _registry(tmp_path, tbs.FakeRuntime())
    with pytest.raises(ValueError, match="at least one browser tool"):
        BrowserAgent(
            router=_router(_provider(_answer())),
            executor=_executor(registry, None),
            tools=[],
        )


def test_agent_declares_its_type() -> None:
    assert BrowserAgent.agent_type == "browser.agent"
    assert BrowserAgent.description


# --------------------------------------------------------------------------- #
# Failure translation
# --------------------------------------------------------------------------- #
async def test_agent_passes_a_typed_error_through_untouched(
    tmp_path: Path, audit_sink: Any
) -> None:
    # A missing prompt is an ``InvalidInputError`` (an ``UltronError``): it must
    # reach the caller as itself, never wrapped in ``AgentError``.
    agent, _ = _agent(
        tmp_path, provider=_provider(_answer()), runtime=tbs.FakeRuntime(), audit_sink=audit_sink
    )
    with pytest.raises(InvalidInputError):
        await agent.run({"prompt": "   "})


async def test_agent_wraps_an_unexpected_error(tmp_path: Path, audit_sink: Any) -> None:
    provider = _provider()
    provider.queue_error(ValueError("boom"))
    agent, _ = _agent(
        tmp_path, provider=provider, runtime=tbs.FakeRuntime(), audit_sink=audit_sink
    )

    with pytest.raises(AgentError) as excinfo:
        await agent.run({"prompt": "go"})
    assert isinstance(excinfo.value.cause, ValueError)


# --------------------------------------------------------------------------- #
# Session lifecycle
# --------------------------------------------------------------------------- #
async def test_agent_releases_its_session_when_asked(
    tmp_path: Path, audit_sink: Any
) -> None:
    provider = _provider(
        _tool_response(_tool_call("browser.open", {"url": "https://example.com"})),
        _answer("done"),
    )
    agent, manager = _agent(
        tmp_path,
        provider=provider,
        runtime=tbs.FakeRuntime(),
        audit_sink=audit_sink,
        close_session=True,
    )

    await agent.run({"prompt": "go"})

    assert manager.get(str(agent.agent_id)) is None


async def test_agent_releases_its_session_even_on_failure(
    tmp_path: Path, audit_sink: Any
) -> None:
    provider = _provider(
        _tool_response(_tool_call("browser.open", {"url": "https://example.com"})),
    )
    provider.queue_error(ValueError("boom"))
    agent, manager = _agent(
        tmp_path,
        provider=provider,
        runtime=tbs.FakeRuntime(),
        audit_sink=audit_sink,
        close_session=True,
    )

    with pytest.raises(AgentError):
        await agent.run({"prompt": "go"})
    assert manager.get(str(agent.agent_id)) is None


# --------------------------------------------------------------------------- #
# Identity binding (§15/§16): the loop is attributed to the agent
# --------------------------------------------------------------------------- #
async def test_agent_attributes_tool_calls_to_itself(
    tmp_path: Path, audit_sink: Any
) -> None:
    provider = _provider(
        _tool_response(_tool_call("browser.open", {"url": "https://example.com"})),
        _answer("done"),
    )
    agent, _ = _agent(
        tmp_path, provider=provider, runtime=tbs.FakeRuntime(), audit_sink=audit_sink
    )

    await agent.run({"prompt": "go", "task_id": "task-9"})

    # The executor writes an audit row per tool execution; the principal is the
    # agent (the loop's fallback), and the tool is the browser family.
    rows = [row for row in audit_sink.rows if row.get("resource_type") == "tool"]
    assert rows
    assert {str(row["actor_id"]) for row in rows} == {str(agent.agent_id)}
    assert {row["resource_id"] for row in rows} == {"browser.open"}
