# Floor position estimation & asset path — BYO ingest + homegrown estimator

> Status: **Planning** — design-doc-first per the 3+-component convention; **no
> code on this branch** (`chore/floor-position-estimation-design`). Operationalizes
> the deferred estimator half of [ADR-024](../adr/024-position-estimation.md) and
> the deferred BYO-precomputed ingest from the Sprint 60 out-of-scope list. Sibling
> to [fixed-reader-positioning-and-warehouse-map.md](fixed-reader-positioning-and-warehouse-map.md)
> (which shipped the *reader placement* + *floor map render*); this doc designs the
> two ways to put an **asset** on that floor with a real `(x, y)` and a movement
> **path**.

## Why now

Sprint 64 shipped the warehouse floor map: reader pins, zone polygons, and a
floorplan backdrop all render. But asset markers **snap to the triggering
reader's antenna** — there is no true per-asset `(x, y)` and **no historical
floor trail**. The `asset_positions` hypertable that would hold a floor path was
created headless in Sprint 59 (migration 051) and **nothing writes to it**.

This is not a regression — it matches the Sprint 59/64 explicit out-of-scope —
but it leaves the "watch an asset move across the floor" story unbuilt. This doc
closes that gap in **two phases that share one table, one read endpoint, and one
UI trail layer**.

## What already exists (do not redesign)

| Capability | Status | Where |
|---|---|---|
| `antennas(device_id, port, x, y, z, label, gain_dbi)` | ✅ | migration 051; port-0 = reader nominal spot |
| `sites.coord_system` (floor frame, floorplan image) | ✅ | migration 051 + Sprint 64 follow-up |
| `asset_positions` hypertable (`source ∈ precomputed\|zone\|computed`) | ✅ **empty** | migration 051 — no writer |
| `tenants.position_strategy` JSONB | ✅ **unused** | migration 051 — created-not-used; this doc fills it |
| EPC→asset fusion (`AssetFusionService`) | ✅ | `src/tagpulse/services/asset_fusion.py` |
| Floor-zone resolution (point-in-polygon, `CRS.Simple`) | ✅ | `src/tagpulse/api/services/floor_zone_resolver.py` |
| Floor map render (reader pins, zones, floorplan) | ✅ | `TagPulse-UI`, Sprint 64 |
| BYO position ingest endpoint | ❌ deferred | Sprint 60 OOS — Phase 1 here |
| RSSI estimator (`rssi_weighted_centroid`) | ❌ deferred | [ADR-024](../adr/024-position-estimation.md) amendment — Phase 2 here |

**Key takeaway:** the schema, fusion lookup, and map render already exist. Both
phases below are *writers* into `asset_positions` plus a *reader* endpoint and a
UI trail layer on top of what shipped.

## The two phases are complementary, not either/or

The `asset_positions.source` enum was designed for multiple writers to coexist
— per tenant, and even per asset (different rows, different `source`, a
resolution policy picks the best available). The phases are sequenced so the
cheap one builds the seam the expensive one reuses.

```mermaid
flowchart TD
  subgraph Phase 1 — BYO precomputed  source=precomputed
    V[Vendor / RTLS engine] --> POST[POST /assets/id/position]
    POST --> AP[(asset_positions)]
  end
  subgraph Phase 2 — Homegrown estimator  source=computed
    RD[(tag_reads: epc, antenna, rssi, cnt)] --> FU[AssetFusionService ✅]
    FU --> EST[rssi_weighted_centroid estimator]
    ANT[(antennas x,y ✅)] --> EST
    CFG[tenants.position_strategy] --> EST
    EST --> AP
  end
  AP --> READ[GET /assets/id/floor-path]
  READ --> UI[Floor map: dot + trail layer]
```

| | Phase 1 — BYO precomputed | Phase 2 — Homegrown estimator |
|---|---|---|
| Who computes `(x,y)` | External vendor / RTLS | TagPulse |
| `source` | `precomputed` | `computed` |
| Extra hardware | Often yes (vendor middleware / UWB) | **None** — reuses placed readers |
| Accuracy | Vendor-grade (10–50 cm typical) | RSSI-grade (~2–5 m, degrades to zone) |
| Effort / risk | ~1 sprint, low | Multi-sprint, real R&D |
| Helps TagPulse-only RFID customer? | ❌ | ✅ |

**Sequencing rationale:** Phase 1 builds the table-write path, the
`GET …/floor-path` read endpoint, and the UI trail layer — all of which Phase 2
reuses unchanged. Phase 1 also unblocks any customer who already owns a location
engine. Phase 2 then adds only the estimator that produces `source='computed'`.

---

## Phase 1 — BYO precomputed positions (`source='precomputed'`)

TagPulse does no math: an external positioning system (vendor middleware, UWB,
BLE-AoA, or a self-locating AMR) pushes a fix, we store and draw it.

### New endpoint — write

```
POST /assets/{asset_id}/position
{
  "site_id": "<uuid>",
  "x": 142.5, "y": 88.0, "z": 1.2,        // floor-frame units (site coord_system)
  "confidence": 0.82,                       // 0..1
  "recorded_at": "2026-06-19T14:03:11Z",   // optional; server time if omitted
  "metadata": { "engine": "itemsense", "fix_id": "abc123" }
}
→ 201, writes one asset_positions row, source='precomputed'
```

Validation: cross-tenant guard on `asset_id`/`site_id` (foreign tenant → 422),
`0 ≤ confidence ≤ 1`, `(x, y)` finite, optional bounds-check against
`coord_system.extent_*` (warn, not reject — vendors may use a different origin).

### New endpoint — read (shared with Phase 2)

```
GET /assets/{asset_id}/floor-path?since=<ts>&until=<ts>&limit=N&source=<filter?>
→ list[FloorPathPoint] { recorded_at, x, y, z?, confidence, source, site_id }
```

Distinct from the existing lat/lon `GET /assets/{id}/path` (geographic). On a
fixed-reader floor site with no GPS, that geographic path is empty; this is its
floor-frame counterpart.

### UI (TagPulse-UI)

A trail layer on the existing `CRS.Simple` floor map: latest fix as a dot,
prior fixes as a fading polyline. Confidence drives opacity. Reuses the Sprint 64
map; no new map.

---

## Phase 2 — Homegrown RSSI estimator (`source='computed'`)

Compute `(x, y)` from the RSSI of the **fixed readers already placed on the
floor** (`antennas.(x, y)`), no extra hardware. This is the deferred ADR-024
`rssi_weighted_centroid` estimator, **extended with a temporal (recency)
weight** — the gap surfaced in design review (a naive centroid treats stale and
fresh antenna reads as equal and smears a moving asset across its recent path).

### Algorithm — decay-weighted, hull-bounded centroid

Relative, bounded, calibration-free (per ADR-024 v2 — *not* absolute
RSSI→distance ranging, which needs per-site calibration and is fragile):

1. **Fuse** each read's EPC → `asset_id` (Sprint 59 `AssetFusionService`).
2. **Group** by asset; per `(asset, antenna)` keep the **strongest** tag (best-
   oriented face — defeats orientation nulls on multi-tag items).
3. **Weight** each contributing antenna's `(x, y)` by RSSI × count × **recency
   decay**, take the centroid, **bounded to the convex hull** of the
   contributing antennas (can never produce an off-floor jump).

```
w_i = g(rssi_i) · h(cnt_i) · decay(Δt_i)

  Δt_i      = t_now − server_ts_i          # age of antenna i's latest observation
  decay(Δt) = 0.5 ^ (Δt / τ)               # τ = half_life_s (per-tenant config)

(x, y) = Σ w_i · (x_i, y_i) / Σ w_i        # clamped to the antennas' convex hull
```

`τ` is the single dial between "average everything" and "last one wins":

| τ | Behavior | Fits |
|---|---|---|
| τ → 0 | only the freshest antenna survives → snaps to it (= choke-point) | **last-one-wins**; fast assets, sparse readers |
| τ small (~1–3 s) | recent reads dominate | forklifts, AMRs |
| τ large (~30 s) | approaches plain centroid | parked pallets |
| τ → ∞ | unweighted centroid | truly static |

### Graceful degradation (honest confidence)

- **1 antenna** → snap to it (choke-point), confidence ~0.3
- **2 antennas** → weighted point on the line, ~0.45
- **3+ antennas** → real `(x, y)`, ~0.6–0.8

Confidence folds in the **Σ of decay weights** (effective fresh-antenna count),
not the raw antenna count — a fix from one fresh + two stale reads scores lower.

### Worked example (τ = 3 s)

| Antenna | `(x, y)` | rssi | cnt | age | decay | final w |
|---|---|---|---|---|---|---|
| R1.p1 | (10, 10) | −55 | 20 | 0.5 s | 0.89 | 11.6 |
| R2.p1 | (40, 10) | −67 | 8 | 6 s | 0.25 | 0.25 |
| R3.p1 | (25, 35) | −61 | 12 | 2 s | 0.63 | 4.4 |

```
x = (11.6·10 + 0.25·40 + 4.4·25) / 16.2 ≈ 14.5
y = (11.6·10 + 0.25·10 + 4.4·35) / 16.2 ≈ 16.8
```

vs. the time-agnostic centroid (16.4, 18.3): recency pulls toward the fresh/
strong R1 and discounts the 6 s-stale R2. With τ→0 it collapses to R1 = (10,10).

### Emit model — server-side recompute tick (Option C)

The estimator trigger is **decoupled from the wire `t=0` snap**. Two reasons:
(1) the wire snap cadence (default 300 s) is a **cellular-bandwidth** knob owned
by the producer, not a positioning parameter; (2) the current dev simulators
emit **v1 HTTP reads with no snaps at all**, so a literally-snap-triggered
estimator could not be exercised in dev.

Instead: a positioning worker keeps a rolling **per-`(asset, antenna)` buffer**
stamped with **server ingest time**, and recomputes on a configurable interval
`D`:

```
State (per tenant/site, in-memory):
  latest[(asset_id, antenna_id)] = { rssi, cnt, server_ts }

On each ingested read (v1 HTTP, v2 t=1, or v2 snap entry):
  fuse epc → asset_id;  latest[(asset_id, antenna)] = { rssi, cnt, server_ts: now }

Every D seconds (the tick):
  for each asset with a fresh observation since its last fix:
    obs = latest[asset] entries with (now − server_ts) ≤ lookback_s
    (x, y), confidence = rssi_weighted_centroid(obs, position_strategy)
    INSERT asset_positions(source='computed', time=now, x, y, confidence, …)
```

- **Server time** ⇒ we never compare reader clocks → no cross-reader NTP
  requirement. Cost: ingestion-latency jitter (tens of ms; negligible at
  warehouse speed) and loss of reader-side sub-second precision.
- The tick is gated on "has a fresh observation" so a parked, unseen asset does
  not write duplicate rows.
- The wire `t=0` snap is still consumed — it just updates the buffer like any
  other read; it is not the trigger.

### `tenants.position_strategy` config (the two knobs + guards)

```jsonc
{
  "strategy": "rssi_weighted_centroid",
  "half_life_s": 5.0,            // τ — recency dial (→0 = last-wins)
  "recompute_interval_s": 3.0,   // D — server tick cadence (the real "cadence")
  "lookback_s": 15.0,            // hard cutoff (~3·τ); older obs dropped
  "min_antennas": 1,             // below this → no fix written
  "rssi_floor_dbm": -75          // ignore weaker-than antennas
}
```

Defaults ship in code; a tenant overrides without a deploy. Start **per-tenant**;
a later refinement could key `τ` off asset category / `devices.mobility`.

---

## Decisions (resolved in discussion)

| # | Question | Decision |
|---|---|---|
| D1 | Both options either/or? | **No — complementary.** `source` enum lets `precomputed` + `computed` coexist per tenant and per asset; a resolution policy prefers `precomputed > computed > zone`. Phase 1 first (builds the shared seam), Phase 2 second. |
| D2 | Clock source for recency | **Server ingest time.** No cross-reader clock sync required; accept latency jitter. |
| D3 | Emit trigger | **Option C — server-side recompute tick** every `recompute_interval_s`, decoupled from the wire `t=0` snap. Works with the existing v1 HTTP simulators; prod consumes v2 deltas + snaps into the same buffer. |
| D4 | Recency model | **Exponential decay** with half-life `τ = half_life_s`; `τ→0` is the "last-one-wins" extreme. One formula, one dial. |
| D5 | Config location | **`tenants.position_strategy`** (the created-not-used JSONB column). Two primary knobs: `τ` (`half_life_s`) and `D` (`recompute_interval_s`). |
| D6 | Estimator output bounds | **Convex hull of contributing antennas.** No off-floor jumps; graceful 1/2/3-antenna degradation with honest confidence. |

## Open questions / `[NEEDS DECISION]`

- **`[NEEDS WM]` v2 wire-format changes.** The estimator may want additive wire
  fields to sharpen positioning — e.g. ADR-024's optional **`rpk`** (peak-RSSI,
  vs. the cycle-mean `rssi`), or per-entry observation timestamps. **Any v2
  change must be finalized with WM** as protocol partner (per
  [edge-wire-format-v2.md §8](edge-wire-format-v2.md)). This doc does **not**
  lock a wire change; it lists candidates for that conversation.
- **`[NEEDS DECISION]` v2 snap simulator (dev tooling).** Current simulators are
  v1 HTTP (no snaps). A v2-emitting simulator (wrap `WmV2Producer` with a short
  `snap_period_s`, publish over MQTT) is needed to exercise the real v2 path —
  **scheduled after the WM wire-format conversation finalizes**, not on the
  critical path for building/testing the estimator (Option C runs off v1 reads).
- **`[NEEDS DECISION]` stationary jitter.** Short `τ` makes a parked asset's fix
  hop as different antennas win each tick. Candidate: a small hysteresis (don't
  move the stored point unless the new fix differs by > X units). Second-order;
  defer to tuning.
- **`[NEEDS DECISION]` per-category τ.** Start per-tenant; revisit if forklifts
  and parked pallets in one tenant need different recency.

## Out of scope

- The `source='zone'` retrieval-time fallback (zone-centroid when no `(x, y)`) —
  separable; the floor-zone resolver already answers zone-level "where is X".
- 3D / `z` positioning — `antennas.z` stays an optional estimator input; no 3D map.
- `geo_anchor` unified geo+floor overlay — deferred per the sibling doc.
- Any wire-format change shipped without WM sign-off.

## Phasing

| Phase | Scope | Rough shape |
|---|---|---|
| **1 — BYO precomputed** | `POST /assets/{id}/position`, `GET …/floor-path`, UI trail layer, resolution policy | ~1 sprint, low risk |
| **2 — Homegrown estimator** | `rssi_weighted_centroid` + recency, positioning worker (Option C tick), `position_strategy` validation, confidence, ground-truth test | multi-sprint; amends [ADR-024](../adr/024-position-estimation.md) to mark the estimator **Accepted/implemented** |
| **(after WM)** | v2 wire-format additions (e.g. `rpk`) + v2 snap simulator | gated on WM protocol sign-off |

Implementation will be scheduled as roadmap sprint(s); this branch carries the
design only.
