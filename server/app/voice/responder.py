"""The agent-turn responder the voice pipeline calls (T154, §25 / §59.21).

Voice is **another front door onto the same agents and tools**, not a parallel
stack (§59.21). This module is the one place where that claim becomes code: it
builds the very same :class:`~app.agents.loop.ToolCallingLoop` the chat routes
use, runs one turn for a spoken transcript, and returns the answer text for
:class:`~app.voice.manager.VoiceManager` to speak.

It lives outside the container because the container is wiring, not behaviour,
and outside the manager because the manager must not import a provider or the
tool pipeline (its whole point is to be testable with a fake ``Responder``).
The closure returned here is the seam between the two.

**A turn opens its own short transaction.** The loop's tool executor writes
audit rows through a session-scoped repository, and a voice session outlives any
one request, so the responder opens a ``session_scope`` per turn -- commit on a
clean turn, roll back on a failed one -- rather than holding a session open for
the life of the socket.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.loop import ToolCallingLoop, definitions_from_registry
from app.core.permissions import PermissionManager
from app.database.models.enums import MessageRole, PermissionLevel
from app.observability.logging import get_memory_manager, memory_context
from app.models.base import ModelMessage
from app.security.audit import AuditLogger
from app.tools.executor import ToolExecutor

__all__ = ["build_agent_responder"]

#: A ``session_scope`` callable, as :meth:`app.container.Container.session_scope`.
SessionScope = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def build_agent_responder(
    *,
    model_router: Any,
    tools: Any,
    events: Any,
    session_scope: SessionScope,
    get_audit_log_repository: Callable[[AsyncSession], Any],
    default_permission_level: PermissionLevel,
    capability: str = "default",
    system_prompt: str | None = None,
) -> Callable[..., Any]:
    """Build the ``Responder`` the voice manager calls for each final utterance.

    The returned callable matches :data:`app.voice.manager.Responder`:
    ``(text, *, principal, session_id, history) -> str``. ``history`` is the
    session's :class:`~app.voice.manager.VoiceTurn` list, replayed as prior
    messages so a spoken conversation has the same context a typed one does.
    """

    def _to_messages(history: Any, text: str) -> tuple[ModelMessage, ...]:
        messages: list[ModelMessage] = []
        for turn in history or ():
            role = MessageRole.ASSISTANT if turn.role == "assistant" else MessageRole.USER
            if turn.text:
                messages.append(ModelMessage(role=role, content=turn.text))
        messages.append(ModelMessage(role=MessageRole.USER, content=text))
        return tuple(messages)

    async def responder(
        text: str,
        *,
        principal: str,
        session_id: str,
        history: Any = (),
    ) -> str:
        async with session_scope() as session:
            # Bind the per-request memory manager so that memory.tools
            # (memory.store / memory.recall) work inside the agent loop.
            manager = get_memory_manager()
            if manager is None:
                # No memory context is globally bound; attempt to obtain it
                # from the session's container. The session_scope yields an
                # AsyncSession; we cannot reach the container here, so we
                # surface a best-effort failure rather than silently skipping
                # memory tools.
                import logging
                logging.getLogger(__name__).warning(
                    "memory_context not bound for voice turn; memory tools will be unavailable"
                )
            else:
                # Memory manager is already bound by get_memory_manager();
                # wrap the loop in it so memory tools can access it.
                async with memory_context(manager):
                    executor = ToolExecutor(
                        registry=tools,
                        permissions=PermissionManager(
                            audit=AuditLogger(get_audit_log_repository(session)),
                            default_level=default_permission_level,
                        ),
                        events=events,
                    )
                    loop = ToolCallingLoop(
                        router=model_router,
                        executor=executor,
                        tools=definitions_from_registry(tools),
                        capability=capability,
                        system_prompt=system_prompt,
                    )
                    result = await loop.run(
                        _to_messages(history, text),
                        principal=principal,
                        client="voice",
                    )
                    return str(result.content)

            # Fallback: run the loop without memory context (memory tools will
            # raise RuntimeError if called, which the model can handle/retry).
            executor = ToolExecutor(
                registry=tools,
                permissions=PermissionManager(
                    audit=AuditLogger(get_audit_log_repository(session)),
                    default_level=default_permission_level,
                ),
                events=events,
            )
            loop = ToolCallingLoop(
                router=model_router,
                executor=executor,
                tools=definitions_from_registry(tools),
                capability=capability,
                system_prompt=system_prompt,
            )
            result = await loop.run(
                _to_messages(history, text),
                principal=principal,
                client="voice",
            )
            return str(result.content)

    return responder
