"""Project model: `projects`.

The spec names the table (line 1130), exposes `/projects/{id}` (line 1323), and
says "do not mix unrelated project memories" (line 1107) without ever defining
what a project is.

A project here is the boundary that scoping rules are written against: memory
namespaces are `project:<slug>` (lines 1101-1102), tasks and agents both carry an
optional `project_id`, and a Git workspace is per-project (spec section 46).
Making it a first-class row rather than a free-text slug on each of those is what
lets "does this agent's memory belong to its project?" be a joined query rather
than a string comparison.

`root_path` is a path stored as text. Spec section 36 forbids Windows-only paths
in code, so the value is validated at write time by the caller and stored
as-is; storing it as a database path type would bake in a filesystem model the
spec explicitly leaves open (Docker volume, native path, or both).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    json_type,
)

if TYPE_CHECKING:
    pass


class Project(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A project, used as the scoping key for memory and work."""

    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        unique=True,
        doc="URL-safe identifier. Also the suffix of the `project:<slug>` "
        "memory namespace (spec lines 1101-1102), which is why it is unique and "
        "never derived on the fly.",
    )
    description: Mapped[str | None] = mapped_column(Text)
    root_path: Mapped[str | None] = mapped_column(
        String(1024),
        doc="Workspace root (spec section 46). Text, not a database path type.",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        index=True,
        doc="Deliberately not a foreign key. Projects are broader than users in "
        "this system and outlive any single owner, so a hard reference would "
        "either block user deletion or cascade into project memory.",
    )
    settings_: Mapped[dict[str, Any]] = mapped_column(
        "settings",
        json_type(),
        nullable=False,
        default=dict,
        server_default="{}",
    )

    __table_args__ = (Index("ix_projects_active_slug", "is_active", "slug"),)

    @property
    def memory_namespace(self) -> str:
        """The memory namespace this project owns."""
        return f"project:{self.slug}"

    def __repr__(self) -> str:
        return f"<Project {self.slug}>"
