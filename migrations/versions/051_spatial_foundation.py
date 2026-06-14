"""Sprint 59 Track 2 (59.9) — spatial foundation schema.

Lands the *schema* half of [ADR-024](../../docs/adr/024-position-estimation.md)
(see the Sprint 59 amendment v2); the RSSI estimator itself stays deferred to a
candidate Sprint 61 spike. Four additive changes, no behavioural change to
existing reads/writes:

- ``antennas`` — new normalized table holding **per-antenna** ``(x, y, z)``.
  Position lives per antenna, not per device: a fixed positioning reader fans
  2–8 antennas across tens of metres of coax, each a distinct radiator at a
  distinct coordinate (ADR-024 v1 wrongly put ``position_*`` on ``devices``;
  amendment v2 moves it here). ``port`` matches ``tag_reads.reader_antenna``.
  Isolation flows through the ``device_id`` FK (devices are tenant-scoped),
  so the table carries no ``tenant_id``/RLS of its own.
- ``sites.coord_system`` — nullable JSONB floor frame (units, extent, origin
  anchor, rotation, optional geo-anchor). ``NULL`` ⇒ geographic-only (today's
  behaviour).
- ``asset_positions`` — new hypertable for per-asset ``(x, y)`` fixes. Created
  here but **written to by nothing in Sprint 59**: ``source=precomputed`` is the
  Sprint 60 BYO-ingest path, ``source=computed`` is the Sprint 61 estimator, and
  ``source=zone`` is the Sprint 60 retrieval-time fallback. ``asset_id`` carries
  **no FK** (hypertable, matches ADR-013/014 / ``external_locations``). The
  ``id + time`` composite PK follows the ``external_locations`` precedent (the
  ADR DDL sketch omitted a PK; the ORM + hypertable need a partition-keyed one).
- ``tenants.position_strategy`` — nullable JSONB placeholder (ADR-024 D8).
  Created-not-used: the estimator's per-tenant RSSI/count weight formula varies
  company-to-company, so it must be config, never hardcoded. Lands now so the
  Sprint 61 estimator has somewhere to read from without a follow-up migration.

Revision ID: 051
Revises: 050
Create Date: 2026-06-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic
revision: str = "051"
down_revision: str | None = "050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- antennas: per-antenna (x, y, z) within the site coord_system --
    op.create_table(
        "antennas",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "device_id",
            UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Matches tag_reads.reader_antenna (0..255).
        sa.Column("port", sa.SmallInteger, nullable=False),
        sa.Column("x", sa.Numeric, nullable=True),
        sa.Column("y", sa.Numeric, nullable=True),
        sa.Column("z", sa.Numeric, nullable=True),  # mount height (nullable)
        sa.Column("label", sa.String(64), nullable=True),
        sa.Column("gain_dbi", sa.Numeric, nullable=True),
        sa.UniqueConstraint("device_id", "port", name="uq_antennas_device_port"),
    )

    # -- sites.coord_system: floor frame (NULL = geographic-only) --
    op.add_column("sites", sa.Column("coord_system", JSONB, nullable=True))

    # -- tenants.position_strategy: per-tenant estimator config placeholder --
    op.add_column("tenants", sa.Column("position_strategy", JSONB, nullable=True))

    # -- asset_positions hypertable: per-asset (x, y) fixes --
    op.create_table(
        "asset_positions",
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
        # No FK: hypertable, matches ADR-013/014 (external_locations).
        sa.Column("asset_id", UUID(as_uuid=True), nullable=False),
        sa.Column("site_id", UUID(as_uuid=True), nullable=False),
        sa.Column("x", sa.Numeric, nullable=False),
        sa.Column("y", sa.Numeric, nullable=False),
        sa.Column("z", sa.Numeric, nullable=True),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("metadata", JSONB, nullable=True),
        sa.PrimaryKeyConstraint("id", "time"),
        sa.CheckConstraint(
            "source IN ('precomputed','zone','computed')",
            name="ck_asset_positions_source",
        ),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_asset_positions_confidence"),
    )
    op.execute("SELECT create_hypertable('asset_positions', 'time', if_not_exists => TRUE)")
    op.create_index(
        "ix_asset_positions_by_asset",
        "asset_positions",
        ["tenant_id", "asset_id", sa.text("time DESC")],
    )
    op.create_index(
        "ix_asset_positions_by_site",
        "asset_positions",
        ["tenant_id", "site_id", sa.text("time DESC")],
    )
    op.execute("ALTER TABLE asset_positions ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_asset_positions ON asset_positions "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_asset_positions ON asset_positions")
    op.execute("ALTER TABLE asset_positions DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_asset_positions_by_site", table_name="asset_positions")
    op.drop_index("ix_asset_positions_by_asset", table_name="asset_positions")
    op.drop_table("asset_positions")

    op.drop_column("tenants", "position_strategy")
    op.drop_column("sites", "coord_system")

    op.drop_table("antennas")
