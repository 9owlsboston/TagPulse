"""Sprint 15b — inventory tracking: products, lots, stock_items, stock_movements,
tag_data_mappings, and the ``stock_levels`` view.

Per docs/design/tracking-modes.md §4 and §11 Q2.

- ``products`` — SKU catalog (tenant-unique ``sku``).
- ``lots`` — production batches (unique per ``(tenant, product, lot_code)``).
- ``stock_items`` — per-tag inventory unit; ships with ``binding_value`` from day
  one (no deprecation dance — table is new in this sprint).
- ``stock_movements`` — append-only ledger; hypertable on ``occurred_at``.
- ``tag_data_mappings`` — per-(tenant, scope) mapping from ``tag_data`` keys to
  semantic fields (lot, expiry, mfg date, etc.). Most-specific scope wins at
  ingest.
- ``stock_levels`` view — live count per ``(product, lot, zone)``.

All tables enable RLS; policies use ``current_setting('app.current_tenant_id')``
for parity with Sprint 5.

Revision ID: 020
Revises: 019
Create Date: 2026-05-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- products --
    op.create_table(
        "products",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("gtin", sa.String(14), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("unit", sa.String(20), nullable=False, server_default="each"),
        sa.Column("attributes", JSONB, nullable=True),
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
        sa.UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_sku"),
        sa.CheckConstraint(
            "unit IN ('each','case','pallet')", name="ck_products_unit"
        ),
    )
    op.create_index(
        "ix_products_tenant_gtin",
        "products",
        ["tenant_id", "gtin"],
        postgresql_where=sa.text("gtin IS NOT NULL"),
    )
    op.execute("ALTER TABLE products ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_products ON products "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )

    # -- lots --
    op.create_table(
        "lots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lot_code", sa.String(64), nullable=False),
        sa.Column(
            "manufactured_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "product_id",
            "lot_code",
            name="uq_lots_tenant_product_code",
        ),
    )
    op.create_index(
        "ix_lots_expiring",
        "lots",
        ["tenant_id", "expires_at"],
        postgresql_where=sa.text("expires_at IS NOT NULL"),
    )
    op.execute("ALTER TABLE lots ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_lots ON lots "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )

    # -- stock_items --
    op.create_table(
        "stock_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("products.id"),
            nullable=False,
        ),
        sa.Column(
            "lot_id",
            UUID(as_uuid=True),
            sa.ForeignKey("lots.id"),
            nullable=True,
        ),
        sa.Column("binding_value", sa.String(256), nullable=False),
        sa.Column(
            "binding_kind", sa.String(8), nullable=False, server_default="epc"
        ),
        sa.Column(
            "state", sa.String(20), nullable=False, server_default="in_stock"
        ),
        sa.Column("current_zone_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.CheckConstraint(
            "binding_kind IN ('epc','tid')",
            name="ck_stock_items_binding_kind",
        ),
        sa.CheckConstraint(
            "state IN ('in_stock','in_transit','consumed','expired','lost')",
            name="ck_stock_items_state",
        ),
    )
    # Partial unique: one active stock_item per (tenant, kind, value).
    op.execute(
        "CREATE UNIQUE INDEX ix_stock_items_active_binding "
        "ON stock_items (tenant_id, binding_kind, binding_value) "
        "WHERE state NOT IN ('consumed','expired','lost')"
    )
    op.create_index(
        "ix_stock_items_aggregation",
        "stock_items",
        ["tenant_id", "product_id", "lot_id", "current_zone_id"],
    )
    op.execute("ALTER TABLE stock_items ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_stock_items ON stock_items "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )

    # -- stock_movements (hypertable) --
    op.create_table(
        "stock_movements",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("stock_item_id", UUID(as_uuid=True), nullable=False),
        sa.Column("from_zone_id", UUID(as_uuid=True), nullable=True),
        sa.Column("to_zone_id", UUID(as_uuid=True), nullable=True),
        sa.Column("movement_type", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False, server_default="1"),
        sa.Column("device_id", UUID(as_uuid=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", "occurred_at"),
        sa.CheckConstraint(
            "movement_type IN ('enter','exit','transfer','consume')",
            name="ck_stock_movements_type",
        ),
    )
    op.execute(
        "SELECT create_hypertable('stock_movements', 'occurred_at', "
        "if_not_exists => TRUE)"
    )
    op.create_index(
        "ix_stock_movements_by_item",
        "stock_movements",
        ["tenant_id", "stock_item_id", sa.text("occurred_at DESC")],
    )
    op.execute("ALTER TABLE stock_movements ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_stock_movements ON stock_movements "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )

    # -- tag_data_mappings (per design §11 Q2) --
    op.create_table(
        "tag_data_mappings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("scope_kind", sa.String(20), nullable=False),
        sa.Column("scope_id", UUID(as_uuid=True), nullable=True),
        sa.Column("semantic_field", sa.String(40), nullable=False),
        sa.Column("tag_data_key", sa.String(64), nullable=False),
        sa.Column("transform", sa.String(40), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "scope_kind IN ('tenant','device_type','product')",
            name="ck_tag_data_mappings_scope_kind",
        ),
        sa.CheckConstraint(
            "(scope_kind = 'tenant' AND scope_id IS NULL) "
            "OR (scope_kind != 'tenant' AND scope_id IS NOT NULL)",
            name="ck_tag_data_mappings_scope_id_consistency",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "scope_kind",
            "scope_id",
            "semantic_field",
            name="uq_tag_data_mappings_scope_field",
        ),
    )
    op.execute("ALTER TABLE tag_data_mappings ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_tag_data_mappings ON tag_data_mappings "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )

    # -- stock_levels view --
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


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS stock_levels")

    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_tag_data_mappings "
        "ON tag_data_mappings"
    )
    op.execute("ALTER TABLE tag_data_mappings DISABLE ROW LEVEL SECURITY")
    op.drop_table("tag_data_mappings")

    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_stock_movements "
        "ON stock_movements"
    )
    op.execute("ALTER TABLE stock_movements DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_stock_movements_by_item", table_name="stock_movements")
    op.drop_table("stock_movements")

    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_stock_items ON stock_items"
    )
    op.execute("ALTER TABLE stock_items DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_stock_items_aggregation", table_name="stock_items")
    op.execute("DROP INDEX IF EXISTS ix_stock_items_active_binding")
    op.drop_table("stock_items")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_lots ON lots")
    op.execute("ALTER TABLE lots DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_lots_expiring", table_name="lots")
    op.drop_table("lots")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_products ON products")
    op.execute("ALTER TABLE products DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_products_tenant_gtin", table_name="products")
    op.drop_table("products")
