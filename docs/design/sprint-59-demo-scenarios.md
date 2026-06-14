# Sprint 59 — Demo scenario depth + spatial foundation

> Status: **Active** — kicked off on `sprint-59/demo-scenarios`
> (backend PR [#89](https://github.com/9owlsboston/TagPulse/pull/89)).
> Supersedes the scratch notes in `/tmp`.

## Sprint goal

Two tracks with different engineering postures.

**Track 1 — Demo scenario depth** (*compose, don't rewrite*): set up the data
surface so each business domain tells a complete, realistic story. Concretely:

1. Separate demo/simulation data by **Inventory Management** vs **Asset
   Tracking** — a purpose-built tenant for each, alongside the combined
   Sprint 58 tenant.
2. Author **extensive, realistic business scenarios** for both domains and use
   them to drive the demo data, simulation data, and seeding scripts.

**Track 2 — Spatial foundation** (*lay the schema, defer the math*): close the
GPS-bias gap in the location model so the platform can eventually answer "where
is X on the floor" in `(x, y)`. Concretely:

3. Land the minimal **schema** — an `antennas` table holding per-antenna
   `(x, y, z)`, a site `coord_system`, and an `asset_positions` hypertable —
   and **amend [ADR-024](../adr/024-position-estimation.md)** (position moves
   from `devices` to `antennas`; estimator contract becomes antenna-keyed +
   asset-grouped).
4. Build the **EPC→asset multi-binding fusion** lookup that both zone-presence
   and future positioning depend on.

Track 2 **defers all RF positioning math** — no `rssi_weighted_centroid`
estimator, no `rpk` wire-format change, no BYO ingest endpoint in Sprint 59
(see [Track 2 — Spatial foundation](#track-2--spatial-foundation-schema--fusion)
and Out of scope).

## Theme

Sprint 58 gave us *one* combined "SuperMart Distribution Center" tenant + a continuous
simulator, composed from the four existing scripts. It's credible at a glance
but it's a generalist — inventory and asset-tracking data share one tenant, so
neither domain tells a complete business story. Sprint 59 splits the demo
surface into **two purpose-built tenants, each with an extensive, realistic
business scenario**, and teaches the seed composer + `sim_loop` to drive
multiple tenants from named profiles.

**Cross-repo.** Backend-only, same as 58. The UI runs unchanged against
whichever tenant you point it at (login by tenant slug/UUID).

**Carry-forward principle (unchanged from 58).** *Compose, don't rewrite.*
Extend the existing simulators via data/profiles and narrow shims; don't open
their internals or add API endpoints (query-param toggles on existing routes
are fine).

## Tenant layout (three total)

| Tenant | Slug | Role |
|---|---|---|
| SuperMart Distribution Center | `demo-wm-dc` | **kept as-is** — the combined "everything on one screen" tenant; still used by the Sprint 58 baseline doc |
| *Inventory* domain tenant | `demo-inv-coldchain` | cold-chain / perishable + pharma **inventory management** story |
| *Asset* domain tenant | `demo-asset-*` | high-value mobile **asset tracking** story |

All three use deterministic `uuid5(NAMESPACE_DNS, "<slug>.tagpulse.local")`
identity (same scheme as 58) so re-runs converge.

## The two business scenarios

### 1. Inventory Management — "Cold-Chain Distribution Center"

Builds on `simulate_inventory.py` (products → lots → stock_items →
stock_movements), scaled from 4 SKUs to a credible catalog.

- **Catalog:** ~12–15 SKUs across 3–4 categories (vaccines/biologics, dairy,
  produce, dry goods), each with **multiple lots at staggered expiries** so
  FEFO picking is visible.
- **Flow:** Receiving → Cold Storage → Pick Floor → Shipping, with per-unit
  timelines (already modelled — extend the roster).
- **Business events that drive alerts / empty-state coverage:**
  - **Near-expiry lots** → `stock.expiring_within` fires on 2–3 lots.
  - **Low-stock SKUs** → reorder-threshold alert on 1–2 SKUs.
  - **Cold-chain excursion** → temperature-shaped read on a Cold Storage reader
    (reuses the `seed_alerts` hybrid path).
  - **Quarantine / hold** → a lot parked in a non-flow zone (seeded movement,
    no new schema).
  - **Cycle-count discrepancy** → optional narrow shim writing a count vs
    system delta.

### 2. Asset Tracking — "Returnable / High-Value Asset Fleet"

Builds on `simulate_assets.py` (assets → EPC → zone transitions,
`subject.zone_changed`).

- **Roster:** ~20–30 named assets in 2–3 categories (e.g. forklifts / totes /
  reusable containers, *or* hospital mobile equipment — pick one narrative in
  Phase A).
- **Topology:** 1 site, 6–8 zones incl. at least one **geofenced "authorized
  area"** and a yard/exit zone.
- **Business events:**
  - **Geofence breach** → asset reads at the exit/unauthorized zone → geofence
    rule.
  - **Inactivity / dwell** → asset with no read for N hours → inactivity rule
    (utilization story).
  - **Transfer in flight** → reuses `seed_transfer.py` (asset handed to
    recipient tenant).
  - **Maintenance-due / idle utilization** → seeded via existing
    telemetry/threshold path.
  - **Missing asset** → one asset deliberately goes dark for the "where is X"
    demo.

## Track 2 — Spatial foundation (schema + fusion)

Track 1 makes the demo *look* complete; Track 2 closes a real capability gap the
demo can't paper over. The location model is **GPS-biased**: `tag_reads` carry
`latitude/longitude`, `sites` carry a GPS anchor + address, and zones are GPS
bounding boxes / GeoJSON polygons. There is no way to express *where an antenna
sits on a floor* or *where an asset is in `(x, y)`*. "Where is X" is answerable
only at **zone** granularity today (via `tag_presence` + `subject_current_zone`),
which is the honest, shippable headline — but the schema can't represent a
floor-plan coordinate at all.

Track 2 lands the **schema and the fusion lookup only**. It writes nothing that
computes a position from RF — that math is a Sprint 61 spike.

### Why antennas, not devices

[ADR-024](../adr/024-position-estimation.md) originally put `position_*` columns
on `devices`. That is the wrong grain for fixed positioning readers: a single
reader fans **2–8 antennas** across tens of metres of coax, and each antenna is
a distinct radiator at a distinct `(x, y)`. Position must live **per antenna**.
The wire format already supports this — v2 carries the antenna port (`an`) per
`(epc, antenna)` observation with its own `rssi` and `cnt`, and explicitly emits
one entry per antenna when a tag is heard on several
([edge-wire-format-v2.md](edge-wire-format-v2.md) §2.2). So the antenna signal
positioning needs is *already on the wire*; Track 2 just gives each antenna a
surveyed coordinate to anchor it.

### Schema (additive — amends ADR-024)

- **`antennas`** — new normalized table: `id`, `device_id` FK, `port`
  (matches `tag_reads.reader_antenna`), `x`, `y`, `z` (nullable mount height),
  `label`, `gain_dbi` (nullable). Per-antenna `(x, y)` replaces ADR-024's
  `devices.position_*`.
- **`sites.coord_system`** — JSONB frame (units, extent, origin anchor,
  rotation, optional geo-anchor) per the ADR-024 shape. `NULL` ⇒ geographic-only
  (today's behaviour).
- **`asset_positions`** — new hypertable: `time`, `tenant_id`, `asset_id`
  (no FK — hypertable, matches ADR-013/014), `site_id`, `x`, `y`, `z`,
  `confidence`, `source` enum (`precomputed | zone | computed`), `metadata`.
  Sprint 59 **creates** it; it writes nothing to `source=computed` (Sprint 61)
  and nothing to `source=precomputed` (Sprint 60 BYO ingest).

### EPC→asset multi-binding fusion

A real item carries **2–3 tags** (top + 2 sides) so at least one face is
readable in any orientation — modelled by `categories.required_tags ≥ 1` and
`asset_tag_bindings` (one asset → many active EPCs, with `bound_at`/`unbound_at`
history; **no** uniqueness on `asset_id`). Track 2 adds the service that resolves
EPC → `asset_id` and groups an asset's tags. This backs **zone-presence** today
("this asset is here, seen via any of its tags") and is **step 1 of the future
positioning pipeline** (Sprint 61: per-`(asset, antenna)` take the strongest tag
= best-oriented face, then weighted-centroid over antenna `(x, y)`).

### What Track 2 deliberately does NOT do in Sprint 59

- **No `rssi_weighted_centroid` estimator** — the relative-RSSI, count-weighted,
  asset-grouped solver is a Sprint 61 spike validated against surveyed positions.
- **No `rpk` wire-format change** — v2 already carries `rssi` (cycle mean) + `cnt`
  per `(epc, antenna)`. A peak-RSSI field (`rpk`, optional, omit when `cnt==1`)
  is an *additive* Sprint 61 candidate, added only if surveyed data shows mean
  dilutes the signal. The per-tenant **weight formula is config, not wire/code**
  (`position_strategy`).
- **No BYO ingest endpoint** — `POST /assets/{id}/position` (vendor `(x, y)` →
  `source=precomputed`) + retrieval with zone fallback + floor-map render is
  Sprint 60 phase H.

## Phases

| Phase | Item | Pass bar |
|---|---|---|
| **A — 59.1 Scenario design** | Design doc `docs/design/sprint-59-demo-scenarios.md`: two business narratives, catalog/asset rosters, per-tenant rule + alert mix, profile model, multi-tenant rate-cap math. Pick the asset narrative. Lock decisions D1–D5 (below). | Doc reviewed; no scope creep without an OOS note |
| **B — 59.2 Profile-driven composer** | Parametrize `seed_demo_tenant.py` with a `--profile {combined,inventory,asset}` (default `combined` = today's behaviour, unchanged). New `make demo-inventory` / `make demo-asset` targets; reset targets per tenant. | Each profile idempotent; existing `make demo-tenant` byte-for-byte unchanged |
| **C — 59.3 Scenario depth** | Extend inventory catalog + asset roster; add the narrow shims for the events above (quarantine, cycle-count, geofence-breach read sequences, inactivity gaps, missing-asset). All via composition / existing write paths. | Every page in both tenants shows non-empty, domain-true data |
| **D — 59.4 Multi-tenant sim_loop** | Teach `sim_loop.py` to drive N tenants from a profile list with **per-tenant keys + per-tenant rate caps** (aggregate ceiling preserved). `make sim-start` drives all active demo tenants. | Runs ≥ 1 h across 2–3 tenants; aggregate stays under the per-tenant Sprint 38 limit; start/stop/status targets |
| **E — 59.5 Docs + closeout** | Split `demo-guide.md` into a combined overview + two domain tours; update `operator-quickstart.md`, CHANGELOG; roadmap §59 written, terminology/nav moved to §60. | Guides match what the seeds produce; `make check` green |
| **F — 59.9 Spatial schema + ADR-024 amendment** (Track 2) | `antennas` table (per-antenna `x,y,z`), `sites.coord_system`, `asset_positions` hypertable (`source` enum, created-not-written-to). Migration + models. Amend ADR-024 (position→antennas; estimator antenna-keyed + asset-grouped; ref algo → `rssi_weighted_centroid`). | Migration up/down clean on a populated tenant; `mypy`/`ruff` green; ADR-024 amended + decision-history bumped |
| **G — 59.10 EPC→asset fusion** (Track 2) | Service resolving EPC → `asset_id` via `asset_tag_bindings` (one asset → many EPCs), grouping an asset's tags; backs zone-presence and is step 1 of the Sprint 61 positioning pipeline. No new endpoint. | Multi-bound asset resolves from any of its EPCs; `bound_at`/`unbound_at` respected; unit tests for 1-tag, 3-tag, rebound |

## Decisions to lock in Phase A (mirroring 58's D-list)

- **D1. Profile model:** a single composer with `--profile` enum vs. three
  separate seed scripts. *Lean: one composer, profile enum* (keeps R1 CLI
  contract test surface small).
- **D2. Asset narrative:** industrial returnables vs. hospital equipment vs.
  yard/logistics — pick one for a coherent story.
- **D3. sim_loop multi-tenant config:** per-tenant key plumbing. Sprint 58 Q2
  settled on *same key as seed* per tenant; extend to a `{slug: key}` map
  sourced from each `make demo-*` run / KV.
- **D4. Rate-cap budget:** how 200 reads/min splits across 3 active tenants
  without tripping the per-tenant Sprint 38 limit (hard aggregate ceiling stays
  600/min).
- **D5. demo-wm-dc fate:** keep verbatim as the combined tenant (chosen) —
  confirm it stays wired to the Sprint 58 baseline doc so measurements don't
  drift.
- **D6. Position grain (Track 2):** per-device vs. per-antenna `(x, y)`.
  *Chosen: per-antenna* — a fixed reader fans 2–8 antennas across tens of
  metres; the wire format already keys observations by antenna port (`an`).
  This **amends ADR-024** (which put `position_*` on `devices`).
- **D7. `asset_positions.source` enum (Track 2):** `precomputed | zone |
  computed`. Sprint 59 creates the table and writes **none** of them —
  `precomputed` is Sprint 60 (BYO ingest), `computed` is Sprint 61 (estimator),
  `zone` is the fallback the Sprint 60 retrieval path will synthesize. Locking
  the enum now avoids a later migration.

## Out of scope

- **Terminology renames / nav rework** (`Device`→`Reader`, etc.) — **moved to
  Sprint 60**.
- **Homegrown RSSI positioning math** — the `rssi_weighted_centroid` estimator,
  confidence scoring, and the `rpk` (peak-RSSI) wire-format adjustment —
  **candidate Sprint 61 spike.** Track 2 lands the schema + fusion only; it
  computes no position from RF. The per-tenant weight formula is config
  (`position_strategy`), not baked into the wire or code.
- **BYO-precomputed ingest + floor-map render** (Track 2 phase "H") —
  **Sprint 60.** `POST /assets/{id}/position` (vendor `(x, y)` →
  `source=precomputed`), retrieval with zone fallback, and the `[ui]` floor-map
  config + position render. Sprint 59 lands the `asset_positions` *table*;
  Sprint 60 fills and draws it. (A zone-fallback for the AssetList Location
  column is a separable Sprint 60 band-aid; don't conflate with the estimator.)
- New device types, new chart types, **rule-engine changes**. **New API
  endpoints:** none in Track 1 (query-param toggles OK); the Track 2 BYO
  endpoint is explicitly Sprint 60.
- **New ADRs** — none; Track 2 **amends** the existing [ADR-024](../adr/024-position-estimation.md)
  rather than spawning a new one. Reuses tenant isolation 008, subject scoping
  013, network hardening 017, edge wire format 025.
- Demo tenants in CI (Sprint 58 D7 still stands), staging/prod simulators
  (dev-only ceiling).
- Load testing at scale (`load_test.py` keeps that).

## Success metrics

- **Primary:** `make demo-inventory` and `make demo-asset` each produce a
  domain-complete, non-empty-on-every-page tenant in ≤ 5 min, idempotent on
  re-run; `make sim-start` keeps all three alive ≥ 1 h without tripping rate
  limits.
- **Secondary:** each domain has a written "demo script" (the 5–7 clicks that
  tell its business story) in its guide tour; Sprint 58 baseline tenant
  unaffected.

## Risks

- **R1 — CLI/endpoint drift across more compositions.** More shims = more flag
  surface. *Mitigation:* extend the existing `test_seed_demo_tenant.py` AST
  contract test to cover every new profile's call sites.
- **R2 — Rate-cap math across 3 tenants.** *Mitigation:* D4 budget + hard
  aggregate ceiling; status target shows per-tenant rate.
- **R3 — Three tenants pollute the dev tenant list.** *Mitigation:* consistent
  `demo-*` slug prefix; admin list already supports search.
- **R4 — sim_loop single-key assumption is load-bearing.** Refactor to a tenant
  map is the riskiest change. *Mitigation:* keep single-tenant path as the
  default; multi-tenant behind an explicit `--tenants` list.

## Carried-in issues (surfaced by the `chore/demo-data-fixes` validation)

Two findings from reset-and-run-through the promoted demo scripts. Neither
blocks the chore PR; both belong here.

- **59.6 — `?force=true` stock-item delete semantics (needs an ADR).** The
  `DELETE /stock-items/{id}?force=true` path only bypasses the `in_stock`
  *state* guard — it still issues a hard `DELETE`, which the `ON DELETE
  RESTRICT` FK `stock_movements_stock_item_id_fkey` (migration 021, Sprint 15b
  — "the ledger can never be orphaned") rejects with an unhandled
  `IntegrityError`, 500-ing and dropping the connection. So force-delete is
  effectively broken for any unit that has ever moved. The chore worked around
  it by making `cleanup_demo_stock_items.py` *soft-retire* (PATCH
  `state=consumed`, which frees the EPC binding via the partial unique index
  without touching the ledger). **Decision needed (ADR):** should `?force=true`
  (a) cascade-delete the ledger, (b) soft-delete the item (state transition +
  hide), or (c) be removed in favour of the consume lifecycle? At minimum the
  route must catch `IntegrityError` → 409 instead of 500. *Pass bar:* ADR
  authored; route returns a structured 409; a regression test covers a moved
  unit.

- **59.7 — Static demo tenant shows "0 active devices" after the online
  window.** The Dashboard's `devices_online` counts `connection_state='online'
  AND last_seen > now − 5min` (`services/dashboard.py`). A freshly seeded
  tenant with no live stream drops to 0 online ~5 min after seeding — the demo
  looks dead on a cold open. This is the **dwell-vs-heartbeat** sim gap in the
  chore cluster surfacing as a hero-metric regression. *Options:* (a) a
  heartbeat-only tick in `sim_loop`/seeder that keeps all readers fresh
  regardless of dwell, (b) a max-dwell cap so the streamed reads never leave a
  reader idle past the window, or (c) `make demo-*` ends by starting the
  simulator so devices stay warm. Fold into §59.4 (multi-tenant sim_loop) so
  every demo tenant stays "alive" without a manual `make sim-start`. *Pass
  bar:* a tenant left idle after seed still reports all readers online for the
  demo session.

- **59.8 — Product "Units" table `[UI]`.** `ProductDetail` shows only a total +
  by-zone chart; the per-unit list (each tagged stock item, its state, zone,
  last-seen) is unreachable in the App — operators must call
  `GET /stock-items?product_id=` by hand, and the Tags page can't substitute
  (it filters **hex hardware EPCs**, not the **SGTIN-URN inventory bindings**;
  the two namespaces don't cross-reference). Add a Units table to `ProductDetail`
  wired to the existing `GET /stock-items?product_id={id}` (no backend change):
  columns EPC/binding · state (`in_stock`/`consumed`) · zone (name-resolved) ·
  last seen; zone + state filters; row → that unit's read history. **Cross-repo:
  `[ui]` only — `9owlsboston/TagPulse-UI`**; rides Sprint 59 only if UI bandwidth
  exists, otherwise it leads Sprint 60. *Out of scope for the fix:* reconciling
  the two EPC namespaces — backlog it; the table reads `binding_value` as-is.
  *Pass bar:* from `SKU-SHOE-RUN-SZ10`, list in-stock units, filter by zone,
  click into one unit's history in ≤ 3 clicks, zero manual API calls; vitest
  render + empty-state; `npm run check` green.

## Related

- Feeds from the **Post-Sprint-58 demo-data chore cluster** in
  [`docs/backlog.md`](../backlog.md) — the latent ingest gate bug and the two
  simulator gaps (serial alignment, dwell-vs-heartbeat) must be addressed for
  §59.3 to produce clean per-page data.
- Predecessor: [`sprint-58-demo-and-simulation.md`](sprint-58-demo-and-simulation.md).

## Kickoff

```bash
scripts/start-sprint.sh 59 demo-scenarios "Sprint 59 — Demo scenario depth"
```

Creates the `sprint-59/demo-scenarios` branch + draft PR and bumps the roadmap
badge. Then move this plan onto that branch and move the terminology/nav entry
to §60.
