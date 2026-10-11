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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.core.errors import UltronError
from app.database.models import MessageRole
from app.models.base import (
    CompletionRequest,
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

    async def _complete(self, transcript: Sequence[ModelMessage]) -> ModelResponse:
        """One model turn, with the tools advertised and the model routed."""
        return await self._router.complete(
            CompletionRequest(
                messages=tuple(transcript),
                model="",
                tools=self._definitions,
            ),
            capability=self._capability,
        )

    async def _dispatch(self, call: ToolCall, caller: _Caller) -> str:
        """Run one ``ToolCall`` through the §16 pipeline, as a JSON ``tool`` turn.

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
            return _dump({"ok": False, "tool": call.name, "error": exc.to_dict()})
        return _dump(
            {
                "ok": True,
                "tool": result.tool,
                "verification": result.verification.value,
                "node": result.node,
                "output": result.output,
            }
        )


def _positive_int(name: str, value: int) -> int:
    """Reject a non-positive/boolean ceiling at construction, not mid-loop."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive whole number")
    return value


def _dump(payload: Mapping[str, Any]) -> str:
    """JSON-encode a tool turn; a non-JSON output degrades to its ``str``."""
    return json.dumps(payload, default=str, ensure_ascii=False, sort_keys=True)
