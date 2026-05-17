# ADR-024: Indoor Position Estimation — Trilateration Processor + `asset_positions` Hypertable

- Status: Proposed (Sprint 33, May 2026)
- Implements: a new gap (row 2.18 in `docs/design/reference-design-remediation.md`) surfaced by SME review of large-zone deployments (football-field-size sites divided by a 400×600 XY grid of fixed readers).
- Related: ADR [002 MQTT for device connectivity](002-mqtt-device-connectivity.md), ADR [003 TimescaleDB storage](003-timescaledb-storage.md), ADR [011 Device identity roadmap](011-device-identity-roadmap.md) (introduces `devices.mobility`), ADR [013 Subject-scoped telemetry](013-telemetry-subject-scoping.md), ADR [014 Multi-subject telemetry ingest](014-telemetry-multi-subject-ingest.md), ADR [021 Configurable Sensing Events v2](021-configurable-sensing-events.md) (the `processor` enum this ADR extends), [edge-hardware-and-rfid-primer.md §3.1](../refs/edge-hardware-and-rfid-primer.md).

## Context

An SME reviewing the Sprint-33 plan described a real customer-class
deployment that stress-tests our model:

1. A football-field-size site is divided by a **400×600 XY coordinate
   grid**, with a mesh of **fixed readers** mounted at known positions on
   columns, racks, or trusses.
2. Each reader periodically emits a **reader-level status message**
   (reader temperature, baseline RSSI, location-XY of the reader itself,
   etc.).
3. The reader then emits **per-tag reads** with optional tag-borne
   telemetry (tag-attached temperature sensor, motion, etc.) and the
   antenna port that observed the read.
4. Because each tag is heard by **multiple readers** with different RSSI
   strengths (stronger = closer), the **tag's position can be
   triangulated**.

### What TagPulse already supports — and where the real gaps are

A grep of the codebase shows the platform is less naïve than it might
look at first glance. Five of the seven things this scenario needs are
already in place:

| Need | Status today |
|---|---|
| Per-read RSSI | ✅ `tag_reads.signal_strength FLOAT` |
| Per-read antenna port (an 8-antenna reader needs this) | ✅ `tag_reads.reader_antenna SMALLINT` (0–255) |
| Tag-borne sensor payload (per-tag temperature, motion, etc.) | ✅ `tag_reads.sensor_data JSONB` + `tag_reads.tag_data JSONB`, with optional first-class `telemetry_readings subject_kind='asset'` rows (ADRs 013/014) |
| Reader-level scalar telemetry (reader temp, RSSI baseline, …) | ✅ `telemetry_readings subject_kind='device'` (ADRs 013/014) |
| Mobile-reader GPS | ✅ `tag_reads.latitude/longitude/location_accuracy_m/location_source` |
| **Fixed-reader XY within a site frame** | ❌ Not modelled |
| **Site coordinate-system definition** (units, extent, origin) | ❌ Not modelled |
| **Triangulation / RSSI-fusion algorithm** | ❌ Not implemented |
| **Persisted estimated `(x, y, confidence)` for an asset** | ❌ No table; `external_locations` is lat/lng-shaped and source-attributed for *received* fixes, not for *computed* indoor positions |
| **Reader-status MQTT topic** | ⚠️ Partial — topic taxonomy is per-device (ADR 002) but `devices/{device_id}/status` isn't formally specified. Edge primer §3 anticipated it. |

ADR 021 v2 introduced a `processor` column on `rules` (`isolated_zones |
overlapping_zones`) specifically so that future processors can plug in
without a schema migration. This ADR adds the third value:
`trilateration`.

### Why this needs its own ADR (not a footnote on 021)

Two things in the gap list above are bigger than a column add:

1. **`asset_positions` is a new hypertable.** Once a processor emits
   `(time, asset_id, x, y, confidence)` it has to land somewhere
   queryable. `tag_reads` is wrong (per-reader, not per-asset);
   `external_locations` is wrong (lat/lng, externally sourced).
2. **TagPulse is *not* trying to be a best-in-class RTLS vendor.** A
   pluggable processor pattern + a "BYO precomputed positions" ingest
   path is a deliberate scope choice that deserves an ADR-level
   discussion, not a paragraph buried in a sensing-events ADR.

## Decision (to be ratified in Sprint 40)

1. Add minimal schema to express *where readers are* and *what
   coordinate system they live in*.
2. Add an `asset_positions` Timescale hypertable for computed positions.
3. Extend the ADR-021-v2 `processor` enum with `trilateration` and
   define the processor's config + algorithm interface.
4. Ship one reference algorithm (`weighted_centroid_log_distance`) under
   the algorithm interface; document the BYO-algorithm and BYO-position
   paths as first-class.
5. Define a `devices/{device_id}/status` MQTT topic for periodic
   reader-level status (already partially anticipated by ADRs 002 +
   013/014).

### Schema (additive)

```sql
-- Reader's static position within its site's local coordinate system.
-- For mobile readers, leave NULL and continue using
-- tag_reads.latitude/longitude per-read (ADR 011 + Sprint 14).
ALTER TABLE devices
    ADD COLUMN position_x NUMERIC,                -- in site coord_system units
    ADD COLUMN position_y NUMERIC,
    ADD COLUMN position_z NUMERIC,                -- ceiling/mount height (optional)
    ADD COLUMN position_updated_at TIMESTAMPTZ,
    ADD COLUMN path_loss_params JSONB;            -- per-reader RSSI<->distance calibration (optional)

-- Site coordinate system. NULL coord_system => geographic only (today's behaviour).
ALTER TABLE sites
    ADD COLUMN coord_system JSONB;
-- coord_system shape (when set):
--   { "units": "meters" | "feet",
--     "extent_x": 400, "extent_y": 600,          -- grid extent
--     "origin_anchor": "nw_corner" | "sw_corner" | "device_id",
--     "origin_device_id": "<uuid>",              -- when origin_anchor='device_id'
--     "rotation_deg": 0,                          -- grid rotation vs. north
--     "geo_anchor": { "lat": ..., "lng": ..., "x": 0, "y": 0 }  -- optional, for map overlays
--   }

-- New hypertable: per-asset position fixes (computed or externally sourced).
CREATE TABLE asset_positions (
    time              TIMESTAMPTZ NOT NULL,
    tenant_id         UUID NOT NULL REFERENCES tenants(id),
    asset_id          UUID NOT NULL,                   -- not FK to avoid cross-shard JOIN cost on hypertable
    site_id           UUID NOT NULL,                   -- coord_system lives on sites
    x                 NUMERIC NOT NULL,
    y                 NUMERIC NOT NULL,
    z                 NUMERIC,                         -- nullable
    confidence        NUMERIC(3,2) NOT NULL,           -- 0.00..1.00
    method            VARCHAR(32) NOT NULL,            -- 'trilateration' | 'precomputed' | future ones
    source_read_count SMALLINT,                        -- # tag_reads aggregated (null for precomputed)
    processor_run_id  UUID,                            -- ties to the rule firing that produced it
    metadata          JSONB
);
SELECT create_hypertable('asset_positions', 'time', if_not_exists => TRUE);
CREATE INDEX ON asset_positions (tenant_id, asset_id, time DESC);
CREATE INDEX ON asset_positions (tenant_id, site_id, time DESC);
```

Why `asset_positions.asset_id` is **not** a FK: hypertable chunks
forbid cross-chunk JOIN-time FK enforcement at scale; ADR-013/014 made
the same choice for `telemetry_readings.subject_id`. Repository-layer
guards ensure referential integrity at write time.

### How it plugs into ADR 021 v2

A new sensing event sets `processor='trilateration'`:

```jsonc
{
  "name": "Forklift position — plant 7",
  "condition_type": "sensing.geolocation.on_change",
  "event_type": "geolocation",
  "trigger": "on_change",
  "processor": "trilateration",
  "category_ids": ["<forklift-category-uuid>"],
  "site_label_filters": [{"key": "site", "value_in": ["plant-7"]}],
  "condition_config": {
    "processor_config": {
      "algorithm": "weighted_centroid_log_distance",
      "path_loss_exponent": 2.5,         // 2.0 free space, 3.0–4.0 dense racking
      "ref_rssi_dbm": -50,               // calibrated RSSI at ref_distance_m
      "ref_distance_m": 1.0,
      "min_readers": 3,                  // ≥3 distinct readers required to emit a fix
      "aggregation_window_ms": 1000,     // collect reads within this window per tag
      "min_position_change_m": 0.5,      // suppress sub-jitter updates
      "max_position_age_ms": 30000       // drop stale per-reader contributions
    },
    "confidence_threshold": 0.5          // emit only when computed confidence ≥ threshold
  },
  "integration_ids": ["<fleet-dashboard-webhook-uuid>"]
}
```

The dispatcher-layer envelope (ADR 021 v2) carries the resolved
position:

```jsonc
{
  "event_type": "geolocation",
  "asset_id": "...",
  "value": { "x": 247.3, "y": 183.1, "z": null, "units": "meters",
             "site_id": "...", "coord_system": "site-7-grid" },
  "confidence": 0.72,
  "keySet": ["asset_id", "site_id"],
  "eventConfigurationId": "...",
  "categoryId": "<forklift-category-uuid>",
  "labels": [{"key": "zone", "value": "shipping"}, ...]
}
```

### Algorithm interface (pluggable)

```python
# tagpulse/sensing/positioning/base.py
class PositionEstimator(Protocol):
    name: ClassVar[str]                           # 'weighted_centroid_log_distance', ...
    def estimate(
        self,
        reads: Sequence[TagReadObservation],      # (reader_id, x, y, antenna, rssi, ts)
        config: PositionEstimatorConfig,
    ) -> PositionFix | None: ...
```

- `tagpulse/sensing/positioning/weighted_centroid.py` — bundled reference
  implementation. Good enough for "where is my forklift, roughly?"
  use-cases (warehouse RFID, yard management). Documented limitations:
  no multipath compensation, no antenna-gain pattern modelling, no
  phase-angle fusion.
- Third-party algorithms register via entry-point group
  `tagpulse.positioning_estimators` (mirrors the analytics-plugin
  pattern from ADR 004).
- **BYO precomputed positions** path: customers running Zebra Aurora /
  Impinj ItemSense / Mojix / RFcode can POST resolved fixes directly to
  `/v1/asset-positions` with `method='precomputed'`. Bypasses the
  processor entirely; their algorithm wins.

### Reader-status MQTT topic

Add to ADR 002's topic taxonomy:

```
devices/{device_id}/status
```

Payload is a `telemetry_readings`-shaped batch with `subject_kind='device'`
(reuses the ADR-014 multi-subject ingest path); includes a
`metadata.position` object that, when present, updates the
`devices.position_*` columns via the existing device-management code-path.
Periodic cadence is operator-configurable (default 60 s; ADR 002's existing
`devices/{device_id}/heartbeat` topic stays separate for liveness only).

## Consequences

**Positive:**

- One small migration unlocks indoor positioning end-to-end. No
  rewriting of `tag_reads`, no breaking changes to existing rules,
  webhooks, or UI.
- The `processor` extension point introduced in ADR 021 v2 proves out
  with a real second use case, justifying its existence.
- Pluggable algorithm + BYO-positions paths means TagPulse plays
  nicely with enterprise RTLS vendors instead of competing with them.
- `asset_positions` over Timescale gives operators free path-replay and
  heatmaps via existing aggregate-query patterns.

**Negative / costs:**

- One more hypertable to keep an eye on (retention policy + compression
  policy, both already standard for Timescale tables here).
- Reference algorithm accuracy will disappoint customers who expected
  sub-meter precision. Documentation must set the expectation
  explicitly: pluggable interface = BYO if you need better.
- Calibration burden (per-reader `path_loss_params`) lands on the
  operator. We provide sensible defaults at the site level; per-reader
  override is opt-in.

## Non-goals (what this ADR deliberately doesn't ship)

These map to the matching items in the plan's §5 "deliberately doesn't
commit to" table:

- **Sub-meter precision positioning out of the box.** That requires
  per-antenna gain patterns, phase-angle fusion, multipath compensation,
  and per-site calibration — all algorithm work that vendor RTLS systems
  spend years tuning.
- **Phase-angle / FMCW / UWB-anchor processing.** The data shape (phase
  in degrees, anchor-to-tag time-of-flight) doesn't fit `tag_reads` and
  isn't on any committed customer's ask list. Out-of-scope until
  demanded; when demanded, it's a new processor + a new column or two.
- **Real-time vendor RTLS replacement.** TagPulse is not competing with
  Zebra Aurora / Impinj ItemSense / Mojix / RFcode. Use them via the
  BYO-positions ingest path.
- **Antenna-gain-pattern modelling.** The bundled reference algorithm
  treats antennas as isotropic. Operators who care can replace it.
- **Automated reader-position calibration.** Reader (x, y) is operator
  input. Algorithms that derive reader position from observed tag
  patterns are out-of-scope.

## Open questions for Sprint 40

1. Should `asset_positions` retention default to 30 days (matches
   `telemetry_readings`) or 7 days (positions are noisier and replayed
   less often than scalar telemetry)?
2. Reader-status topic cadence: enforce a minimum (e.g., 10 s) to
   prevent operator misconfiguration from DOSing the broker?
3. Compression policy on `asset_positions`: opportunistic (segment-by
   asset_id) vs. always-on?
4. Confidence calculation in the reference algorithm — geometric
   dilution of precision (GDOP) is the textbook choice; sufficient
   for a v1?
5. Per-site coordinate-system editing UI lives in TagPulse-UI; tracked
   separately. This ADR ships the backend + API only.

## Decision history

- v1 (this version): extend ADR 021 v2's `processor` enum; add
  `asset_positions` hypertable + minimal `devices`/`sites` schema; ship
  one reference algorithm + BYO-positions path.
