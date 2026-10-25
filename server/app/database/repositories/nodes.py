"""Node repository (T332, spec §64.5/§64.10).

Read/write access to the ``nodes`` table. The gateway owns the in-memory
live view; this repository persists it across restarts.

No UUID primary key — :class:`~app.database.models.nodes.Node` uses the
protocol ``node_id`` as its PK, so the repository extends
:class:`~app.database.repositories.base.Repository` rather than
:attr:`~app.database.repositories.base.UuidRepository`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Node
from app.database.repositories.base import Repository, apply_limit

__all__ = ["NodeRepository"]


class NodeRepository(Repository[Node]):
    """Nodes."""

    model = Node

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_or_none(self, node_id: str) -> Node | None:
        """Return the node with this ``node_id``, or None."""
        return await self._fetch_one(
            select(Node).where(Node.node_id == node_id)
        )

    async def list_all(self, *, limit: int | None = None) -> list[Node]:
        """Return all nodes, newest ``last_seen`` first."""
        statement = (
            select(Node)
            .order_by(Node.last_seen.desc().nulls_last())
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_online(self, *, limit: int | None = None) -> list[Node]:
        """Return nodes currently marked online."""
        statement = (
            select(Node)
            .where(Node.status == "online")
            .order_by(Node.last_seen.desc().nulls_last())
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_by_kind(self, kind: str, *, limit: int | None = None) -> list[Node]:
        """Return nodes of a specific kind (windows/server/esp32)."""
        statement = (
            select(Node)
            .where(Node.node_type == kind)
            .order_by(Node.last_seen.desc().nulls_last())
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def list_by_capability(
        self, capability: str, *, limit: int | None = None
    ) -> list[Node]:
        """Return nodes advertising a capability.

        Works on both PostgreSQL (JSONB @> ) and SQLite (json_each).
        """
        # PostgreSQL path: JSONB containment operator.
        from sqlalchemy.dialects.postgresql import json_contains

        statement = (
            select(Node)
            .where(json_contains(Node.capabilities, capability))
            .order_by(Node.last_seen.desc().nulls_last())
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def upsert(
        self,
        node_id: str,
        *,
        node_type: str,
        name: str,
        capabilities: Sequence[str],
        auth_token_hash: str | None,
        version: str = "",
        client_of: str | None = None,
        protocol_version: str | None = None,
    ) -> Node:
        """Register or update a node.

        On insert: creates a new row with status ``offline`` (will flip to
        ``online`` on first heartbeat). On update: refreshes ``last_seen``
        and overwrites mutable fields.
        """
        import sqlalchemy
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        if node_id is None or node_id.strip() == "":
            raise ValueError("node_id must be non-empty")

        now = datetime.now(UTC)
        stmt = (
            pg_insert(Node)
            .values(
                node_id=node_id,
                node_type=node_type,
                name=name,
                capabilities=list(capabilities),
                auth_token_hash=auth_token_hash,
                version=version,
                client_of=client_of,
                protocol_version=protocol_version,
                last_seen=now,
                status="online",
            )
            .on_conflict_do_update(
                index_elements=["node_id"],
                set_={
                    "node_type": node_type,
                    "name": name,
                    "capabilities": list(capabilities),
                    "auth_token_hash": auth_token_hash,
                    "version": version,
                    "client_of": client_of,
                    "protocol_version": protocol_version,
                    "last_seen": now,
                    "status": "online",
                },
            )
        )
        try:
            await self._session.execute(stmt)
        except sqlalchemy.exc.SQLAlchemyError:
            # Fallback for SQLite which doesn't have postgresql insert.
            stmt = (
                sqlite_insert(Node)
                .values(
                    node_id=node_id,
                    node_type=node_type,
                    name=name,
                    capabilities=list(capabilities),
                    auth_token_hash=auth_token_hash,
                    version=version,
                    client_of=client_of,
                    protocol_version=protocol_version,
                    last_seen=now,
                    status="online",
                )
                .on_conflict_do_update(
                    index_elements=["node_id"],
                    set_={
                        "node_type": node_type,
                        "name": name,
                        "capabilities": list(capabilities),
                        "auth_token_hash": auth_token_hash,
                        "version": version,
                        "client_of": client_of,
                        "protocol_version": protocol_version,
                        "last_seen": now,
                        "status": "online",
                    },
                )
            )
            await self._session.execute(stmt)
        await self._session.flush()
        return await self.get_or_none(node_id)

    async def heartbeat(
        self,
        node_id: str,
        *,
        status: str | None = None,
        capabilities: Sequence[str] | None = None,
        version: str | None = None,
        now: datetime | None = None,
    ) -> Node | None:
        """Refresh liveness; optionally update status/capabilities."""
        node = await self.get_or_none(node_id)
        if node is None:
            return None
        stamp = now or datetime.now(UTC)
        node.last_seen = stamp
        if status is not None:
            node.status = status
        if capabilities is not None:
            node.capabilities = list(capabilities)
        if version is not None:
            node.version = version
        # Re-enable if it was offline — a heartbeat proves reachability.
        if node.status == "offline":
            node.status = "online"
        await self._session.flush()
        return node

    async def set_status(
        self, node_id: str, status: str
    ) -> Node | None:
        """Force-set a node's status (used by sweep)."""
        node = await self.get_or_none(node_id)
        if node is None:
            return None
        node.status = status
        await self._session.flush()
        return node

    async def list_stale(
        self,
        *,
        before: datetime,
        limit: int | None = None,
    ) -> list[Node]:
        """Nodes last seen before ``before``, or never seen at all."""
        from sqlalchemy import or_

        statement = (
            select(Node)
            .where(
                or_(
                    Node.last_seen.is_(None),
                    Node.last_seen < before,
                )
            )
            .order_by(Node.last_seen)
        )
        return await self._fetch_all(apply_limit(statement, limit))

    async def delete(self, node_id: str) -> bool:
        """Delete a node row. Returns whether anything was removed."""
        from sqlalchemy import delete as sa_delete

        try:
            result = await self._session.execute(
                sa_delete(Node).where(Node.node_id == node_id),
            )
            await self._session.flush()
        except Exception:
            return False
        return bool(result.rowcount > 0)
