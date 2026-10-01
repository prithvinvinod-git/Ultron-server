"""Shared declarative base, primary keys, and mixins.

Design notes that the spec does not dictate
------------------------------------------
`server_arc.md` names every table but specifies columns for only three of them
(agents, tasks, devices) and specifies no data type, primary key, index, or
foreign key for any table. The conventions below are therefore engineering
decisions, recorded here so a later reader can tell them apart from the three
places the spec actually spoke:

1.  **UUID primary keys.** The spec exposes entities in URLs as
    `/agents/{id}` (line 1314) and names identifiers `<entity>_id`
    (lines 917, 443, 1206). Nothing says the type. UUID was chosen because it
    lets an id be minted before the row is written -- an agent that exists in
    the runtime before its first flush, or an event emitted before the
    transaction commits -- and because a sequential integer would leak volume
    to anything holding an id.

2.  **Timezone-aware timestamps everywhere.** Required in practice by ruff's
    `DTZ` rules and correct regardless: a naive timestamp read back in a
    different offset is a different instant.

3.  **JSONB on PostgreSQL, JSON elsewhere.** PostgreSQL is the authoritative
    store (spec line 3080), so payload columns use JSONB there. The variant
    keeps the same models creatable on SQLite for unit tests, where JSONB does
    not exist.

4.  **`onupdate` fires only through the ORM.** A raw `UPDATE` statement leaves
    `updated_at` untouched. That is a known limit of the ORM-level approach, not
    a database trigger; anything that must not depend on it should set the
    column itself.

`pgvector` is imported lazily by the memory model rather than here, because
importing it unconditionally would make the whole package require the vector
extension to be installed even when `ENABLE_PGVECTOR=false`.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Any

from sqlalchemy import DateTime, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, TypeEngine

# Re-exported, not redefined. T013 already declared `Base` in
# `app/database/session.py`, and Alembic's env.py targets *that* class. A second
# DeclarativeBase here would give the process two independent `metadata`
# registries: models would register into this one, `session.Base.metadata` would
# stay empty, and autogenerate would emit a migration dropping every table in the
# database. `test_base_is_the_one_alembic_targets` in the repository suite pins
# this, because nothing else fails loudly when it drifts.
from app.database.session import Base

__all__ = [
    "NAMING_CONVENTION",
    "Base",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "json_type",
]

#: T013's convention, re-exported so model modules have a single import source.
NAMING_CONVENTION: Mapping[str, str] = MappingProxyType(
    {key: str(value) for key, value in Base.metadata.naming_convention.items()}
)


def json_type() -> TypeEngine[Any]:
    """Return the payload column type for the active backend.

    A factory rather than a shared instance: reusing one `with_variant` object
    across every column would couple them, and a type object holds no state that
    needs sharing.
    """
    return JSON().with_variant(JSONB(), "postgresql")


class UUIDPrimaryKeyMixin:
    """A client-mintable UUID primary key."""

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        # Generated in Python rather than by the database so the id is known
        # before the INSERT, which lets a child row reference its parent within
        # the same flush.
    )


class TimestampMixin:
    """``created_at`` and ``updated_at``, both timezone-aware and server-set."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
