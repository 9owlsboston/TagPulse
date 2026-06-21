# Sprint 71 — Asset state consolidation (Phase 1)

- Status: **In progress** (2026-06-20). Backend [#142](https://github.com/9owlsboston/TagPulse/pull/142) + UI [#108](https://github.com/9owlsboston/TagPulse-UI/pull/108).
- Owner: backend consolidation tick + history; UI Asset "Current" card. Cross-repo.
- Related: [ADR-034 (asset state consolidation)](../adr/034-asset-state-consolidation.md),
  [ADR-024 (position estimation — recompute tick)](../adr/024-position-estimation.md),
  [floor-position-estimation.md](floor-position-estimation.md),
  [`positioning.py` `PositionStrategy`](../../src/tagpulse/services/positioning.py),
  `subject_current_zone` (migration 027), `overlapping_zones.py`.

## 1. Goal

Consolidate an asset's bound tags (`a`, `b`, `c`) into **one** asset-level answer
to the two questions the Assets page must answer:

> **Where** is X (and where *was* it), in site/zone terms?
> **What** temperature / humidity is it (and what *was* it)?

…with a single trustworthy answer per asset instead of three jittering per-tag,
last-writer-wins answers.

The worked scenario is a **cold-chain milk lot**: loaded at an origin DC
(fixed-reader zone) → onto a truck (geo reader) → transit → SuperMart DC
(fixed) → transit → SuperMart store (fixed). The asset crosses **frames** over
time; the transitions are first-class custody events.

**Non-goals (Phase 1):** explicit transit **legs** + ETA + leg-level SLA
(→ Phase 2); excursion/threshold alerting redesign (the fused mean does **not**
replace raw-reading alerting — see §6); cross-frame numeric voting (a dock zone
is never ranked against a highway GPS point — see §4).

## 2. Decisions locked with the user

| # | Decision |
|---|---|
| Zone vote | `read_count × recency`-weighted **vote** across bound tags |
| Environment | `read_count × recency`-weighted **mean** (temp/humidity), frame-agnostic |
| Recency-decay (τ) | per-tenant `fusion_strategy.half_life_s` (`0` = last-wins) |
| Cadence / window | `recompute_interval_s` (tick D) + `lookback_s` (window) |
| Compute | periodic **recompute tick** → `asset_state_history` hypertable (ADR-024 "Option C") |
| Frames | per-read in-frame resolution + **frame-tagged custody timeline**; recency arbitrates handoffs |
| Phasing | Phase 1 = zone + env + frame + custody events; **legs = Phase 2** |

## 3. The consolidation tick

A gated worker (mirrors the positioning worker pattern; off by default until
validated on `demo-wm-dc`). Every `recompute_interval_s`, per tenant, per asset
with reads in the last `lookback_s`:

1. Gather the asset's bound-tag reads (resolve bindings URI-or-hex per ADR-033)
   over the window — one `tag_reads` row already carries `read_count`,
   `temperature_c`, `humidity_pct`, RSSI, and reader/zone/GPS.
2. Compute each read's weight `w = read_count × 0.5 ** (Δt / τ)`.
3. **Location:** resolve each read to `(frame, zone)` in its own frame; pick the
   `Σw`-max zone (generalizes `overlapping_zones.py` attribution to per-asset).
4. **Environment:** `Σ(w · value) / Σw` for temperature and humidity.
5. Write one snapshot row to `asset_state_history`; if `frame` changed vs the
   prior snapshot, emit a **custody event**.

The same `w` drives steps 3 and 4 → location and environment are mutually
consistent (same reads, same weights).

> **Phase-1 implementation note.** The tick writes `asset_state_history` and
> emits `ASSET_CUSTODY_CHANGED` only. `GET /assets/{id}/state` reads the latest
> row directly, so Phase 1 does **not** warm `subject_current_zone` /
> `LATEST_TELEMETRY_CACHE` — those are written by the existing signaling /
> telemetry paths, and having the consolidation tick also write them would
> create a dual-writer race. Promoting the fused zone into `subject_current_zone`
> (making consolidation authoritative) is a deliberate follow-up once the worker
> is validated and flipped on.

## 4. Frames & custody

```
t0  Origin DC, Dock 3      reader   "Dock 3"            fixed reader
t1  Loading                reader+geo  OVERLAP          dock + truck both see a,b,c
t2  In transit             geo      "in transit" + fix  truck reader only
t3  Arrive SuperMart DC    geo→reader  OVERLAP          truck → DC dock handoff
t4  DC cold room           reader   "Cold Room A"       fixed
t5  In transit to store    geo      "in transit" + fix
t6  SuperMart store        reader   "Backroom Chiller"  fixed
```

- Frames are **temporally exclusive** except at handoffs. The recency decay
  flips the winning frame automatically as one frame's reads stop and decay.
- `frame ∈ {reader, floor, geo, none}` on each snapshot. A change emits a custody
  event (`departed`, `in-transit`, `arrived`, `at-store`).
- **Geo "zone" in Phase 1** = the string `"in transit"` + last lat/lon fix. The
  route **leg** (origin → DC) is **Phase 2**.

## 5. Schema — `asset_state_history`

TimescaleDB hypertable, tenant-scoped (RLS), with a retention policy.

| Column | Type | Notes |
|---|---|---|
| `time` | `timestamptz` | tick time (server-ingest frame, per ADR-024) |
| `tenant_id` | `uuid` | RLS |
| `asset_id` | `uuid` | |
| `frame` | `text` | `reader` / `floor` / `geo` / `none` |
| `zone_id` | `uuid` null | resolved zone (reader/floor frames) |
| `site_id` | `uuid` null | |
| `lat` / `lon` | `double` null | geo frame last fix |
| `x` / `y` | `double` null | floor frame |
| `temperature_c` | `double` null | weighted mean |
| `humidity_pct` | `double` null | weighted mean |
| `sample_count` | `int` | reads that fed this tick |
| `tag_count` | `int` | distinct bound tags that contributed |
| `confidence` | `double` null | geometry × freshness × agreement (later) |

"Is" = latest row per asset; "was" = range query. Zone timeline + environment
series come from the **same** rows.

## 6. Cold-chain / alerting caveat

A weighted **mean** can mask a short excursion on one tag. Phase 1 fuses to a
mean **for display/trend**; it does **not** subsume threshold alerting. Alerting
keeps seeing raw per-tag readings (and may add a windowed max/min) — tracked as a
follow-up. The `frame` on each snapshot gives alert-routing context (excursion
*in transit* vs *at DC*).

## 7. Config — `fusion_strategy`

Generalize the `tenants.position_strategy` JSONB (modeled by `PositionStrategy`)
to a tenant-level **`fusion_strategy`** governing location *and* environment
fusion. Knobs already exist: `half_life_s` (τ), `recompute_interval_s` (D),
`lookback_s`, `min_antennas`. Gated off by default (a `fusion_enabled` flag,
mirroring `position_estimator_enabled`).

## 8. UI — Asset "Current" card

The Asset detail page gains a "Current" card: fused zone/site (frame-aware
label), current temp/humidity, last-seen, contributing tag count, and a custody
timeline / mini history (zone changes + temp/humidity sparkline) from
`asset_state_history`. Consumes a new `GET /assets/{id}/state` (+ history) — the
backend regenerates `openapi.json` (**merges first**), UI runs `generate-api`.

## 9. Open / deferred

- **Phase 2:** transit legs (origin → DC → store), ETA, leg-level SLA.
- **Confidence** column population (geometry × freshness × tag agreement).
- **Excursion alerting** against raw per-tag readings (not the fused mean).
- Reader→frame registry attribute (fixed = facility zone; mobile = geo).
