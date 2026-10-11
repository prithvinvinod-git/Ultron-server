"""Phase 3 composition tests (T069).

The individual Phase 3 units already have their own suites: the contract and
vocabulary (T060), the four adapters (T061-T064, one bespoke ``respx`` suite
each), the scripted fake (T065), the router (T066), usage recording (T067) and
the tool-calling loop (T068). What none of them asserts is the thing §20/§21
actually promise -- that *one* interface has four interchangeable
implementations -- and that composed in the router the phase gate holds:

    Phase 3 gate: works with only Ollama configured; no crash when unavailable.

So there are two halves here. First, a *contract conformance* suite: the same
six invariants asserted against all four real adapters through ``respx``, so a
provider that drifts from the T060 contract fails the shared suite rather than
passing only its own bespoke one. Second, a *composition* suite: the router over
real adapters doing selection, streaming and fallback, the §33 degradation path
(Ollama unreachable, a configured cloud provider still answering), and the T068
loop driving a real adapter end to end.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx
import pytest
import respx

from app.agents.loop import ToolCallingLoop, definitions_from_registry
from app.config import ModelRouterSettings, OllamaSettings, ProviderSettings
from app.core.errors import LocalModelUnavailableError
from app.core.permissions import PermissionManager
from app.database.models import MessageRole, PermissionLevel, VerificationOutcome
from app.events.bus import EventBus
from app.models.anthropic import AnthropicProvider
from app.models.base import (
    CompletionRequest,
    FinishReason,
    ModelMessage,
    ModelProvider,
)
from app.models.gemini import GeminiProvider
from app.models.ollama import OllamaProvider
from app.models.openai import OpenAIProvider
from app.models.router import ModelRouter
from app.security.audit import AuditLogger
from app.tools.base import Reversibility, Tool
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.unit

OLLAMA_BASE = "http://ollama.test:11434"
OPENAI_BASE = "https://openai.test/v1"
GEMINI_BASE = "https://gemini.test"
ANTHROPIC_BASE = "https://anthropic.test"

OLLAMA_MODEL = "llama3"
OPENAI_MODEL = "gpt-4o-mini"
GEMINI_MODEL = "gemini-1.5-flash"
ANTHROPIC_MODEL = "claude-3-5-sonnet-20241022"


# --------------------------------------------------------------------------- #
# Wire builders -- the exact shapes the T061-T064 suites pin, reused here
# --------------------------------------------------------------------------- #
def _ollama_chat(*, content: str = "answer") -> dict[str, Any]:
    return {
        "model": OLLAMA_MODEL,
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 3,
        "eval_count": 2,
    }


def _openai_chat() -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "model": OPENAI_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "answer"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }


def _gemini_chat() -> dict[str, Any]:
    return {
        "modelVersion": "gemini-1.5-flash-001",
        "candidates": [
            {"content": {"role": "model", "parts": [{"text": "answer"}]}, "finishReason": "STOP"}
        ],
        "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
    }


def _anthropic_chat() -> dict[str, Any]:
    return {
        "id": "msg_1",
        "model": ANTHROPIC_MODEL,
        "content": [{"type": "text", "text": "answer"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 3, "output_tokens": 2},
    }


def _ollama_stream() -> str:
    return "\n".join(
        [
            json.dumps({"model": OLLAMA_MODEL, "message": {"content": "Hel"}, "done": False}),
            json.dumps({"model": OLLAMA_MODEL, "message": {"content": "lo"}, "done": False}),
            json.dumps(
                {
                    "model": OLLAMA_MODEL,
                    "message": {"content": ""},
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 3,
                    "eval_count": 2,
                }
            ),
        ]
    )


def _data_sse(*events: dict[str, Any], done: bool = False) -> str:
    """``data:``-framed SSE, optionally terminated with the OpenAI ``[DONE]``."""
    lines = [f"data: {json.dumps(event)}" for event in events]
    if done:
        lines.append("data: [DONE]")
    return "\n\n".join(lines) + "\n\n"


def _openai_stream() -> str:
    return _data_sse(
        {"model": OPENAI_MODEL, "choices": [{"index": 0, "delta": {"content": "Hel"}}]},
        {"model": OPENAI_MODEL, "choices": [{"index": 0, "delta": {"content": "lo"}}]},
        {
            "model": OPENAI_MODEL,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        },
        done=True,
    )


def _gemini_stream() -> str:
    return _data_sse(
        {"candidates": [{"content": {"parts": [{"text": "Hel"}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "lo"}]}}]},
        {
            "candidates": [{"content": {"parts": []}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
        },
    )


def _typed_sse(*events: dict[str, Any]) -> str:
    """Anthropic-style ``event:``/``data:`` SSE frames."""
    lines: list[str] = []
    for event in events:
        lines.append(f"event: {event.get('type')}")
        lines.append(f"data: {json.dumps(event)}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _anthropic_stream() -> str:
    return _typed_sse(
        {"type": "message_start", "message": {"usage": {"input_tokens": 3, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hel"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "lo"},
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 2},
        },
        {"type": "message_stop"},
    )


@dataclass(frozen=True)
class _Contract:
    """One adapter's coordinates: how to build it and where each wire lives."""

    name: str
    local: bool
    model: str
    build: Callable[[httpx.AsyncClient | None], ModelProvider]
    chat_url: str
    chat_payload: dict[str, Any]
    stream_url: str
    stream_body: str
    health_url: str
    health_payload: dict[str, Any]


CASES: list[_Contract] = [
    _Contract(
        name="ollama",
        local=True,
        model=OLLAMA_MODEL,
        build=lambda client: OllamaProvider(OllamaSettings(url=OLLAMA_BASE), client=client),
        chat_url=f"{OLLAMA_BASE}/api/chat",
        chat_payload=_ollama_chat(),
        stream_url=f"{OLLAMA_BASE}/api/chat",
        stream_body=_ollama_stream(),
        health_url=f"{OLLAMA_BASE}/api/tags",
        health_payload={"models": []},
    ),
    _Contract(
        name="openai",
        local=False,
        model=OPENAI_MODEL,
        build=lambda client: OpenAIProvider(
            ProviderSettings(openai_api_key="inert", openai_base_url=OPENAI_BASE), client=client
        ),
        chat_url=f"{OPENAI_BASE}/chat/completions",
        chat_payload=_openai_chat(),
        stream_url=f"{OPENAI_BASE}/chat/completions",
        stream_body=_openai_stream(),
        health_url=f"{OPENAI_BASE}/models",
        health_payload={"data": []},
    ),
    _Contract(
        name="gemini",
        local=False,
        model=GEMINI_MODEL,
        build=lambda client: GeminiProvider(
            ProviderSettings(gemini_api_key="inert", gemini_base_url=GEMINI_BASE), client=client
        ),
        chat_url=f"{GEMINI_BASE}/v1beta/models/{GEMINI_MODEL}:generateContent",
        chat_payload=_gemini_chat(),
        stream_url=f"{GEMINI_BASE}/v1beta/models/{GEMINI_MODEL}:streamGenerateContent?alt=sse",
        stream_body=_gemini_stream(),
        health_url=f"{GEMINI_BASE}/v1beta/models",
        health_payload={"models": []},
    ),
    _Contract(
        name="anthropic",
        local=False,
        model=ANTHROPIC_MODEL,
        build=lambda client: AnthropicProvider(
            ProviderSettings(anthropic_api_key="inert", anthropic_base_url=ANTHROPIC_BASE),
            client=client,
        ),
        chat_url=f"{ANTHROPIC_BASE}/v1/messages",
        chat_payload=_anthropic_chat(),
        stream_url=f"{ANTHROPIC_BASE}/v1/messages",
        stream_body=_anthropic_stream(),
        health_url=f"{ANTHROPIC_BASE}/v1/models",
        health_payload={"data": []},
    ),
]


def _request(case: _Contract) -> CompletionRequest:
    return CompletionRequest(
        messages=(ModelMessage(role=MessageRole.USER, content="hello"),),
        model=case.model,
    )


def _case_id(case: _Contract) -> str:
    return case.name


# --------------------------------------------------------------------------- #
# Contract conformance -- one set of invariants over all four real adapters
# --------------------------------------------------------------------------- #
def test_adapter_satisfies_the_shared_declarations() -> None:
    for case in CASES:
        provider = case.build(None)
        assert provider.name == case.name
        assert provider.local is case.local
        assert provider.supports_tools is True
        assert provider.supports_streaming is True
        assert provider.is_configured is True


@respx.mock
@pytest.mark.parametrize("case", CASES, ids=_case_id)
async def test_chat_returns_a_normalised_response(case: _Contract) -> None:
    respx.post(case.chat_url).mock(return_value=httpx.Response(200, json=case.chat_payload))
    provider = case.build(None)

    response = await provider.chat(_request(case))

    assert response.content == "answer"
    assert response.provider == case.name
    assert response.finish_reason is FinishReason.STOP
    assert response.has_tool_calls is False
    assert response.usage is not None
    assert response.usage.prompt_tokens == 3
    assert response.usage.completion_tokens == 2


@respx.mock
@pytest.mark.parametrize("case", CASES, ids=_case_id)
async def test_stream_yields_deltas_and_a_terminal_chunk(case: _Contract) -> None:
    respx.post(case.stream_url).mock(return_value=httpx.Response(200, text=case.stream_body))
    provider = case.build(None)

    chunks = [chunk async for chunk in provider.stream(_request(case))]

    assert [chunk.delta for chunk in chunks if chunk.delta] == ["Hel", "lo"]
    final = chunks[-1]
    assert final.is_final
    assert final.finish_reason is FinishReason.STOP
    assert final.usage is not None and final.usage.completion_tokens == 2


@respx.mock
@pytest.mark.parametrize("case", CASES, ids=_case_id)
async def test_health_reports_available(case: _Contract) -> None:
    respx.get(case.health_url).mock(return_value=httpx.Response(200, json=case.health_payload))
    provider = case.build(None)

    health = await provider.health()

    assert health.status.value == "available"


@respx.mock
@pytest.mark.parametrize("case", CASES, ids=_case_id)
async def test_health_never_raises_when_unreachable(case: _Contract) -> None:
    respx.get(case.health_url).mock(side_effect=httpx.ConnectError("refused"))
    provider = case.build(None)

    health = await provider.health()

    assert health.status.value == "unavailable"
    assert health.detail


@pytest.mark.parametrize("case", CASES, ids=_case_id)
async def test_a_borrowed_client_is_left_open(case: _Contract) -> None:
    injected = httpx.AsyncClient()
    provider = case.build(injected)

    await provider.aclose()

    assert injected.is_closed is False
    await injected.aclose()


# --------------------------------------------------------------------------- #
# Composition -- the router over real adapters (§20/§49/§66.6)
# --------------------------------------------------------------------------- #
def _ollama_provider() -> OllamaProvider:
    return OllamaProvider(OllamaSettings(url=OLLAMA_BASE))


def _openai_provider() -> OpenAIProvider:
    return OpenAIProvider(ProviderSettings(openai_api_key="inert", openai_base_url=OPENAI_BASE))


def _tags(*models: str) -> httpx.Response:
    return httpx.Response(200, json={"models": [{"name": name} for name in models]})


@respx.mock
async def test_router_streams_through_the_local_adapter() -> None:
    respx.get(f"{OLLAMA_BASE}/api/tags").mock(return_value=_tags(OLLAMA_MODEL))
    respx.post(f"{OLLAMA_BASE}/api/chat").mock(
        return_value=httpx.Response(200, text=_ollama_stream())
    )
    router = ModelRouter(
        [_ollama_provider()],
        settings=ModelRouterSettings(default=f"ollama:{OLLAMA_MODEL}", fallback_chain=[]),
    )

    chunks = [chunk async for chunk in router.stream(_request(CASES[0]))]

    assert [chunk.delta for chunk in chunks if chunk.delta] == ["Hel", "lo"]
    assert chunks[-1].is_final


@respx.mock
async def test_router_falls_back_from_unreachable_ollama_to_the_cloud() -> None:
    """§33: an unreachable local model must not fail the request -- the cloud answers."""
    respx.get(f"{OLLAMA_BASE}/api/tags").mock(return_value=_tags(OLLAMA_MODEL))
    respx.post(f"{OLLAMA_BASE}/api/chat").mock(side_effect=httpx.ConnectError("refused"))
    respx.get(f"{OPENAI_BASE}/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": OPENAI_MODEL}]})
    )
    respx.post(f"{OPENAI_BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json=_openai_chat())
    )
    router = ModelRouter(
        [_ollama_provider(), _openai_provider()],
        settings=ModelRouterSettings(
            default=f"ollama:{OLLAMA_MODEL}",
            fallback_chain=[f"openai:{OPENAI_MODEL}"],
            max_retries=0,
        ),
    )

    response = await router.complete(_request(CASES[0]))

    assert response.content == "answer"
    assert response.provider == "openai"


@respx.mock
async def test_router_with_only_ollama_and_unreachable_degrades_cleanly() -> None:
    """The phase gate: only Ollama configured and it is down -- typed error, no crash."""
    respx.get(f"{OLLAMA_BASE}/api/tags").mock(side_effect=httpx.ConnectError("refused"))
    respx.post(f"{OLLAMA_BASE}/api/chat").mock(side_effect=httpx.ConnectError("refused"))
    router = ModelRouter(
        [_ollama_provider()],
        settings=ModelRouterSettings(default=f"ollama:{OLLAMA_MODEL}", fallback_chain=[]),
    )

    with pytest.raises(LocalModelUnavailableError) as caught:
        await router.complete(_request(CASES[0]))

    assert caught.value.retryable is True


# --------------------------------------------------------------------------- #
# Composition -- the T068 loop driving a real adapter (§16 end to end)
# --------------------------------------------------------------------------- #
class _EchoTool(Tool):
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


def _ollama_tool_call(*, name: str, text: str) -> dict[str, Any]:
    return {
        "model": OLLAMA_MODEL,
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": {"text": text}}}],
        },
        "done": True,
        "done_reason": "stop",
    }


@respx.mock
async def test_loop_drives_a_real_adapter_through_the_pipeline(audit_sink: Any) -> None:
    """The loop + router + Ollama adapter over ``respx``: the model asks, the tool
    runs through every §16 stage, and the model's second turn is the answer."""
    respx.get(f"{OLLAMA_BASE}/api/tags").mock(return_value=_tags(OLLAMA_MODEL))
    respx.post(f"{OLLAMA_BASE}/api/chat").mock(
        side_effect=[
            httpx.Response(200, json=_ollama_tool_call(name="mock.echo", text="hi")),
            httpx.Response(200, json=_ollama_chat(content="done")),
        ]
    )
    tool = _EchoTool()
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(
        registry=registry,
        permissions=PermissionManager(
            audit=AuditLogger(audit_sink), default_level=PermissionLevel.DESTRUCTIVE
        ),
        events=EventBus(),
    )
    router = ModelRouter(
        [_ollama_provider()],
        settings=ModelRouterSettings(default=f"ollama:{OLLAMA_MODEL}", fallback_chain=[]),
    )
    loop = ToolCallingLoop(
        router=router,
        executor=executor,
        tools=definitions_from_registry(registry),
    )

    result = await loop.run(
        [ModelMessage(role=MessageRole.USER, content="say hi back")], principal="alice"
    )

    assert result.completed
    assert result.tool_calls == 1
    assert result.content == "done"
    assert tool.calls == [{"text": "hi"}]
    assistant_turn = result.messages[-2]
    assert assistant_turn.role is MessageRole.ASSISTANT
    assert assistant_turn.tool_calls[0].name == "mock.echo"
    payload = json.loads(result.messages[-1].content)
    assert payload["ok"] is True
    assert payload["output"] == {"echoed": "hi"}
