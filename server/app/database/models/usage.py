"""Model usage accounting: `model_usage`.

The spec names the table (lines 1134, 3098), lists `model usage` as a Phase 12
deliverable (line 2169), and says nothing else about it -- not even that it
records tokens. The only textual hint anywhere in the repository is
`.env.example` line 138: *"Record token usage and latency to the model_usage
table."* That comment is a configuration note, not schema, but it is the only
statement of intent available and this table follows it.

Column choices that the "not specified" status leaves open:

*   **`total_tokens` is computed, not stored.** `prompt_tokens` and
    `completion_tokens` are the provider's own numbers; their sum is arithmetic
    that must not drift from them. A generated column would enforce that in the
    database, but a stored total can be written once and never recomputed, and
    it would then disagree with its own inputs silently.
*   **Latency is milliseconds as an integer.** Providers report both seconds
    (float, Ollama) and milliseconds (int, most hosted APIs); one integer column
    avoids a float-vs-Decimal conversion question on every read.
*   **Cost is `Numeric`, not float.** Money must not accumulate binary
    rounding error, and a `float` column cannot be corrected after the fact
    without losing the original.
*   **One row per call.** Rows are the audit unit, so a retried request is two
    rows, not one row with a retry counter -- otherwise the retry's cost is
    invisible, which is the number most likely to be needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Index, Integer, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, UUIDPrimaryKeyMixin
from app.database.models.enums import ModelUsageStatus


class ModelUsage(Base, UUIDPrimaryKeyMixin):
    """One model request/response pair."""

    __tablename__ = "model_usage"

    model: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    provider: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[ModelUsageStatus] = mapped_column(
        String(16),
        nullable=False,
        default=ModelUsageStatus.SUCCESS,
        server_default=ModelUsageStatus.SUCCESS.value,
    )
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 8),
        doc="Numeric, never float: 8 decimal places is finer than any current "
        "per-token price, and float accumulation would drift.",
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    __table_args__ = (
        Index("ix_model_usage_model_time", "model", "recorded_at"),
        Index("ix_model_usage_task_time", "task_id", "recorded_at"),
    )

    @property
    def total_tokens(self) -> int | None:
        """Prompt plus completion tokens, or None if either is unrecorded.

        None rather than 0 for a partially recorded call: a failed request may
        have consumed prompt tokens and no completion tokens, and reporting that
        as 0 would understate spend. Aggregations must therefore skip None
        explicitly rather than relying on SQL's NULL propagation.
        """
        if self.prompt_tokens is None or self.completion_tokens is None:
            return None
        return self.prompt_tokens + self.completion_tokens

    @property
    def succeeded(self) -> bool:
        """True when the call completed without error."""
        return self.status is ModelUsageStatus.SUCCESS

    def __repr__(self) -> str:
        return f"<ModelUsage {self.model} {self.status}>"
