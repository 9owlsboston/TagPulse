"""Sprint 15 Phase A-C audit mitigations.

Closes audit gaps in the asset-tracking substrate (sites, zones, assets,
asset_tag_bindings, external_locations) shipped across Phases A-C:

* #1 Add missing FK ``external_locations.asset_id -> assets.id`` (CASCADE).
* #4 GIN partial index on ``zones.fixed_reader_ids`` (kind='reader_bound')
  so ``get_zone_for_reader``'s JSONB ``@>`` lookup stops sequential-scanning.
* #8 Tighten ``ck_zones_kind_payload`` so reader_bound zones must list at
  least one reader (``jsonb_array_length(fixed_reader_ids) > 0``).

Per docs/design/assets-and-zones.md, docs/design/mobile-carriers-and-manifests.md,
and the Phase A-C audit summary.

Revision ID: 022
Revises: 021
Create Date: 2026-05-02
"""
from typing import Sequence, Union

from alembic import op

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # #1 — Add the missing FK from external_locations.asset_id to assets.id.
    # Cascade on delete: positional history follows the asset (asset deletes
    # are otherwise soft via status='retired', so cascade only fires on
    # genuine hard-deletes — see assets repo `delete()`).
    op.create_foreign_key(
        "external_locations_asset_id_fkey",
        "external_locations",
        "assets",
        ["asset_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # #4 — GIN index on fixed_reader_ids restricted to reader_bound zones.
    # Powers the `fixed_reader_ids @> [device_id]` lookup in
    # TimescaleZoneRepository.get_zone_for_reader. Tenant scoping happens via
    # the existing ix_zones_tenant index plus the WHERE in the query planner.
    op.execute(
        "CREATE INDEX ix_zones_fixed_readers_gin "
        "ON zones USING GIN (fixed_reader_ids) "
        "WHERE kind = 'reader_bound'"
    )

    # #8 — A reader_bound zone with an empty reader list serves no purpose
    # and silently breaks get_zone_for_reader. Require >=1 reader.
    op.execute("ALTER TABLE zones DROP CONSTRAINT ck_zones_kind_payload")
    op.execute(
        "ALTER TABLE zones ADD CONSTRAINT ck_zones_kind_payload CHECK ("
        "(kind = 'reader_bound' AND fixed_reader_ids IS NOT NULL "
        " AND jsonb_array_length(fixed_reader_ids) > 0)"
        " OR (kind = 'geofence' AND polygon_geojson IS NOT NULL))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE zones DROP CONSTRAINT ck_zones_kind_payload")
    op.execute(
        "ALTER TABLE zones ADD CONSTRAINT ck_zones_kind_payload CHECK ("
        "(kind = 'reader_bound' AND fixed_reader_ids IS NOT NULL)"
        " OR (kind = 'geofence' AND polygon_geojson IS NOT NULL))"
    )
    op.execute("DROP INDEX IF EXISTS ix_zones_fixed_readers_gin")
    op.drop_constraint(
        "external_locations_asset_id_fkey",
        "external_locations",
        type_="foreignkey",
    )
