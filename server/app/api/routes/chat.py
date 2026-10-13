"""Interactive chat route: one prompt in, one answer out.

Exposes the §16 pipeline as a single non-streaming call, so an interactive
client (the bundled web page) can just ask a question. Unlike task execution
(wiring pending, T042+) this runs the agent loop inline in the request: one
turn is its own unit of work, nothing is persisted beyond the audit rows the
tool pipeline already writes, and the workspace permission model still applies
to every tool the loop calls.

The loop is deliberately NOT the task queue. Tasks (T039) are durable units of
work with retries and a state machine; this route is the conversational
front door — same agents, same tools, same provider, no orchestration book.
"""

from __future__ import annotations

from time import perf_counter
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.agents.loop import ToolCallingLoop, definitions_from_registry
from app.api.dependencies import Auth, Container, DbSession
from app.core.permissions import PermissionManager
from app.database.models.enums import MessageRole, PermissionLevel
from app.models.base import ModelMessage
from app.security.audit import AuditLogger
from app.tools.executor import ToolExecutor

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    """One question, plus optional tweaks to how it is answered.

    ``extra="forbid"`` so a mistyped field is a 422 instead of a silently
    ignored argument — the same rule every other request body here follows.
    """

    model_config = ConfigDict(extra="forbid")

    message: Annotated[str, Field(min_length=1, max_length=8192)]
    system: Annotated[str | None, Field(max_length=4096)] = None


class ChatResponse(BaseModel):
    """The answer, plus a little transparency about how it was produced."""

    answer: str
    model: str
    finish_reason: str
    tool_calls: int
    iterations: int
    took_ms: int


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

    The principal's permission level decides which tools the agent may call
    (same ``AGENT_DEFAULT_PERMISSION_LEVEL`` fallback the runtime uses): the
    chat front door gets no more power than any other client.
    """
    started = perf_counter()

    executor = ToolExecutor(
        registry=container.tools,
        permissions=PermissionManager(
            audit=AuditLogger(container.get_audit_log_repository(session)),
            default_level=PermissionLevel(str(container.settings.agent_default_permission_level)),
        ),
        events=container.events,
    )

    loop = ToolCallingLoop(
        router=container.model_router,
        executor=executor,
        tools=definitions_from_registry(container.tools),
        capability="default",
        system_prompt=payload.system,
    )

    result = await loop.run(
        (ModelMessage(role=MessageRole.USER, content=payload.message),),
        principal=principal.actor_id,
        client="chat",
    )

    return ChatResponse(
        answer=result.content,
        model=result.response.model,
        finish_reason=result.response.finish_reason.value,
        tool_calls=result.tool_calls,
        iterations=result.iterations,
        took_ms=int((perf_counter() - started) * 1000),
    )


__all__ = [
    "ChatRequest",
    "ChatResponse",
    "router",
]
