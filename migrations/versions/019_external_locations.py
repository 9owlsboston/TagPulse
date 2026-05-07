"""Sprint 15 Phase C — external_locations hypertable.

Adds the ``external_locations`` hypertable for non-RFID position sources
(TMS pushes, manual carrier check-ins, etc.) per
docs/design/mobile-carriers-and-manifests.md §10 Q5.

- Tenant-scoped, RLS by ``tenant_id``.
- Hypertable on ``recorded_at`` to match ``device_telemetry`` defaults.
- Index on ``(tenant_id, asset_id, recorded_at DESC)`` powers the
  "latest external position per asset" query that ``asset_current_location``
  will UNION with the latest tag-read-derived position.

Revision ID: 019
Revises: 018
Create Date: 2026-05-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "external_locations",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("asset_id", UUID(as_uuid=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latitude", sa.Float, nullable=False),
        sa.Column("longitude", sa.Float, nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("accuracy_meters", sa.Float, nullable=True),
        sa.Column("speed_kph", sa.Float, nullable=True),
        sa.Column("heading_deg", sa.Float, nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.PrimaryKeyConstraint("id", "recorded_at"),
        sa.CheckConstraint(
            "latitude BETWEEN -90 AND 90", name="ck_external_locations_lat"
        ),
        sa.CheckConstraint(
            "longitude BETWEEN -180 AND 180", name="ck_external_locations_lon"
        ),
    )
    op.execute(
        "SELECT create_hypertable('external_locations', 'recorded_at', "
        "if_not_exists => TRUE)"
    )
    op.create_index(
        "ix_external_locations_by_asset",
        "external_locations",
        ["tenant_id", "asset_id", sa.text("recorded_at DESC")],
    )
    op.create_index(
        "ix_external_locations_source",
        "external_locations",
        ["tenant_id", "source", sa.text("recorded_at DESC")],
    )
    op.execute("ALTER TABLE external_locations ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_external_locations ON external_locations "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_external_locations "
        "ON external_locations"
    )
    op.execute("ALTER TABLE external_locations DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "ix_external_locations_source", table_name="external_locations"
    )
    op.drop_index(
        "ix_external_locations_by_asset", table_name="external_locations"
    )
    op.drop_table("external_locations")
