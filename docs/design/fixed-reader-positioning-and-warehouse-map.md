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
| D1 | **Grain** the placement UI edits | **Port-0 model (layered, reader-grain default).** Per-**antenna** `(x, y)` rows stay the source of truth; **port 0 is the reader's nominal location** (consistent with the wire format's `an: 0 = unknown/muxed`), ports 1..N are individual radiators. Position resolves by **availability fallback** — a read with `an=N` uses port-N's `(x,y)` if surveyed, else falls back to the reader's port-0 location. **Reader-grain is the default** (survey only port 0); antenna-grain is an **opt-in upgrade** (survey ports 1..N) on the *same* data, no mode flag. **No `devices.position_*` columns** are re-added — the reader pin *is* the port-0 `antennas` row. See §"Reader & antenna positions". |
| D2 | **Asset position** before the estimator exists | **Snap** the asset marker to the **triggering reader's** `(x, y)` (the reader that produced the read — its port-N coordinate if surveyed, else port-0 per D1), *not* a zone centroid. Honest ("heard *here*, by *this* reader") and needs no extra computation. A **zone-centroid** variant (b) stays reachable later as a pure render-time aggregation over retained data (`device_id` + `fixed_reader_ids` + `antennas`) — **no lock-in, no migration**; keep the snap logic in an **isolated resolver** (`read → (x,y)`) so a future swap is localized. Trilateration is a later swap of the marker source to `asset_positions`. |
| D3 | **Map rendering model** | **Pure floorplan first** via Leaflet **`CRS.Simple`** (floor-local units, no projection). Both render modes are **immediate and first-class** — the *geographic* map (lat/lon, already exists) serves mobile/truck-fleet customers; the *floor* map serves warehouse customers; the Map page switches by site `coord_system` (NULL ⇒ geographic, set ⇒ floor). The `coord_system.geo_anchor` **seam is designed** (see §"Geo-anchor seam") but its build is **deferred** — see the trigger there. |
| D4 | **Floorplan image** | **Optional, inline.** When provided, an uploaded floorplan image renders behind the grid as a `CRS.Simple` image layer scaled to `coord_system.extent_x × extent_y`, stored **inline as a base64 `data:` URL capped at ~1–2 MB** (option (a) — no blob infra, consistent with the branding-logo precedent; column stays a string so a future move to blob is non-breaking). When **absent**, the map renders a **plain grid** from `coord_system.extent` (e.g. 600×400) — no image required. |
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

**Confirmed: no geo-anchor build for now.** Both immediate customer types are
served *without* it — truck-fleet (mobile/GPS) on the geographic map, warehouse
(fixed/XY) on the floor map, as two render modes of one page. Geo-anchoring is a
**real, proven pattern** (campus/indoor wayfinding — Azure Maps Creator, Google/
Apple Indoor, Esri ArcGIS Indoors; the floor-selector UX there also corroborates
D6's discrete-levels choice), so the seam is worth preserving. Its **trigger** is
narrow and specific: a **single facility that has mobile *and* fixed readers and
wants both on one combined canvas** (e.g. yard trucks + indoor forklifts at the
same DC). Distinct mobile-only and fixed-only customers/sites do **not** trip it.
Until that customer appears, the floor map stands alone.

## Reader & antenna positions — the port-0 model (D1)

**Port 0 is the reader's nominal location; ports 1..N are radiators.** This
reuses the wire contract, which already reserves
[`an: 0 .. 255 (0 = unknown/muxed)`](edge-wire-format-v2.md) — a read the reader
couldn't attribute to a specific port. Real radiators are 1-based. So the
port-0 `antennas` row (one per reader, guaranteed by the existing
`UNIQUE(device_id, port)`) holds the reader's own spot, and an unknown-port
(`an=0`) read snaps there naturally.

**Position resolves by availability fallback**, per read:

> `an=N` → use `antennas(device_id, port=N).(x, y)` **if surveyed**, else fall
> back to the **port-0** row (the reader's nominal location).

This gives two tiers on **one** model — no parallel "simplified mode", no flag:

| | **Reader-grain (default, low precision)** | **Antenna-grain (opt-in, surveyed)** |
|---|---|---|
| Operator surveys | only port 0 (drops the reader) | ports 1..N as well |
| Every read resolves to | the one reader location | its specific radiator `(x, y)` |
| Zones (D5) | `reader_bound` (per-device) | antenna-position → floor-polygon |
| Map marker | one per reader | per-radiator |

**The simplified customer** ("reader + all its antennas = one location") is just
the **default tier**: they survey only port 0 and every read — regardless of its
`an` — falls back to the reader location. The `an` value is **retained** on the
read (still shown in the Tag Reads *Antenna* column, still used for dedup /
signal analytics) — positioning simply ignores it when the port has no
coordinate. The read stays **upgrade-ready**: survey the ports later and the
same data gains precision with no migration.

**No `position_grain` flag (D-OQ2 = option a).** Reader-grain is purely "port 0
surveyed, ports 1..N empty"; precision is opt-in by surveying more. The placement
UI must therefore **not** treat empty ports 1..N as *incomplete* (no nag). An
explicit per-reader/per-site grain hint is **deferred** unless the UX proves
naggy — it would be advisory only and changes none of the fallback math.

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

**Zone resolution is *current* (query-time join), not point-in-time (OQ4 = a).**
The fixed-read zone is resolved live (`device_id → zone`) when the page loads, so
a historical read shows the zone its reader belongs to **now** — simplest, no new
column, and consistent with the rest of the system (zones are unversioned/live
everywhere today). A **historical** variant (b) — denormalize `zone_id` onto the
`tag_reads` row at ingest (the zone is *already* resolved there for zone-change
events) — stays a **deferred, additive** upgrade if customers report confusion
about moved readers; it doesn't require choosing it now (only *new* reads would
carry the historical value). No hot-path/hypertable change in v1.

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
    **port 0 = reader nominal location**; position resolves read-side by
    **availability fallback** (port-N if surveyed, else port-0). No stored
    `devices.position_*`.
  - `floorToGeo` util + `geo_anchor` validation (seam, unused by UI yet).
  - **Tag Reads location descriptor** in the `GET /tag-reads` projection
    (resolves the fixed-read zone server-side per D5) — feeds the contextual
    "Location" column.
  - Floor-space zone polygons + antenna-position → floor-polygon resolution
    (D5 accurate path), reusing the geofence point-in-polygon engine.
- **Phase 1 — placement UI**
  - Site coordinate-system editor (units, extent, origin, optional floorplan
    image upload).
  - Floor placement view: drop **fixed** readers onto the grid (writes the
    **port-0** `antennas` row = reader nominal location); per-radiator survey
    (ports 1..N) is an **opt-in advanced expansion**. Empty ports 1..N are *not*
    flagged incomplete (reader-grain is a valid end state).
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

*All design-time open questions are resolved (see the Decisions table and the
section notes).* Remaining decisions are **implementation-time** choices that do
not block the design:

- Exact `coord_system` validation bounds (extent limits, units enum).
- Floorplan image cap tuning (1 MB vs 2 MB) and accepted formats (PNG/SVG/WebP).
- Marker/precision affordance — whether the map visually distinguishes a
  reader-grain (port-0-only) reader from a fully surveyed one.

## Out of scope

- Trilateration / RSSI estimator implementation (Phase 3; deferred per ADR-024).
- Geo-anchored unified map *implementation* (seam designed, build deferred).
- Re-adding `devices.position_*` (superseded by `antennas`).
- Mobile-reader / lat-lon map changes (unchanged).
- **3D coordinate UI / 3D map / continuous `z` positioning (D6)** — `z` stays an
  optional estimator-only mount-height field; vertical needs are served by
  **discrete level/floor attributes**, addressed if and when a real need
  surfaces.
