"""Conversation routes: the transcript surface behind the chat front door.

The repositories (T015) already model a conversation and its messages; the chat
routes (``/chat``) do not persist what they answer. This module is the REST
surface that lets a client own its threads: create one, list its own, read one
back with its transcript, rename it, and delete it.

**The routes are thin on purpose.** Each one resolves a repository through the
container, calls one method, and serialises the result. No route writes a column
or commits: the request's ``session_scope`` owns the transaction, so a route
that committed would make the request span two units of work and let a later
failure leave a half-written thread behind.

**Tenancy is enforced by the repository, not the route.** Every read and write
passes the caller's ``user_id`` into ``require_conversation``: a conversation
that belongs to someone else is reported as not-found, so the route cannot leak
the shape of data it does not own. A mismatch is a 404 by ``NotFoundError``'s own
status, installed by ``app.main.add_exception_handlers`` — nothing here inspects
an exception to pick a code.

**Delete is a soft delete.** ``DELETE`` moves the row to ``DELETED`` rather than
removing it (the repository's own rule): the messages are the record of what an
agent was asked to do, and a hard delete would cascade them away.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import Auth, Container, DbSession
from app.database.models import Conversation
from app.database.models.enums import ConversationStatus, MessageRole

router = APIRouter(prefix="/conversations", tags=["conversations"])

#: Ceiling on a page, matching the repository's own ``MAX_LIMIT``. A larger
#: request is refused by validation rather than silently clamped, so a client
#: cannot believe it read everything when it did not.
MAX_PAGE = 1000


class ConversationCreate(BaseModel):
    """The caller-supplied half of a conversation; the rest is derived.

    ``extra="forbid"`` so a mistyped field is a 422 instead of a silently
    ignored argument. ``status`` and ``user_id`` are deliberately absent: a
    conversation is always born ``ACTIVE`` and owned by the caller, and
    accepting either here would let a client claim someone else's thread.
    """

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str | None, Field(max_length=255)] = None
    project_id: uuid.UUID | None = None


class ConversationRename(BaseModel):
    """A new title for an existing conversation."""

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(min_length=1, max_length=255)]


class ConversationRead(BaseModel):
    """A conversation as it crosses the wire, without its transcript.

    ``from_attributes`` lets an ORM row validate directly. ``message_count`` is
    computed by the route (one count query) rather than exposed as a lazy
    relationship, so the summary a client sees in a sidebar never loads every
    message of every thread to be rendered.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None = None
    status: ConversationStatus
    project_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None
    message_count: int = 0


class MessageRead(BaseModel):
    """One message in a transcript, in ``sequence`` order."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: MessageRole
    sequence: int
    content: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    model: str | None = None
    created_at: datetime


class ConversationDetail(ConversationRead):
    """A conversation with its full transcript."""

    messages: list[MessageRead] = Field(default_factory=list)


@router.get("", response_model=list[ConversationRead], summary="List conversations")
async def list_conversations(
    container: Container,
    session: DbSession,
    principal: Auth,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ConversationRead]:
    """Return the caller's active conversations, most recently active first.

    An anonymous request has no user id, so it has no conversations to return:
    an empty list rather than an error, because "you have no threads" and "you
    are not signed in" are the same state to a listing client.
    """
    if principal.user_id is None:
        return []
    repository = container.get_conversation_repository(session)
    conversations = await repository.list_for_user(
        principal.user_id, limit=limit, offset=offset
    )
    counts = container.get_message_repository(session)
    summaries = []
    for conversation in conversations:
        read = ConversationRead.model_validate(conversation)
        read.message_count = await counts.count_for_conversation(conversation.id)
        summaries.append(read)
    return summaries


@router.post(
    "",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a conversation",
)
async def create_conversation(
    payload: ConversationCreate,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> ConversationRead:
    """Create an empty conversation owned by the caller and return it."""
    repository = container.get_conversation_repository(session)
    conversation = await repository.add(
        _new_conversation(payload, user_id=principal.user_id)
    )
    return ConversationRead.model_validate(conversation)


@router.get(
    "/{conversation_id}",
    response_model=ConversationDetail,
    summary="Get one conversation with its transcript",
)
async def get_conversation(
    conversation_id: uuid.UUID,
    container: Container,
    session: DbSession,
    principal: Auth,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = MAX_PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ConversationDetail:
    """Return one conversation and its messages, or ``404`` for a foreign id."""
    conversations = container.get_conversation_repository(session)
    conversation = await conversations.require_conversation(
        conversation_id, user_id=principal.user_id
    )
    messages = container.get_message_repository(session)
    transcript = await messages.list_for_conversation(
        conversation.id, limit=limit, offset=offset
    )
    # Build the header without touching ``Conversation.messages``: the
    # relationship is ``lazy="raise_on_sql"`` precisely so a caller must load
    # the transcript deliberately (the query above) rather than by accident here.
    header = ConversationRead.model_validate(conversation)
    return ConversationDetail(
        **header.model_dump(exclude={"message_count"}),
        messages=[MessageRead.model_validate(message) for message in transcript],
        message_count=len(transcript),
    )


@router.patch(
    "/{conversation_id}",
    response_model=ConversationRead,
    summary="Rename a conversation",
)
async def rename_conversation(
    conversation_id: uuid.UUID,
    payload: ConversationRename,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> ConversationRead:
    """Change a conversation's title; the row is otherwise untouched."""
    repository = container.get_conversation_repository(session)
    conversation = await repository.require_conversation(
        conversation_id, user_id=principal.user_id
    )
    conversation.title = payload.title
    # ``updated_at`` is an ``onupdate`` column: the UPDATE expires it, and reading
    # it back would be a lazy load outside greenlet context. Refresh loads it in
    # the request's own awaitable path so serialisation stays synchronous.
    await session.flush()
    await session.refresh(conversation)
    return ConversationRead.model_validate(conversation)


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a conversation",
)
async def delete_conversation(
    conversation_id: uuid.UUID,
    container: Container,
    session: DbSession,
    principal: Auth,
) -> None:
    """Soft-delete a conversation, leaving its messages readable."""
    repository = container.get_conversation_repository(session)
    await repository.require_conversation(conversation_id, user_id=principal.user_id)
    await repository.delete_conversation(conversation_id)


def _new_conversation(
    payload: ConversationCreate, *, user_id: uuid.UUID | None
) -> Conversation:
    """Build an unsaved ``Conversation`` row from a validated request."""
    return Conversation(
        title=payload.title,
        project_id=payload.project_id,
        user_id=user_id,
        status=ConversationStatus.ACTIVE,
    )


__all__ = [
    "ConversationCreate",
    "ConversationDetail",
    "ConversationRead",
    "ConversationRename",
    "MessageRead",
    "router",
]
