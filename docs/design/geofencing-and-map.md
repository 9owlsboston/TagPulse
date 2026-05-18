# Design Document: Geofencing & Map (Sprint 17)

**Date:** 2026-05-02
**Status:** proposed
**Related:** [asset-tracking-gap-analysis.md](asset-tracking-gap-analysis.md) (A8, A9, A10), [assets-and-zones.md](assets-and-zones.md), [telemetry-and-location.md](telemetry-and-location.md)

---

## 1. Problem Statement

After Sprint 15, zones are reader-bound only — the platform can answer *"which assets are near reader X"* but not *"which assets are inside polygon Y."* And there is no map: zone shapes, asset positions, and motion are invisible in the admin UI.

This sprint closes both gaps:

- **17a — Geofencing + Map UI** (this design): polygon zones, geofence rule conditions, Leaflet map with live markers and time-slider replay.
- **17b — mTLS for MQTT** (separate ADR-012): production-grade per-device crypto identity. Tracked separately because broker selection and PKI tooling are independent decisions.

Sprint 17a is the focus of this document.

---

## 2. Scope

In scope (17a):

- Polygon storage + point-in-polygon evaluation (no PostGIS; Python algorithm).
- Zone editor in UI with polygon-draw mode.
- `asset.zone_changed` event emission for geofence transitions (extends Sprint 15 logic).
- Rules engine condition types: `zone.entered`, `zone.exited`, `zone.dwell_exceeded`.
- Map page in TagPulse-UI: live asset markers, zone overlays, time-slider path replay.
- Simulator: synthetic GPS tracks crossing geofence polygons.

Out of scope:

- mTLS (17b, separate ADR).
- PostGIS adoption (only if profiling demands it post-launch).
- 3D / floor-level geofencing.
- Indoor positioning beacons.

---

## 3. Polygon Storage

`zones.polygon_geojson` (added blank in Sprint 15) is now populated for `kind='geofence'` zones. Format: a single GeoJSON `Polygon` feature, EPSG:4326:

```json
{
  "type": "Polygon",
  "coordinates": [
    [[-122.31, 47.60], [-122.30, 47.60], [-122.30, 47.61], [-122.31, 47.61], [-122.31, 47.60]]
  ]
}
```

Constraints (validated at write time):

- Single ring (no holes, no multi-polygon) for the first cut.
- Closed (first == last vertex).
- ≤500 vertices.
- All coordinates in valid lat/lon ranges.

Stored alongside a denormalized **bounding box** for cheap prefilter:

```sql
ALTER TABLE zones
  ADD COLUMN bbox_min_lat DOUBLE PRECISION NULL,
  ADD COLUMN bbox_max_lat DOUBLE PRECISION NULL,
  ADD COLUMN bbox_min_lon DOUBLE PRECISION NULL,
  ADD COLUMN bbox_max_lon DOUBLE PRECISION NULL;

CREATE INDEX ix_zones_bbox ON zones (tenant_id, bbox_min_lat, bbox_max_lat,
                                      bbox_min_lon, bbox_max_lon)
  WHERE polygon_geojson IS NOT NULL;
```

Bbox is recomputed on zone create/update by the service layer.

---

## 4. Spatial Evaluation (no PostGIS)

`tagpulse.geo.point_in_polygon(point, polygon)` — pure-Python ray-casting algorithm. ≈ 200 lines including bbox shortcut.

### 4.1 Hot path: tag read → zone

On each `tag_read` with `(latitude, longitude)`:

1. SQL prefilter: `SELECT id, polygon_geojson FROM zones WHERE tenant_id = :t AND polygon_geojson IS NOT NULL AND :lat BETWEEN bbox_min_lat AND bbox_max_lat AND :lon BETWEEN bbox_min_lon AND bbox_max_lon`. Cached at tenant level (TTL 30 s).
2. Python `point_in_polygon` against each candidate polygon (typically ≤3 after bbox prefilter).
3. The first matching zone (lowest `created_at` for determinism) is the read's geofence zone.

For reader-bound zones (Sprint 15 logic), the read's zone is whichever covers `device_id`. **A read may belong to both a reader-bound zone and a geofence zone** — both transitions emit independent `asset.zone_changed` events tagged with `zone_kind`.

### 4.2 Performance budget

- Prefilter SQL: <2 ms p99.
- Python evaluation: <1 ms p99 for ≤3 candidates @ ≤500 vertices each.
- Cache hit ratio target: >95 % (zones change rarely).

If real workloads break this budget, ADR-013 (PostGIS adoption) opens.

---

## 5. Rules Engine Extension

### 5.1 New condition types

```yaml
# zone.entered
condition:
  type: zone.entered
  zone_id: <UUID>
  asset_filter:           # optional
    category_id: <pallet-category-uuid>
  cooldown_s: 60          # don't re-fire within window

# zone.exited (mirror)
condition:
  type: zone.exited
  zone_id: <UUID>
  cooldown_s: 60

# zone.dwell_exceeded
condition:
  type: zone.dwell_exceeded
  zone_id: <UUID>
  threshold_minutes: 30
  asset_filter:
    category_id: <container-category-uuid>
```

### 5.2 Producers

- `zone.entered` / `zone.exited` — trigger directly on `asset.zone_changed` events from §4 (and from Sprint 15 reader-bound logic).
- `zone.dwell_exceeded` — periodic worker scans `asset_current_zone` (a new view derived from latest `asset.zone_changed`) every 60 s; emits a synthetic event when an asset's dwell crosses the threshold.

### 5.3 Schema

`rules.condition_json` already accepts arbitrary shape; existing CRUD handles new types without a migration. Validator updated to accept the new `type` values.

---

## 6. UI: Map Page

New top-level **Map** page (sidebar entry, viewer+ access).

### 6.1 Components (Leaflet + react-leaflet, provider-agnostic tiles)

Frontend is **provider-agnostic** — it consumes a `GET /tenants/me/map-config` endpoint that returns `{tile_url_template, attribution, max_zoom, subdomains?}` and feeds those four fields directly into Leaflet's `<TileLayer>`. No vendor-specific code paths in the UI. See §11 Q4 Resolved for the resolver design and provider-switching mechanism.

- **Live mode (default):** markers for each asset with `asset_current_location.latitude/longitude`, refreshed every 5 s via SSE (existing live channel) and every 30 s via REST fallback. Marker click → asset detail.
- **Zone layer:** all zones with `polygon_geojson` rendered as semi-transparent polygons; reader-bound zones rendered as marker clusters at reader positions.
- **Filter bar:** asset type, status, "in zone X."
- **Time-slider replay:** scrub through the last 24 h; markers animate along their `asset_path` (uses `GET /assets/{id}/path` from Sprint 15, batched per visible asset).

### 6.2 Zone editor (extension of Sprint 15 page)

- New "Geofence" tab on the zone form.
- Polygon-draw via `leaflet-draw`; outputs GeoJSON validated client-side and server-side.
- Vertex editing post-creation; bbox recomputed server-side on save.
- Reader-bound and geofence are mutually exclusive per zone (existing CHECK constraint).

### 6.3 Rule wizard

- New step in the existing 4-step wizard (becomes 5 steps when `zone.*` selected): zone picker (polygon preview), cooldown / dwell-threshold inputs, optional asset filter.

### 6.4 Bundle impact

`leaflet`, `react-leaflet`, `leaflet-draw`: ~70 KB gzip total. Acceptable per ADR-007.

UI parity is a **release gate**.

---

## 7. Backend Event Flow

```
tag_read (with lat/lon)
   │
   ▼
ingestion.enrich
   ├─ asset lookup       (Sprint 15)
   ├─ reader-bound zone  (Sprint 15)
   └─ geofence zone      (Sprint 17, §4)
   │
   ▼
publish asset.zone_changed { zone_kind: 'reader_bound' | 'geofence', from, to, … }
   │
   ▼
rules.evaluator (subscribes)
   │
   ├─ matches zone.entered / zone.exited → emit alert
   └─ updates asset_current_zone (for dwell worker)

dwell_worker (every 60 s)
   └─ for asset still in zone past threshold → emit synthetic dwell_exceeded → rules
```

EventBus already supports this fan-out; no schema changes beyond §3.

---

## 8. Simulator Updates

`scripts/simulate_devices.py` gains a `--with-gps` mode:

- For mobile-tagged assets, emit a synthetic GPS track (random walk constrained to a city block).
- Tracks deliberately cross at least one geofence polygon every ~5 minutes.
- Some assets dwell long enough to trigger `dwell_exceeded` rules created by the simulator on startup.

Result: a fresh `docker-compose up` shows live markers moving on the Map page within 30 s, and at least one geofence alert fires within 10 minutes.

---

## 9. Testing Strategy

- Unit: `point_in_polygon` (inside, outside, on edge, on vertex, concave polygon).
- Unit: bbox computation correctness across polygons that span lat/lon zero crossings (we explicitly exclude antimeridian for v1).
- Unit: rules engine handles new condition types; cooldown suppresses rapid re-fires.
- Integration: ingest a stream of GPS-tagged reads → expected zone transitions → expected alerts.
- Integration: dwell worker + clock advancement → dwell_exceeded fires once.
- UI: Cypress / Playwright — draw polygon, save zone, see polygon reappear after reload.

---

## 10. Rollout

1. Migration 019 — add bbox columns; backfill any existing geofence zones (none expected at this point).
2. Deploy backend with `GEOFENCE_EVALUATION_ENABLED=false`; verify no perf regression on tag-read ingestion.
3. Flip flag to `true`.
4. Deploy UI.
5. Simulator update last; demo content appears.

Rollback: feature flag off, UI hides Map page link, polygons remain untouched in DB.

---

## 11. Decisions & Open Questions

### Resolved

| # | Question | Decision |
|---|---|---|
| 1 | Multi-polygon zones? | **One zone per polygon for v1.** Multi-polygon (e.g., warehouse + outdoor yard as one logical zone) is a v2 enhancement. |
| 2 | Z-axis / multi-floor? | **Out of scope** — tracked as future work; today's model is 2-D. |
| 3 | Cluster rendering at low zoom? | **Add `react-leaflet-markercluster` when needed** — trigger is >500 assets in view. Don't pull the dependency in until then. |
| 4 | Tile provider strategy? | **Ship a `MapConfigResolver` abstraction now (POC default = OSM public); defer the production tile-provider infrastructure decision until first paying customer or public demo (C-deferred).** Rationale: at POC stage we run dev/test on a laptop — shipping a TileServer GL container today is real ops burden for zero current value, and the OSM Foundation usage policy targets "high-traffic" production sites, not internal POCs. **What ships now:** (a) `tenants.tile_provider JSONB NULL` (NULL = system default = OSM public); (b) `GET /tenants/me/map-config` returns `{tile_url_template, attribution, max_zoom, subdomains?}` — the entire frontend contract; (c) `MapConfigResolver` service in `src/tagpulse/services/map_config.py` with one builder function per `kind` (`osm`, `mapbox`, `maptiler`, `self_hosted`); (d) UI footer always renders the resolver's `attribution` string; (e) when `kind='osm'` the map page renders a small footer note: "Default tiles intended for development; configure a production provider before public deployment." **Switching providers later** = settings PATCH on a tenant or a one-line constant change for the system default — no code, no migration, no deploy. **Adding a new provider** = one new builder function. **Deferred until trigger:** TileServer GL container, OpenMapTiles/Protomaps tooling, ADR-014, production cost analysis. **Triggers to revisit:** first paying customer, public demo, or sovereign/firewalled customer (per [storage-strategy.md §6 Q2](storage-strategy.md) mixed-tier model). |
| 5 | PostGIS migration trigger threshold? | **Latency primary + zone-density secondary, instrumented via OpenTelemetry (Option C).** ADR-013 (PostGIS adoption) opens when *either* of the following holds for at least **1 sustained hour for any tenant in production**: (1) **`geofence.evaluation.duration` p99 > 10 ms** (OTel histogram, unit `s`, attribute `tenant_id` — surfaces as `geofence_evaluation_duration_seconds_bucket` via the Prometheus exporter); (2) **`geofence.candidates_per_evaluation` p95 > 50** (OTel histogram, unit count — measures polygons surviving the bbox prefilter; high values mean prefilter selectivity has collapsed at scale). **Active-subject count is *not* a trigger** — it affects event volume, not per-evaluation latency, and is already covered by ingestion observability. **Trace spans:** the per-evaluation work is wrapped in a `tracer.start_as_current_span("geofence.evaluate")` block so individual slow evaluations are debuggable in Jaeger / OTel-collector targets without adding any extra metrics. **Alert rule** (`tagpulse-platform` namespace, not per-tenant): Prometheus alerting consumes the OTel-exported histograms directly. **Runbook action when the alert fires:** open ADR-013 draft, run a PostGIS migration spike, schedule a sprint. Both instruments ship with Sprint 17 alongside the geofence engine; conforms to [observability.md §2](observability.md) (OTel SDK + Prometheus exporter). |

*(All open questions in this document are now resolved.)*
