# Sprint 59 — Demo scenario depth: split inventory & asset demo tenants

> Status: **Draft plan** (recorded ahead of kickoff on `chore/demo-data-fixes`;
> promote to the `sprint-59/demo-scenarios` branch at kickoff via
> `scripts/start-sprint.sh`). Supersedes the scratch notes in `/tmp`.

## Sprint goal

Continue improving demo-data and simulation capabilities, and set up the data
surface so each business domain tells a complete, realistic story. Concretely:

1. Separate demo/simulation data by **Inventory Management** vs **Asset
   Tracking** — a purpose-built tenant for each, alongside the combined
   Sprint 58 tenant.
2. Author **extensive, realistic business scenarios** for both domains and use
   them to drive the demo data, simulation data, and seeding scripts.

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

## Phases

| Phase | Item | Pass bar |
|---|---|---|
| **A — 59.1 Scenario design** | Design doc `docs/design/sprint-59-demo-scenarios.md`: two business narratives, catalog/asset rosters, per-tenant rule + alert mix, profile model, multi-tenant rate-cap math. Pick the asset narrative. Lock decisions D1–D5 (below). | Doc reviewed; no scope creep without an OOS note |
| **B — 59.2 Profile-driven composer** | Parametrize `seed_demo_tenant.py` with a `--profile {combined,inventory,asset}` (default `combined` = today's behaviour, unchanged). New `make demo-inventory` / `make demo-asset` targets; reset targets per tenant. | Each profile idempotent; existing `make demo-tenant` byte-for-byte unchanged |
| **C — 59.3 Scenario depth** | Extend inventory catalog + asset roster; add the narrow shims for the events above (quarantine, cycle-count, geofence-breach read sequences, inactivity gaps, missing-asset). All via composition / existing write paths. | Every page in both tenants shows non-empty, domain-true data |
| **D — 59.4 Multi-tenant sim_loop** | Teach `sim_loop.py` to drive N tenants from a profile list with **per-tenant keys + per-tenant rate caps** (aggregate ceiling preserved). `make sim-start` drives all active demo tenants. | Runs ≥ 1 h across 2–3 tenants; aggregate stays under the per-tenant Sprint 38 limit; start/stop/status targets |
| **E — 59.5 Docs + closeout** | Split `demo-guide.md` into a combined overview + two domain tours; update `operator-quickstart.md`, CHANGELOG; roadmap §59 written, terminology/nav moved to §60. | Guides match what the seeds produce; `make check` green |

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

## Out of scope

- **Terminology renames / nav rework** (`Device`→`Reader`, etc.) — **moved to
  Sprint 60**.
- New device types, new chart types, **rule-engine changes**, **new API
  endpoints** (query-param toggles OK).
- New ADRs (reuses tenant isolation 008, subject scoping 013, network hardening
  017, edge wire format 025).
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
