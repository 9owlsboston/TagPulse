"""Sprint 15 — asset_current_location SQL view + recursive path support.

Implements the [planned] view from Sprint 15 (originally deferred to Phase
B.3): for every active asset binding, return the *latest* known position,
preferring whichever of (last RFID tag read, last external_locations row) is
newer. The view ``UNION``s both sources so the UI renders "via Reader-12" or
"via Samsara" badged uniformly.

Per [docs/design/assets-and-zones.md §5](../../docs/design/assets-and-zones.md)
and [docs/design/mobile-carriers-and-manifests.md §10 Q5](../../docs/design/mobile-carriers-and-manifests.md).

Revision ID: 024
Revises: 023
Create Date: 2026-05-02
"""
from typing import Sequence, Union

from alembic import op

revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Two CTEs (one per source), pick the newer per asset, expose
# ``latest_position_source`` so the UI can badge it. RLS comes from the
# underlying tables (asset_tag_bindings, tag_reads, external_locations all
# enforce app.current_tenant_id).
_CREATE_VIEW = """
CREATE OR REPLACE VIEW asset_current_location AS
WITH active_bindings AS (
    SELECT
        b.tenant_id,
        b.asset_id,
        b.binding_value,
        b.binding_kind
    FROM asset_tag_bindings b
    WHERE b.unbound_at IS NULL
),
rfid_latest AS (
    SELECT DISTINCT ON (b.tenant_id, b.asset_id)
        b.tenant_id,
        b.asset_id,
        tr.id            AS source_id,
        tr."timestamp"   AS recorded_at,
        tr.latitude,
        tr.longitude,
        tr.location_accuracy_m AS accuracy_meters,
        tr.device_id     AS device_id,
        'rfid'::text     AS source
    FROM active_bindings b
    JOIN tag_reads tr
      ON tr.tenant_id = b.tenant_id
     AND (
            (b.binding_kind = 'epc'    AND tr.epc    = b.binding_value) OR
            (b.binding_kind = 'tid'    AND tr.tid    = b.binding_value) OR
            (b.binding_kind = 'device' AND tr.tag_id = b.binding_value)
         )
    WHERE tr.latitude IS NOT NULL AND tr.longitude IS NOT NULL
    ORDER BY b.tenant_id, b.asset_id, tr."timestamp" DESC
),
external_latest AS (
    SELECT DISTINCT ON (b.tenant_id, b.asset_id)
        b.tenant_id,
        b.asset_id,
        el.id            AS source_id,
        el.recorded_at   AS recorded_at,
        el.latitude,
        el.longitude,
        el.accuracy_meters,
        NULL::uuid       AS device_id,
        COALESCE(el.source, 'external')::text AS source
    FROM active_bindings b
    JOIN external_locations el
      ON el.tenant_id = b.tenant_id
     AND el.asset_id  = b.asset_id
    ORDER BY b.tenant_id, b.asset_id, el.recorded_at DESC
),
combined AS (
    SELECT * FROM rfid_latest
    UNION ALL
    SELECT * FROM external_latest
)
SELECT DISTINCT ON (tenant_id, asset_id)
    tenant_id,
    asset_id,
    source_id,
    recorded_at,
    latitude,
    longitude,
    accuracy_meters,
    device_id,
    source AS latest_position_source
FROM combined
ORDER BY tenant_id, asset_id, recorded_at DESC;
"""


def upgrade() -> None:
    op.execute(_CREATE_VIEW)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS asset_current_location")
