"""Sprint 71 (ADR-034) — asset state consolidation: fusion_strategy + asset_state_history.

Adds the per-tenant ``tenants.fusion_strategy`` JSONB config column (generalises
``position_strategy`` to govern the ``read_count × recency`` fusion of an asset's
bound-tag reads) and the ``asset_state_history`` hypertable that the consolidation
worker writes one fused snapshot per active asset per tick to.

Mirrors the ``asset_positions`` hypertable shape (migration 051): no FK on
``asset_id``/``zone_id``/``site_id`` (hypertable, ADR-013/014), ``id + time``
composite PK, RLS by ``tenant_id``. No retention policy is added here — TSL-only
``add_retention_policy`` is unavailable on the Apache-licensed TimescaleDB edition
used on Azure PG Flex; retention is an ops follow-up.

Revision ID: 058
Revises: 057
Create Date: 2026-06-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "058"
down_revision: str | None = "057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- tenants.fusion_strategy: per-tenant consolidation config (NULL = off) --
    op.add_column("tenants", sa.Column("fusion_strategy", JSONB, nullable=True))

    # -- asset_state_history hypertable: one fused snapshot per asset per tick --
    op.create_table(
        "asset_state_history",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        # No FK on asset_id/zone_id/site_id: hypertable, matches ADR-013/014.
        sa.Column("asset_id", UUID(as_uuid=True), nullable=False),
        sa.Column("frame", sa.String(16), nullable=False),
        sa.Column("zone_id", UUID(as_uuid=True), nullable=True),
        sa.Column("site_id", UUID(as_uuid=True), nullable=True),
        sa.Column("lat", sa.Float, nullable=True),
        sa.Column("lon", sa.Float, nullable=True),
        sa.Column("x", sa.Float, nullable=True),
        sa.Column("y", sa.Float, nullable=True),
        sa.Column("temperature_c", sa.Float, nullable=True),
        sa.Column("humidity_pct", sa.Float, nullable=True),
        sa.Column("sample_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tag_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.PrimaryKeyConstraint("id", "time"),
        sa.CheckConstraint(
            "frame IN ('reader','floor','geo','none')",
            name="ck_asset_state_history_frame",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
            name="ck_asset_state_history_confidence",
        ),
    )
    op.execute("SELECT create_hypertable('asset_state_history', 'time', if_not_exists => TRUE)")
    op.create_index(
        "ix_asset_state_history_by_asset",
        "asset_state_history",
        ["tenant_id", "asset_id", sa.text("time DESC")],
    )
    op.execute("ALTER TABLE asset_state_history ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_asset_state_history ON asset_state_history "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_asset_state_history ON asset_state_history")
    op.drop_index("ix_asset_state_history_by_asset", table_name="asset_state_history")
    op.drop_table("asset_state_history")
    op.drop_column("tenants", "fusion_strategy")
