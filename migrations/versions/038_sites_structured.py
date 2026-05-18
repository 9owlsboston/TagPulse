"""Sprint 34 (gap 2.7): sites.kind + lat/lon + structured address.

Revision ID: 038
Revises: 037
Create Date: 2026-05-17

Implements gap 2.7 of
[reference-design-remediation](../../docs/design/reference-design-remediation.md):
adds the Site/Transporter distinction and geolocation/structured-address
columns required by the Locations UI redesign.

Adds (all nullable except ``kind``):

- ``sites.kind`` VARCHAR(16) NOT NULL DEFAULT ``'site'`` with CHECK
  (``'site' | 'transporter'``). The reference design treats fixed
  facilities and mobile carriers as distinct "where is this?" answers;
  TagPulse keeps them in one table with a discriminator. Existing rows
  backfill to ``'site'``.
- ``sites.latitude`` / ``sites.longitude`` DOUBLE PRECISION with CHECK
  on lat/lon ranges. Nullable so existing rows + transporter rows
  without a known anchor are valid.
- ``sites.street_line1`` / ``street_line2`` VARCHAR(255), ``city`` /
  ``region`` VARCHAR(128), ``postal_code`` VARCHAR(32), ``country``
  CHAR(2) (ISO 3166-1 alpha-2; uppercased + validated in API layer).

The pre-existing ``sites.address`` TEXT column is **retained** this
release as a free-form compatibility shadow (mirrors the
``assets.asset_type`` shadow added in 037). The application layer is
free to populate it from the structured fields, but it is no longer
the source of truth.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision: str = "038"
down_revision: str | None = "037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SITE_KINDS = ("site", "transporter")


def upgrade() -> None:
    # -- 1. kind discriminator --
    op.add_column(
        "sites",
        sa.Column(
            "kind",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'site'"),
        ),
    )
    op.create_check_constraint(
        "ck_sites_kind",
        "sites",
        "kind IN (" + ", ".join(f"'{value}'" for value in _SITE_KINDS) + ")",
    )

    # -- 2. geolocation --
    op.add_column("sites", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("sites", sa.Column("longitude", sa.Float(), nullable=True))
    op.create_check_constraint(
        "ck_sites_latitude_range",
        "sites",
        "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
    )
    op.create_check_constraint(
        "ck_sites_longitude_range",
        "sites",
        "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
    )
    # Both-or-neither: if one is set, the other must be too.
    op.create_check_constraint(
        "ck_sites_latlon_paired",
        "sites",
        "(latitude IS NULL AND longitude IS NULL) "
        "OR (latitude IS NOT NULL AND longitude IS NOT NULL)",
    )

    # -- 3. structured address --
    op.add_column("sites", sa.Column("street_line1", sa.String(255), nullable=True))
    op.add_column("sites", sa.Column("street_line2", sa.String(255), nullable=True))
    op.add_column("sites", sa.Column("city", sa.String(128), nullable=True))
    op.add_column("sites", sa.Column("region", sa.String(128), nullable=True))
    op.add_column("sites", sa.Column("postal_code", sa.String(32), nullable=True))
    op.add_column("sites", sa.Column("country", sa.CHAR(2), nullable=True))
    op.create_check_constraint(
        "ck_sites_country_alpha2",
        "sites",
        "country IS NULL OR country ~ '^[A-Z]{2}$'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_sites_country_alpha2", "sites", type_="check")
    op.drop_column("sites", "country")
    op.drop_column("sites", "postal_code")
    op.drop_column("sites", "region")
    op.drop_column("sites", "city")
    op.drop_column("sites", "street_line2")
    op.drop_column("sites", "street_line1")
    op.drop_constraint("ck_sites_latlon_paired", "sites", type_="check")
    op.drop_constraint("ck_sites_longitude_range", "sites", type_="check")
    op.drop_constraint("ck_sites_latitude_range", "sites", type_="check")
    op.drop_column("sites", "longitude")
    op.drop_column("sites", "latitude")
    op.drop_constraint("ck_sites_kind", "sites", type_="check")
    op.drop_column("sites", "kind")
