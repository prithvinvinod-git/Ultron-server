"""Interactive chat routes: one prompt in, one answer out.

``POST /chat`` exposes the §16 pipeline as a single non-streaming call, so an
interactive client (the bundled web page) can just ask a question. Unlike task
execution (wiring pending, T042+) this runs the agent loop inline in the
request: one turn is its own unit of work, nothing is persisted beyond the
audit rows the tool pipeline already writes, and the workspace permission model
still applies to every tool the loop calls.

``POST /chat/stream`` is the same pipeline over Server-Sent Events: the model's
answer arrives token-by-token and each tool call is announced as it starts and
finishes. It exists because the web front door (the ``uiproto`` command center)
renders answers as they are written, and because a long tool-using turn with no
output reads as a hung request (§60.4: silence must never look like a stall).

The loop is deliberately NOT the task queue. Tasks (T039) are durable units of
work with retries and a state machine; these routes are the conversational front
door — same agents, same tools, same provider, no orchestration book.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from time import perf_counter
from typing import Annotated, Any

from fastapi import APIRouter, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agents.loop import (
    LoopChunkKind,
    ToolCallingLoop,
    ToolLoopResult,
    definitions_from_registry,
)
from app.api.dependencies import Auth, Container, DbSession
from app.core.errors import UltronError
from app.core.permissions import PermissionManager
from app.database.models.enums import MessageRole, PermissionLevel
from app.models.base import ModelMessage
from app.observability.logging import memory_context
from app.security.audit import AuditLogger
from app.tools.executor import ToolExecutor

router = APIRouter(prefix="/chat", tags=["chat"])

#: ``text/event-stream`` is what makes a client treat the body as a stream of
#: frames rather than as a document that ends at the first connection close.
SSE_CONTENT_TYPE = "text/event-stream"


class ChatRequest(BaseModel):
    """One question, plus optional tweaks to how it is answered.

    ``extra="forbid"`` so a mistyped field is a 422 instead of a silently
    ignored argument — the same rule every other request body here follows.
    """

    model_config = ConfigDict(extra="forbid")

    message: Annotated[str, Field(min_length=1, max_length=8192)]
    system: Annotated[str | None, Field(max_length=4096)] = None
    model: Annotated[str | None, Field(max_length=128)] = None
    conversation_id: uuid.UUID | None = None
    """When set, the turn is appended to that conversation's transcript.

    The id must belong to the caller (a foreign id is a 404, checked by the
    repository). Omitting it runs the turn statelessly, which is what the
    bundled web page does until it creates a thread.
    """


class ChatResponse(BaseModel):
    """The answer, plus a little transparency about how it was produced."""

    answer: str
    model: str
    finish_reason: str
    tool_calls: int
    iterations: int
    took_ms: int
    conversation_id: uuid.UUID | None = None
    """The thread this turn belongs to, echoed back so a client that never
    named one can start tracking it from the first answer."""
    selection_reason: str = ""
    """Why this model was chosen: ``"capability"`` or ``"fallback"`` (T224, §59.9)."""


async def _system_with_memory(
    container: Container,
    session: DbSession,
    *,
    message: str,
    system: str | None,
) -> str | None:
    """Prepend what memory already knows, so the first answer is informed.

    The agent can always call ``memory.recall`` itself, but a turn that opens by
    asking about yesterday should not have to spend a round-trip discovering the
    one fact it needed. Recalling here — against the caller's namespace, keyed by
    their message — puts those lines in the system prompt up front.

    Recall is best-effort: a layer that cannot answer (no embedder, Redis down)
    contributes nothing and the turn proceeds, because a memory outage must not
    fail a chat. Only when lines come back is the caller's own ``system`` text
    kept *last*, so an explicit instruction still outranks a remembered fact.
    """
    manager = container.get_memory_manager(session)
    try:
        lines = await manager.build_context(query=message, limit=8)
    except Exception:
        # Recall is a hint, never a hard dependency: a memory outage must not
        # fail a chat turn, so any error simply degrades to the caller's system.
        return system
    if not lines:
        return system
    block = "# Relevant memory\n" + "\n".join(f"- {line}" for line in lines)
    if system:
        return f"{block}\n\n{system}"
    return block


def _build_loop(
    container: Container, session: DbSession, system_prompt: str | None, model: str | None = None
) -> ToolCallingLoop:
    """Assemble the §16 loop bound to this request's registry, permissions and bus.

    The principal's permission level decides which tools the agent may call
    (same ``AGENT_DEFAULT_PERMISSION_LEVEL`` fallback the runtime uses): the
    chat front door gets no more power than any other client. Both the
    non-streaming and streaming routes build the loop this way, so they cannot
    drift apart.

    ``model`` is the operator's per-request pick from Settings (a
    ``provider:model`` id). It is only a *preference*: the router prepends it and
    silently falls back to normal selection when that provider is unconfigured,
    so a stale pick never 500s a turn (§33).
    """
    executor = ToolExecutor(
        registry=container.tools,
        permissions=PermissionManager(
            audit=AuditLogger(container.get_audit_log_repository(session)),
            default_level=PermissionLevel(str(container.settings.agent_default_permission_level)),
        ),
        events=container.events,
    )
    return ToolCallingLoop(
        router=container.model_router,
        executor=executor,
        tools=definitions_from_registry(container.tools),
        capability="default",
        system_prompt=system_prompt,
        model=model or "",
    )


async def _resolve_conversation(
    container: Container,
    session: DbSession,
    *,
    conversation_id: uuid.UUID | None,
    principal: Auth,
) -> uuid.UUID | None:
    """Return the conversation id this turn belongs to, or ``None``.

    ``None`` in means ``None`` out: a turn with no ``conversation_id`` persists
    nothing, so an existing stateless caller (and its tests) keeps working
    unchanged — persistence is opt-in, not a surprise side effect. A given id is
    checked for ownership through the repository, so a foreign id is a 404
    rather than a silent write into someone else's thread.
    """
    if conversation_id is None:
        return None
    repository = container.get_conversation_repository(session)
    await repository.require_conversation(conversation_id, user_id=principal.user_id)
    return conversation_id


async def _persist_turn(
    container: Container,
    session: DbSession,
    *,
    conversation_id: uuid.UUID,
    user_message: str,
    answer: str,
    model: str,
    result: ToolLoopResult | None,
) -> None:
    """Append the user turn and the assistant answer to a transcript.

    Two messages, written in the request's own unit of work so a failure to
    store leaves no half-turn behind. The conversation is touched so a sidebar
    that orders by ``last_message_at`` shows this thread at the top. Token
    counts are recorded when the provider reported them; ``None`` otherwise,
    which the model reads as "unknown" rather than as zero.
    """
    messages = container.get_message_repository(session)
    await messages.append(
        conversation_id=conversation_id,
        role=MessageRole.USER,
        content=user_message,
    )
    usage = getattr(result.response, "usage", None) if result is not None else None
    await messages.append(
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT,
        content=answer,
        model=model,
        prompt_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
        completion_tokens=(
            getattr(usage, "completion_tokens", None) if usage else None
        ),
    )
    await container.get_conversation_repository(session).touch(conversation_id)


@router.post(
    "",
    response_model=ChatResponse,
    summary="Ask one question",
)
async def chat(
    payload: ChatRequest,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> ChatResponse:
    """Run the user's message through the agent loop and return the answer.

    The non-streaming path: the whole answer is produced before the response is
    written. Clients that want to render as it is written use ``/chat/stream``.

    When ``conversation_id`` is given (or the caller is a known user), the turn
    is appended to that thread's transcript before the response is written.
    """
    started = perf_counter()
    conversation_id = await _resolve_conversation(
        container,
        session,
        conversation_id=payload.conversation_id,
        principal=principal,
    )
    system = await _system_with_memory(
        container, session, message=payload.message, system=payload.system
    )
    loop = _build_loop(container, session, system, payload.model)
    with memory_context(container.get_memory_manager(session)):
        result = await loop.run(
            (ModelMessage(role=MessageRole.USER, content=payload.message),),
            principal=principal.actor_id,
            client="chat",
        )
    if conversation_id is not None:
        await _persist_turn(
            container,
            session,
            conversation_id=conversation_id,
            user_message=payload.message,
            answer=result.content,
            model=result.response.model,
            result=result,
        )

    return ChatResponse(
        answer=result.content,
        model=result.response.model,
        finish_reason=result.response.finish_reason.value,
        tool_calls=result.tool_calls,
        iterations=result.iterations,
        took_ms=int((perf_counter() - started) * 1000),
        conversation_id=conversation_id,
        selection_reason=result.selection_reason,
    )


def _sse(event: str, data: dict[str, Any]) -> bytes:
    """Encode one SSE frame: a named ``event:`` line and a JSON ``data:`` line.

    The shape mirrors the ``/events`` bus frames (``event:`` + ``id:`` +
    ``data:``), minus the id: this stream is not resumable — it is one turn, and
    a reconnect re-runs the prompt rather than replaying a half-finished answer.
    JSON is the payload so a client parses one thing, never prose it must parse.
    """
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n".encode()


@router.post(
    "/stream",
    summary="Ask one question, streamed (Server-Sent Events)",
    response_description="A text/event-stream of token, tool and done frames.",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "No usable credential."},
    },
)
async def chat_stream(
    payload: ChatRequest,
    request: Request,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> StreamingResponse:
    """Stream one turn as Server-Sent Events.

    Frames (``event:`` name, JSON ``data:``):

    - ``token`` — ``{"text": "<delta>"}``; the model's answer as it is written.
    - ``tool_started`` — ``{"tool": "<name>", "arguments": {...}}``.
    - ``tool_completed`` — ``{"tool": "<name>", "ok": true|false, "output": ...}``.
    - ``done`` — the terminal frame: ``{"answer", "model", "finish_reason",
      "tool_calls", "iterations", "took_ms", "conversation_id"}``, the same
      fields ``POST /chat`` returns. A client that ignores every other frame
      still ends up with the full answer.
    - ``error`` — ``{"code", "message"}`` if the turn fails mid-stream. The
      pipeline's typed errors are surfaced here rather than as a broken socket.

    When ``conversation_id`` is given (or the caller is a known user), the
    completed turn is appended to that thread's transcript before the terminal
    frame is emitted — the answer the client just read is the answer stored.
    """
    started = perf_counter()
    conversation_id = await _resolve_conversation(
        container,
        session,
        conversation_id=payload.conversation_id,
        principal=principal,
    )
    system = await _system_with_memory(
        container, session, message=payload.message, system=payload.system
    )
    loop = _build_loop(container, session, system, payload.model)
    message = (ModelMessage(role=MessageRole.USER, content=payload.message),)

    async def body() -> AsyncIterator[bytes]:
        answer_parts: list[str] = []
        try:
            # Bound here, not in the handler: the generator outlives the
            # handler's frame, so a context set outside would be gone by the
            # time the first tool call runs.
            with memory_context(container.get_memory_manager(session)):
                async for chunk in loop.stream(
                    message,
                    principal=principal.actor_id,
                    client="chat",
                ):
                    if await request.is_disconnected():
                        return
                    if chunk.kind is LoopChunkKind.TOKEN:
                        answer_parts.append(chunk.text)
                        yield _sse("token", {"text": chunk.text})
                    elif chunk.kind is LoopChunkKind.TOOL_STARTED:
                        yield _sse(
                            "tool_started",
                            {"tool": chunk.tool, "arguments": dict(chunk.arguments)},
                        )
                    elif chunk.kind is LoopChunkKind.TOOL_COMPLETED:
                        yield _sse(
                            "tool_completed",
                            {"tool": chunk.tool, "ok": chunk.ok, "output": chunk.output},
                        )
                    elif chunk.kind is LoopChunkKind.COMPLETED and chunk.result is not None:
                        result = chunk.result
                        if conversation_id is not None:
                            await _persist_turn(
                                container,
                                session,
                                conversation_id=conversation_id,
                                user_message=payload.message,
                                answer=result.content or "".join(answer_parts),
                                model=result.response.model,
                                result=result,
                            )
                        yield _sse(
                            "done",
                            _done_payload(result, answer_parts, started, conversation_id),
                        )
        except Exception as exc:
            if isinstance(exc, UltronError):
                error = exc.to_dict()
            else:
                error = {"code": "INTERNAL", "message": str(exc)}
            yield _sse("error", error)

    return StreamingResponse(
        body(),
        media_type=SSE_CONTENT_TYPE,
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _done_payload(
    result: ToolLoopResult,
    answer_parts: list[str],
    started: float,
    conversation_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """The terminal ``done`` frame, matching ``ChatResponse``'s fields."""
    answer = result.content if result.content else "".join(answer_parts)
    return {
        "answer": answer,
        "model": result.response.model,
        "finish_reason": result.response.finish_reason.value,
        "tool_calls": result.tool_calls,
        "iterations": result.iterations,
        "took_ms": int((perf_counter() - started) * 1000),
        "conversation_id": str(conversation_id) if conversation_id is not None else None,
        "selection_reason": result.selection_reason,
    }


__all__ = [
    "ChatRequest",
    "ChatResponse",
    "router",
]
