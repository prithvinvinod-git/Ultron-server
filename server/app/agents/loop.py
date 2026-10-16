"""The model-driven tool-calling loop (T068): §16's cycle, run to completion.

§16 draws the only legal path from an intention to an action:

```text
LLM -> Tool Request -> Schema → Permission → Policy -> Execution
    → Result → Verification -> Event - LLM
```

The arrow that closes the loop back into the model is the one this module owns.
A single model call is a *step*; an agent that can use tools is a *conversation*
with the model that keeps going while the model keeps asking for tools. This
class is that cycle and nothing else: it selects a model through the router
(T066/T067), advertises the tools (``ToolDefinition``, T060), and when the model
answers with ``tool_calls`` rather than prose it runs each one **through the
pipeline** — never directly — appends the results to the transcript and asks
again. It stops when the model stops asking, or when a declared bound is
reached.

The rules the loop keeps:

- **Never around the pipeline.** Every call goes through `ToolExecutor.run`
  (§16 stage 1 onward: schema validation, permission, policy, bounded execution,
  verification, events). The loop never imports a tool and never calls
  ``Tool.execute`` — that is the bypass §16 exists to prevent. The loop's job is
  *orchestration*: turn a ``ToolCall`` into an executor call and a
  ``ModelMessage`` back into the transcript.

- **A tool failure is information, not an abort** (§33's spirit: a failure is a
  recoverable outcome). A typed error from the pipeline — schema invalid,
  permission denied, timeout, not found — is serialised into the ``tool`` turn
  so the model sees *why* and can adjust its next step, exactly as §16's loop
  feeds the result back. The one thing the loop does not swallow is a
  non-``UltronError``: that is a bug in the server, not a fact about the world,
  and it propagates.

- **Bounded by construction** (§59.6: nothing runs unbounded). ``max_iterations``
  caps model turns and ``max_tool_calls`` caps executions; hitting either stops
  the loop and says so in :class:`StopReason`, so a model that would loop
  forever ends with a recorded reason rather than a hung request. A stopped
  loop still returns the transcript and the last response — the caller decides
  what a half-finished run means.

- **The transcript is the record.** :class:`ToolLoopResult` carries the full
  ``messages`` list (system, user, assistant turns with their ``tool_calls``,
  and ``tool`` turns with results) beside the final response, so a caller can
  persist the conversation without replaying the loop.

The loop is deliberately *not* an :class:`~app.agents.base.Agent`: an agent owns
an identity and a lifecycle (§40), while this is a reusable behaviour an agent's
``run`` can drive. Keeping it separate is what lets T069 test the §16 cycle with
a ``FakeProvider`` and no agent at all.
"""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.core.errors import UltronError
from app.database.models import MessageRole
from app.models.base import (
    CompletionRequest,
    FinishReason,
    ModelMessage,
    ModelResponse,
    ToolCall,
    ToolDefinition,
)
from app.models.router import ModelRouter
from app.security.audit import ACTOR_USER
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry

__all__ = [
    "LoopChunk",
    "LoopChunkKind",
    "StopReason",
    "ToolCallingLoop",
    "ToolLoopResult",
    "definitions_from_registry",
]


class StopReason(StrEnum):
    """Why the loop stopped — always recorded, never guessed.

    ``COMPLETED`` is the only *normal* ending: the model answered with prose
    instead of another tool request. The other two are the configured ceilings
    (§59.6: bounded), surfaced so a caller can distinguish "done" from "gave
    up" rather than treating a truncated transcript as an answer.
    """

    COMPLETED = "completed"
    MAX_ITERATIONS = "max_iterations"
    MAX_TOOL_CALLS = "max_tool_calls"


@dataclass(frozen=True, slots=True)
class ToolLoopResult:
    """The end of one loop: the last response, the transcript, and why it stopped."""

    response: ModelResponse
    messages: tuple[ModelMessage, ...]
    iterations: int
    tool_calls: int
    stop_reason: StopReason

    @property
    def content(self) -> str:
        """The final answer's text — the response's content, named for callers."""
        return self.response.content

    @property
    def completed(self) -> bool:
        """True only when the model stopped on its own (``COMPLETED``)."""
        return self.stop_reason is StopReason.COMPLETED


class LoopChunkKind(StrEnum):
    """The kind of one incremental event a streaming loop run emits.

    The four kinds mirror the four things a caller may want to render live:
    the model's text arriving (``TOKEN``), a tool starting and finishing
    (``TOOL_STARTED``/``TOOL_COMPLETED``), and the run ending (``COMPLETED``,
    carrying the same :class:`ToolLoopResult` the non-streaming :meth:`run`
    returns, so a caller that only wants the final answer can ignore the rest).
    """

    TOKEN = "token"  # noqa: S105 - an event name, not a credential
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class LoopChunk:
    """One incremental piece of a streaming run (:meth:`ToolCallingLoop.stream`).

    Only the fields relevant to :attr:`kind` are populated: ``text`` for a
    ``TOKEN``, ``tool``/``arguments``/``ok``/``output`` around a tool, and
    ``result`` on the terminal ``COMPLETED`` chunk. Keeping one shape (rather
    than four types) keeps the SSE route's serialisation trivial.
    """

    kind: LoopChunkKind
    text: str = ""
    tool: str = ""
    arguments: Mapping[str, Any] = field(default_factory=dict)
    ok: bool | None = None
    output: Any = None
    result: ToolLoopResult | None = None


def definitions_from_registry(registry: ToolRegistry) -> list[ToolDefinition]:
    """The registered tools as model-facing ``ToolDefinition``s (§16, §59.6).

    The schema is deep-copied out of the registry: it travels into a prompt and
    a provider that scribbled on it must not corrupt the registered tool — the
    same reason ``ToolRegistry.describe`` copies. Order follows the registry's
    sorted ``list()``, so a prompt built from this is stable across calls.
    """
    return [
        ToolDefinition(
            name=tool.name,
            description=tool.description,
            parameters=copy.deepcopy(tool.input_schema),
        )
        for tool in registry.list()
    ]


@dataclass(frozen=True, slots=True)
class _Caller:
    """The identity every tool execution in one loop is attributed to (§15/§59)."""

    principal: str
    client: str = "agent"
    principal_type: str = ACTOR_USER
    target: str | None = None
    task_id: str | None = None
    agent_id: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


class ToolCallingLoop:
    """Drive §16's model → tool → model cycle until the model stops calling."""

    def __init__(
        self,
        *,
        router: ModelRouter,
        executor: ToolExecutor,
        tools: Sequence[ToolDefinition] = (),
        capability: str = "default",
        max_iterations: int = 10,
        max_tool_calls: int = 32,
        system_prompt: str | None = None,
    ) -> None:
        """Bind the loop to its router, executor and advertised tools.

        ``capability`` is the §49 selection input the router resolves to a
        configured model. ``max_iterations``/``max_tool_calls`` are the §59.6
        ceilings: a model turn may request several calls, so the two limits are
        independent — five iterations of ten calls each is still bounded by
        ``max_tool_calls``. ``system_prompt`` is prepended once, when the
        incoming transcript has no system turn of its own.
        """
        self._router = router
        self._executor = executor
        self._definitions = tuple(tools)
        self._capability = capability
        self._max_iterations = _positive_int("max_iterations", max_iterations)
        self._max_tool_calls = _positive_int("max_tool_calls", max_tool_calls)
        self._system_prompt = system_prompt

    async def run(
        self,
        messages: Sequence[ModelMessage],
        *,
        principal: str,
        client: str = "agent",
        principal_type: str = ACTOR_USER,
        target: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
    ) -> ToolLoopResult:
        """Run the cycle on ``messages`` and return the transcript and outcome."""
        caller = _Caller(
            principal=principal,
            client=client,
            principal_type=principal_type,
            target=target,
            task_id=task_id,
            agent_id=agent_id,
        )
        transcript: list[ModelMessage] = list(messages)
        if self._system_prompt is not None and not any(
            message.role is MessageRole.SYSTEM for message in transcript
        ):
            transcript.insert(0, ModelMessage(role=MessageRole.SYSTEM, content=self._system_prompt))

        executed = 0
        response: ModelResponse | None = None
        iteration = 0
        for iteration in range(1, self._max_iterations + 1):
            response = await self._complete(transcript)
            if not response.has_tool_calls:
                return ToolLoopResult(
                    response=response,
                    messages=tuple(transcript),
                    iterations=iteration,
                    tool_calls=executed,
                    stop_reason=StopReason.COMPLETED,
                )

            transcript.append(
                ModelMessage(
                    role=MessageRole.ASSISTANT,
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )
            for call in response.tool_calls:
                executed += 1
                transcript.append(
                    ModelMessage(
                        role=MessageRole.TOOL,
                        content=await self._dispatch(call, caller),
                        name=call.name,
                        tool_call_id=call.id,
                    )
                )
                if executed >= self._max_tool_calls:
                    return ToolLoopResult(
                        response=response,
                        messages=tuple(transcript),
                        iterations=iteration,
                        tool_calls=executed,
                        stop_reason=StopReason.MAX_TOOL_CALLS,
                    )

        # Only reachable when every iteration requested tools: the ceilings hold.
        if response is None:  # pragma: no cover - max_iterations >= 1 guarantees a turn
            raise RuntimeError("tool-calling loop ran no iterations")
        return ToolLoopResult(
            response=response,
            messages=tuple(transcript),
            iterations=iteration,
            tool_calls=executed,
            stop_reason=StopReason.MAX_ITERATIONS,
        )

    async def stream(
        self,
        messages: Sequence[ModelMessage],
        *,
        principal: str,
        client: str = "agent",
        principal_type: str = ACTOR_USER,
        target: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
    ) -> AsyncIterator[LoopChunk]:
        """Run the same §16 cycle as :meth:`run`, yielding increments as they happen.

        The model turns are streamed through the router (T066), so a caller
        sees text arrive token-by-token (§21's streaming) instead of waiting for
        the whole answer. Tool turns are identical to :meth:`run`: dispatched
        through the pipeline, never around it, with a ``TOOL_STARTED`` chunk
        before and a ``TOOL_COMPLETED`` chunk after each (the ``ok`` flag is the
        same one the JSON ``tool`` turn carries). The terminal ``COMPLETED``
        chunk carries the *same* :class:`ToolLoopResult` the non-streaming path
        returns, so both entry points agree on what "finished" means.

        A provider that cannot stream falls back to a single ``TOKEN``; that is
        the router's concern, not this loop's — it consumes chunks uniformly.
        """
        caller = _Caller(
            principal=principal,
            client=client,
            principal_type=principal_type,
            target=target,
            task_id=task_id,
            agent_id=agent_id,
        )
        transcript: list[ModelMessage] = list(messages)
        if self._system_prompt is not None and not any(
            message.role is MessageRole.SYSTEM for message in transcript
        ):
            transcript.insert(0, ModelMessage(role=MessageRole.SYSTEM, content=self._system_prompt))

        executed = 0
        response: ModelResponse | None = None
        iteration = 0
        for iteration in range(1, self._max_iterations + 1):
            response = None
            async for chunk in self._stream_turn(transcript):
                if isinstance(chunk, LoopChunk):
                    yield chunk
                else:
                    response = chunk
            if response is None:  # pragma: no cover - a turn always ends with a response
                raise RuntimeError("streamed model turn produced no response")
            if not response.has_tool_calls:
                yield LoopChunk(
                    kind=LoopChunkKind.COMPLETED,
                    result=ToolLoopResult(
                        response=response,
                        messages=tuple(transcript),
                        iterations=iteration,
                        tool_calls=executed,
                        stop_reason=StopReason.COMPLETED,
                    ),
                )
                return

            transcript.append(
                ModelMessage(
                    role=MessageRole.ASSISTANT,
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )
            for call in response.tool_calls:
                executed += 1
                yield LoopChunk(
                    kind=LoopChunkKind.TOOL_STARTED,
                    tool=call.name,
                    arguments=call.arguments,
                )
                payload = await self._dispatch_payload(call, caller)
                output = _dump(payload)
                transcript.append(
                    ModelMessage(
                        role=MessageRole.TOOL,
                        content=output,
                        name=call.name,
                        tool_call_id=call.id,
                    )
                )
                yield LoopChunk(
                    kind=LoopChunkKind.TOOL_COMPLETED,
                    tool=call.name,
                    ok=bool(payload.get("ok")),
                    output=payload.get("output"),
                )
                if executed >= self._max_tool_calls:
                    yield LoopChunk(
                        kind=LoopChunkKind.COMPLETED,
                        result=ToolLoopResult(
                            response=response,
                            messages=tuple(transcript),
                            iterations=iteration,
                            tool_calls=executed,
                            stop_reason=StopReason.MAX_TOOL_CALLS,
                        ),
                    )
                    return

        if response is None:  # pragma: no cover - max_iterations >= 1 guarantees a turn
            raise RuntimeError("tool-calling loop ran no iterations")
        yield LoopChunk(
            kind=LoopChunkKind.COMPLETED,
            result=ToolLoopResult(
                response=response,
                messages=tuple(transcript),
                iterations=iteration,
                tool_calls=executed,
                stop_reason=StopReason.MAX_ITERATIONS,
            ),
        )

    async def _stream_turn(
        self, transcript: Sequence[ModelMessage]
    ) -> AsyncIterator[LoopChunk | ModelResponse]:
        """Stream one model turn: yield ``TOKEN`` chunks, then the assembled response.

        The last item yielded is always the :class:`ModelResponse` (there is no
        ``isinstance``-vs-``LoopChunk`` ambiguity because the response is not a
        ``LoopChunk``); everything before it are the text deltas. Tool calls are
        assembled from the stream and attached to that response, exactly as the
        non-streaming :meth:`_complete` produces them.
        """
        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        finish_reason: FinishReason = FinishReason.STOP
        usage = None
        model = ""
        async for chunk in self._router.stream(
            self._request(transcript),
            capability=self._capability,
        ):
            if chunk.model:
                model = chunk.model
            if chunk.delta:
                content_parts.append(chunk.delta)
                yield LoopChunk(kind=LoopChunkKind.TOKEN, text=chunk.delta)
            if chunk.tool_call is not None:
                tool_calls.append(chunk.tool_call)
            if chunk.finish_reason is not None:
                finish_reason = chunk.finish_reason
            if chunk.usage is not None:
                usage = chunk.usage
        yield ModelResponse(
            model=model,
            content="".join(content_parts),
            tool_calls=tuple(tool_calls),
            finish_reason=finish_reason,
            usage=usage,
        )

    def _request(self, transcript: Sequence[ModelMessage]) -> CompletionRequest:
        """Build the completion request both the non-streaming and streaming turns use."""
        return CompletionRequest(
            messages=tuple(transcript),
            model="",
            tools=self._definitions,
        )

    async def _complete(self, transcript: Sequence[ModelMessage]) -> ModelResponse:
        """One model turn, with the tools advertised and the model routed."""
        return await self._router.complete(
            self._request(transcript),
            capability=self._capability,
        )

    async def _dispatch(self, call: ToolCall, caller: _Caller) -> str:
        """Run one ``ToolCall`` through the §16 pipeline, as a JSON ``tool`` turn."""
        return _dump(await self._dispatch_payload(call, caller))

    async def _dispatch_payload(self, call: ToolCall, caller: _Caller) -> dict[str, Any]:
        """Run one ``ToolCall`` through the §16 pipeline, as a JSON-ready mapping.

        Success and failure both serialise to one JSON object the model reads:
        ``{"ok": true, ...}`` carries the output and verification outcome,
        ``{"ok": false, "error": {...}}`` carries the typed error's code,
        message and details. The pipeline is invoked by *name*, so the
        registry's registered instance executes (§59.6), never a stray one.
        """
        try:
            result = await self._executor.run(
                call.name,
                call.arguments,
                principal=caller.principal,
                client=caller.client,
                principal_type=caller.principal_type,
                target=caller.target,
                task_id=caller.task_id,
                agent_id=caller.agent_id,
            )
        except UltronError as exc:
            return {"ok": False, "tool": call.name, "error": exc.to_dict()}
        return {
            "ok": True,
            "tool": result.tool,
            "verification": result.verification.value,
            "node": result.node,
            "output": result.output,
        }


def _positive_int(name: str, value: int) -> int:
    """Reject a non-positive/boolean ceiling at construction, not mid-loop."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive whole number")
    return value


def _dump(payload: Mapping[str, Any]) -> str:
    """JSON-encode a tool turn; a non-JSON output degrades to its ``str``."""
    return json.dumps(payload, default=str, ensure_ascii=False, sort_keys=True)
