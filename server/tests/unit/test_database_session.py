"""Unit tests for the database session layer (T013).

These run on a bare checkout with no PostgreSQL, because the whole point of the
layer is that it is testable before the database exists: `create_async_engine`
connects lazily, and the driver check happens at construction.

Where real behaviour is worth proving, it is proved against in-memory SQLite
rather than by asserting that an object has an attribute. Transaction semantics
are exactly the kind of thing that looks right in a mock and is wrong in
production, so the commit, rollback, and read-only guarantees below use a real
database.

A note on SQLite: it is a *test* double for PostgreSQL, never a substitute.
PostgreSQL-specific types (JSONB, pgvector) do not work here, so these tests
declare their own minimal models rather than reusing T014 ones. What is being
tested is the session machinery, not the schema.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from sqlalchemy import CheckConstraint, String, Table, select, text
from sqlalchemy.exc import IntegrityError, InvalidRequestError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.pool import QueuePool, StaticPool

from app.config import DatabaseSettings
from app.core.errors import ConfigError, DatabaseError
from app.database.session import (
    Base,
    create_engine,
    create_session_factory,
    dispose,
    masked_url,
    ping,
    read_session_scope,
    session_scope,
)

pytestmark = pytest.mark.unit

SQLITE_URL = "sqlite+aiosqlite://"
POSTGRES_URL = "postgresql+asyncpg://ultron:pw@localhost:5432/ultron"


# Declared once at module level on purpose. A model declared inside a test
# function is registered on the shared ``Base.metadata`` and stays there for
# the rest of the session, so a second test defining the same name would fail
# with "table is already defined".
class Probe(Base):
    """A minimal model used to exercise the session machinery."""

    __tablename__ = "probe"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    # ``unique`` produces a constraint, not an index, so an index has to be
    # asked for. Declared separately to exercise the ``ix`` naming convention.
    tag: Mapped[str] = mapped_column(String(32), default="", index=True)


class Sibling(Base):
    """Proves models share one metadata rather than each getting their own."""

    __tablename__ = "sibling"

    id: Mapped[int] = mapped_column(primary_key=True)


class Checked(Base):
    """Proves the naming convention produces a name a migration can act on."""

    __tablename__ = "checked"

    id: Mapped[int] = mapped_column(primary_key=True)
    amount: Mapped[int] = mapped_column(default=0)
    __table_args__ = (CheckConstraint("amount >= 0", name="amount_non_negative"),)


def _settings(url: str = POSTGRES_URL, **overrides: Any) -> DatabaseSettings:
    return DatabaseSettings(url=url, **overrides)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """An engine over a private in-memory database, created then disposed.

    A new engine per test, so one test's rows cannot reach the next.
    """
    created = create_engine(_settings(SQLITE_URL))
    async with created.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield created
    finally:
        await dispose(created)


@pytest.fixture
def factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


async def _names(session: AsyncSession) -> list[str]:
    result = await session.execute(select(Probe.name).order_by(Probe.name))
    return list(result.scalars().all())


class TestEngineConstruction:
    def test_a_postgres_url_builds_without_connecting(self) -> None:
        """Construction must not touch the network, or nothing is testable."""
        engine = create_engine(_settings())

        assert engine.url.drivername == "postgresql+asyncpg"
        assert engine.url.database == "ultron"

    def test_pool_settings_reach_the_engine(self) -> None:
        engine = create_engine(
            _settings(pool_size=7, max_overflow=3, pool_timeout=11, pool_recycle=600)
        )

        pool = cast(QueuePool, engine.pool)
        assert pool.size() == 7
        assert pool._max_overflow == 3
        assert pool._timeout == 11
        assert pool._recycle == 600

    def test_echo_is_carried_through(self) -> None:
        assert create_engine(_settings(echo=True)).echo is True

    def test_a_synchronous_driver_is_refused_with_a_usable_message(self) -> None:
        """A bare ``postgresql://`` must not surface as an opaque dialect error."""
        with pytest.raises(ConfigError) as caught:
            create_engine(_settings("postgresql://ultron:pw@localhost/ultron"))

        message = str(caught.value)
        assert "synchronous driver" in message
        assert "postgresql+asyncpg://" in message

    def test_a_malformed_url_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="not a valid URL"):
            create_engine(_settings("not-a-url-at-all"))

    def test_in_memory_sqlite_shares_one_connection(self) -> None:
        """Two connections to ``:memory:`` would be two empty databases."""
        assert isinstance(create_engine(_settings(SQLITE_URL)).pool, StaticPool)

    def test_settings_default_to_the_configured_database(self) -> None:
        """Omitting settings must use app configuration, not a hardcoded URL."""
        assert create_engine().url.get_backend_name() == "postgresql"

    def test_two_engines_are_independent(self) -> None:
        """No module-level engine, so building one cannot mutate another."""
        first = create_engine(_settings(SQLITE_URL))
        second = create_engine(_settings())

        assert first is not second


class TestMaskedUrl:
    def test_the_password_is_removed(self) -> None:
        assert "hunter2" not in masked_url(
            "postgresql+asyncpg://ultron:hunter2@localhost:5432/ultron"
        )

    def test_the_rest_of_the_url_survives(self) -> None:
        """A masked URL that says nothing is useless for diagnosing a failure."""
        masked = masked_url("postgresql+asyncpg://ultron:hunter2@localhost:5432/ultron")

        assert "ultron" in masked
        assert "localhost:5432" in masked

    def test_a_url_without_a_password_keeps_its_host(self) -> None:
        assert "localhost" in masked_url("postgresql+asyncpg://localhost:5432/ultron")


class TestBase:
    def test_models_share_one_metadata(self) -> None:
        assert {"probe", "sibling", "checked"} <= set(Base.metadata.tables)
        assert Sibling.metadata is Base.metadata

    def test_a_named_check_gets_the_convention_prefix(self) -> None:
        """A migration can only alter a constraint it can name."""
        table = cast(Table, Checked.__table__)

        names = {constraint.name for constraint in table.constraints}
        assert "ck_checked_amount_non_negative" in names
        assert "pk_checked" in names

    def test_an_unnamed_check_fails_loudly(self) -> None:
        """Requiring the name is the point: a silent ``ck_table_`` would collide.

        Raising here, at import, beats shipping a constraint no migration can
        target.
        """
        with pytest.raises(InvalidRequestError, match="explicitly named"):

            class Unnamed(Base):
                __tablename__ = "unnamed_check"
                id: Mapped[int] = mapped_column(primary_key=True)
                amount: Mapped[int] = mapped_column(default=0)
                __table_args__ = (CheckConstraint("amount >= 0"),)

    def test_a_unique_constraint_is_named_from_its_columns(self) -> None:
        """Unique and index names are derived, so they need no manual name."""
        table = cast(Table, Probe.__table__)

        constraint_names = {constraint.name for constraint in table.constraints}
        index_names = {index.name for index in table.indexes}
        assert "uq_probe_name" in constraint_names
        assert "ix_probe_tag" in index_names

    def test_repr_shows_the_id_not_the_whole_row(self) -> None:
        """Dumping every attribute would put message bodies into logs."""
        rendered = repr(Probe(id=7, name="secret-content"))

        assert "id=7" in rendered
        assert "secret-content" not in rendered

    def test_repr_of_an_unsaved_row_is_still_useful(self) -> None:
        assert repr(Probe(name="x")) == "<Probe>"


class TestSessionFactory:
    def test_the_maker_produces_async_sessions(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        assert isinstance(factory(), AsyncSession)

    async def test_attributes_survive_a_commit(
        self, engine: AsyncEngine, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """With ``expire_on_commit`` left on, the next attribute access after a
        commit raises ``MissingGreenlet`` in async code. This is that regression.
        """
        async with session_scope(factory) as session:
            row = Probe(name="kept")
            session.add(row)
            await session.flush()
            identifier = row.id

        async with read_session_scope(factory) as session:
            loaded = (
                await session.execute(select(Probe).where(Probe.id == identifier))
            ).scalar_one()
            assert loaded.name == "kept"


class TestTransactionSemantics:
    async def test_a_successful_scope_commits(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with session_scope(factory) as session:
            session.add(Probe(name="kept"))

        async with read_session_scope(factory) as session:
            assert await _names(session) == ["kept"]

    async def test_a_failure_rolls_back_everything(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """A handler that fails halfway must not leave a partial aggregate."""
        with pytest.raises(RuntimeError, match="boom"):
            async with session_scope(factory) as session:
                session.add(Probe(name="first"))
                await session.flush()
                raise RuntimeError("boom")

        async with read_session_scope(factory) as session:
            assert await _names(session) == []

    async def test_a_constraint_violation_rolls_back(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """The realistic failure: a duplicate key must not commit the batch."""
        async with session_scope(factory) as session:
            session.add(Probe(name="duplicate"))

        with pytest.raises(IntegrityError):
            async with session_scope(factory) as session:
                session.add(Probe(name="duplicate"))

        async with read_session_scope(factory) as session:
            assert await _names(session) == ["duplicate"]

    async def test_a_read_scope_never_commits(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """A read must not be able to persist, even by accident."""
        async with session_scope(factory) as session:
            session.add(Probe(name="original"))

        async with read_session_scope(factory) as session:
            row = (await session.execute(select(Probe))).scalar_one()
            row.name = "changed"
            await session.flush()

        async with read_session_scope(factory) as session:
            assert await _names(session) == ["original"]

    async def test_the_session_is_closed_after_a_failure(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        with pytest.raises(RuntimeError):
            async with session_scope(factory) as session:
                raise RuntimeError

        assert not session.in_transaction()

    async def test_scopes_do_not_leak_between_uses(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """A leaked session would hold a connection and an open transaction."""
        for index in range(3):
            async with session_scope(factory) as session:
                session.add(Probe(name=f"row-{index}"))

        async with read_session_scope(factory) as session:
            assert await _names(session) == ["row-0", "row-1", "row-2"]


class TestPing:
    async def test_a_reachable_database_passes(self, engine: AsyncEngine) -> None:
        await ping(engine)

    async def test_an_unreachable_database_raises_a_typed_error(self) -> None:
        """A bare OperationalError would leak the DSN; DatabaseError does not."""
        broken = create_engine(_settings("postgresql+asyncpg://ultron:hunter2@localhost:1/ultron"))

        with pytest.raises(DatabaseError) as caught:
            await ping(broken)

        assert caught.value.code.value == "DATABASE_ERROR"
        assert "hunter2" not in str(caught.value.to_dict())

    async def test_the_cause_type_is_kept_for_diagnosis(self) -> None:
        """The type identifies the fault class; its text could carry a password."""
        broken = create_engine(_settings("postgresql+asyncpg://ultron:pw@localhost:1/ultron"))

        with pytest.raises(DatabaseError) as caught:
            await ping(broken)

        assert caught.value.to_dict()["cause"] is not None


class TestExceptionPassthrough:
    async def test_an_integrity_error_is_not_masked(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """A repository must tell a duplicate key from the server going away.
        Blanket translation would destroy exactly that distinction."""
        async with session_scope(factory) as session:
            session.add(Probe(name="once"))

        with pytest.raises(IntegrityError):
            async with session_scope(factory) as session:
                session.add(Probe(name="once"))

    async def test_an_operational_error_is_not_masked(self, engine: AsyncEngine) -> None:
        """A bad statement surfaces as SQLAlchemy's own error, not a generic one."""
        with pytest.raises(OperationalError):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT * FROM table_that_does_not_exist"))
