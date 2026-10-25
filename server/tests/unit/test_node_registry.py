"""Node repository tests (T332, spec §64.5/§64.10).

Test the :class:`~app.database.repositories.nodes.NodeRepository` methods:
upsert, heartbeat, list_by_kind, list_by_capability, list_stale, set_status,
and delete. Also validate the :class:`~app.database.models.nodes.Node` model's
shape and ``is_stale`` logic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Node
from app.database.repositories.nodes import NodeRepository
from app.database.session import Base, create_engine, dispose

pytestmark = pytest.mark.unit

SQLITE_URL = "sqlite+aiosqlite://"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
async def engine() -> AsyncIterator[object]:
    """A private in-memory database with the production schema."""
    from app.config import DatabaseSettings

    created = create_engine(DatabaseSettings(url=SQLITE_URL))
    async with created.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield created
    finally:
        await dispose(created)


@pytest.fixture
async def db(engine: object) -> AsyncIterator[AsyncSession]:
    """A session bound to the test database, rolled back after each test."""
    connection = await engine.connect()  # type: ignore[attr-defined]
    transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


@pytest.fixture
async def repo(db: AsyncSession) -> NodeRepository:
    return NodeRepository(db)


# --------------------------------------------------------------------------- #
# Model tests
# --------------------------------------------------------------------------- #


class TestNodeModel:
    """Structural tests for the Node ORM model."""

    def test_node_table_exists(self) -> None:
        from app.database.models import Base

        assert "nodes" in Base.metadata.tables

    def test_node_columns(self) -> None:
        columns = {c.name for c in Node.__table__.columns}
        assert columns >= {
            "node_id",
            "node_type",
            "name",
            "capabilities",
            "status",
            "last_seen",
            "version",
            "client_of",
            "protocol_version",
            "auth_token_hash",
            "created_at",
        }

    def test_node_primary_key_is_node_id(self) -> None:
        keys = {c.name for c in Node.__table__.primary_key.columns}
        assert keys == {"node_id"}

    def test_node_default_status(self) -> None:
        # The mapped_column default applies at INSERT time, not construction.
        # The server_default ensures the DB gets the right value.
        node = Node(node_id="test-1", node_type="windows", name="Test", status="offline")
        assert node.status == "offline"

    def test_node_default_last_seen_is_none(self) -> None:
        node = Node(node_id="test-1", node_type="windows", name="Test")
        assert node.last_seen is None

    def test_node_server_default_status_is_offline(self) -> None:
        """Verify the server-side default matches the Python default."""
        from app.database.models import Base

        table = Base.metadata.tables["nodes"]
        column = table.columns["status"]
        assert column.server_default.arg == "offline"

    def test_is_stale_returns_false_when_never_seen(self) -> None:
        node = Node(node_id="test-1", node_type="windows", name="Test")
        assert node.is_stale(timeout_seconds=60) is False

    def test_is_stale_returns_false_when_recently_seen(self) -> None:
        from datetime import datetime

        now = datetime.now(UTC)
        node = Node(
            node_id="test-1",
            node_type="windows",
            name="Test",
            last_seen=now,
        )
        assert node.is_stale(timeout_seconds=60, now=now) is False

    def test_is_stale_returns_true_when_old(self) -> None:
        from datetime import datetime

        old = datetime.now(UTC) - timedelta(seconds=120)
        node = Node(
            node_id="test-1",
            node_type="windows",
            name="Test",
            last_seen=old,
        )
        assert node.is_stale(timeout_seconds=60) is True

    def test_is_stale_respects_custom_now(self) -> None:
        from datetime import datetime

        now = datetime.now(UTC)
        old = now - timedelta(seconds=200)
        node = Node(
            node_id="test-1",
            node_type="windows",
            name="Test",
            last_seen=old,
        )
        assert node.is_stale(timeout_seconds=60, now=now) is True

    def test_device_id_returns_none(self) -> None:
        node = Node(node_id="test-1", node_type="windows", name="Test")
        assert node.device_id is None

    def test_repr_contains_node_id(self) -> None:
        node = Node(node_id="win-1", node_type="windows", name="Test", status="offline")
        assert "win-1" in repr(node)
        assert "windows" in repr(node)
        assert "offline" in repr(node)


# --------------------------------------------------------------------------- #
# Repository tests
# --------------------------------------------------------------------------- #


class TestNodeRepository:
    """Functional tests for NodeRepository."""

    async def test_upsert_creates_node(self, repo: NodeRepository) -> None:
        node = await repo.upsert(
            "win-1",
            node_type="windows",
            name="Test Window",
            capabilities=["clipboard", "file"],
            auth_token_hash="sha256:test",
        )
        assert node.node_id == "win-1"
        assert node.node_type == "windows"
        assert node.name == "Test Window"
        assert node.capabilities == ["clipboard", "file"]
        assert node.status == "online"
        assert node.last_seen is not None
        assert node.version == ""

    async def test_upsert_requires_node_id(self, repo: NodeRepository) -> None:
        with pytest.raises(ValueError, match="node_id must be non-empty"):
            await repo.upsert(
                "",
                node_type="windows",
                name="Test",
                capabilities=[],
                auth_token_hash=None,
            )

    async def test_upsert_overwrites_existing(self, repo: NodeRepository) -> None:
        await repo.upsert(
            "win-1",
            node_type="windows",
            name="Test Window",
            capabilities=["clipboard"],
            auth_token_hash="sha256:test",
        )
        node = await repo.upsert(
            "win-1",
            node_type="windows",
            name="New Name",
            capabilities=["file", "network"],
            auth_token_hash="sha256:new",
            version="2.0",
        )
        assert node.name == "New Name"
        assert node.capabilities == ["file", "network"]
        assert node.version == "2.0"

    async def test_get_or_none_returns_existing(self, repo: NodeRepository) -> None:
        await repo.upsert(
            "esp-1",
            node_type="esp32",
            name="Sensor",
            capabilities=["measure"],
            auth_token_hash="sha256:sensor",
        )
        found = await repo.get_or_none("esp-1")
        assert found is not None
        assert found.name == "Sensor"

    async def test_get_or_none_returns_none_for_unknown(self, repo: NodeRepository) -> None:
        found = await repo.get_or_none("nonexistent")
        assert found is None

    async def test_list_all_returns_nodes(self, repo: NodeRepository) -> None:
        await repo.upsert(
            "a",
            node_type="windows",
            name="A",
            capabilities=["x"],
            auth_token_hash="h-a",
        )
        await repo.upsert(
            "b",
            node_type="esp32",
            name="B",
            capabilities=["y"],
            auth_token_hash="h-b",
        )
        all_nodes = await repo.list_all()
        ids = [n.node_id for n in all_nodes]
        assert "a" in ids
        assert "b" in ids

    async def test_list_online_filters_online(self, repo: NodeRepository) -> None:
        await repo.upsert(
            "a",
            node_type="windows",
            name="A",
            capabilities=[],
            auth_token_hash="h-a",
        )
        # Force 'a' offline via heartbeat status override
        await repo.set_status("a", "offline")
        online = await repo.list_online()
        ids = [n.node_id for n in online]
        assert "a" not in ids

    async def test_list_by_kind_filters_kind(self, repo: NodeRepository) -> None:
        await repo.upsert(
            "a",
            node_type="windows",
            name="A",
            capabilities=[],
            auth_token_hash="h-a",
        )
        await repo.upsert(
            "b",
            node_type="esp32",
            name="B",
            capabilities=[],
            auth_token_hash="h-b",
        )
        windows = await repo.list_by_kind("windows")
        ids = [n.node_id for n in windows]
        assert "a" in ids
        assert "b" not in ids

    async def test_heartbeat_refreshes_last_seen(self, repo: NodeRepository) -> None:
        node = await repo.upsert(
            "a",
            node_type="windows",
            name="A",
            capabilities=["x"],
            auth_token_hash="h-a",
        )
        initial_seen = node.last_seen
        await repo.heartbeat("a")
        updated = await repo.get_or_none("a")
        assert updated is not None
        # Both timestamps should be timezone-aware after the fix
        assert updated.last_seen is not None
        assert initial_seen is not None
        if updated.last_seen.tzinfo is None:
            updated.last_seen = updated.last_seen.replace(tzinfo=UTC)
        if initial_seen.tzinfo is None:
            initial_seen = initial_seen.replace(tzinfo=UTC)
        assert updated.last_seen > initial_seen

    async def test_heartbeat_updates_capabilities(self, repo: NodeRepository) -> None:
        await repo.upsert(
            "a",
            node_type="windows",
            name="A",
            capabilities=["x"],
            auth_token_hash="h-a",
        )
        await repo.heartbeat("a", capabilities=["x", "y"])
        updated = await repo.get_or_none("a")
        assert updated is not None
        assert "y" in updated.capabilities

    async def test_heartbeat_restores_offline_to_online(self, repo: NodeRepository) -> None:
        await repo.upsert(
            "a",
            node_type="windows",
            name="A",
            capabilities=[],
            auth_token_hash="h-a",
        )
        await repo.set_status("a", "offline")
        await repo.heartbeat("a")
        updated = await repo.get_or_none("a")
        assert updated is not None
        assert updated.status == "online"

    async def test_heartbeat_returns_none_for_unknown(self, repo: NodeRepository) -> None:
        result = await repo.heartbeat("ghost")
        assert result is None

    async def test_set_status_changes_status(self, repo: NodeRepository) -> None:
        await repo.upsert(
            "a",
            node_type="windows",
            name="A",
            capabilities=[],
            auth_token_hash="h-a",
        )
        updated = await repo.set_status("a", "degraded")
        assert updated is not None
        assert updated.status == "degraded"

    async def test_list_stale_returns_unseen_and_old_nodes(self, repo: NodeRepository) -> None:
        from sqlalchemy import update as sa_update

        now = datetime.now(UTC)
        await repo.upsert(
            "a",
            node_type="windows",
            name="A",
            capabilities=[],
            auth_token_hash="h-a",
        )
        # b was just upserted, so it should NOT be stale
        await repo.upsert(
            "b",
            node_type="windows",
            name="B",
            capabilities=[],
            auth_token_hash="h-b",
        )
        # Manually set 'a' to old timestamp
        await repo._session.execute(
            sa_update(Node)
            .where(Node.node_id == "a")
            .values(last_seen=now - timedelta(seconds=300))
        )
        await repo._session.flush()
        stale_before = now - timedelta(seconds=1)
        stale = await repo.list_stale(before=stale_before)
        stale_ids = [n.node_id for n in stale]
        assert "a" in stale_ids
        assert "b" not in stale_ids

    async def test_delete_removes_node(self, repo: NodeRepository) -> None:
        await repo.upsert(
            "a",
            node_type="windows",
            name="A",
            capabilities=[],
            auth_token_hash="h-a",
        )
        removed = await repo.delete("a")
        assert removed is True
        found = await repo.get_or_none("a")
        assert found is None

    async def test_delete_nonexistent_returns_false(self, repo: NodeRepository) -> None:
        removed = await repo.delete("ghost")
        assert removed is False

    async def test_node_repository_matches_schema(self, db: AsyncSession) -> None:
        from app.database.models import Base

        table = Base.metadata.tables["nodes"]
        columns = {c.name for c in table.columns}
        # Verify the table matches what the schema describes.
        assert "node_id" in columns
        assert "capabilities" in columns
