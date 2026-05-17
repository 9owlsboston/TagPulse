"""Sprint 34: Categories as a first-class entity + assets.category_id FK.

Revision ID: 037
Revises: 036
Create Date: 2026-05-17

Implements [ADR-019 Categories](../../docs/adr/019-categories.md).

Adds:

- ``categories`` table — tenant-scoped, RLS-protected, with
  ``UNIQUE(tenant_id, name)``. ``category_type`` is one of
  ``liquid_container`` / ``reference_tag`` / ``rti_container`` /
  ``object`` (CHECK constraint; immutability enforced in the API
  layer, not here). ``required_tags`` defaults to 1 and must be
  positive.
- ``assets.category_id`` — nullable FK to ``categories(id)`` with
  ``ON DELETE RESTRICT``. Indexed for the per-category Asset filter.
- Backfill — every distinct ``(tenant_id, asset_type)`` pair in the
  existing ``assets`` table becomes a ``category_type='object'`` row
  with ``required_tags=1`` and ``name=asset_type``; the asset's new
  ``category_id`` is set to that row.

Note: ``assets.asset_type`` stays in place this sprint as a
compatibility shadow per the ADR ("one release"). It is dropped in a
future migration once the UI + clients have switched to
``category_id``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic
revision: str = "037"
down_revision: str | None = "036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CATEGORY_TYPES = ("liquid_container", "reference_tag", "rti_container", "object")


def upgrade() -> None:
    # -- 1. Create categories table --
    op.create_table(
        "categories",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("sku_upc", sa.String(64), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category_type", sa.String(32), nullable=False),
        sa.Column(
            "required_tags",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_categories_tenant_name"),
    )
    op.create_index("ix_categories_tenant", "categories", ["tenant_id"])
    op.create_check_constraint(
        "ck_categories_type",
        "categories",
        "category_type IN (" + ", ".join(f"'{value}'" for value in _CATEGORY_TYPES) + ")",
    )
    op.create_check_constraint(
        "ck_categories_required_tags_positive",
        "categories",
        "required_tags >= 1",
    )
    op.execute("ALTER TABLE categories ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_categories ON categories "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )

    # -- 2. Backfill from existing assets.asset_type --
    # One Category row per distinct (tenant_id, asset_type). Default
    # category_type='object' / required_tags=1 per the ADR. Migrations
    # run as the table owner and bypass the RLS policy (Postgres default
    # without FORCE ROW LEVEL SECURITY), so no per-tenant SET is
    # required.
    op.execute(
        """
        INSERT INTO categories (tenant_id, name, category_type, required_tags)
        SELECT DISTINCT tenant_id, asset_type, 'object', 1
          FROM assets
         WHERE asset_type IS NOT NULL AND asset_type <> ''
        ON CONFLICT (tenant_id, name) DO NOTHING
        """
    )

    # -- 3. Add nullable assets.category_id FK + backfill the pointer --
    op.add_column(
        "assets",
        sa.Column(
            "category_id",
            UUID(as_uuid=True),
            sa.ForeignKey("categories.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index("ix_assets_category", "assets", ["category_id"])
    op.execute(
        """
        UPDATE assets
           SET category_id = c.id
          FROM categories c
         WHERE c.tenant_id = assets.tenant_id
           AND c.name = assets.asset_type
        """
    )


def downgrade() -> None:
    op.drop_index("ix_assets_category", table_name="assets")
    op.drop_column("assets", "category_id")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_categories ON categories")
    op.execute("ALTER TABLE categories DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_categories_tenant", table_name="categories")
    op.drop_table("categories")
