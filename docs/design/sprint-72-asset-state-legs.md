# Sprint 72 — Asset state consolidation (Phase 2: transit legs)

- Status: **In progress** (2026-06-20). Backend [#144](https://github.com/9owlsboston/TagPulse/pull/144) + UI [#109](https://github.com/9owlsboston/TagPulse-UI/pull/109).
- Owner: backend leg tracker + SLA; UI leg surface. Cross-repo.
- Related: [ADR-034 (asset state consolidation)](../adr/034-asset-state-consolidation.md),
  [Sprint 71 design doc](sprint-71-asset-state-consolidation.md),
  `asset_state_history` (migration 058), `Topic.ASSET_CUSTODY_CHANGED`,
  `subject_current_zone` / `DwellTracker` (the subscriber precedent).

## 1. Goal

Phase 1 gives each asset a fused **frame** + zone + environment per tick, and
emits a **custody event** when the frame changes (reader ⇄ geo). Phase 2 turns
that custody timeline into explicit **transit legs** so the Assets page answers:

> Which **leg** is the lot on? How **long** has it been in transit? Was the
> **cold chain held for the whole leg** (leg-level SLA)?

Worked scenario (the milk lot): `Origin DC (Dock 3)` → **leg** → `SuperMart DC
(Cold Room A)` → **leg** → `SuperMart store (Backroom Chiller)`. Each leg is the
geo-frame interval between two facility anchors.

## 2. Key insight — legs are derived, not newly ingested

A **leg** is the interval an asset spends in the `geo` frame between two facility
frames (`reader`/`floor`). Phase 1 already emits exactly the transitions that
open and close one:

- **Open a leg** on a custody event `facility → geo`: record `origin` (the
  zone/site just left) + `departed_at` + the asset.
- **Close the open leg** on `geo → facility`: record `destination` (the zone/site
  arrived) + `arrived_at`; compute `duration` and the **leg SLA** from the fused
  environment already stored in `asset_state_history` over `[departed_at,
  arrived_at]` (min/max/mean temp + humidity, longest excursion, % in-range).

So Phase 2 adds **no ingest path and no new fusion** — it's a thin **leg tracker**
subscribing to `ASSET_CUSTODY_CHANGED` (mirroring how `DwellTracker` subscribes
to `SUBJECT_ZONE_CHANGED`), plus a table and a read API.

## 3. Schema — `asset_legs`

Regular tenant-scoped table (RLS), one row per leg (open or closed).

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `tenant_id` | uuid | RLS |
| `asset_id` | uuid | |
| `status` | text | `open` \| `closed` |
| `origin_zone_id` / `origin_site_id` | uuid null | facility left (from the closing snapshot before departure) |
| `dest_zone_id` / `dest_site_id` | uuid null | facility arrived (null while open) |
| `departed_at` | timestamptz | leg start (the `facility → geo` event time) |
| `arrived_at` | timestamptz null | leg end (the `geo → facility` event time) |
| `last_lat` / `last_lon` | double null | most recent in-transit fix (for the map) |
| `temp_min_c` / `temp_max_c` / `temp_mean_c` | double null | leg env envelope (computed on close) |
| `humidity_min` / `humidity_max` | double null | |
| `excursion_s` | int null | longest contiguous out-of-SLA duration |
| `in_range_pct` | double null | share of the leg within the SLA envelope |
| `sla_breached` | bool null | any excursion beyond tolerance |

**Index:** `(tenant_id, asset_id, departed_at DESC)`; partial `(tenant_id, asset_id) WHERE status='open'` (one open leg per asset).

## 4. Leg tracker

`AssetLegTracker` subscribes to `Topic.ASSET_CUSTODY_CHANGED` (in-process, ADR-010):
- `from_frame ∈ {reader,floor}` and `to_frame = geo` → **open** a leg (close any
  stale open leg first as a safety net).
- `to_frame ∈ {reader,floor}` → **close** the open leg: set `dest_*`,
  `arrived_at`, query `asset_state_history` over the leg window for the env
  envelope + SLA, write the summary.
- Write-through to `asset_legs`; hydrate the open-leg map on startup (mirrors
  `DwellTracker.hydrate`). Gated by the same `consolidation_enabled` flag.

## 5. API + UI

### 5.1 API

- **`GET /assets/{id}/legs`** (viewer) — legs newest-first, `status` filter,
  `limit`. Each closed leg carries duration + origin/dest + the env envelope &
  SLA summary.
- The **open** leg also surfaces on `GET /assets/{id}/state` as an optional
  `open_leg` block (origin + `departed_at` + last fix), so the "Current" card can
  say *"In transit: Origin DC → … · 2h 14m"* without a second call.

### 5.2 UI narrative — Where/What × Now/Over-time

The Asset page answers a 2×2 — **Where** (location) / **What** (environment) ×
**Now** ("is") / **Over time** ("was"). Phase 2's legs + SLA are what turn the
"was" into a *story*. Two surfaces carry it: the **Current card** (the "is",
Overview tab — extends the Phase-1 card) and a renamed **Journey tab** (the "was"
— the existing "Path" tab becomes a 3-panel linked view).

**Current card (the "is").** Frame-aware headline that flips between *at facility*
(`At SuperMart DC · Cold Room A`) and *in transit* (the open leg):

```
+-----------------------------------------------------------+
| Current (fused)                          [In transit] o    |
|-----------------------------------------------------------|
| Where   In transit:  Origin DC  ->  (arriving...)          |
|         elapsed 2h 14m   ·  last fix 41.88, -71.02         |
| Temp    4.0 C  [in range]       Humidity   60 %  [in range]|
| Tags 3  ·  samples 12  ·  confidence 0.71  ·  updated 12:03|
+-----------------------------------------------------------+
```

The SLA chip turns red (`[3.2 C - breach]`) when the fused value is outside
`fusion_strategy.sla`. Data: `GET /assets/{id}/state` (with `open_leg`).

**Journey tab (the "was") — three linked panels.** Selecting a leg/dwell in any
panel cross-filters the others.

*(a) Journey timeline* — facility dwells (`o`) interleaved with legs (`=`),
newest at top; each leg row = duration + origin→dest + cold-chain SLA badge (the
headline cold-chain answer), each facility row = zone + dwell:

```
+-- Journey -----------------------------------------------+
| o  At SuperMart store · Backroom Chiller    now          |
| =  LEG  SuperMart DC -> store        45m   [SLA OK]      |
| o  At SuperMart DC · Cold Room A     14:00 (dwell 5m)    |
| =  LEG  Origin DC -> SuperMart DC   3h30m  [SLA BREACH]  |
| |    3.5-9.1 C · 88% in range · excursion 12m            |
| o  At Origin DC · Dock 3            08:00-10:30          |
+----------------------------------------------------------+
```

Data: `GET /assets/{id}/legs` (closed legs + SLA) interleaved with facility
intervals derived from `/state/history` frame changes; the open leg at top.

*(b) Environment chart* — the fused `temperature_c`/`humidity_pct` series over the
same time axis, the **SLA band shaded**, excursions highlighted, and **leg
boundaries as vertical guides** so you read "temp *during the Origin→DC leg*":

```
+-- Environment (was)  [range: this journey v]  Temp | Humidity -+
| C                                                              |
| 9|              ___  <- excursion (red)                        |
| 6|··········· /   \ ···········  SLA max ......................|
| 4|~~~~~~~~~~~/     \~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~              |
| 2|·············································  SLA min ........|
|  +----|-------------|--------|----------------                 |
|    Dock3   leg A        DC      leg B    store                 |
+----------------------------------------------------------------+
```

Data: fused series from `GET /assets/{id}/state/history`; band from
`fusion_strategy.sla`; excursion shading from the leg SLA. (This is the *fused
mean* — a "show raw per-tag" toggle stays in the Telemetry tab for excursion
forensics, honoring the "mean ≠ alerting" caveat.)

*(c) Map* — the existing `AssetPathMap` trail with facility markers + the geo
trail for legs; the open leg's last fix as a live marker.

**The linkage (the narrative):** clicking **leg A** in the timeline clamps the
chart to leg A's window and highlights leg A on the map — one gesture answers
"where was it *and* what was the temp, on that leg."

**Decisions (locked).** (1) the existing **"Path" tab is renamed "Journey"** and
becomes this 3-panel view (Path already owns the map + range picker); (2) the
chart's **default range is "this journey"** (origin → now) when an open/recent
leg exists, else the existing 24h default.

**States.** *Gated off / no data* → "No journey yet" empty state.
*Single-frame deployment (no transit)* → no legs; timeline is facility dwells and
the chart is one continuous series. *Open leg* → top row is a live, growing leg
(no SLA verdict until close); chart right edge is live. *SLA breach* → one
consistent red signal across the leg row, the chart excursion, and the Current
card chip.

**Build mapping.** *Reuses:* `AssetCurrentStateCard` (extend with `open_leg`),
`AssetPathMap`, the `/state` + `/state/history` hooks, the Telemetry tab for raw.
*New:* the Journey timeline component, the SLA-banded environment chart, the
`useAssetLegs` hook, and the cross-filter wiring.

## 6. Cold-chain SLA config

The leg envelope needs a per-tenant temp/humidity target. Extend
`tenants.fusion_strategy` with an optional `sla` block
(`temp_min_c`, `temp_max_c`, `humidity_max`, `excursion_tolerance_s`); absent =
no SLA evaluation (legs still record duration + env envelope). This reuses the
Phase 1 config column — no new tenant column.

## 7. Decisions (locked)

- **A. Leg derivation = auto from custody events.** **Locked: yes** — reuses
  Phase 1's `ASSET_CUSTODY_CHANGED`, zero new ingest. (Alternative considered:
  explicit shipment/manifest declaration — heavier, needs a new write surface;
  deferred.)
- **B. ETA in v1 = deferred.** An in-flight ETA needs the **destination known
  before arrival**, which we don't have without a declared shipment/manifest.
  **v1 = actuals-only legs** (origin + elapsed while open; origin→destination +
  duration + SLA on close). ETA → a later phase once a destination-declaration
  exists.
- **C. SLA source = `fusion_strategy.sla` block.** **Locked: yes** (per-tenant
  config; absent = envelope-only, no breach flag).
- **D. UI placement.** **Locked:** rename the existing "Path" tab → **"Journey"**
  (3-panel linked view); env chart **default range = "this journey"** when an
  open/recent leg exists, else 24h.

## 8. Out of scope (this sprint)

In-flight **ETA** + destination prediction; multi-leg **shipment** grouping
(a shipment = an ordered set of legs); route/distance/geocoding; per-leg
carrier attribution. All gated on the destination-declaration decision (B).

## 9. Known follow-ups (v1 simplifications)

- **Leg selection highlights the chart, not the map.** The Journey cross-filter
  wires the timeline ↔ env-chart (highlight the selected leg's window). Panning /
  highlighting the **map** trail to that window is deferred. `[ui]`
- **`asset_legs.last_lat`/`last_lon` unpopulated in v1.** The live in-transit fix
  is served from `/state` (the geo snapshot's lat/lon); the leg columns exist for
  a future per-tick update of the open leg's last fix. `[backend]`
- **Stale-open safety net leaves a thin row.** A second `facility → geo` without an
  intervening arrival closes the prior open leg with `arrived_at` = the new
  departure and no `dest_*`/SLA (rare; the partial-unique index requires it). Such
  rows are identifiable by `arrived_at IS NOT NULL AND dest_zone_id IS NULL`. `[backend]`
