"""Sprint 15b — Phase D hardening (post-audit follow-ups).

Closes the gaps surfaced by the Phase D audit:

* ``stock_items.lot_id`` now ``ON DELETE SET NULL`` so cascading a lot deletion
  cannot blow up unrelated stock_items (and so ``products`` deletes don't
  trip a downstream FK violation through the lots cascade).
* ``stock_movements.stock_item_id`` gains an FK with ``ON DELETE RESTRICT`` so
  the ledger can never be orphaned. Timescale ≥2.0 supports FKs from a
  hypertable to a regular table.
* Per-side zone indexes on ``stock_movements`` so the ``WHERE from_zone_id = X
  OR to_zone_id = X`` filter on the read API is satisfiable by index access.
* ``stock_levels`` view is recreated with ``WITH (security_invoker=true)`` so
  the view honors RLS once we wire ``app.current_tenant_id`` (today the GUC is
  never set, but pin the right semantics now).
* ``tag_data_mappings.scope_kind`` enum tightened to ``('tenant','product')``
  — the ``device_type`` scope was reachable from the API but unimplemented in
  ingestion (no scope_id semantics defined for it). Drop until we have a
  device_types table.

Revision ID: 021
Revises: 020
Create Date: 2026-05-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- stock_items.lot_id -> ON DELETE SET NULL --
    op.execute(
        "ALTER TABLE stock_items DROP CONSTRAINT stock_items_lot_id_fkey"
    )
    op.create_foreign_key(
        "stock_items_lot_id_fkey",
        "stock_items",
        "lots",
        ["lot_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # -- stock_movements.stock_item_id FK + per-zone indexes --
    op.create_foreign_key(
        "stock_movements_stock_item_id_fkey",
        "stock_movements",
        "stock_items",
        ["stock_item_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_stock_movements_from_zone",
        "stock_movements",
        ["tenant_id", "from_zone_id", sa.text("occurred_at DESC")],
        postgresql_where=sa.text("from_zone_id IS NOT NULL"),
    )
    op.create_index(
        "ix_stock_movements_to_zone",
        "stock_movements",
        ["tenant_id", "to_zone_id", sa.text("occurred_at DESC")],
        postgresql_where=sa.text("to_zone_id IS NOT NULL"),
    )

    # -- stock_levels view with security_invoker --
    op.execute("DROP VIEW IF EXISTS stock_levels")
    op.execute(
        """
        CREATE VIEW stock_levels
        WITH (security_invoker = true)
        AS
        SELECT
            si.tenant_id,
            si.product_id,
            si.lot_id,
            si.current_zone_id,
            COUNT(*)::bigint AS quantity
        FROM stock_items si
        WHERE si.state = 'in_stock'
        GROUP BY si.tenant_id, si.product_id, si.lot_id, si.current_zone_id
        """
    )

    # -- tag_data_mappings.scope_kind: drop 'device_type' --
    op.execute(
        "ALTER TABLE tag_data_mappings "
        "DROP CONSTRAINT ck_tag_data_mappings_scope_kind"
    )
    op.execute(
        "ALTER TABLE tag_data_mappings ADD CONSTRAINT "
        "ck_tag_data_mappings_scope_kind "
        "CHECK (scope_kind IN ('tenant','product'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE tag_data_mappings "
        "DROP CONSTRAINT ck_tag_data_mappings_scope_kind"
    )
    op.execute(
        "ALTER TABLE tag_data_mappings ADD CONSTRAINT "
        "ck_tag_data_mappings_scope_kind "
        "CHECK (scope_kind IN ('tenant','device_type','product'))"
    )

    op.execute("DROP VIEW IF EXISTS stock_levels")
    op.execute(
        """
        CREATE VIEW stock_levels AS
        SELECT
            si.tenant_id,
            si.product_id,
            si.lot_id,
            si.current_zone_id,
            COUNT(*)::bigint AS quantity
        FROM stock_items si
        WHERE si.state = 'in_stock'
        GROUP BY si.tenant_id, si.product_id, si.lot_id, si.current_zone_id
        """
    )

    op.drop_index("ix_stock_movements_to_zone", table_name="stock_movements")
    op.drop_index("ix_stock_movements_from_zone", table_name="stock_movements")
    op.execute(
        "ALTER TABLE stock_movements "
        "DROP CONSTRAINT stock_movements_stock_item_id_fkey"
    )

    op.execute(
        "ALTER TABLE stock_items DROP CONSTRAINT stock_items_lot_id_fkey"
    )
    op.create_foreign_key(
        "stock_items_lot_id_fkey",
        "stock_items",
        "lots",
        ["lot_id"],
        ["id"],
    )
