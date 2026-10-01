"""Memory model: `memories`.

The spec names the table (line 1129), requires PostgreSQL + pgvector (line 1091),
requires that memory have namespaces (line 1095), gives namespace examples
(lines 1100-1104), and forbids mixing unrelated project memories (line 1107).
It defines no columns.

Namespace handling
------------------
`namespace` is a single string in a prefixed scheme -- `user`, `project:<slug>`,
`agent:<slug>` -- stored as given. The spec calls those "Example namespaces"
(lines 1097, 2909), i.e. an open scheme rather than an enum, so an enum here
would reject `project:my-new-thing`. The prefix is parsed by `namespace_scope()`
and the kind/slug indexed separately, because the spec's prohibition on mixing
project memories (line 1107) has to be enforceable as a query filter, and a
substring match on `namespace` cannot be indexed reliably.

The embedding column
--------------------
`embedding` is a `pgvector.sqlalchemy.Vector` sized from
`settings.embeddings.dimensions` (default 768). Three things follow from that
and are handled here rather than assumed away:

*   **The dimension is a schema fact.** Changing `EMBEDDING_DIMENSIONS` changes
    the column type and needs a migration. `vector_dimensions` is stored
    redundantly so a mismatch is detectable at query time rather than only as a
    cast error deep inside a retrieval.
*   **pgvector is optional.** `ENABLE_PGVECTOR=false` is a supported setting
    (settings line 145). When disabled the column type degrades to JSONB, so the
    table still exists and semantic retrieval can report "unavailable" instead of
    the whole subsystem failing to import.
*   **The extension is not created by the model.** `CREATE EXTENSION` is a
    migration concern (task T016) requiring privileges an application role
    should not hold.
"""

from __future__ import annotations

import json
import struct
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.config.settings import get_settings
from app.database.models.base import Base, UUIDPrimaryKeyMixin, json_type
from app.database.models.enums import MemoryLayer

if TYPE_CHECKING:
    from app.config.settings import Settings

_NAMESPACE_SEPARATORS = (":", "/")


def _resolve_settings() -> Settings | None:
    """Load settings, or None if the environment cannot satisfy them.

    A column type is fixed at import time, so this cannot be deferred to first
    use. Returning None rather than raising keeps the package importable in a
    context with no environment at all (a bare `python -c "import ..."`), at the
    cost of falling back to the documented default dimension. `vector_dimensions`
    on each row still records what was actually stored, so a fallback that never
    matched the real setting is detectable rather than silent.
    """
    try:
        return get_settings()
    except Exception:
        return None


def _embedding_type(settings: Settings | None) -> Any:
    """Return the column type for `memories.embedding`.

    Imported lazily so that a deployment without the pgvector package installed
    still works when the extension is disabled.
    """
    if settings is not None and not settings.database.enable_pgvector:
        return json_type()
    try:
        from pgvector.sqlalchemy import Vector
    except ImportError:  # pragma: no cover - depends on the install profile
        return json_type()
    dimensions = settings.embeddings.dimensions if settings is not None else 768
    return Vector(dimensions)


def namespace_scope(namespace: str) -> tuple[str, str]:
    """Split a namespace into (kind, slug).

    ``"project:campuscare"`` -> ``("project", "campuscare")``.
    ``"user"`` -> ``("user", "")``.

    Both ``:`` and ``/`` are accepted as separators because operators write
    namespace keys by hand and a slash is a natural choice for a hierarchical
    name such as ``"project/campuscare/api"``. Everything after the first
    separator is the slug, so a multi-segment slug is preserved whole.
    """
    for separator in _NAMESPACE_SEPARATORS:
        if separator in namespace:
            kind, _, slug = namespace.partition(separator)
            return kind.strip().lower(), slug.strip().lower()
    return namespace.strip().lower(), ""


class Memory(Base, UUIDPrimaryKeyMixin):
    """One remembered item."""

    __tablename__ = "memories"

    namespace: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    namespace_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        doc="Parsed prefix of `namespace` ('user', 'project', 'agent'). Stored "
        "so scope filtering is an indexed equality test.",
    )
    namespace_slug: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    layer: Mapped[MemoryLayer] = mapped_column(
        String(16),
        nullable=False,
        default=MemoryLayer.LONG_TERM,
        server_default=MemoryLayer.LONG_TERM.value,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any | None] = mapped_column(_embedding_type(_resolve_settings()))
    vector_dimensions: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Dimensions of the stored vector. Redundant with the column type on "
        "purpose: it makes an embedding of the wrong width detectable instead of "
        "silently unsearchable.",
    )
    importance: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.5,
        server_default="0.5",
        doc="0..1 retention weight. Semantic (line 1083) and long-term (1075) "
        "memories compete for the same store, so something has to arbitrate.",
    )
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    access_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
        doc="Short-term expiry (settings.short_term_ttl). NULL for memories that do not expire.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_memories_scope", "namespace_kind", "namespace_slug"),
        Index("ix_memories_layer_expires", "layer", "expires_at"),
        Index("ix_memories_user_created", "user_id", "created_at"),
    )

    @property
    def is_expired(self) -> bool:
        """True when `expires_at` has passed.

        Naive datetimes are treated as UTC rather than rejected: a row read from
        a legacy write or a driver that dropped the offset should not make
        `expires_at` silently un-comparable, which would pin the row as
        non-expiring forever.
        """
        if self.expires_at is None:
            return False
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return expires <= datetime.now(UTC)

    def embedding_as_json(self) -> str | None:
        """Serialise the embedding, or None if absent.

        Written out rather than relying on the driver's array binding so the same
        column shape works whether pgvector or the JSON fallback is in use.
        """
        embedding = self.embedding
        if embedding is None:
            return None
        if isinstance(embedding, str):
            return embedding
        if isinstance(embedding, memoryview | bytearray | bytes):
            return self._embedding_from_buffer(embedding)
        return json.dumps([float(x) for x in embedding])

    def _embedding_from_buffer(self, buffer: memoryview | bytearray | bytes) -> str:
        """Serialise an embedding handed over as a raw binary buffer.

        Embedding providers (Ollama, OpenAI, sentence-transformers) return
        numpy arrays, and the memoryview of one is a flat buffer of float32
        values. Decoding that as text would be meaningless, so the bytes are
        unpacked as little-endian float32 -- the wire order pgvector and numpy
        both use -- and the length is checked against `vector_dimensions` when
        that was recorded.

        A buffer whose byte length is not a multiple of 4 is not a float32
        vector at all, so it is rejected rather than truncated to a partial
        value: a silently shortened embedding would be unsearchable rather than
        wrong-looking, which is the harder failure to notice.
        """
        raw = bytes(buffer)
        if len(raw) % 4 != 0:
            msg = f"embedding buffer is {len(raw)} bytes, not a whole number of float32 values"
            raise ValueError(msg)
        values = [value[0] for value in struct.iter_unpack("<f", raw)]
        if self.vector_dimensions is not None and len(values) != self.vector_dimensions:
            msg = (
                f"embedding has {len(values)} values but vector_dimensions "
                f"records {self.vector_dimensions}"
            )
            raise ValueError(msg)
        return json.dumps(values)

    def __repr__(self) -> str:
        preview = self.content[:40].replace("\n", " ")
        return f"<Memory {self.namespace} {self.layer} {preview!r}>"
