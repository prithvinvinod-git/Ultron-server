"""Conversation and message repositories (T015).

Message ordering and tenancy are the two things worth stating.

**Ordering is by ``sequence``, not by ``created_at``.** Two messages can share a
timestamp, and ``created_at`` ordering is then whatever the storage engine
returns -- which would silently reorder a conversation and, in a tool-calling
transcript, reorder the tool result away from its call.

**Tenancy is checked on the data layer.** :func:`ConversationRepository.require_conversation`
takes the expected owner and refuses a mismatch. A repository that returned a
conversation by id alone would be one missing ``where`` clause away from serving
another user's messages, and a check that lives only in the API layer is a check
that a second caller reaching this code later will not know to repeat.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.core.errors import NotFoundError
from app.database.models import Conversation, ConversationStatus, Message, MessageRole
from app.database.repositories.base import UuidRepository, apply_limit, apply_offset


class ConversationRepository(UuidRepository[Conversation]):
    """Conversations."""

    model = Conversation

    async def require_conversation(
        self,
        identifier: uuid.UUID | str,
        *,
        user_id: uuid.UUID | None = None,
    ) -> Conversation:
        """Return a conversation, asserting ``user_id`` owns it.

        ``user_id=None`` means "ownership not checked" and is appropriate only for
        internal callers that have already established authorisation. It is an
        explicit argument rather than a default so that the unchecked case is
        visible at every call site instead of being what you get by forgetting
        something.

        A mismatch is reported as not-found rather than forbidden: confirming
        that a conversation id exists but belongs to someone else leaks the shape
        of the data, and the caller has no useful action to take either way.
        """
        conversation = await self.get_required(identifier)
        if user_id is not None and conversation.user_id not in (None, user_id):
            raise NotFoundError(Conversation.__tablename__, str(conversation.id))
        return conversation

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Conversation]:
        """Return a user's active conversations, most recently active first.

        ``nulls_last`` keeps a conversation that has never had a message from
        sorting above one that has, which is what a plain ``DESC`` on a nullable
        column would do.
        """
        statement = (
            select(Conversation)
            .where(
                Conversation.user_id == user_id,
                Conversation.status == ConversationStatus.ACTIVE,
            )
            .order_by(Conversation.last_message_at.desc().nulls_last(), Conversation.created_at)
        )
        return await self._fetch_all(apply_limit(apply_offset(statement, offset), limit))

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        limit: int | None = None,
    ) -> list[Conversation]:
        """Return a project's conversations, oldest first."""
        statement = (
            select(Conversation)
            .where(Conversation.project_id == project_id)
            .order_by(Conversation.created_at)
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def touch(
        self, identifier: uuid.UUID | str, *, now: datetime | None = None
    ) -> Conversation:
        """Record that a message was just added, for list ordering.

        Separate from the message write so the caller can stamp it in the same
        transaction as the message it belongs to.
        """
        conversation = await self.get_required(identifier)
        conversation.last_message_at = now or datetime.now(UTC)
        await self._session.flush()
        return conversation

    async def archive(self, identifier: uuid.UUID | str) -> Conversation:
        """Archive a conversation, leaving its messages readable.

        Archive, not delete. The messages are the record of what an agent was
        asked to do and what it did -- the same evidence the audit log depends on
        -- and archiving keeps that record while removing the thread from a
        default listing.

        Idempotent: re-archiving returns the row untouched.
        """
        conversation = await self.get_required(identifier)
        if conversation.status == ConversationStatus.ARCHIVED:
            return conversation
        conversation.status = ConversationStatus.ARCHIVED
        await self._session.flush()
        return conversation

    async def delete_conversation(self, identifier: uuid.UUID | str) -> Conversation:
        """Mark a conversation deleted, keeping the rows.

        A soft delete. The status moves to ``DELETED`` rather than removing
        anything, because a hard delete on the conversation cascades to its
        messages, and those messages are the audit trail for whatever the
        conversation caused.
        """
        conversation = await self.get_required(identifier)
        conversation.status = ConversationStatus.DELETED
        await self._session.flush()
        return conversation


class MessageRepository(UuidRepository[Message]):
    """Messages within a conversation."""

    model = Message

    async def append(
        self,
        *,
        conversation_id: uuid.UUID,
        role: MessageRole,
        content: str,
        sequence: int | None = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        model: str | None = None,
        task_id: uuid.UUID | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        request_id: str | None = None,
        created_at: datetime | None = None,
    ) -> Message:
        """Append a message, numbering it within the conversation.

        ``sequence`` defaults to the next free number for the conversation. Two
        concurrent appends can compute the same number, and the loser's
        ``IntegrityError`` surfaces here -- as a ``ConflictError`` from
        :meth:`Repository.add` -- rather than later on an unrelated lazy load, so
        the retry can be local to the append.
        """
        if sequence is None:
            statement = select(func.max(Message.sequence)).where(
                Message.conversation_id == conversation_id
            )
            result = await self._session.execute(statement)
            highest = result.scalar_one()
            sequence = 0 if highest is None else int(highest) + 1

        message = Message(
            conversation_id=conversation_id,
            role=role,
            sequence=sequence,
            content=content,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            model=model,
            task_id=task_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            request_id=request_id,
            created_at=created_at or datetime.now(UTC),
        )
        return await self.add(message)

    async def list_for_conversation(
        self,
        conversation_id: uuid.UUID,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Message]:
        """Return a conversation's messages in order.

        Ordered by ``sequence`` and not ``created_at``: two messages written in
        the same millisecond would otherwise come back in whatever order the
        storage engine produced.
        """
        statement = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.sequence)
        )
        return await self._fetch_all(apply_limit(apply_offset(statement, offset), limit))

    async def list_by_role(
        self,
        conversation_id: uuid.UUID,
        role: MessageRole,
        *,
        limit: int | None = None,
    ) -> list[Message]:
        """Return one role's messages, in order."""
        statement = (
            select(Message)
            .where(Message.conversation_id == conversation_id, Message.role == role)
            .order_by(Message.sequence)
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_tool_messages(
        self,
        conversation_id: uuid.UUID,
        *,
        limit: int | None = None,
    ) -> list[Message]:
        """Return the tool-role messages, in order.

        A dedicated method rather than ``list_by_role`` at each call site,
        because "the tool results for this conversation" is a thing a verifier
        needs and it should not require the caller to remember the role name.
        """
        return await self.list_by_role(conversation_id, MessageRole.TOOL, limit=limit)

    async def total_tokens(self, conversation_id: uuid.UUID) -> tuple[int, int]:
        """Return summed ``(prompt, completion)`` tokens for a conversation.

        One query for both halves, so the two cannot be measured at different
        moments and disagree. ``coalesce`` is there because a conversation with
        no rows sums to ``NULL``, and ``int(None)`` is not a useful answer.
        """
        statement = select(
            func.coalesce(func.sum(Message.prompt_tokens), 0),
            func.coalesce(func.sum(Message.completion_tokens), 0),
        ).where(Message.conversation_id == conversation_id)
        result = await self._session.execute(statement)
        prompt, completion = result.one()
        return int(prompt or 0), int(completion or 0)

    async def count_for_conversation(self, conversation_id: uuid.UUID) -> int:
        """Count a conversation's messages, for a length guard before truncation."""
        statement = (
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id)
        )
        result = await self._session.execute(statement)
        return int(result.scalar_one() or 0)


__all__ = ["ConversationRepository", "MessageRepository"]
