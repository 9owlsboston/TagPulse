# Runbook: Geofence performance triggered ADR-013 (PostGIS adoption)

**Source:** [docs/design/geofencing-and-map.md §11 Q5](../design/geofencing-and-map.md), [Sprint 17a roadmap](../roadmap.md#sprint-17a--geofencing--map-ui)
**Alert rules:** [ops/prometheus/alerts.yml](../../ops/prometheus/alerts.yml) (`tagpulse.geofence` group)

## When this fires

Either of the following sustained for **1 hour** for any tenant:

- `tagpulse_geofence_evaluation_duration_seconds` **p99 > 10 ms**, OR
- `tagpulse_geofence_candidates_per_evaluation` **p95 > 50**

## Why it matters

Sprint 17a ships pure-Python ray-casting + a Postgres bbox prefilter (no
PostGIS). The thresholds above were picked as the point at which the simple
algorithm starts costing more than the PostGIS migration is worth (per design
§4.2 perf budget). When tripped, **don't try to optimize the Python path** —
that's a dead end at this scale. Open ADR-013 and migrate.

## Procedure

1. **Acknowledge the alert** in Alertmanager so it doesn't re-page during the work below.
2. **Confirm the trigger isn't a single-tenant pathological polygon.** Run:

   ```bash
   curl -sS "$PROM_URL/api/v1/query" \
     --data-urlencode 'query=topk(5, histogram_quantile(0.99, sum by (le, tenant_id) (rate(tagpulse_geofence_evaluation_duration_seconds_bucket[5m]))))'
   ```

   If one tenant dominates, check their zones for unusually large vertex counts:

   ```sql
   SELECT id, name, jsonb_array_length(polygon_geojson->'coordinates'->0) AS verts
   FROM zones
   WHERE tenant_id = '<TENANT_ID>' AND polygon_geojson IS NOT NULL
   ORDER BY verts DESC LIMIT 10;
   ```

   If a tenant has accidentally uploaded a 10 000-vertex polygon, ask them to
   simplify it; that buys time but is **not** a fix — schedule the migration
   anyway.
3. **Open ADR-013 draft** at `docs/adr/013-postgis-adoption.md` with status
   `proposed`. Reference this runbook trigger in the "Context" section.
4. **Schedule the migration spike** (estimate: 1 sprint). Migration outline:
   - Add PostGIS extension to integration-test containers + Helm chart.
   - Replace `zones.polygon_geojson` JSONB with `zones.polygon GEOMETRY(Polygon, 4326)`.
   - Add GIST index on `zones (polygon)`.
   - Replace `tagpulse.geo.point_in_polygon` calls with `ST_Contains`.
   - Replace `find_geofence_candidates` SQL with `ST_DWithin` / `&&` operator.
   - Drop the bbox columns + `ix_zones_bbox` partial index added in migration 026.
   - Update portability gate (now requires PostGIS).
5. **Communicate** in `#tagpulse-eng`: ADR opened, sprint scheduled, ETA.

## Rollback

Not applicable — this runbook *opens* a migration, doesn't apply one. The
alert continues to fire until the migration ships and the metrics drop below
threshold.

## Failure modes

- **Alert flaps below 1 h sustain** — ignore; the `for: 1h` clause exists
  precisely to filter transient spikes.
- **Both alerts fire for every tenant simultaneously** — likely a global
  regression (e.g., a code change accidentally disabled the bbox prefilter).
  Check the most recent deploy, rollback if needed, then re-evaluate triggers.
