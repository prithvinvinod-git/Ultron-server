"""Event repository (T015).

The event log is append-only, and this module is what makes that enforceable
rather than aspirational: there is no update method, and the only removal path
is :func:`EventRepository.delete_older_than`, which is retention.

That function is separate from the inherited per-row ``delete`` on purpose.
"Prune the log" and "remove one event" are different operations with very
different blast radii, and merging them means a mistaken argument empties the
table.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete as sa_delete, func, select

from app.database.models import Event
from app.database.repositories.base import UuidRepository, apply_limit, apply_offset, rowcount


class EventRepository(UuidRepository[Event]):
    """Append-only event log."""

    model = Event

    async def append(
        self,
        *,
        event_type: str,
        payload: dict[str, object] | None = None,
        occurred_at: datetime | None = None,
        sequence: int | None = None,
        source: str | None = None,
        correlation_id: str | None = None,
        task_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        error: str | None = None,
    ) -> Event:
        """Append one event.

        ``event_type`` is a plain string because the spec's two event lists
        disagree; T030 is meant to settle the vocabulary. Until then nothing is
        refused, but the column is indexed, so settling it later is a check
        constraint rather than a data migration.
        """
        event = Event(
            event_type=event_type,
            payload=payload or {},
            occurred_at=occurred_at or datetime.now(UTC),
            sequence=sequence,
            source=source,
            correlation_id=correlation_id,
            task_id=task_id,
            agent_id=agent_id,
            user_id=user_id,
            project_id=project_id,
            device_id=device_id,
            error=error[:4000] if error else None,
        )
        return await self.add(event)

    async def list_for_task(
        self,
        task_id: uuid.UUID,
        *,
        limit: int | None = None,
    ) -> list[Event]:
        """Return a task's events, oldest first.

        Ordered by ``occurred_at`` then ``sequence``. The secondary key matters:
        events emitted within one timestamp are ordered by the sequence their
        emitter assigned, and ``created_at`` would be a weaker tiebreak than
        that.
        """
        statement = (
            select(Event)
            .where(Event.task_id == task_id)
            .order_by(Event.occurred_at, Event.sequence)
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_for_correlation(
        self,
        correlation_id: str,
        *,
        limit: int | None = None,
    ) -> list[Event]:
        """Return everything sharing a correlation id, oldest first.

        This is the request-tracing query: one user action fans out across several
        services, and ``correlation_id`` is what ties them back together without a
        join on anything else.
        """
        statement = (
            select(Event)
            .where(Event.correlation_id == correlation_id)
            .order_by(Event.occurred_at, Event.sequence)
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_by_type(
        self,
        event_type: str,
        *,
        since: datetime,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Event]:
        """Return events of one type since a time, newest first.

        ``since`` is a required argument rather than an optional filter. This
        table only ever grows, so an unbounded type scan is exactly the failure
        ``apply_limit`` exists to prevent -- and making the time bound mandatory
        turns the dangerous version of the call into a type error.
        """
        statement = (
            select(Event)
            .where(Event.event_type == event_type, Event.occurred_at >= since)
            .order_by(Event.occurred_at.desc())
        )
        return await self._fetch_all(apply_limit(apply_offset(statement, offset), limit))

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Event]:
        """Return a project's events, newest first, optionally bounded by time."""
        statement = select(Event).where(Event.project_id == project_id)
        if since is not None:
            statement = statement.where(Event.occurred_at >= since)
        statement = statement.order_by(Event.occurred_at.desc())
        return await self._fetch_all(apply_limit(statement, limit))

    async def count_by_type(self, event_type: str) -> int:
        """Count events of one type, for metrics without materialising rows."""
        statement = select(func.count()).select_from(Event).where(Event.event_type == event_type)
        result = await self._session.execute(statement)
        return int(result.scalar_one() or 0)

    async def delete_older_than(self, cutoff: datetime) -> int:
        """Delete events older than ``cutoff``, returning how many.

        Retention, and the only removal this repository offers. A cutoff is
        mandatory: there is no "delete everything" overload, because a misplaced
        argument should fail to compile rather than empty the log.

        Callers wanting to prune frequently should call this on a schedule. The
        unit of work still governs the transaction, so a large prune holds its
        locks for the whole batch -- which is the argument for doing this rarely
        and in bulk rather than incrementally on every write.
        """
        result = await self._session.execute(sa_delete(Event).where(Event.occurred_at < cutoff))
        await self._session.flush()
        return rowcount(result)


__all__ = ["EventRepository"]
