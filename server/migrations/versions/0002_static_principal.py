"""Seed the static principal's user row.

``require_principal`` attributes every request to one fixed administrator
(``STATIC_PRINCIPAL_ID`` in ``app.api.dependencies``) while authentication is
being replaced by an external provider. Any write that stores
``principal.user_id`` -- conversations, schedules, sessions -- has a foreign key
onto ``users``, so on a freshly migrated database every such write failed with a
409 (``IntegrityError``) because the referenced row did not exist.

Seeding the row here makes a new database usable without a manual insert. It is
idempotent: ``ON CONFLICT (id) DO NOTHING`` leaves an existing account (for
example one whose username was changed) untouched.

Revision ID: 0002_static_principal
Revises: 0001_initial
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_static_principal"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATIC_PRINCIPAL_ID = "00000000-0000-0000-0000-000000000001"
_STATIC_PRINCIPAL_USERNAME = "admin prithvin"


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO users (id, username, is_active, is_superuser, is_service_account)
        VALUES ('{_STATIC_PRINCIPAL_ID}', '{_STATIC_PRINCIPAL_USERNAME}', true, true, false)
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM users WHERE id = '{_STATIC_PRINCIPAL_ID}'")
