# Fixed vs movable readers — warehouse map & coordinate UI

> Status: **Planning** — design captured on the `chore/tag-reads-sensor-columns`
> branch alongside the Tag Reads column plan ("more design to come"). This is a
> larger, multi-phase, multi-component effort (backend contract + UI + later an
> estimator), so unlike the sibling doc it **does** warrant a full design doc
> ahead of code. No code on this branch. Governing ADR: [ADR-024](../adr/024-position-estimation.md).

## Why now

TagPulse models two reader mobilities but only one of them has a coordinate
story end-to-end:

- **Movable readers** → real-world **lat/lon** per read, geofence polygons, and
  the existing geographic Map page. Complete.
- **Fixed readers** → live indoors on a floor where the natural coordinate is a
  **floor-local `(x, y)`** (e.g. "aisle at `(2, 3)`"), not lat/lon. The
  *schema* for this exists (Sprint 59) but it is **headless**: no API surface,
  no placement UI, and no map that renders a floor plane.

This doc designs the fixed-reader coordinate model and the warehouse-map /
placement UI that has never been designed.

## What already exists (do not redesign)

| Capability | Status |
|---|---|
| **Reader mobility flag** | ✅ `devices.mobility` = `fixed` \| `mobile`, exposed on `DeviceResponse`; drives ingestion enrichment (fixed → reader-bound zone; mobile → geofence via the read's own lat/lon) — [mobile-carriers-and-manifests.md §4.1](mobile-carriers-and-manifests.md) |
| **Movable = lat/lon** | ✅ `tag_reads.latitude/longitude/location_source`; geofence polygons; geographic Map page (Leaflet, EPSG:3857) |
| **Fixed-XY *schema*** | ✅ Sprint 59 / migration 051: `antennas(device_id, port, x, y, z, label, gain_dbi)`, `sites.coord_system` JSONB, `asset_positions` hypertable — [ADR-024](../adr/024-position-estimation.md) |
| **Indoor estimator** | ❌ Trilateration / RSSI fusion **not implemented** → `asset_positions` has no writer (empty) |
| **`coord_system` / `antennas` in API** | ❌ `SiteResponse` does not expose `coord_system`; no antenna CRUD schema |
| **Placement / floor-map UI** | ❌ None — the Map page is geographic only |
| **Today's "XY grid" answer** | Model each area as a `reader_bound` zone, *no coordinates* — [user-guide.md](../user-guide.md) |

**Key takeaway:** the gap is not "no spatial model" — it's that the existing
model is **antenna-grain and headless**. This design adds the API + UI on top of
the Sprint 59 schema rather than inventing a new one.

## Decisions (resolved in discussion)

| # | Question | Decision |
|---|----------|----------|
| D1 | **Grain** the placement UI edits | **Layered.** Per-**antenna** `(x, y)` rows stay the source of truth (what trilateration needs); a **reader's** display coordinate = **centroid of its antennas** (a simple single-antenna reader is one port-0 antenna at the reader's spot). The UI starts at reader-grain ("(2,3)") and exposes antenna-grain as an advanced expansion. **No `devices.position_*` columns** are re-added — ADR-024 v1's per-device position stays superseded by the per-antenna `antennas` table. |
| D2 | **Asset position** before the estimator exists | **Snap** the asset marker to the `(x, y)` of the reader/zone that last heard it — the indoor analogue of today's "Map snaps markers to reader positions." Trilateration is a later swap of the marker source from reader-centroid → `asset_positions`. |
| D3 | **Map rendering model** | **Pure floorplan first** via Leaflet **`CRS.Simple`** (floor-local units, no projection). The `coord_system.geo_anchor` **seam is designed explicitly** (see §"Geo-anchor seam") so a future unified mobile+fixed map needs no data rework — but its *implementation* is deferred. |
| D4 | **Floorplan image** | **Supported** — an uploaded floorplan image renders behind the grid as a `CRS.Simple` image layer scaled to `coord_system.extent_x × extent_y`. (Storage approach is an open question — see §Open questions.) |
| D5 | **Zone resolution** for fixed reads | **Layered fallback.** `reader_bound` zones (configured `device_id` membership) remain the **zero-survey default**; surveyed sites graduate to **antenna-position → floor-polygon** zones resolved by the *same point-in-polygon engine* the geofence path already runs, just in `CRS.Simple` floor units instead of lat/lon. `reader_bound` is reframed as the *coarse fallback*, not the canonical model. See §"Zone resolution for fixed reads". |
| D6 | **3D / z-axis** | **Deferred until a real need surfaces.** `antennas.z` stays an *optional, estimator-only* mount-height input (already nullable); genuine vertical requirements (high-bay racking, multi-storey) are modeled as **discrete level/floor attributes on zones/sites**, never a continuous `z` coordinate. **No 3D coordinate UI or 3D map** in scope. |

## Coordinate system (`sites.coord_system`)

Per-site JSONB, already in the DB ([ADR-024](../adr/024-position-estimation.md) shape), `NULL` ⇒ geographic-only (today):

```jsonc
{
  "units": "meters" | "feet",
  "extent_x": 400, "extent_y": 600,        // floor extent in units
  "origin_anchor": "nw_corner" | "sw_corner" | "device_id",
  "origin_device_id": "<uuid>",            // when origin_anchor = 'device_id'
  "rotation_deg": 0,                        // grid rotation vs. north (for geo overlay)
  "geo_anchor": { "lat": ..., "lng": ..., "x": 0, "y": 0 }  // OPTIONAL — see seam
}
```

The Map page picks its render mode from this field: **`coord_system` NULL ⇒
geographic (lat/lon)**, **set ⇒ floorplan (`CRS.Simple`)**. This is the seam
ADR-024 designed; the UI just reads it.

## Map rendering architecture — why pure-floorplan-first

The existing Map page is geographic (Leaflet / EPSG:3857). A warehouse floor is
**non-geographic**; Leaflet's purpose-built `CRS.Simple` renders raw floor units
with no projection. **`CRS.Simple` and geographic tiles cannot meaningfully
share one Leaflet instance**, which is what makes the three classic options
genuinely different *architectures*, not just UX flavors:

- **Pure floorplan (`CRS.Simple`)** — a second map instance in floor units;
  floorplan image as a flat background; readers/antennas/assets plot at raw
  `(x, y)`. Matches "(2,3)", **zero surveying friction**. *Chosen.*
- **Geo-anchored overlay** — project the floor grid + image onto the existing
  lat/lon basemap via `geo_anchor`, unifying mobile + fixed on one map. Requires
  each site **surveyed into the real world** (corner lat/lon, rotation, scale) —
  real friction most warehouses won't have. *Deferred.*
- **Both** — two rendering pipelines at once. *Rejected for v1.*

### Geo-anchor seam (designed now, implemented later)

`coord_system.geo_anchor` is **nullable and additive**, so unification is an
*enhancement*, never a prerequisite:

- A floor-local point `(x, y)` projects to lat/lon as a pure function of
  `geo_anchor` (origin lat/lon + the `x/y` offset of that anchor) + `units` +
  `rotation_deg`. This `floorToGeo(x, y, coord_system)` util is the **only** new
  code the future unified map needs; the antenna/reader/asset rows never change.
- Concretely: when `geo_anchor` is set, the same floorplan image becomes a
  (rotated) Leaflet `ImageOverlay` on the geographic map, and asset/reader
  markers are projected through `floorToGeo`. When it's `NULL`, the floorplan
  map stands alone.
- **v1 obligation:** ship `floorToGeo` (and validate `geo_anchor` shape) even
  though no map consumes it yet, and keep the placement UI writing coordinates
  that are already geo-projection-ready. That is the whole "seam."

## Zone resolution for fixed reads (D5)

Today a fixed read's zone is a **configured per-reader lookup** —
[`get_zone_for_reader`](../../src/tagpulse/repositories/timescaledb/sites_zones.py)
returns the `reader_bound` zone whose `fixed_reader_ids` JSONB array contains the
read's `device_id` (oldest-`created_at` wins when a reader is in several zones,
per [assets-and-zones.md §11 Q4](assets-and-zones.md)). No geometry, no antenna.

**Why per-reader is too coarse.** A fixed reader fans **2–8 antennas across tens
of metres**; those antennas routinely sit in *different* physical areas (dock
door 3 vs. 4; two aisles). Per-reader membership **collapses all of them into one
zone** — wrong attribution, and the *normal* layout, not a corner case. The read
already carries `reader_antenna`, and `antennas` already carries per-port
`(x, y)`, so the information to do better exists; only zone resolution discards
it.

**The layered model (D5):**

1. **`reader_bound` (fallback, no survey).** Single-antenna or co-located
   readers keep the simple "pick readers" config. Resolution unchanged.
2. **Antenna-position → floor-polygon (accurate, surveyed).** Once antennas have
   floor `(x, y)` and zones carry **floor-space polygons**, "what zone" becomes
   **point-in-polygon of the antenna position** — the *same engine* as geofence
   ([geofencing-and-map.md §4](geofencing-and-map.md)), in `CRS.Simple` units
   instead of lat/lon. Fixed-reader zones and geofences **converge** into one
   mechanism differing only by coordinate space.

This makes a single read resolvable at **antenna grain** (per port), not just
reader grain — the granularity ceiling that blocked "dock door 3 vs. 4 on one
reader" disappears for surveyed sites. The cost is the survey burden, which is
exactly the Phase 1 placement UI; un-surveyed sites lose nothing (they stay on
the `reader_bound` fallback).

## Displaying location in Tag Reads

The Tag Reads page is where this design meets the sensor-columns design — the
unifying theme of this chore. **"Location" is two regimes in one column**, and
`location_source` (`gps` | `fixed` | `inferred`) is the discriminator (no need to
guess from which fields are non-null):

| Read (`location_source`) | Table shows | Map anchor (separate concern) |
|---|---|---|
| `gps` (mobile) | **Lat/Lon** (existing) + accuracy + source | the lat/lon, geographic map |
| `fixed` | **Zone name** (Device/Antenna already shown); **no coordinate** | antenna/reader centroid `(x, y)`, floor map |
| `inferred` / none | `—` | — |

**Coordinates are a *map* concern, not a *table* concern.** A fixed read means
"antenna 3 on reader R heard EPC E" — the asset is somewhere *within read range*
(1–10 m), **not** on the antenna's surveyed point. Printing `(2,3)` per read
overstates precision; true measured `(x, y)` only exists once the (deferred)
estimator writes `asset_positions`. So the **truthful** fixed-read location in
the table is the **zone** (resolved per D5) — the coordinate surfaces on the map,
where the cell can deep-link (geographic map for `gps`, floor map for `fixed`).

**Decision — one contextual "Location" column** that renders lat/lon for `gps`,
zone name for `fixed`, else `—` (raw lat/lon stays in CSV / an advanced column).

**Contract implication.** Both the zone name and any map-link coordinate need
data the `tag_reads` row doesn't carry. Recommended: a **backend location
descriptor** in the `GET /tag-reads` projection, e.g.
`{ kind: "geo"|"floor"|"none", lat?, lon?, accuracy_m?, source?, zone_id?, zone_name?, x?, y?, units? }`
— one server-side join, UI just renders. This is a contract change (+
`openapi.json` regen) that folds into **Phase 0**. (A frontend-only join over
separate zone/antenna fetches is rejected: fragile, chatty, duplicates
server-side resolution.)

## Phased plan

- **Phase 0 — backend contract**
  - Expose `coord_system` on `SiteResponse` + a `SiteUpdate` write path (with
    shape validation). `openapi.json` regen.
  - Antenna CRUD schema/endpoints (`AntennaCreate/Response`, list-by-device);
    reader **centroid** derived read-side (no stored reader position).
  - `floorToGeo` util + `geo_anchor` validation (seam, unused by UI yet).
  - **Tag Reads location descriptor** in the `GET /tag-reads` projection
    (resolves the fixed-read zone server-side per D5) — feeds the contextual
    "Location" column.
  - Floor-space zone polygons + antenna-position → floor-polygon resolution
    (D5 accurate path), reusing the geofence point-in-polygon engine.
- **Phase 1 — placement UI**
  - Site coordinate-system editor (units, extent, origin, optional floorplan
    image upload).
  - Floor placement view: drop **fixed** readers onto the grid (reader-grain),
    antenna-grain as an advanced expansion. Writes `antennas.(x,y)`.
- **Phase 2 — warehouse map (read-only)**
  - `CRS.Simple` floor map: floorplan image + grid + fixed readers/zones +
    **asset markers snapped to last-reader/zone `(x, y)`** (D2).
  - Mobile readers continue on the geographic map; the page switches mode by
    site `coord_system`.
- **Phase 3 — later (out of this design's build scope)**
  - Trilateration estimator writes `asset_positions`; map swaps marker source to
    computed positions.
  - Optional per-site `geo_anchor` → unified geographic overlay via the seam.

## Open questions

- **Floorplan image storage.** The branding-logo precedent stores images inline
  as base64 `data:` URLs capped at ~96 KB to avoid blob storage. A floorplan is
  typically **much larger** (hundreds of KB–MB). Options: (a) a larger inline
  cap (simple, bloats the `sites` row / API payloads), (b) real object storage
  (Azure Blob + SAS — new infra, contradicts the "no blob" stance so far),
  (c) operator-hosted URL only (no upload, but D4 said support upload). **Needs
  a decision before Phase 1.**
- **Reader-centroid vs. explicit reader pin.** For readers whose antennas are
  *not* yet surveyed, is the reader placed directly (a transient port-0 antenna)
  or hidden until it has at least one antenna coordinate?
- **Zone XY for snapping (D2).** A `reader_bound` zone spans several readers —
  snap the asset to the *triggering reader's* centroid, or to a computed zone
  centroid? Leaning triggering-reader (more precise, already known per read).
- **Historical vs. current zone (Tag Reads display).** Zone membership is
  mutable and unversioned. A *historical* read's "Location" column can show the
  zone the reader belongs to **now** (query-time join, simple) or the zone as it
  was **then** (denormalize `zone_id` onto the read at ingest, point-in-time
  accurate). "Then" is arguably more correct for a reads log; "now" matches the
  rest of the system. **Needs a decision before the Location column ships.**

## Out of scope

- Trilateration / RSSI estimator implementation (Phase 3; deferred per ADR-024).
- Geo-anchored unified map *implementation* (seam designed, build deferred).
- Re-adding `devices.position_*` (superseded by `antennas`).
- Mobile-reader / lat-lon map changes (unchanged).
- **3D coordinate UI / 3D map / continuous `z` positioning (D6)** — `z` stays an
  optional estimator-only mount-height field; vertical needs are served by
  **discrete level/floor attributes**, addressed if and when a real need
  surfaces.
