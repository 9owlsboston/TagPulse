"""Sprint 17 — geofence storage + tile-provider config + cert thumbprints.

Three additive changes that share a single migration since they're all small
column adds with no data migration:

- ``zones.bbox_min_lat / bbox_max_lat / bbox_min_lon / bbox_max_lon`` — denormalized
  bounding box for the geofence prefilter described in
  [docs/design/geofencing-and-map.md §3](../../docs/design/geofencing-and-map.md).
  Partial index over rows with a polygon set keeps the index tight.
- ``tenants.tile_provider JSONB NULL`` — per-tenant tile-provider config consumed
  by ``MapConfigResolver`` (§11 Q4 Resolved). NULL = system default (OSM public).
- ``devices.cert_thumbprint`` + ``cert_subject`` — Sprint 17b A6 Phase 2 (mTLS
  for MQTT) per ADR-012. Stored alongside the existing ``token_hash`` columns
  so the broker / backend can prefer cert auth when set, fall back to token.

Revision ID: 026
Revises: 025
Create Date: 2026-05-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- 17a: zones bbox + tenants.tile_provider --
    op.add_column(
        "zones",
        sa.Column("bbox_min_lat", sa.Float(), nullable=True),
    )
    op.add_column(
        "zones",
        sa.Column("bbox_max_lat", sa.Float(), nullable=True),
    )
    op.add_column(
        "zones",
        sa.Column("bbox_min_lon", sa.Float(), nullable=True),
    )
    op.add_column(
        "zones",
        sa.Column("bbox_max_lon", sa.Float(), nullable=True),
    )
    op.create_index(
        "ix_zones_bbox",
        "zones",
        [
            "tenant_id",
            "bbox_min_lat",
            "bbox_max_lat",
            "bbox_min_lon",
            "bbox_max_lon",
        ],
        postgresql_where=sa.text("polygon_geojson IS NOT NULL"),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "tile_provider",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
        ),
    )
    # -- 17b: devices.cert_thumbprint / cert_subject --
    op.add_column(
        "devices",
        sa.Column("cert_thumbprint", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "devices",
        sa.Column("cert_subject", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_devices_cert_thumbprint",
        "devices",
        ["cert_thumbprint"],
        unique=True,
        postgresql_where=sa.text("cert_thumbprint IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_devices_cert_thumbprint", table_name="devices")
    op.drop_column("devices", "cert_subject")
    op.drop_column("devices", "cert_thumbprint")
    op.drop_column("tenants", "tile_provider")
    op.drop_index("ix_zones_bbox", table_name="zones")
    op.drop_column("zones", "bbox_max_lon")
    op.drop_column("zones", "bbox_min_lon")
    op.drop_column("zones", "bbox_max_lat")
    op.drop_column("zones", "bbox_min_lat")
