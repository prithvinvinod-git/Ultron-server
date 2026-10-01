"""Generic repository base (T015).

Every aggregate gets a repository, and every repository shares this base. The
base does four things and nothing else:

- ``get`` / ``get_required`` / ``add`` / ``delete``
- :func:`apply_limit`, a default cap on every read that takes a limit
- :func:`translate`, the single place a SQLAlchemy failure becomes an Ultron one
- :func:`scalars` / ``_fetch_all`` / ``_fetch_one``, the typed result helpers

Three decisions are worth stating outright, because each of them is a boundary
someone could later move by accident.

**Repositories never commit.** The unit of work is
:func:`app.database.session.session_scope`; a repository participates in the
caller's transaction. A repository that committed would make "commit on success,
roll back on any failure" (T013) impossible to honour for a multi-write handler,
and the failure would appear only in whichever aggregate happened to be last.

**Missing rows are not exceptions by default.** ``get`` returns ``None``; only
``get_required`` raises :class:`NotFoundError`. Most call sites legitimately
expect absence -- a lookup before a create, a poll for a task that may not have
started -- and a repository that raised would force every one of them to catch
an exception to express "not there yet".

**SQLAlchemy errors are translated exactly once, here.** T013 deliberately does
not translate, because a repository has to tell ``IntegrityError`` from
``OperationalError`` to decide whether a retry is worth attempting. Both are
mapped here to distinct Ultron errors so that information survives the boundary:
an integrity failure becomes a non-retryable ``ConflictError``, and everything
else becomes a retryable ``DatabaseError`` carrying the cause's type but never
its text (a driver message for a failed connection embeds the DSN).

**Reads are bounded.** ``apply_limit`` refuses to build an unbounded
``SELECT``. An unbounded read on a table that grows with usage -- ``events``,
``agent_logs``, ``messages`` -- is a memory exhaustion bug that only shows up in
production, and the default here is chosen to make the omission loud.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, ClassVar, cast

from sqlalchemy import Select, delete as sa_delete, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult

from app.core.errors import ConflictError, DatabaseError, NotFoundError

#: Cap applied when a caller does not supply one. Large enough to cover any UI
#: page, small enough that an accidental full-table read cannot exhaust memory.
DEFAULT_LIMIT = 100

#: Hard ceiling. A caller may ask for more than ``DEFAULT_LIMIT``, but not
#: without being explicit about how much.
MAX_LIMIT = 1000


def apply_limit[StatementT](statement: StatementT, limit: int | None = None) -> StatementT:
    """Return ``statement`` with a bounded ``LIMIT``, preserving its exact type.

    Generic over the statement rather than annotated as ``Select[Any]``, so a
    caller's ``select()`` keeps its own column types on the way back out instead
    of being widened to ``Any`` at this boundary.

    A limit of zero or a negative value is treated as "use the default" rather
    than passed through, because ``LIMIT 0`` returning nothing is almost never
    what a caller meant and fails silently.
    """
    if limit is None or limit <= 0:
        limit = DEFAULT_LIMIT
    if limit > MAX_LIMIT:
        msg = f"limit {limit} exceeds the maximum of {MAX_LIMIT}"
        raise ValueError(msg)
    return cast("StatementT", cast("Select[Any]", statement).limit(limit))


def apply_offset[StatementT](statement: StatementT, offset: int | None) -> StatementT:
    """Return ``statement`` with an ``OFFSET``, ignoring a negative value."""
    if offset is None or offset < 0:
        return statement
    return cast("StatementT", cast("Select[Any]", statement).offset(offset))


def scalars(result: Any) -> list[Any]:
    """Materialise a single-column result as a list.

    Deliberately untyped. The caller annotates the local it lands in -- usually a
    repository method whose return type already says what the rows are -- so
    there is one annotation per method instead of a ``cast`` at every call site,
    and a cast nobody re-checks is how a list method quietly starts returning the
    wrong type.

    Wrapped in ``list(...)`` because the result is a view over the driver's
    buffer, and handing that out lets a caller keep rows alive past the
    transaction that produced them.
    """
    return list(result.scalars().all())


def rowcount(result: Any) -> int:
    """Return the affected row count from a DML result.

    ``AsyncSession.execute`` is typed as returning ``Result``, which has no
    ``rowcount``, even though a ``DELETE`` or ``UPDATE`` always produces a
    ``CursorResult`` at runtime. ``-1`` is SQLAlchemy's own "unknown" sentinel,
    so it is passed through as ``0`` rather than reported as a count.
    """
    affected = getattr(cast("CursorResult[Any]", result), "rowcount", 0)
    return max(0, int(affected or 0))


def translate(error: SQLAlchemyError, operation: str) -> Exception:
    """Map a SQLAlchemy failure onto the Ultron error that describes it.

    ``IntegrityError`` becomes a non-retryable ``ConflictError`` -- the write was
    rejected on its merits (duplicate key, foreign key, check), and retrying it
    unchanged would fail identically. Everything else becomes a retryable
    ``DatabaseError``, because a dropped connection or a deadlock is exactly the
    case where a retry is worth attempting.

    Only the cause's *type* is retained. ``DatabaseError`` never copies the
    driver's message, and a psycopg message for a failed connection includes the
    DSN.
    """
    if isinstance(error, IntegrityError):
        return ConflictError(
            f"the {operation} conflicts with an existing row",
            details={"operation": operation},
        )
    return DatabaseError(operation, cause=error)


class Repository[ModelT]:
    """Common behaviour for one aggregate.

    ``model`` is the ORM class. Subclasses set it; the base never guesses,
    because inferring it from the generic parameter would only work where the
    annotation survived, and a wrong model class produces a query that returns
    plausible-looking wrong rows.
    """

    model: ClassVar[type[Any]]

    def __init__(self, session: AsyncSession) -> None:
        """Bind to ``session``. The session is not owned and never closed here."""
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """The bound session, for the rare query this base does not cover."""
        return self._session

    async def _fetch_all(self, statement: Select[Any]) -> list[ModelT]:
        """Execute a select and return its single column as a typed list.

        The row type comes from the class parameter, so a subclass's list methods
        are typed as ``list[ThatModel]`` with no annotation repeated per call and
        no ``cast`` to keep correct. Every list method goes through here, so the
        result handling -- and the ``LIMIT`` the caller was supposed to apply --
        is visible in one place.
        """
        result = await self._session.execute(statement)
        rows: list[ModelT] = scalars(result)
        return rows

    async def _fetch_one(self, statement: Select[Any]) -> ModelT | None:
        """Execute a select and return its first single-column value, or None."""
        result = await self._session.execute(statement)
        row: ModelT | None = result.scalar_one_or_none()
        return row

    async def add(self, instance: ModelT) -> ModelT:
        """Stage ``instance`` for insert and return it.

        Staged, not written: the flush happens when the unit of work commits, or
        when the caller explicitly flushes. An error here is translated, because
        a duplicate key usually surfaces at flush time rather than at ``add``.
        """
        try:
            self._session.add(instance)
            await self._session.flush()
        except SQLAlchemyError as error:
            await self._session.rollback()
            raise translate(error, f"{self.model.__tablename__}.add") from error
        return instance

    async def add_all(self, instances: Sequence[ModelT]) -> Sequence[ModelT]:
        """Stage several rows in one flush."""
        try:
            self._session.add_all(instances)
            await self._session.flush()
        except SQLAlchemyError as error:
            await self._session.rollback()
            raise translate(error, f"{self.model.__tablename__}.add_all") from error
        return instances

    async def get(self, identifier: Any) -> ModelT | None:
        """Return the row with primary key ``identifier``, or ``None``."""
        try:
            return cast("ModelT | None", await self._session.get(self.model, identifier))
        except SQLAlchemyError as error:
            raise translate(error, f"{self.model.__tablename__}.get") from error

    async def get_required(self, identifier: Any) -> ModelT:
        """Return the row with primary key ``identifier``, or raise.

        The identifier is coerced to ``str`` for the error message only. A
        :class:`NotFoundError` names the row that was missing, and an id is not a
        secret -- but it is still user-supplied, so it is never logged from here.
        """
        instance = await self.get(identifier)
        if instance is None:
            raise NotFoundError(self.model.__tablename__, str(identifier))
        return instance

    async def exists(self, identifier: Any) -> bool:
        """Report whether a row with this primary key exists."""
        statement = select(func.count()).select_from(self.model).where(self.model.id == identifier)
        result = await self._session.execute(statement)
        return bool(result.scalar_one())

    async def delete(self, identifier: Any) -> bool:
        """Delete the row, returning whether anything was removed.

        Returns a bool rather than raising, so "delete this if it is there" is
        expressible without a prior read -- which matters for idempotent
        teardown paths.
        """
        try:
            result = await self._session.execute(
                sa_delete(self.model).where(self.model.id == identifier),
            )
            await self._session.flush()
        except SQLAlchemyError as error:
            await self._session.rollback()
            raise translate(error, f"{self.model.__tablename__}.delete") from error
        return rowcount(result) > 0

    async def count(self) -> int:
        """Return the total number of rows in the table."""
        statement = select(func.count()).select_from(self.model)
        result = await self._session.execute(statement)
        return int(result.scalar_one())


class UuidRepository[ModelT](Repository[ModelT]):
    """A repository whose primary key is a UUID.

    Separate from :class:`Repository` because the identifier type is a real
    difference to a caller: passing a malformed id straight through would raise a
    driver error deep in a bind, whereas coercing it here means the mismatch
    surfaces at the repository boundary where it can be named.
    """

    async def get(self, identifier: uuid.UUID | str) -> ModelT | None:
        return await super().get(self._coerce(identifier))

    async def get_required(self, identifier: uuid.UUID | str) -> ModelT:
        return await super().get_required(self._coerce(identifier))

    async def exists(self, identifier: uuid.UUID | str) -> bool:
        return await super().exists(self._coerce(identifier))

    async def delete(self, identifier: uuid.UUID | str) -> bool:
        return await super().delete(self._coerce(identifier))

    @staticmethod
    def _coerce(identifier: uuid.UUID | str) -> uuid.UUID:
        """Return ``identifier`` as a UUID, or raise a nameable error.

        A malformed id is a client mistake, not a missing row, so it must not be
        reported as ``NOT_FOUND`` -- that would send the caller looking for a row
        that does not exist instead of at its own input.
        """
        if isinstance(identifier, uuid.UUID):
            return identifier
        try:
            return uuid.UUID(str(identifier))
        except (ValueError, AttributeError, TypeError) as error:
            msg = f"{identifier!r} is not a valid identifier"
            raise ValueError(msg) from error


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "Repository",
    "UuidRepository",
    "apply_limit",
    "apply_offset",
    "rowcount",
    "scalars",
    "translate",
]
