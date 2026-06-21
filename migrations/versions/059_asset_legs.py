"""Sprint 72 (ADR-034 Phase 2) — asset_legs: transit legs derived from custody.

One row per transit leg (the ``geo``-frame interval between two facility frames),
opened/closed by the ``AssetLegTracker`` from Phase-1 ``ASSET_CUSTODY_CHANGED``
events. Regular tenant-scoped table (RLS), not a hypertable — leg cardinality is
low (a handful per asset per journey). The leg env envelope + cold-chain SLA
summary are computed on close from ``asset_state_history`` (migration 058).

Revision ID: 059
Revises: 058
Create Date: 2026-06-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "059"
down_revision: str | None = "058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_legs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # No FK on asset_id/zone/site: matches the ADR-013/014 no-FK convention.
        sa.Column("asset_id", UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(8), nullable=False, server_default="open"),
        sa.Column("origin_zone_id", UUID(as_uuid=True), nullable=True),
        sa.Column("origin_site_id", UUID(as_uuid=True), nullable=True),
        sa.Column("dest_zone_id", UUID(as_uuid=True), nullable=True),
        sa.Column("dest_site_id", UUID(as_uuid=True), nullable=True),
        sa.Column("departed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("arrived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_lat", sa.Float, nullable=True),
        sa.Column("last_lon", sa.Float, nullable=True),
        sa.Column("temp_min_c", sa.Float, nullable=True),
        sa.Column("temp_max_c", sa.Float, nullable=True),
        sa.Column("temp_mean_c", sa.Float, nullable=True),
        sa.Column("humidity_min", sa.Float, nullable=True),
        sa.Column("humidity_max", sa.Float, nullable=True),
        sa.Column("excursion_s", sa.Integer, nullable=True),
        sa.Column("in_range_pct", sa.Float, nullable=True),
        sa.Column("sla_breached", sa.Boolean, nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('open','closed')", name="ck_asset_legs_status"),
    )
    op.create_index(
        "ix_asset_legs_by_asset",
        "asset_legs",
        ["tenant_id", "asset_id", sa.text("departed_at DESC")],
    )
    # At most one open leg per asset.
    op.create_index(
        "ix_asset_legs_open",
        "asset_legs",
        ["tenant_id", "asset_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.execute("ALTER TABLE asset_legs ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_asset_legs ON asset_legs "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_asset_legs ON asset_legs")
    op.drop_index("ix_asset_legs_open", table_name="asset_legs")
    op.drop_index("ix_asset_legs_by_asset", table_name="asset_legs")
    op.drop_table("asset_legs")
