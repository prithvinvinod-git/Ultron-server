"""Create the `nodes` table (T332, spec §64.5).

Merges the legacy `devices` table (T161) and the in-memory `NodeGateway`
(T141) into one table. Existing ESP32 device rows become `kind=esp32` node
rows (D021) without a data migration — the `node_type` column defaults to
`future` and callers interpret missing/legacy rows appropriately.

Revision ID: 0003_nodes
Revises: 0002_static_principal
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0003_nodes"
down_revision: str | None = "0002_static_principal"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "nodes",
        sa.Column(
            "node_id",
            sa.String(128),
            primary_key=True,
            comment="The node's chosen identifier (e.g. 'windows-1', 'esp32-abc').",
        ),
        sa.Column(
            "node_type",
            sa.String(32),
            nullable=False,
            server_default="future",
            comment="windows | server | esp32 | future",
        ),
        sa.Column(
            "name",
            sa.String(128),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "capabilities",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="offline",
            comment="online | offline | degraded",
        ),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "version",
            sa.String(64),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "client_of",
            sa.String(64),
            nullable=True,
        ),
        sa.Column(
            "protocol_version",
            sa.String(32),
            nullable=True,
        ),
        sa.Column(
            "auth_token_hash",
            sa.String(255),
            nullable=True,
            unique=True,
            comment="Digest of the node credential; hashed at rest.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_nodes_status_last_seen", "nodes", ["status", "last_seen"])
    op.create_index("ix_nodes_type_name", "nodes", ["node_type", "name"])


def downgrade() -> None:
    op.drop_index("ix_nodes_type_name", table_name="nodes")
    op.drop_index("ix_nodes_status_last_seen", table_name="nodes")
    op.drop_table("nodes")