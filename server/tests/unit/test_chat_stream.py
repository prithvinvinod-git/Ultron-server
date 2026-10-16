"""Streaming chat (T?): ``ToolCallingLoop.stream`` and ``POST /chat/stream``.

The non-streaming loop is pinned by ``test_agent_loop.py``; what these tests add
is the *incremental* contract — that the answer arrives token-by-token, that a
tool turn still runs through the pipeline with a start/finish pair either side,
and that the terminal chunk carries the same :class:`ToolLoopResult` the
blocking path returns. The route tests then cover the wire shape: named SSE
frames (``token``, ``tool_started``, ``tool_completed``, ``done``) as JSON.

Two seams are stubbed, at the same places the node route tests stub them: the
container (so no PostgreSQL/Redis) and ``require_principal`` (so no credential
dance). Everything between — the loop, the executor, the router — is real.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from app.agents.loop import (
    LoopChunk,
    LoopChunkKind,
    StopReason,
    ToolCallingLoop,
    definitions_from_registry,
)
from app.api.dependencies import get_db_session, require_principal
from app.config import ModelRouterSettings, get_settings, reload_settings
from app.core.permissions import PermissionManager
from app.database.models import MessageRole, PermissionLevel, VerificationOutcome
from app.events.bus import EventBus
from app.main import create_app
from app.models.base import (
    FinishReason,
    ModelMessage,
    ModelResponse,
    ModelStreamChunk,
    ToolCall,
)
from app.models.fake import FakeProvider
from app.models.router import ModelRouter
from app.security.audit import AuditLogger
from app.security.authentication import Principal
from app.tools.base import Reversibility, Tool
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Doubles and helpers
# --------------------------------------------------------------------------- #


class EchoTool(Tool):
    """Record each call and echo its arguments back as output."""

    name = "mock.echo"
    description = "Echo the text back."
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
        return {"echoed": arguments["text"]}

    async def verify(self, result: Any, arguments: Mapping[str, Any]) -> VerificationOutcome:
        return VerificationOutcome.SUCCESS


def _provider(*streams: list[ModelStreamChunk]) -> FakeProvider:
    """A fake provider whose successive ``stream`` calls yield ``streams`` in turn."""
    return FakeProvider(name="ollama", local=True, streams=streams)


def _router(provider: FakeProvider) -> ModelRouter:
    return ModelRouter(
        [provider],
        settings=ModelRouterSettings(default="ollama:llama3", fallback_chain=[]),
    )


def _harness(
    audit_sink: Any,
    *tools: Tool,
    default_level: PermissionLevel = PermissionLevel.DESTRUCTIVE,
) -> tuple[ToolExecutor, ToolRegistry]:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    executor = ToolExecutor(
        registry=registry,
        permissions=PermissionManager(
            audit=AuditLogger(audit_sink),
            default_level=default_level,
        ),
        events=EventBus(),
    )
    return executor, registry


def _user(text: str = "hello") -> list[ModelMessage]:
    return [ModelMessage(role=MessageRole.USER, content=text)]


def _tool_turn(*calls: ToolCall) -> list[ModelStreamChunk]:
    return [
        *(ModelStreamChunk(tool_call=call) for call in calls),
        ModelStreamChunk(finish_reason=FinishReason.TOOL_CALLS),
    ]


def _text_turn(*deltas: str) -> list[ModelStreamChunk]:
    return [
        *(ModelStreamChunk(delta=delta) for delta in deltas),
        ModelStreamChunk(finish_reason=FinishReason.STOP),
    ]


async def _collect(loop: ToolCallingLoop) -> list[LoopChunk]:
    return [chunk async for chunk in loop.stream(_user(), principal="alice")]


def _kinds(chunks: list[LoopChunk]) -> list[LoopChunkKind]:
    return [chunk.kind for chunk in chunks]


# --------------------------------------------------------------------------- #
# ToolCallingLoop.stream
# --------------------------------------------------------------------------- #


async def test_stream_yields_tokens_then_a_completed_chunk(audit_sink: Any) -> None:
    provider = _provider(_text_turn("hel", "lo", " there"))
    executor, _ = _harness(audit_sink, EchoTool())
    loop = ToolCallingLoop(router=_router(provider), executor=executor)

    chunks = await _collect(loop)

    assert _kinds(chunks) == [
        LoopChunkKind.TOKEN,
        LoopChunkKind.TOKEN,
        LoopChunkKind.TOKEN,
        LoopChunkKind.COMPLETED,
    ]
    assert [chunk.text for chunk in chunks if chunk.kind is LoopChunkKind.TOKEN] == [
        "hel",
        "lo",
        " there",
    ]
    result = chunks[-1].result
    assert result is not None
    assert result.completed
    assert result.stop_reason is StopReason.COMPLETED
    assert result.content == "hello there"
    assert result.iterations == 1
    assert result.tool_calls == 0


async def test_stream_tool_turn_is_wrapped_in_started_and_completed(audit_sink: Any) -> None:
    tool = EchoTool()
    provider = _provider(
        _tool_turn(ToolCall(id="c1", name="mock.echo", arguments={"text": "hi"})),
        _text_turn("you said hi"),
    )
    executor, registry = _harness(audit_sink, tool)
    loop = ToolCallingLoop(
        router=_router(provider),
        executor=executor,
        tools=definitions_from_registry(registry),
    )

    chunks = await _collect(loop)

    assert _kinds(chunks) == [
        LoopChunkKind.TOOL_STARTED,
        LoopChunkKind.TOOL_COMPLETED,
        LoopChunkKind.TOKEN,
        LoopChunkKind.COMPLETED,
    ]
    started = chunks[0]
    assert started.tool == "mock.echo"
    assert dict(started.arguments) == {"text": "hi"}
    finished = chunks[1]
    assert finished.tool == "mock.echo"
    assert finished.ok is True
    assert finished.output == {"echoed": "hi"}
    assert tool.calls == [{"text": "hi"}]
    result = chunks[-1].result
    assert result is not None
    assert result.iterations == 2
    assert result.tool_calls == 1
    assert result.content == "you said hi"


async def test_stream_terminal_result_matches_the_blocking_path(audit_sink: Any) -> None:
    """Both entry points must agree on what "finished" means for the same script."""
    call = ToolCall(id="c1", name="mock.echo", arguments={"text": "x"})
    answers = [
        ModelResponse(
            model="llama3",
            content="",
            tool_calls=(call,),
            finish_reason=FinishReason.TOOL_CALLS,
            provider="ollama",
        ),
        ModelResponse(
            model="llama3",
            content="done",
            finish_reason=FinishReason.STOP,
            provider="ollama",
        ),
    ]

    def scripted() -> FakeProvider:
        # One provider holds both queues: ``chat`` drives the blocking path from
        # ``responses``, ``stream`` drives the streaming path from ``streams``.
        return FakeProvider(
            name="ollama",
            local=True,
            responses=answers,
            streams=[_tool_turn(call), _text_turn("done")],
        )

    executor, registry = _harness(audit_sink, EchoTool())
    tools = definitions_from_registry(registry)
    blocking = ToolCallingLoop(router=_router(scripted()), executor=executor, tools=tools)
    streamed = ToolCallingLoop(router=_router(scripted()), executor=executor, tools=tools)

    blocking_result = await blocking.run(_user(), principal="alice")
    streamed_chunks = await _collect(streamed)
    streamed_result = streamed_chunks[-1].result

    assert streamed_result is not None
    assert streamed_result.content == blocking_result.content
    assert streamed_result.iterations == blocking_result.iterations
    assert streamed_result.tool_calls == blocking_result.tool_calls
    assert streamed_result.stop_reason is blocking_result.stop_reason


# --------------------------------------------------------------------------- #
# POST /chat/stream
# --------------------------------------------------------------------------- #


class _StubContainer:
    """The slice of the container the chat routes reach for."""

    def __init__(
        self,
        *,
        router: ModelRouter,
        tools: ToolRegistry,
        events: EventBus,
        audit: Any,
    ) -> None:
        self.settings = get_settings()
        self.model_router = router
        self.tools = tools
        self.events = events
        self._audit = audit

    def get_audit_log_repository(self, session: Any) -> Any:
        return self._audit


def _principal() -> Principal:
    return Principal(
        actor_type="user",
        user_id=uuid.uuid4(),
        username="tester",
        is_superuser=True,
    )


@asynccontextmanager
async def _no_lifespan(app: Any) -> AsyncIterator[None]:
    """Skip the real lifespan (PostgreSQL/Redis pings) but keep the ASGI portal."""
    yield


async def _fake_session() -> AsyncIterator[object]:
    yield object()


def _build_app(container: _StubContainer) -> Any:
    app = create_app()
    app.router.lifespan_context = _no_lifespan
    app.state.container = container
    app.dependency_overrides[require_principal] = _principal
    app.dependency_overrides[get_db_session] = _fake_session
    return app


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, audit_sink: Any) -> Iterator[tuple[TestClient, EchoTool]]:
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    reload_settings()
    tool = EchoTool()
    registry = ToolRegistry()
    registry.register(tool)
    provider = _provider(
        _tool_turn(ToolCall(id="c1", name="mock.echo", arguments={"text": "hi"})),
        _text_turn("you said ", "hi"),
    )
    container = _StubContainer(
        router=_router(provider),
        tools=registry,
        events=EventBus(),
        audit=audit_sink,
    )
    app = _build_app(container)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, tool
    app.dependency_overrides.clear()


def _parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse an SSE body into ``(event, data)`` pairs; blank lines are separators."""
    frames: list[tuple[str, dict[str, Any]]] = []
    event = ""
    for line in body.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            frames.append((event, json.loads(line[len("data: ") :])))
    return frames


class TestChatStreamRoute:
    def test_streams_token_tool_and_done_frames(self, api: tuple[TestClient, EchoTool]) -> None:
        client, tool = api
        response = client.post("/chat/stream", json={"message": "say hi back"})

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        frames = _parse_sse(response.text)
        names = [name for name, _ in frames]
        assert names == ["tool_started", "tool_completed", "token", "token", "done"]

        assert frames[0][1] == {"tool": "mock.echo", "arguments": {"text": "hi"}}
        assert frames[1][1]["ok"] is True
        assert frames[1][1]["output"] == {"echoed": "hi"}
        assert [data["text"] for name, data in frames if name == "token"] == ["you said ", "hi"]

        done = frames[-1][1]
        assert done["answer"] == "you said hi"
        assert done["tool_calls"] == 1
        assert done["iterations"] == 2
        assert done["finish_reason"] == "stop"
        assert tool.calls == [{"text": "hi"}]

    def test_rejects_an_unknown_field(self, api: tuple[TestClient, EchoTool]) -> None:
        client, _ = api
        response = client.post("/chat/stream", json={"message": "hi", "bogus": 1})
        assert response.status_code == 422
