"""Sprint 15 — shared substrate: sites, zones, tenant tracking_modes, device mobility.

Revision ID: 017
Revises: 016
Create Date: 2026-05-02

Adds:
- ``tenants.tracking_modes`` JSONB (default ``["asset"]``).
- ``devices.mobility`` (``fixed`` | ``mobile``, default ``fixed``).
- ``sites`` table (tenant-scoped, RLS).
- ``zones`` table (reader-bound; polygon column reserved for Sprint 17a; RLS).

See docs/design/assets-and-zones.md, docs/design/tracking-modes.md,
docs/design/mobile-carriers-and-manifests.md.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic
revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- Tenant tracking modes --
    op.add_column(
        "tenants",
        sa.Column(
            "tracking_modes",
            JSONB,
            nullable=False,
            server_default=sa.text("'[\"asset\"]'::jsonb"),
        ),
    )

    # -- Device mobility flag --
    op.add_column(
        "devices",
        sa.Column(
            "mobility",
            sa.String(16),
            nullable=False,
            server_default="fixed",
        ),
    )
    op.create_check_constraint(
        "ck_devices_mobility",
        "devices",
        "mobility IN ('fixed','mobile')",
    )

    # -- Sites --
    op.create_table(
        "sites",
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
        sa.Column("address", sa.Text, nullable=True),
        sa.Column(
            "default_timezone",
            sa.String(64),
            nullable=False,
            server_default="UTC",
        ),
        sa.Column("metadata", JSONB, nullable=True),
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
        sa.UniqueConstraint("tenant_id", "name", name="uq_sites_tenant_name"),
    )
    op.create_index("ix_sites_tenant", "sites", ["tenant_id"])
    op.execute("ALTER TABLE sites ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_sites ON sites "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )

    # -- Zones (reader-bound; polygon reserved for Sprint 17a) --
    op.create_table(
        "zones",
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
        sa.Column(
            "site_id",
            UUID(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("fixed_reader_ids", JSONB, nullable=True),
        sa.Column("polygon_geojson", JSONB, nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
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
        sa.UniqueConstraint("site_id", "name", name="uq_zones_site_name"),
    )
    op.create_index("ix_zones_tenant", "zones", ["tenant_id"])
    op.create_index("ix_zones_site", "zones", ["site_id"])
    op.create_check_constraint(
        "ck_zones_kind_payload",
        "zones",
        "(kind = 'reader_bound' AND fixed_reader_ids IS NOT NULL)"
        " OR (kind = 'geofence' AND polygon_geojson IS NOT NULL)",
    )
    op.execute("ALTER TABLE zones ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_zones ON zones "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_zones ON zones")
    op.execute("ALTER TABLE zones DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_zones_site", table_name="zones")
    op.drop_index("ix_zones_tenant", table_name="zones")
    op.drop_table("zones")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_sites ON sites")
    op.execute("ALTER TABLE sites DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_sites_tenant", table_name="sites")
    op.drop_table("sites")

    op.drop_constraint("ck_devices_mobility", "devices", type_="check")
    op.drop_column("devices", "mobility")

    op.drop_column("tenants", "tracking_modes")
