# Device→site assignment & floor-polygon zone resolution

> Status: **Planning → in implementation** (Sprint 64 follow-up, branch
> `sprint-64/device-site-floor-zones`). Unblocks the **accurate D5 path** parked
> in [fixed-reader-positioning-and-warehouse-map.md](fixed-reader-positioning-and-warehouse-map.md)
> ("Implementation blocker" note). Additive — changes nothing about today's
> `reader_bound` resolution.

## Why now

Floor-polygon zone resolution (the accurate D5 path) needs to answer *"which
floor's polygons should I test this antenna's `(x, y)` against?"* — but
`devices` has **no `site_id`**, and the only device→site link today is circular
(through the `reader_bound` zone the floor path is meant to replace). This doc
adds a first-class device→site assignment and the floor-polygon resolver that
consumes it.

## Decisions (resolved)

| # | Question | Decision |
|---|----------|----------|
| D1 | **Assignment model** | **`devices.site_id`** — nullable FK → `sites`, `ON DELETE SET NULL`. A reader is physically at **one** site (1:1), so no join table. Mobile/un-assigned readers stay `NULL`. |
| D2 | **Floor-polygon storage** | **Reuse `zones.polygon_geojson`**, interpreted as **floor `(x, y)`** coordinates when the zone's site has a `coord_system` (geographic lat/lon otherwise). The ray-casting [`point_in_polygon`](../../src/tagpulse/geo/__init__.py) engine is coordinate-agnostic, so no new column or zone kind — a zone on a floor-site *is* a floor polygon. |
| D3 | **Assignment UX** | **Implicit on placement + explicit override.** Dropping a reader on a site's floor plan (Phase 1 placement) sets `device.site_id` to that site; the device edit form also exposes an explicit `site_id` selector. |
| D4 | **Backfill** | **Heuristic backfill** in the migration: a fixed reader with **exactly one** `reader_bound` zone inherits that zone's `site_id`; ambiguous/none stay `NULL`. |
| D5 | **Consistency** | **Soft.** A reader's `reader_bound` memberships are *not* enforced to lie within its `site_id` (avoids breaking existing cross-site configs); a future audit can warn. |

## Data model

```sql
ALTER TABLE devices
  ADD COLUMN site_id UUID NULL REFERENCES sites(id) ON DELETE SET NULL;
CREATE INDEX ix_devices_site_id ON devices (site_id);
-- Backfill: fixed readers in exactly one reader_bound zone inherit its site.
UPDATE devices d SET site_id = z.site_id
FROM (
  SELECT (jsonb_array_elements_text(fixed_reader_ids))::uuid AS device_id,
         site_id
  FROM zones WHERE kind = 'reader_bound' AND fixed_reader_ids IS NOT NULL
) z
WHERE d.id = z.device_id
  AND d.mobility = 'fixed'
  AND d.site_id IS NULL
  AND (SELECT count(*) FROM zones z2
       WHERE z2.kind = 'reader_bound'
         AND z2.fixed_reader_ids @> to_jsonb(d.id::text)) = 1;
```

`site_id` is added to `DeviceCreate` / `DeviceUpdate` / `DeviceResponse`
(nullable). RLS is unaffected (`devices` is already tenant-scoped); a
cross-tenant `site_id` is rejected at the service layer.

## Floor-polygon zone resolution (the accurate D5 path)

New repository method, e.g. `get_floor_zone_for_point(tenant_id, site_id, x, y)`:

1. Load the site's zones with a `polygon_geojson` (the floor polygons).
2. Optional bbox prefilter in floor units (reuse the geofence bbox columns,
   recomputed in floor space — see "Open implementation points").
3. `point_in_polygon(y, x, ring_of_xy)` against each candidate; lowest
   `created_at` wins (mirrors `get_zone_for_reader` determinism).

Wired into the tag-reads **location descriptor** ([query service](../../src/tagpulse/api/services/query_service.py))
as the **preferred** resolver for a fixed read **when** the device has a
`site_id`, the site has a `coord_system`, and the read's antenna (port, falling
back to port 0 per the port-0 model) has a surveyed `(x, y)`. Otherwise it falls
back to the existing `reader_bound` lookup. So:

```
fixed read →
  device.site_id + site.coord_system + antenna(port→port0).xy present?
    yes → point_in_polygon over the site's floor zones  (accurate)
    no  → reader_bound zone via fixed_reader_ids         (coarse fallback)
```

## Assignment UX

- **Implicit (primary).** The Phase 1 floor placement modal is already per-site;
  on placing a reader (port-0 upsert) the UI also sets `device.site_id` to that
  site if unset (a `PATCH /device-registry/{id}` with `site_id`).
- **Explicit (override).** The device edit form gains a **Site** selector
  (sites with a `coord_system` first), writing `site_id` directly.

## Phased implementation

- **Backend**
  1. Migration: `devices.site_id` + index + heuristic backfill.
  2. `site_id` on `DeviceCreate`/`Update`/`Response`; service-layer
     cross-tenant guard; repo persists it.
  3. Floor-zone resolver (`get_floor_zone_for_point`) reusing `point_in_polygon`.
  4. Location-descriptor prefers the floor resolver (above), antenna-position
     lookup via the antenna repo (port→port0 fallback). `openapi.json` regen.
- **UI**
  5. Device form **Site** selector + implicit assignment on placement.
  6. (Optional) draw floor zones on the `FloorMap`/placement canvas.

## Open implementation points

- **Floor bbox prefilter.** The `zones` bbox columns (`bbox_min_lat`, …) are
  populated for geofence (lat/lon) zones. For floor zones they'd hold floor-unit
  bounds; either recompute on write for floor-site zones or skip the prefilter
  (floor sites have few zones — a full scan is cheap). Lean: **skip prefilter
  for floor zones in v1**, add it if zone counts grow.
- **Antenna position lookup in the hot path.** The descriptor resolver needs
  `antennas(device_id, port)` (→ port 0 fallback). Batch per distinct
  `(device, port)` across the page, like the existing per-device zone cache.
- **Zone polygon authoring in floor space.** Drawing floor-coordinate polygons
  is a later UI nicety; v1 can accept floor polygons via the existing zone API
  (operators/seeders post floor-unit coordinates).

## Out of scope

- Enforcing reader↔zone site consistency (D5 soft).
- Trilateration estimator (still ADR-024-deferred).
- Geo-anchored unified map (separate seam).
