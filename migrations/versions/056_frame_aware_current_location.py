"""Sprint 69 (A1) — frame-aware ``asset_current_location`` view.

The Sprint 15 view (migration 024) was **geographic-only**: it joined
``tag_reads`` *with lat/lon* and ``external_locations``, so on a fixed-reader
floor deployment (reads carry NULL lat/lon, position lives in
``asset_positions`` as computed ``(x, y)``) every asset showed
``Location —`` and ``Last seen: never`` despite streaming reads.

This rewrite makes the view **frame-aware** and adds a **true last-seen**:

* ``last_seen_at`` — the newest ``tag_read`` for any active binding,
  **regardless of lat/lon** (a fixed-reader read still means "seen").
* ``kind`` (``geo`` | ``floor`` | ``none``) — which frame the current
  position is in, picked as the newer of the latest geo fix vs the latest
  floor ``(x, y)`` fix.
* floor fields ``x`` / ``y`` / ``site_id`` (when ``kind='floor'``) alongside
  the existing geo ``latitude`` / ``longitude`` / ``accuracy_meters``.

The base set is the union of assets with any read, any geo fix, or any floor
fix, so an asset with reads-but-no-position still appears (with a populated
``last_seen_at`` and ``kind='none'``). RLS is unchanged (the view inherits the
base tables' policies).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "056"
down_revision: str | None = "055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FRAME_AWARE_VIEW = """
CREATE OR REPLACE VIEW asset_current_location AS
WITH active_bindings AS (
    SELECT b.tenant_id, b.asset_id, b.binding_value, b.binding_kind
    FROM asset_tag_bindings b
    WHERE b.unbound_at IS NULL
),
reads_latest AS (
    -- True last-seen: newest tag_read for any active binding, REGARDLESS of
    -- lat/lon. A fixed-reader (floor) read with NULL lat/lon still counts.
    SELECT DISTINCT ON (b.tenant_id, b.asset_id)
        b.tenant_id,
        b.asset_id,
        tr."timestamp" AS last_seen_at,
        tr.device_id   AS last_seen_device_id
    FROM active_bindings b
    JOIN tag_reads tr
      ON tr.tenant_id = b.tenant_id
     AND (
            (b.binding_kind = 'epc'    AND tr.epc    = b.binding_value) OR
            (b.binding_kind = 'tid'    AND tr.tid    = b.binding_value) OR
            (b.binding_kind = 'device' AND tr.tag_id = b.binding_value)
         )
    ORDER BY b.tenant_id, b.asset_id, tr."timestamp" DESC
),
geo_latest AS (
    -- Newest geographic fix: RFID reads that carried lat/lon + external fixes.
    SELECT DISTINCT ON (tenant_id, asset_id)
        tenant_id, asset_id, recorded_at, latitude, longitude,
        accuracy_meters, device_id, source
    FROM (
        SELECT
            b.tenant_id, b.asset_id,
            tr."timestamp"         AS recorded_at,
            tr.latitude            AS latitude,
            tr.longitude           AS longitude,
            tr.location_accuracy_m AS accuracy_meters,
            tr.device_id           AS device_id,
            'rfid'::text           AS source
        FROM active_bindings b
        JOIN tag_reads tr
          ON tr.tenant_id = b.tenant_id
         AND (
                (b.binding_kind = 'epc'    AND tr.epc    = b.binding_value) OR
                (b.binding_kind = 'tid'    AND tr.tid    = b.binding_value) OR
                (b.binding_kind = 'device' AND tr.tag_id = b.binding_value)
             )
        WHERE tr.latitude IS NOT NULL AND tr.longitude IS NOT NULL
        UNION ALL
        SELECT
            b.tenant_id, b.asset_id,
            el.recorded_at          AS recorded_at,
            el.latitude             AS latitude,
            el.longitude            AS longitude,
            el.accuracy_meters      AS accuracy_meters,
            NULL::uuid              AS device_id,
            COALESCE(el.source, 'external')::text AS source
        FROM active_bindings b
        JOIN external_locations el
          ON el.tenant_id = b.tenant_id AND el.asset_id = b.asset_id
    ) geo
    ORDER BY tenant_id, asset_id, recorded_at DESC
),
floor_latest AS (
    -- Newest floor-frame (x, y) fix (Sprint 65/66 — computed or precomputed).
    SELECT DISTINCT ON (tenant_id, asset_id)
        tenant_id, asset_id, "time" AS recorded_at, x, y, site_id, source
    FROM asset_positions
    ORDER BY tenant_id, asset_id, "time" DESC
),
base AS (
    SELECT tenant_id, asset_id FROM reads_latest
    UNION
    SELECT tenant_id, asset_id FROM geo_latest
    UNION
    SELECT tenant_id, asset_id FROM floor_latest
),
picked AS (
    SELECT
        base.tenant_id,
        base.asset_id,
        r.last_seen_at,
        r.last_seen_device_id,
        g.recorded_at      AS g_recorded_at,
        g.latitude,
        g.longitude,
        g.accuracy_meters,
        g.device_id        AS g_device_id,
        g.source           AS g_source,
        f.recorded_at      AS f_recorded_at,
        f.x,
        f.y,
        f.site_id,
        f.source           AS f_source,
        CASE
            WHEN g.recorded_at IS NOT NULL
                 AND (f.recorded_at IS NULL OR g.recorded_at >= f.recorded_at)
                THEN 'geo'
            WHEN f.recorded_at IS NOT NULL
                THEN 'floor'
            ELSE 'none'
        END AS kind
    FROM base
    LEFT JOIN reads_latest r
      ON r.tenant_id = base.tenant_id AND r.asset_id = base.asset_id
    LEFT JOIN geo_latest g
      ON g.tenant_id = base.tenant_id AND g.asset_id = base.asset_id
    LEFT JOIN floor_latest f
      ON f.tenant_id = base.tenant_id AND f.asset_id = base.asset_id
)
SELECT
    tenant_id,
    asset_id,
    last_seen_at,
    kind,
    CASE kind WHEN 'geo' THEN g_recorded_at WHEN 'floor' THEN f_recorded_at END AS recorded_at,
    CASE WHEN kind = 'geo' THEN latitude END        AS latitude,
    CASE WHEN kind = 'geo' THEN longitude END       AS longitude,
    CASE WHEN kind = 'geo' THEN accuracy_meters END AS accuracy_meters,
    CASE WHEN kind = 'floor' THEN x END             AS x,
    CASE WHEN kind = 'floor' THEN y END             AS y,
    CASE WHEN kind = 'floor' THEN site_id END       AS site_id,
    COALESCE(CASE WHEN kind = 'geo' THEN g_device_id END, last_seen_device_id) AS device_id,
    CASE kind WHEN 'geo' THEN g_source WHEN 'floor' THEN f_source END AS latest_position_source
FROM picked;
"""

# The Sprint 15 geographic-only view, restored verbatim on downgrade.
_GEO_ONLY_VIEW = """
CREATE OR REPLACE VIEW asset_current_location AS
WITH active_bindings AS (
    SELECT b.tenant_id, b.asset_id, b.binding_value, b.binding_kind
    FROM asset_tag_bindings b
    WHERE b.unbound_at IS NULL
),
rfid_latest AS (
    SELECT DISTINCT ON (b.tenant_id, b.asset_id)
        b.tenant_id, b.asset_id, tr.id AS source_id, tr."timestamp" AS recorded_at,
        tr.latitude, tr.longitude, tr.location_accuracy_m AS accuracy_meters,
        tr.device_id AS device_id, 'rfid'::text AS source
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
        b.tenant_id, b.asset_id, el.id AS source_id, el.recorded_at AS recorded_at,
        el.latitude, el.longitude, el.accuracy_meters, NULL::uuid AS device_id,
        COALESCE(el.source, 'external')::text AS source
    FROM active_bindings b
    JOIN external_locations el
      ON el.tenant_id = b.tenant_id AND el.asset_id = b.asset_id
    ORDER BY b.tenant_id, b.asset_id, el.recorded_at DESC
),
combined AS (
    SELECT * FROM rfid_latest
    UNION ALL
    SELECT * FROM external_latest
)
SELECT DISTINCT ON (tenant_id, asset_id)
    tenant_id, asset_id, source_id, recorded_at, latitude, longitude,
    accuracy_meters, device_id, source AS latest_position_source
FROM combined
ORDER BY tenant_id, asset_id, recorded_at DESC;
"""


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS asset_current_location")
    op.execute(_FRAME_AWARE_VIEW)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS asset_current_location")
    op.execute(_GEO_ONLY_VIEW)
