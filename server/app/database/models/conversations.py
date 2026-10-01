"""Conversation models: `conversations` and `messages`.

The spec names both tables (lines 1127-1128), defines no columns, and says only
that "tasks are more important than individual conversations" (line 2918). That
ordering is reflected here: a conversation is a transcript, and it does not own
tasks. `conversations` has no task foreign key for exactly that reason -- the
link runs the other way, from `tasks.conversation_id`, so a conversation can be
deleted without touching the work it prompted.

`messages.content` is `Text`, not a string column, because a transcript entry
can hold a long tool transcript. `tool_call_id` links a `tool` message back to
the assistant message that requested the call; without it a transcript with
several tool round-trips cannot be reconstructed, because assistant messages may
contain more than one tool call.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    json_type,
)
from app.database.models.enums import ConversationStatus, MessageRole

if TYPE_CHECKING:
    pass


class Conversation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A conversation thread."""

    __tablename__ = "conversations"

    title: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[ConversationStatus] = mapped_column(
        String(32),
        nullable=False,
        default=ConversationStatus.ACTIVE,
        server_default=ConversationStatus.ACTIVE.value,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        doc="SET NULL: deleting a user keeps the transcript, which is often the "
        "only record of what the system was asked to do.",
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        json_type(),
        nullable=False,
        default=dict,
        server_default="{}",
        doc="Trailing underscore because `metadata` collides with "
        "DeclarativeBase.metadata. Mapped to the column name 'metadata'.",
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.sequence",
        lazy="raise_on_sql",
    )

    __table_args__ = (Index("ix_conversations_project_status", "project_id", "status"),)

    def __repr__(self) -> str:
        return f"<Conversation {self.id} {self.title!r}>"


class Message(Base, UUIDPrimaryKeyMixin):
    """One message in a conversation.

    `sequence` is the position within the conversation. It is not a global
    autoincrement: ordering must be stable and gap-free within a thread, and a
    conversation's messages are the unit that gets replayed into a model prompt.

    `role` uses `MessageRole`, whose value set is an engineering decision -- the
    spec never enumerates roles (see that enum's docstring).
    """

    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[MessageRole] = mapped_column(
        String(16),
        nullable=False,
        default=MessageRole.USER,
        server_default=MessageRole.USER.value,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(128))
    tool_call_id: Mapped[str | None] = mapped_column(
        String(64),
        doc="Set on a TOOL message: the tool_call this answers. Needed because one "
        "assistant message may request several calls.",
    )
    model: Mapped[str | None] = mapped_column(String(255))
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(
        back_populates="messages", lazy="raise_on_sql"
    )

    __table_args__ = (Index("uq_messages_sequence", "conversation_id", "sequence", unique=True),)

    @property
    def total_tokens(self) -> int | None:
        """Prompt plus completion tokens, or None if either is unrecorded."""
        if self.prompt_tokens is None or self.completion_tokens is None:
            return None
        return self.prompt_tokens + self.completion_tokens

    def __repr__(self) -> str:
        return f"<Message {self.conversation_id}#{self.sequence} {self.role}>"
