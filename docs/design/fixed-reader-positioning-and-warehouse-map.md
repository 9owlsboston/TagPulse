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

## Phased plan

- **Phase 0 — backend contract**
  - Expose `coord_system` on `SiteResponse` + a `SiteUpdate` write path (with
    shape validation). `openapi.json` regen.
  - Antenna CRUD schema/endpoints (`AntennaCreate/Response`, list-by-device);
    reader **centroid** derived read-side (no stored reader position).
  - `floorToGeo` util + `geo_anchor` validation (seam, unused by UI yet).
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

## Out of scope

- Trilateration / RSSI estimator implementation (Phase 3; deferred per ADR-024).
- Geo-anchored unified map *implementation* (seam designed, build deferred).
- Re-adding `devices.position_*` (superseded by `antennas`).
- Mobile-reader / lat-lon map changes (unchanged).
