"""Sprint 41 Phase H: drop assets.asset_type column + close ADR 019.

Revision ID: 041
Revises: 040
Create Date: 2026-06-15

Implements [ADR-019 Categories](../../docs/adr/019-categories.md) close-out
(roadmap Sprint 41 Phase H). After Sprint 34 introduced
``categories`` + ``assets.category_id`` (migration 037) as a nullable FK,
the legacy ``assets.asset_type`` String column was kept as a compatibility
shadow. UI (Sprint 36) and clients (Sprint 37) have since moved to
``category_id``. This migration:

1. Safety-net backfill — for any asset rows still missing
   ``category_id`` (newly-inserted rows since 037, or rows whose 037
   backfill was skipped because ``asset_type`` was empty), create a
   per-tenant ``_uncategorized`` Category and point the row at it.
2. Promote ``assets.category_id`` to ``NOT NULL``.
3. Drop the ``ix_assets_tenant_type`` index (was on
   ``(tenant_id, asset_type)``).
4. Drop the ``asset_type`` column.

The downgrade re-adds the column nullable, backfills from
``categories.name``, promotes to ``NOT NULL``, and recreates the index.
Both directions are idempotent on the column / index existence checks.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision: str = "041"
down_revision: str | None = "040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    has_asset_type = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'assets' AND column_name = 'asset_type'"
        )
    ).scalar()

    # -- 1a. First attempt: backfill any NULL category_id rows from
    # their (still-present) asset_type via the existing 037-style join.
    # No-op if asset_type column is already gone.
    if has_asset_type:
        op.execute(
            """
            UPDATE assets
               SET category_id = c.id
              FROM categories c
             WHERE c.tenant_id = assets.tenant_id
               AND c.name = assets.asset_type
               AND assets.category_id IS NULL
               AND assets.asset_type IS NOT NULL
               AND assets.asset_type <> ''
            """
        )

    # -- 1b. Fallback: any rows still with NULL category_id (empty
    # asset_type before 037, or rows inserted after 037 without
    # category_id) get pointed at a per-tenant ``_uncategorized``
    # Category. Created on demand, idempotent on the unique
    # (tenant_id, name) constraint.
    op.execute(
        """
        INSERT INTO categories (tenant_id, name, category_type, required_tags)
        SELECT DISTINCT tenant_id, '_uncategorized', 'object', 1
          FROM assets
         WHERE category_id IS NULL
        ON CONFLICT (tenant_id, name) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE assets
           SET category_id = c.id
          FROM categories c
         WHERE c.tenant_id = assets.tenant_id
           AND c.name = '_uncategorized'
           AND assets.category_id IS NULL
        """
    )

    # -- 2. Promote category_id to NOT NULL. --
    op.alter_column("assets", "category_id", nullable=False)

    # -- 3. Drop the legacy index. --
    op.execute("DROP INDEX IF EXISTS ix_assets_tenant_type")

    # -- 4. Drop the column. --
    if has_asset_type:
        op.drop_column("assets", "asset_type")


def downgrade() -> None:
    bind = op.get_bind()
    has_asset_type = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'assets' AND column_name = 'asset_type'"
        )
    ).scalar()

    if not has_asset_type:
        op.add_column(
            "assets",
            sa.Column("asset_type", sa.String(50), nullable=True),
        )
        # Backfill from the Category name (which is what 037 used to
        # populate it in the first place).
        op.execute(
            """
            UPDATE assets
               SET asset_type = c.name
              FROM categories c
             WHERE c.id = assets.category_id
            """
        )
        # Any remaining NULLs (defensive — shouldn't happen if step 2
        # above ran cleanly) fall back to a sentinel.
        op.execute("UPDATE assets SET asset_type = '_uncategorized' WHERE asset_type IS NULL")
        op.alter_column("assets", "asset_type", nullable=False)
        op.create_index("ix_assets_tenant_type", "assets", ["tenant_id", "asset_type"])

    op.alter_column("assets", "category_id", nullable=True)
