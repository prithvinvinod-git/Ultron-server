"""Project-scoped memory (T125, spec §22).

§22's fifth layer is *"Project-specific context"* (line 1150), and the section's
one prohibition is *"Do not mix unrelated project memories"* (line 1168). This
class exists to make that prohibition structural rather than a convention: it
takes a **project slug**, never a namespace, and builds ``project:<slug>``
itself. A caller therefore cannot write ``project:alpha`` context while reading
``project:beta`` -- there is no argument with which to express it.

**The slug, not the project id, is the boundary.** A project row in
``projects`` has a UUID, but a memory's access boundary is its *namespace*
string -- that is what the model indexes and what every read filters on (§22's
namespace rule, enforced by :class:`MemoryRepository`). Binding to the slug keeps
this class working for a project that has no row yet, and keeps the boundary
identical to the one the storage layer enforces rather than one that merely
usually agrees with it.

**A project memory needs a project.** :meth:`ProjectMemory.remember` with an
empty slug raises, because a slugless ``project::`` namespace would be one bucket
shared by every project -- the exact mixing the spec forbids.
"""

from __future__ import annotations

import uuid

from app.database.models import MemoryLayer
from app.database.repositories.memories import MemoryRepository
from app.memory import namespaces
from app.memory.types import MemoryRecord


class ProjectMemory:
    """Namespaced context for one project."""

    def __init__(self, repository: MemoryRepository) -> None:
        self._repository = repository

    @staticmethod
    def _namespace(project_slug: str) -> namespaces.Namespace:
        return namespaces.parse(namespaces.for_project(project_slug))

    async def remember(
        self,
        project_slug: str,
        content: str,
        *,
        importance: float = 0.5,
        project_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        source_event_id: uuid.UUID | None = None,
    ) -> MemoryRecord:
        """Persist one project context item and return it."""
        parsed = self._namespace(project_slug)
        memory = await self._repository.remember(
            namespace=parsed.raw,
            namespace_kind=parsed.kind,
            namespace_slug=parsed.slug,
            content=content,
            layer=MemoryLayer.PROJECT,
            importance=importance,
            project_id=project_id,
            user_id=user_id,
            agent_id=agent_id,
            task_id=task_id,
            source_event_id=source_event_id,
        )
        return MemoryRecord.from_model(memory)

    async def recall(
        self,
        project_slug: str,
        *,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        """Return one project's context, most important first."""
        parsed = self._namespace(project_slug)
        memories = await self._repository.list_by_layer(
            parsed.raw, MemoryLayer.PROJECT, limit=limit
        )
        return [MemoryRecord.from_model(memory) for memory in memories]

    async def clear(self, project_slug: str) -> int:
        """Delete one project's context, returning how many rows were removed.

        Scoped to ``project:<slug>`` exactly, so clearing one project can never
        reach another's memories.
        """
        parsed = self._namespace(project_slug)
        return await self._repository.clear_namespace(parsed.raw)


__all__ = ["ProjectMemory"]
