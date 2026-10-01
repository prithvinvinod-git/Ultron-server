"""Alembic environment (T016).

Three decisions in here exist because getting them wrong is silently
destructive rather than loudly broken.

**The models package is imported explicitly.**
``target_metadata`` is ``app.database.session.Base.metadata``, and that registry
is only populated once the model modules have been imported -- SQLAlchemy has no
filesystem scan, so a metadata object with no imports in it is simply empty. An
``env.py`` that forgets this import still runs, still connects, and reports "no
changes detected" against a database full of tables. Worse, ``alembic revision
--autogenerate`` would then emit a revision that *drops every table*. The import
below is what makes the empty-metadata failure impossible; a unit test asserts
that this module's metadata is non-empty.

**The DSN comes from application settings, not from ``alembic.ini``.**
``app.config.get_settings().database.url`` is the single source of truth, so a
migration cannot be pointed at a different database than the application by
editing a config file. It also means no password is ever written into a tracked
file. The URL is masked in any log line.

**Enum columns get no ``CHECK`` constraint from autogenerate.**
The models store ``StrEnum`` members as their *names* in ``VARCHAR`` columns.
That is deliberate -- the names appear in API payloads and event types, and a
native PostgreSQL enum cannot be altered without locking a table -- but it means
the database itself will accept a string that is not a member. Rather than have
autogenerate invent constraints that would need a migration every time a member
is added, the first revision states the invariant once, explicitly, and each
enum's own docs record its member set. See ``versions/0001_initial.py``.

Offline mode (``alembic upgrade --sql``) is supported and needs no driver
installed, which is what makes a migration reviewable in CI without a database.
"""

from __future__ import annotations

import asyncio
import logging
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# The import is the point of these lines, not a side effect of them. See the
# module docstring: without it, autogenerate proposes dropping every table.
import app.database.models  # noqa: F401  (registers the tables on Base.metadata)
from app.config import get_settings
from app.database.session import Base, masked_url

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name, disable_existing_loggers=False)

config = context.config

_settings = get_settings()
_database = _settings.database

#: Recorded in the log so a reader of a failed run knows which backend and
#: extension state the revision was written against.
_target_note = (
    f"backend=postgresql pgvector={_database.enable_pgvector} "
    f"embeddings={_settings.embeddings.dimensions}"
)

# `alembic.ini` deliberately leaves this empty; see the module docstring.
config.set_main_option("sqlalchemy.url", _database.url)

target_metadata = Base.metadata

logger = logging.getLogger("alembic.env")

# Alembic echoes `sqlalchemy.url` at INFO when it builds the engine, so the
# masked form is what gets set and what can safely be logged. The module
# docstring promises the URL is never printed in the clear; this is that promise
# being kept rather than merely asserted.
logger.info("Targeting %s", masked_url(_database.url))
logger.info("Schema: %s", _target_note)


def include_object(
    object_: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object,
) -> bool:
    """Keep autogenerate from touching anything outside the ULTRON schema.

    Deliberately permissive by default. The one exclusion is the Alembic version
    table: ULTRON never models it, and if it were ever reflected as a difference
    a revision would try to manage rows that Alembic needs for its own bookkeeping.
    """
    return not (type_ == "table" and name == "alembic_version")


def _configure(connection: Connection | None = None, **kwargs: Any) -> None:
    """Apply the shared comparison settings for both offline and online runs."""
    settings: dict[str, Any] = {
        "target_metadata": target_metadata,
        "compare_type": True,
        "compare_server_default": True,
        "include_object": include_object,
        # Enum columns are VARCHAR holding StrEnum *names*; see the module
        # docstring. Nothing here should try to synthesise a native enum type.
        "render_as_batch": _database.url.startswith("sqlite"),
        **kwargs,
    }
    if connection is None:
        context.configure(**settings)
    else:
        context.configure(connection=connection, **settings)


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting.

    ``literal_binds`` is on so the output is directly runnable, and
    ``dialect_opts`` supplies the async driver's name -- without it Alembic
    guesses ``postgresql://`` and emits driver-specific SQL for a dialect that
    is not the one in use.
    """
    _configure(
        # Offline mode has no connection and no engine, so Alembic cannot read
        # the URL off one. Handing it `url` explicitly is what tells it which
        # dialect to render for; without this, configure() raises
        # "Connection, url, or dialect_name is required" and emits nothing.
        url=_database.url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations on an already-open synchronous connection."""
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Open an async engine, adapt it, and run the migrations.

    The engine is built by Alembic's own ``async_engine_from_config`` rather than
    by ``app.database.create_engine``, because a migration must not inherit the
    application's pool sizing -- it opens one connection, runs, and closes, and a
    10-connection pool for that is waste. The URL still comes from settings.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
            await connection.commit()
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for a live database."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
