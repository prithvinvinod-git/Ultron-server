"""Async engine, session factory, and declarative base (T013).

PostgreSQL is the authoritative store (spec §23). This module owns three
things and nothing else:

- :class:`Base`, the declarative base every ORM model inherits.
- :func:`create_engine`, which builds an :class:`AsyncEngine` from settings.
- :func:`create_session_factory`, which builds the session maker repositories use.

Two deliberate omissions. There is **no module-level engine**: construction
belongs to the composition root in ``app/container.py`` (T019), so a test can
build one without mutating global state and an import can never open a socket.
And SQLAlchemy exceptions are **not** translated here. A repository has to tell
an ``IntegrityError`` (a duplicate key) from an ``OperationalError`` (the server
went away) in order to decide whether to retry, and a blanket translation would
destroy exactly the information it needs. Wrapping happens in the adapter that
understands the failure, using :class:`~app.core.errors.DatabaseError`.

Nothing here connects at import or construction time, so every function is
testable without a running PostgreSQL.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any, Final

from sqlalchemy import MetaData, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, NoSuchModuleError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool, StaticPool

from app.config import DatabaseSettings, get_settings
from app.core.errors import ConfigError, DatabaseError
from app.observability import get_logger

_LOGGER = get_logger(__name__)

#: Driver names that speak asyncio. ``create_async_engine`` only accepts these,
#: but it reports a plain ``postgresql://`` URL as an opaque dialect error, so
#: the check happens here where the message can say what to fix.
_ASYNC_DRIVERS: Final[frozenset[str]] = frozenset(
    {"asyncpg", "aiosqlite", "psycopg", "asyncmy", "aiomysql"}
)

#: Deterministic constraint names. Alembic can only alter or drop a constraint
#: it can name, and a database created outside Alembic needs to match.
#:
#: ``ck`` uses ``%(constraint_name)s``, so a check constraint **must** be given
#: an explicit name. That is deliberate, and it is SQLAlchemy's own documented
#: recommendation. The two alternatives are both worse:
#:
#: - ``%(column_0_N_name)s`` yields ``ck_table_`` for a check written as a
#:   string literal, because a string check has no associated columns, and two
#:   unnamed checks on one table would collide.
#: - Leaving checks unnamed produces exactly that collision silently, and a
#:   migration cannot target a constraint with no stable name.
#:
#: Requiring a name fails loudly at import instead, naming the exact problem.
_NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every ULTRON ORM model.

    SQLAlchemy 2.0 style: models declare mapped attributes and inherit this
    rather than using ``declarative_base()``.
    """

    metadata = MetaData(naming_convention=_NAMING_CONVENTION)

    def __repr__(self) -> str:
        """Identify the row by type and primary key, never by whole state.

        Dumping every attribute would put task content, message bodies, and
        audit records into logs and test failure output.
        """
        identifier = getattr(self, "id", None)
        if identifier is None:
            return f"<{type(self).__name__}>"
        return f"<{type(self).__name__} id={identifier!r}>"


def _parse_url(url: str) -> URL:
    """Return the parsed URL, or raise :class:`ConfigError` with a usable message."""
    try:
        return make_url(url)
    except ArgumentError as error:
        raise ConfigError(f"DATABASE_URL is not a valid URL: {error}") from error


def _require_async_driver(url: URL) -> None:
    """Refuse a synchronous driver before SQLAlchemy reports it obscurely."""
    driver = url.drivername
    _, _, driver_name = driver.partition("+")
    if driver_name not in _ASYNC_DRIVERS:
        supported = ", ".join(sorted(_ASYNC_DRIVERS))
        raise ConfigError(
            f"DATABASE_URL uses the synchronous driver '{driver}'. "
            f"ULTRON is async throughout; use one of: {supported}. "
            f"For PostgreSQL that means 'postgresql+asyncpg://'."
        )


def _is_in_memory_sqlite(url: URL) -> bool:
    """True for a SQLite URL with no file, which must share one connection."""
    database = url.database
    return bool(url.get_backend_name() == "sqlite" and database in {None, "", ":memory:"})


def _engine_options(settings: DatabaseSettings) -> dict[str, Any]:
    """Return the pool arguments appropriate to this backend.

    SQLite rejects ``pool_size`` and friends: it has no connection pool to size,
    and passing them raises at engine creation. In-memory SQLite additionally
    needs a single shared connection, because each new connection would
    otherwise get its own empty database.
    """
    if _is_in_memory_sqlite(_parse_url(settings.url)):
        return {"poolclass": StaticPool}
    if _parse_url(settings.url).get_backend_name() == "sqlite":
        return {"poolclass": NullPool}
    return {
        "pool_size": settings.pool_size,
        "max_overflow": settings.max_overflow,
        "pool_timeout": settings.pool_timeout,
        "pool_recycle": settings.pool_recycle,
    }


def masked_url(url: str) -> str:
    """Return ``url`` with the password removed, for logs and error messages.

    A DSN reaches error messages constantly, and a DSN carries the password.
    """
    return _parse_url(url).render_as_string(hide_password=True)


def create_engine(
    settings: DatabaseSettings | None = None,
) -> AsyncEngine:
    """Build an :class:`AsyncEngine`. No connection is opened here.

    Pool sizing comes from :class:`DatabaseSettings`, and the DSN is masked
    before it is logged, because a startup line is not a safe place for a
    password.
    """
    database = settings or get_settings().database
    url = _parse_url(database.url)
    _require_async_driver(url)

    try:
        engine = create_async_engine(
            url,
            echo=database.echo,
            future=True,
            **_engine_options(database),
        )
    except (NoSuchModuleError, ModuleNotFoundError) as error:
        # A declared driver that is not installed fails here, at construction,
        # rather than on the first query. That is the friendlier moment.
        raise ConfigError(
            f"the driver '{url.drivername}' is not installed: {error}. "
            f"Run 'uv sync' to install the declared dependencies."
        ) from error

    _LOGGER.info(
        "database engine created",
        extra={"event": "database.engine_created", "dsn": masked_url(database.url)},
    )
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return the session maker repositories and the DI container will share.

    ``expire_on_commit=False`` is not a default to override casually. With it
    on, every attribute is expired at commit and the next access triggers a
    lazy load, which in async SQLAlchemy raises ``MissingGreenlet`` unless it
    happens to be inside greenlet context. Keeping loaded attributes readable
    after commit is what makes the async usage safe.

    ``autoflush=False`` because a flush is a write, and a read that silently
    writes pending changes is a surprise. Repositories flush explicitly.
    """
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


@contextlib.asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a session that commits on success and rolls back on any failure.

    This is the unit of work for a write. A failure at all rolls the whole thing
    back, so a handler that commits halfway cannot leave a partial aggregate
    behind. SQLAlchemy exceptions propagate untouched; see the module docstring
    for why translation happens in the adapter instead.
    """
    session = factory()
    try:
        yield session
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    finally:
        await session.close()


@contextlib.asynccontextmanager
async def read_session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a read-only session that is always rolled back.

    A read must never be able to commit, even by accident: a handler that
    decides to write inside a read scope should fail, not quietly persist.
    """
    session = factory()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()


async def ping(engine: AsyncEngine) -> None:
    """Check the database is reachable, raising :class:`DatabaseError` if not.

    Used by startup and by the health check (T018). The failure is wrapped here
    because there is no adapter in the way: a bare ``OperationalError`` escaping
    to the caller would leak the DSN, and ``DatabaseError`` reports the cause's
    type without its text.
    """
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as error:
        raise DatabaseError("ping", cause=error) from error


async def dispose(engine: AsyncEngine) -> None:
    """Close every pooled connection. Called from the lifespan shutdown path."""
    await engine.dispose()
    _LOGGER.info(
        "database engine disposed",
        extra={"event": "database.engine_disposed"},
    )


def session_dependency(
    factory: async_sessionmaker[AsyncSession],
) -> Any:
    """Return a FastAPI dependency yielding a transactional session (T021).

    Declared here rather than in ``app/api/`` so the transaction boundary lives
    next to the session factory it depends on.
    """

    async def _get_session() -> AsyncIterator[AsyncSession]:
        async with session_scope(factory) as session:
            yield session

    return _get_session


__all__ = [
    "AsyncConnection",
    "AsyncEngine",
    "AsyncSession",
    "Base",
    "async_sessionmaker",
    "create_engine",
    "create_session_factory",
    "dispose",
    "masked_url",
    "ping",
    "read_session_scope",
    "session_dependency",
    "session_scope",
]
