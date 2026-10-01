"""Memory repository (T015).

A memory is namespaced, and **the namespace is the access boundary**. Every read
here takes a namespace explicitly and filters on it. There is deliberately no
"search all memories" query: an unnamespaced search over a table holding other
projects' and users' context is how one agent ends up recalling another agent's
contents.

**Similarity search is absent on purpose.** It needs pgvector, which needs live
PostgreSQL and the T016 migration. A ``ORDER BY random()`` stand-in would be
worse than nothing: it would look like retrieval, pass a unit test, and return
unrelated neighbours in production. :func:`MemoryRepository.list_by_layer` is the
honest substrate the vector query will later build on.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete as sa_delete, or_, select, update

from app.core.errors import InvalidInputError
from app.database.models import Memory, MemoryLayer
from app.database.repositories.base import UuidRepository, apply_limit, apply_offset, rowcount


class MemoryRepository(UuidRepository[Memory]):
    """Namespaced memory."""

    model = Memory

    async def remember(
        self,
        *,
        namespace: str,
        namespace_kind: str,
        namespace_slug: str,
        content: str,
        layer: MemoryLayer = MemoryLayer.SHORT_TERM,
        user_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        source_event_id: uuid.UUID | None = None,
        importance: float = 0.5,
        expires_at: datetime | None = None,
    ) -> Memory:
        """Store one memory.

        ``importance`` is rejected when out of range rather than clamped. A
        clamped value would hide the caller's mistake behind a stored number that
        no longer matches what was passed, and importance drives later retrieval
        ordering -- so a silent correction would quietly change which memories a
        future agent sees first.

        No embedding is accepted here. Writing a vector without the T016 schema in
        place would produce a row no similarity query can use, and the caller
        would have no way to tell.
        """
        if not 0.0 <= importance <= 1.0:
            raise InvalidInputError(
                "importance must be between 0 and 1",
                details={"importance": importance},
            )
        if not namespace.strip():
            raise InvalidInputError("a memory needs a namespace")
        if not namespace_kind.strip() or not namespace_slug.strip():
            raise InvalidInputError(
                "a memory needs both a namespace kind and a namespace slug",
                details={"namespace_kind": namespace_kind, "namespace_slug": namespace_slug},
            )

        memory = Memory(
            namespace=namespace,
            namespace_kind=namespace_kind,
            namespace_slug=namespace_slug,
            layer=layer,
            content=content,
            importance=importance,
            user_id=user_id,
            project_id=project_id,
            agent_id=agent_id,
            task_id=task_id,
            source_event_id=source_event_id,
            expires_at=expires_at,
        )
        return await self.add(memory)

    async def list_by_layer(
        self,
        namespace: str,
        layer: MemoryLayer,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Memory]:
        """Return a namespace's memories in one layer, most important first."""
        statement = (
            select(Memory)
            .where(Memory.namespace == namespace, Memory.layer == layer)
            .order_by(Memory.importance.desc(), Memory.created_at)
        )
        return await self._fetch_all(apply_limit(apply_offset(statement, offset), limit))

    async def list_for_namespace(
        self,
        namespace: str,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Memory]:
        """Return a namespace's memories across layers, newest first.

        ``namespace`` is matched exactly, never as a prefix. A prefix match would
        let ``proj:alpha`` see ``proj:alpha-private``, which is the precise
        opposite of what a namespacing scheme is for.
        """
        statement = (
            select(Memory).where(Memory.namespace == namespace).order_by(Memory.created_at.desc())
        )
        return await self._fetch_all(apply_limit(apply_offset(statement, offset), limit))

    async def list_for_task(
        self,
        task_id: uuid.UUID,
        *,
        limit: int | None = None,
    ) -> list[Memory]:
        """Return memories a task produced, oldest first."""
        statement = select(Memory).where(Memory.task_id == task_id).order_by(Memory.created_at)
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_live(
        self,
        namespace: str,
        *,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> list[Memory]:
        """Return unexpired memories in a namespace, newest first.

        Expiry is applied in SQL rather than by filtering in Python, so an expired
        row is never loaded in order to be discarded.
        """
        statement = (
            select(Memory)
            .where(
                Memory.namespace == namespace,
                or_(
                    Memory.expires_at.is_(None),
                    Memory.expires_at > (now or datetime.now(UTC)),
                ),
            )
            .order_by(Memory.created_at.desc())
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def touch(self, identifier: uuid.UUID | str, *, now: datetime | None = None) -> None:
        """Record that a memory was read, incrementing its access count.

        A single ``UPDATE`` rather than load-then-increment, so two concurrent
        readers do not lose one another's count. The ORM is not told, so the
        in-session copy keeps its old values -- use this for accounting, not when
        the caller needs the updated row back.
        """
        stamp = now or datetime.now(UTC)
        await self._session.execute(
            update(Memory)
            .where(Memory.id == self._coerce(identifier))
            .values(access_count=Memory.access_count + 1, last_accessed_at=stamp)
        )
        await self._session.flush()

    async def purge_expired(self, *, now: datetime | None = None) -> int:
        """Delete memories past their expiry, returning how many.

        Retention-style, like the event log. Deliberately not namespace-scoped:
        it is a global sweep, and a namespace-scoped version exists as
        :meth:`clear_namespace` for the case where the intent really is one
        namespace.
        """
        result = await self._session.execute(
            sa_delete(Memory).where(
                Memory.expires_at.is_not(None),
                Memory.expires_at <= (now or datetime.now(UTC)),
            )
        )
        await self._session.flush()
        return rowcount(result)

    async def clear_namespace(self, namespace: str) -> int:
        """Delete every memory in one namespace, returning how many.

        Scoped by exact namespace equality rather than a like-pattern, so clearing
        one project's memory cannot reach another's.
        """
        result = await self._session.execute(sa_delete(Memory).where(Memory.namespace == namespace))
        await self._session.flush()
        return rowcount(result)


__all__ = ["MemoryRepository"]
