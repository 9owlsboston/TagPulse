# ADR-034: Asset state consolidation — weighted fusion of bound-tag reads into one asset zone + environment

- Status: **Accepted** (sprint-71/asset-state-consolidation, June 2026)
- Scope: `TagPulse` backend — a per-asset consolidation tick that fuses an
  asset's bound-tag reads into one location answer (zone/site) and one
  environment answer (temperature/humidity), with history; plus the
  `TagPulse-UI` Asset "Current" surface that consumes it.
- Related: [ADR-024 (indoor position estimation — recompute tick)](024-position-estimation.md),
  [ADR-013 (subject-scoped telemetry)](013-telemetry-subject-scoping.md),
  [ADR-028 (tags as a first-class entity)](028-tags-as-first-class-entity.md),
  [ADR-033 (epc bindings resolve URI-or-hex)](033-epc-binding-resolves-uri-or-hex.md),
  [`positioning.py` `PositionStrategy`](../../src/tagpulse/services/positioning.py),
  `subject_current_zone` (migration 027), `overlapping_zones.py`,
  [Sprint 71 design doc](../design/sprint-71-asset-state-consolidation.md).

## Context

An asset is bound to one or more tags via `asset_tag_bindings` (ADR-028). A lot
of milk **X** may carry tags `a`, `b`, `c`. Each tag streams reads independently,
and each read can carry location signal (reader/zone, RSSI, or GPS) **and**
environment signal (temperature, humidity) in `tag_reads.sensor_data`.

Today every consolidation surface is **per-tag, last-writer-wins**:

- `subject_current_zone` (migration 027) is written through from the dwell
  worker per subject, but the asset's "current zone" is whichever bound tag
  last fired — no vote across `a`, `b`, `c`.
- `LATEST_TELEMETRY_CACHE` keeps the latest temperature/humidity per
  subject+metric — again last-writer, no aggregation across the asset's tags.
- `overlapping_zones.py` already does **RSSI + recency**-weighted zone
  attribution **per read**, but the result is not fused across an asset's tags.
- `read_count` is now carried on reads (Sprint 70, PR #139) but is **unused as a
  weight**.

So the Assets page cannot answer the two questions an operator actually asks:

> Where is X (and where *was* it), in site/zone terms? What temperature /
> humidity is it (and what *was* it)?

…with one trustworthy answer per asset rather than three jittering per-tag ones.

A second gap is **frame handoff**. A real asset journey crosses frames over
time — origin dock (fixed **reader** frame) → truck (**geo** frame) → DC (reader)
→ store (reader) — and the meaningful events are the *transitions*. No surface
models the asset's current **frame** or emits custody events on change.

## Decision

Introduce a per-asset **consolidation tick**: a periodic worker that, every
`recompute_interval_s`, looks back `lookback_s` over an asset's bound-tag reads
and writes **one fused snapshot per active asset** to a new
`asset_state_history` hypertable. The new `GET /assets/{id}/state` reads the
latest row directly. (Promoting the fused zone into `subject_current_zone` /
warming the latest-telemetry cache is a deliberate follow-up — see Consequences
— to avoid a dual-writer race with the existing signaling/telemetry paths.)

### 1. Weighting — `read_count × recency`, shared

A read's weight is `read_count × 0.5 ** (Δt / τ)` where `τ` is the per-tenant
half-life (the recency dial; `τ → 0` = last-wins). The **same weight** drives
both the location vote and the environment mean, so location and environment
stay mutually consistent (they are the same reads).

### 2. Location — weighted zone vote

Each read resolves to a `(frame, zone)` in **its own frame** (reader→zone map,
floor point-in-polygon, or geo geofence). The fused zone is the
`read_count × recency`-weighted **vote** across the asset's reads in the look-back
window. This generalizes the per-read attribution already in
`overlapping_zones.py` to a per-asset answer.

### 3. Environment — weighted mean

Temperature and humidity are the `read_count × recency`-weighted **mean** across
the asset's reads. Environment is **frame-agnostic** — the cold chain is
continuous whether the lot is on a dock, on the highway, or in a store chiller.

### 4. Frames — per-read resolution + custody timeline

Frames are **temporally exclusive** in the steady state (an asset is in
reader-world *or* geo-world); the only concurrency is the brief **handoff
overlap** at a loading/arrival dock. The recency decay arbitrates the handoff
automatically: as the truck pulls away, dock-reader reads decay and geo reads
dominate, flipping the winning frame. Each `frame` change is recorded as a
**custody event** (`departed`, `in-transit`, `arrived`, `at-store`). We do **not**
numerically rank a dock zone against a highway GPS point — the vote operates
*within* a frame, and across the overlap only to pick which frame is current.

### 5. Compute — recompute tick + history table (the positioning "Option C")

This is the [ADR-024](024-position-estimation.md) server-side recompute-tick
pattern, generalized from floor `(x,y)` to the whole asset state. One snapshot
row per active asset per tick → `asset_state_history` (TimescaleDB hypertable,
RLS, retention). "Is" = latest row; "was" = query the table; zone timeline and
temp/humidity series come from the **same fused rows**.

### 6. Config — generalize `position_strategy` → `fusion_strategy`

The knobs already exist on
[`PositionStrategy`](../../src/tagpulse/services/positioning.py) (the
`tenants.position_strategy` JSONB): `half_life_s` (τ), `recompute_interval_s`
(the tick cadence D), `lookback_s` (the window). They are currently
positioning-specific and **gated off** ("created-not-used"). We generalize the
column to a tenant-level **`fusion_strategy`** that governs location *and*
environment fusion, with `half_life_s` as the shared recency dial.

### Phasing

- **Phase 1 (this sprint):** fused zone + environment + `frame` + custody events;
  geo-zone is "in transit" + last fix. Asset "Current" card in the UI.
- **Phase 2 (later):** explicit transit **legs** (origin → DC → store) with
  ETA and leg-level cold-chain SLA. Tracked in the design doc / backlog.

## Consequences

- The Assets page gets one trustworthy `(zone, site, temp, humidity, frame)`
  per asset, plus a history for "was". Supersedes per-tag last-writer-wins.
- **Reuses substrate:** `subject_current_zone` (current zone),
  `LATEST_TELEMETRY_CACHE`, subject-scoped `telemetry_readings`,
  `overlapping_zones` attribution, and the `asset_current_location` view (now
  dual-match per ADR-033). The new piece is the tick + `asset_state_history`.
- **Phase-1 scope:** the worker writes `asset_state_history` + emits custody
  events only; the new endpoint reads that table. Making consolidation
  authoritative for `subject_current_zone` (write-through) and warming
  `LATEST_TELEMETRY_CACHE` from the fused values are deferred follow-ups (avoid a
  dual-writer race with the existing signaling/telemetry writers).
- **Gated off by default**, mirroring the position estimator
  (`position_estimator_enabled`), until validated on `demo-wm-dc`.
- **Cold-chain caveat:** we fuse to a **mean** (decision #3 above). A weighted
  mean can mask a short excursion on one tag. Threshold/excursion **alerting**
  is therefore *not* subsumed by this mean — alerting should keep seeing raw
  per-tag readings (and/or a windowed max/min), tracked as a follow-up. The
  `frame` on each snapshot gives alert-routing context (excursion *in transit* vs
  *at DC* routes to different owners).
- **Reader→frame registry:** fixed readers carry a facility zone; mobile/truck
  readers are flagged geo. This adds a `frame`/mobility attribute to the reader
  registry + the geo source (design doc §reader-frame).
- A future hardening could compute confidence per snapshot (geometry × freshness
  × tag agreement), reusing the positioning `_confidence` shape.
