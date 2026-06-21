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

- **`GET /assets/{id}/legs`** (viewer) — legs newest-first, `status` filter,
  `limit`. The **open** leg also surfaces on `GET /assets/{id}/state` (add an
  optional `open_leg` block) so the "Current" card can say *"In transit: Origin
  DC → … · 2h 14m"*.
- **UI** — the `AssetCurrentStateCard` shows the open leg (origin + elapsed) when
  `frame=geo`; a new **Legs** tab/timeline lists closed legs with duration +
  cold-chain SLA badge (in-range % / excursion).

## 6. Cold-chain SLA config

The leg envelope needs a per-tenant temp/humidity target. Extend
`tenants.fusion_strategy` with an optional `sla` block
(`temp_min_c`, `temp_max_c`, `humidity_max`, `excursion_tolerance_s`); absent =
no SLA evaluation (legs still record duration + env envelope). This reuses the
Phase 1 config column — no new tenant column.

## 7. Decisions to lock (before building)

- **A. Leg derivation = auto from custody events?** *Recommend **yes*** — reuses
  Phase 1's `ASSET_CUSTODY_CHANGED`, zero new ingest. (Alternative: explicit
  shipment/manifest declaration — heavier, needs a new write surface.)
- **B. ETA in v1?** *Recommend **defer*** — an in-flight ETA needs the
  **destination known before arrival**, which we don't have without a declared
  shipment/manifest. **v1 = actuals-only legs** (origin + elapsed while open;
  origin→destination + duration + SLA on close). ETA → a later phase once a
  shipment/destination-declaration exists.
- **C. SLA source = `fusion_strategy.sla` block?** *Recommend **yes*** (per-tenant
  config; absent = envelope-only, no breach flag).

## 8. Out of scope (this sprint)

In-flight **ETA** + destination prediction; multi-leg **shipment** grouping
(a shipment = an ordered set of legs); route/distance/geocoding; per-leg
carrier attribution. All gated on the destination-declaration decision (B).
