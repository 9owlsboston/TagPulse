# Design Document: Mobile Carriers & Manifests

**Date:** 2026-05-02
**Status:** proposed
**Related:** [assets-and-zones.md](assets-and-zones.md), [telemetry-and-location.md](telemetry-and-location.md), [tracking-modes.md](tracking-modes.md), [edge-device-contract.md](edge-device-contract.md), [rfid-tag-data-model.md](rfid-tag-data-model.md), [data-models.md](../data-models.md), [refs/edge-hardware-and-rfid-primer.md](../refs/edge-hardware-and-rfid-primer.md)

---

## 1. Problem Statement

[assets-and-zones.md](assets-and-zones.md) (Sprint 15) and [tracking-modes.md](tracking-modes.md) (Sprint 15 / 15b) both assume **fixed readers** — a zone is a set of `fixed_reader_ids`, and `get_zone_for_reader(device_id)` is a stable lookup.

Real deployments include **mobile readers** mounted on vehicles or transport equipment (trucks, forklifts, tugs, conveyors, handheld terminals) that scan a population of tags and then move that population to a new physical location. Three things break under the fixed-reader assumption:

1. **The reader itself moves.** Its zone changes with every GPS fix; the zone-lookup cache and `subject.zone_changed` event would fire constantly.
2. **The cargo's location is *implied* by the carrier's location.** Re-scanning every tag to update its position is wasteful — a truck holding 1,200 pallets shouldn't generate 1,200 location updates per minute.
3. **Containment is real.** "These pallets are on truck T" is a first-class fact that the data model needs to express, not a derived join across `tag_reads`.

This document specifies the **mobile carrier + manifest** model: how mobile readers are flagged, how cargo is bound to a carrier, and how the edge agent should communicate during loading, transit, and unloading. It is non-breaking — it adds optional columns and behaviors on top of Sprints 14 / 15 / 15b.

---

## 2. Scope

In scope:

- `devices.mobility` flag (`fixed | mobile`).
- Carrier containment model (`assets.parent_asset_id`; promotion of backlog item `stock_items.parent_stock_item_id`).
- Reader-as-asset binding via `binding_kind='device'`.
- Three canonical communication patterns (manifest-only, manifest + periodic re-scan, cold-chain).
- Edge-agent throttling knobs for `…/location` topic.
- Ingestion routing differences for `mobility='mobile'` reads.

Out of scope (deferred):

- UWB / BLE indoor positioning — orthogonal; same hooks apply.
- Dynamic geofence creation per moving carrier (e.g., "alert if truck T's position deviates from planned route") — analytics module candidate, not platform.
- Multi-leg trip orchestration / route planning — ERP/TMS territory; we expose the events, they own the workflow.

---

## 3. Use-Case Taxonomy

| Pattern | Reader location | Tag location | Who carries GPS? | Example |
|---|---|---|---|---|
| **A. Fixed reader, mobile tag** *(today's default)* | static | mobile | nobody — zone inferred from reader | Dock-door portal scanning passing pallets |
| **B. Mobile reader, fixed tags** | mobile | static | reader | Forklift scanning shelf-mounted tags |
| **C. Mobile reader, mobile tags** | mobile | mobile (carried by reader) | reader (and tag if sensor-enabled) | Loaded truck in transit |
| **D. Reader *is* the asset** | reader's GPS = asset position | n/a | reader | Vehicle-tracking via the reader's own GPS, no cargo |

Sprint 15 covers A. This doc adds B / C / D on the same substrate.

---

## 4. Data Model Additions

### 4.1 Reader mobility flag

```sql
ALTER TABLE devices
  ADD COLUMN mobility VARCHAR(16) NOT NULL DEFAULT 'fixed'
    CHECK (mobility IN ('fixed','mobile'));
```

Migration **020a** (planned, ships with Sprint 15 or 15b — additive, harmless to deploy early).

Ingestion enrichment ([assets-and-zones.md §5](assets-and-zones.md#5-ingestion-enrichment)) branches on this:

- `mobility='fixed'` → today's path: `get_zone_for_reader(device_id)` then emit `subject.zone_changed` on transition.
- `mobility='mobile'` → **skip** the fixed-zone lookup; resolve zone from the read's own `latitude/longitude` against geofence polygons (Sprint 17a). Until Sprint 17a ships, mobile reads carry location but do not produce zone-change events. This avoids noisy events from mobile readers' constantly-changing "fixed zone."

### 4.2 Asset containment (carrier model)

```sql
ALTER TABLE assets
  ADD COLUMN parent_asset_id UUID NULL REFERENCES assets(id);

CREATE INDEX ix_assets_parent ON assets (tenant_id, parent_asset_id);
```

A pallet's `parent_asset_id` points at the truck it's loaded onto. NULL = stand-alone (default). Tree depth is unrestricted but typically 2–3 (truck → pallet → case).

Loading and unloading are **two operations** on this column, exposed via:

```
POST   /assets/{id}/load           body: { parent_asset_id: <carrier-uuid>, at: <ts> }
POST   /assets/{id}/unload         body: { at: <ts> }
GET    /assets/{id}/manifest       returns recursive children with current location
```

Both operations write an `audit_logs` entry (`event_type='asset.loaded' | 'asset.unloaded'`) and emit a new EventBus topic (see §6). They are idempotent: loading a child already loaded onto the same carrier is a no-op.

The current-location view ([assets-and-zones.md §3.4](assets-and-zones.md#34-current-location-view)) gains a fall-through: if an asset has no recent reads of its own but has a `parent_asset_id`, return the carrier's location with `location_source='inherited'`.

### 4.3 Inventory-side containment (Sprint 15b)

The backlog item *Pallet-of-cases hierarchy (`stock_items.parent_stock_item_id`)* is **promoted out of backlog** into Sprint 15b when this design lands:

```sql
ALTER TABLE stock_items
  ADD COLUMN parent_stock_item_id UUID NULL REFERENCES stock_items(id);

CREATE INDEX ix_stock_items_parent ON stock_items (tenant_id, parent_stock_item_id);
```

Same shape as assets — a case's parent is a pallet (SSCC), the pallet's parent is a truck stock-item if the truck is itself stock-tracked. Stock-level queries follow the tree.

### 4.4 Reader-as-asset binding

[rfid-tag-data-model.md §4.3](rfid-tag-data-model.md#43-asset-binding-sprint-15--bind-by-epc-or-tid) defines `binding_kind ∈ {epc, tid}`. Extend to:

```
binding_kind ∈ {'epc', 'tid', 'device'}
```

When `binding_kind='device'`, the binding's identifier is a `devices.id` (UUID stored in the `binding_value` column). Lookup at ingest: a tag read on device R whose `device_id` is bound to asset A means asset A is "the truck itself." The asset's location is then directly the reader's location, no cargo needed.

This makes Pattern D (Reader-as-asset) a one-row binding — no schema special case.

---

## 5. Communication Patterns

All three patterns build on the dedup + ENTER/EXIT contract in [edge-device-contract.md §3.3](edge-device-contract.md), which already collapses 30-Hz raw inventory into one event per tag per state change. **The agent does not need to publish every read.**

### 5.1 Pattern 1 — Manifest + GPS (cheapest)

> Use when: location precision = vehicle precision is acceptable; loss detection not required.

```
Load dock      ENTER × N tags        (one event per tag, batched per §3.4)
In transit     no tag reads          (broker silent for tags)
               …/location every 30 s {lat, lon, accuracy_m}
Receiving dock EXIT × N tags         (truck pulls away from origin reader)
               ENTER × N tags        (destination reader picks them up)
```

Bandwidth: roughly `2N` tag events for the whole trip + `O(trip_minutes × 2)` location events. For a 1,200-pallet, 7-hour trip: ~2,400 tag events + ~840 location events, vs. millions of raw reads.

The platform answer to "where is pallet P right now?" is:

```
P.current_location = location_of(P.parent_asset_id) if P has parent
                   = last_known_read(P) otherwise
```

The view from §4.2 already does this fall-through.

### 5.2 Pattern 2 — Manifest + periodic re-scan (loss / tamper detection)

> Use when: customer cares about "did anything fall off / get stolen mid-route?"

Add to Pattern 1: the agent runs a full inventory cycle every `rescan_interval_s` (config; default 0 = off). Dedup window suppresses noise within each cycle; ENTER/EXIT only fires for *changes*. A tag silently leaving the truck triggers EXIT — exactly the "missing item" signal you want.

Steady-state bandwidth in transit ≈ `(# changes per cycle) × rate`, near-zero for a stable load. Pair with Pattern 1 GPS to know *where* the loss happened.

### 5.3 Pattern 3 — Cold chain / sensor cycle (compliance)

> Use when: cargo includes sensor-enabled tags (RFMicron / Axzon / ams) and you need a temperature trail.

The tag-borne sensor mirror ([rfid-tag-data-model.md §3 D4](rfid-tag-data-model.md)) writes per-cycle sensor reads into `device_telemetry`. Bandwidth scales with `sensor_tag_count × cycle_frequency`, not with all tags. Non-sensor tags contribute nothing to the per-cycle bandwidth (dedup suppresses them).

Cold-chain regulators typically want one reading per N minutes. Set `rescan_interval_s=300` and you get a 5-minute compliance trail per sensor tag.

### 5.4 Pattern picker

| Customer ask | Pattern |
|---|---|
| "Where is my truck?" | 1 |
| "What's on my truck?" | 1 (manifest at load time is sufficient) |
| "Did anything fall off my truck?" | 2 |
| "Is the freezer truck staying cold?" | 3 |
| All four | 1 + 2 + 3 layered (each is independent) |

---

## 6. Ingestion & Event Bus

New EventBus topics (subscribers: rules engine, integration layer, audit):

| Topic | Producer | Payload |
|---|---|---|
| `asset.loaded` | Carrier load API | `{tenant_id, asset_id, parent_asset_id, at}` |
| `asset.unloaded` | Carrier unload API | `{tenant_id, asset_id, prior_parent_asset_id, at}` |
| `subject.zone_changed` *(extended)* | Ingestion | now also fires for `subject_kind='device'` when a mobile reader's GPS crosses a geofence (Sprint 17a) |

`subject.zone_changed` already carries `subject_kind` ([tracking-modes.md](tracking-modes.md), [assets-and-zones.md §5](assets-and-zones.md#5-ingestion-enrichment)) — no contract change. Adding `device` as a third value is forward-compatible; rules that match on `subject_kind='asset'` are unaffected.

Ingestion routing for `mobility='mobile'` reads:

1. Persist the `tag_read` exactly as today (still part of the cargo manifest).
2. **Skip** `get_zone_for_reader(device_id)` — that lookup is meaningless for a mobile reader.
3. If Sprint 17a is live and the device has a recent `…/location`, do point-in-polygon for **the device's** position and emit `subject.zone_changed` with `subject_kind='device'`.
4. The cargo (children of the device-bound asset, if any) is *not* re-evaluated for zone changes — its location is inherited via the view in §4.2. Inherited-location consumers (UI, exports) should treat `location_source='inherited'` distinctly from a direct fix.

---

## 7. Edge-Agent Configuration

Two new knobs in `EdgeConfig` ([edge-device-contract.md §3.9](edge-device-contract.md)):

| Key | Default | Notes |
|---|---|---|
| `location_min_interval_s` | 30 | Minimum seconds between `…/location` publishes |
| `location_min_distance_m` | 50 | Skip publish if movement since last fix < this distance |
| `rescan_interval_s` | 0 | Full re-inventory cycle interval; 0 disables (Pattern 1) |

Either-or for location: publish when `interval_s` **or** `distance_m` first triggers. Stationary trucks at a loading dock generate near-zero location traffic.

Implementation note: the reference client already has `submit_location` ([clients/pi/](../../clients/pi/)). Throttling logic is a small wrapper and a new unit test; ships in Sprint 14 alongside the topic wiring.

---

## 8. UI Implications

| Page | Change |
|---|---|
| Devices list | New column **Mobility** (chip: `fixed` / `mobile`) |
| Device detail | When `mobility='mobile'`: show GPS trail mini-map instead of "Covers zones" panel |
| Asset detail | New **Manifest** tab — recursive children with `current_location` (annotated `inherited` if from carrier) |
| Asset detail | New **Load / Unload** actions (editor+) |
| Map (Sprint 17a) | Carriers render as a moving icon with a click-through manifest pop-out |
| Rule wizard | New condition: `subject_kind='device'` for vehicle-geofence rules |
| Data Explorer | New filter: `mobility=mobile`; surface `location_source='inherited'` on tag-read joined views |

UI parity is a **release gate** for the sprint that lands the underlying schema.

---

## 9. Phasing

| Sprint | Adds |
|---|---|
| **14** | `EdgeConfig.location_min_interval_s` / `location_min_distance_m`; agent wiring of `…/location` |
| **15** | `devices.mobility` (migration 020a); `assets.parent_asset_id`; `binding_kind='device'`; load / unload APIs; manifest UI tab |
| **15b** | `stock_items.parent_stock_item_id` (promoted from backlog); inventory-side carrier semantics |
| **17a** | Geofence-driven `subject.zone_changed` for `subject_kind='device'`; carrier-on-map UI |
| **future** | Route adherence / ETA analytics modules; multi-leg trip orchestration |

None of the additions are breaking. A tenant that doesn't load anything sees no behavior change.

---

## 10. Decisions & Open Questions

### Resolved

| # | Question | Decision |
|---|---|---|
| 2 | Carrier zone events for cargo? | **No.** Inherited-location view is sufficient. Emitting per-cargo zone events when a carrier crosses a geofence would undo the bandwidth win that mobile-carrier mode is built around. |
| 3 | Snapshot column vs view for current location? | **View-only** until slow-query logs show the view in the top offenders. Don't denormalize speculatively. |
| 4 | Reader-as-asset uniqueness? | **Yes** — a `device_id` is bindable to at most one asset at a time, enforced by partial unique index, with the same bind / unbind / re-bind lifecycle as tag bindings. |
| 6 | Race on cross-dock load + unload? | **Serialize via `pg_advisory_xact_lock(asset_id)`** inside the load/unload transaction. Lightweight, scoped to the transaction, no new infrastructure. |
| 1 | Rename `tag_id` after extending `binding_kind` to `device`? | **Ship `binding_value` from day one.** Both `asset_tag_bindings` (Sprint 15) and `stock_items` (Sprint 15b) are *new* tables; there's no existing API consumer to deprecate. The originally-discussed deprecation-window approach (serialize both `tag_id` and `binding_value` for one release, OpenAPI marks old field deprecated) is unnecessary because the rename happens *before* the table reaches any consumer. **Out of scope:** `tag_reads.tag_id` (unrelated — that's the application-facing read identifier, not a binding identifier) and `quickstart.md` ingestion examples (those reference `tag_reads`). |
| 5 | Non-RFID carriers (TMS-pushed location)? | **Generic external-location ingestion endpoint, no vendor adapters in v1.** New hypertable `external_locations(tenant_id, asset_id, latitude, longitude, recorded_at, source, accuracy_meters?, speed_kph?, heading_deg?, metadata)` with RLS by `tenant_id` and the same compression/retention defaults as `device_telemetry`. New endpoint `POST /assets/{asset_id}/external-position` (editor+, tenant rate-limited — default 60/min). The `asset_current_location` view is updated to UNION the latest `external_locations` row with the latest `tag_reads`-derived position; a `latest_position_source` column lets the UI render "via Samsara" vs "via Reader-12." Asset detail page merges both sources in the timeline, badged by source. **Geofence engine ([geofencing-and-map.md](geofencing-and-map.md)) is source-agnostic** — it sees positions, not their provenance, so Sprint 17 rules fire on TMS positions for free. **TMS vendor adapters** (Samsara, Geotab, Motive, etc.) become a paid integration tier in `src/tagpulse/integrations/tms/` later, gated on a paying customer requesting a specific vendor; each adapter is a thin service that calls our generic endpoint on a polling schedule, so nothing built in v1 is wasted. |

### Still open

_(none currently — all open questions resolved.)_

---

## 11. Acceptance Criteria

- [planned] A tenant can mark a device `mobile` via API and UI; ingestion stops doing fixed-zone lookups for it.
- [planned] A truck (asset) can be loaded with N pallets (assets) via `POST /assets/{id}/load` in one batch call.
- [planned] `GET /assets/<truck>/manifest` returns the full tree with each child's current (or inherited) location.
- [planned] Edge agent in Pattern 1 mode generates ≤ `2N + (trip_minutes × 60 / location_min_interval_s)` events for a complete load → transit → unload cycle.
- [planned] Cold-chain demo: simulator emits a 5-minute temperature cycle from sensor-tagged cargo; rules engine fires `temperature_high` against `device_telemetry` rows with `metadata.source='tag'`.
- [planned] Removing a tag from the truck mid-cycle (Pattern 2) generates exactly one `tag_read` with `event_type='exit'` and one alert via the `stock.unexpected_in_zone` rule type ([tracking-modes.md](tracking-modes.md) §7).
