# Sprint 58 — Demo data & simulation foundation

- Status: **planned** (Sprint 58 kickoff — backend PR [#83](https://github.com/9owlsboston/TagPulse/pull/83))
- Sprint number: **58 (backend-only — composes existing simulators; UI side only runs measurements)**
- Cross-repo: **no UI PR** in this sprint. UI involvement is running Lighthouse + the §55.C stopwatch tasks against the demo tenant produced here.
- Related ADRs: none new. Reuses tenant-isolation (008), wire format v2 (025), telemetry subject scoping (013), edge contract (017/edge-device-contract.md).
- Roadmap entry: [§sprint-58 in docs/roadmap.md](../roadmap.md).

## Theme

We have four good simulator scripts (`simulate_devices.py`, `simulate_assets.py`, `simulate_inventory.py`, `mqtt_canary.py`) and they cover their individual surfaces well, but **no single command produces a tenant that looks credible on screen for a focus-group session, and nothing runs continuously to keep that tenant alive overnight**. Sprint 58 wraps the existing pieces into (1) a one-shot seed bundle and (2) a long-running orchestrator, then uses the result to capture the three measurement closeouts that have been deferred across Sprints 55, 56, 57.

Roughly **50% composition of existing simulators + 30% orchestrator + Container Apps job + 20% measurement capture and closeout**.

## Primary users (of the deliverables, not of the demo tenant)

- **Me + future operator running a review session.** Run one `make` target, get a demo-ready tenant in ≤ 5 min, run another command to keep it alive, walk away.
- **WM (and future customer) pilot reviewers.** Open the SPA against the demo tenant; see realistic data on every page they're asked to evaluate.
- **The three deferred measurement items** (§55.C / §56.B / §57.G). Stopwatch + Lighthouse pass only mean something against a tenant that has *something to look at*.

Explicitly **not** the user: load testing at scale (`scripts/load_test.py` already owns that), conformance / fuzzing, security scanning.

## Problem

| Symptom | Root cause |
|---|---|
| Default-seed tenant has zero alerts, zero transfers, no low-stock products, no historical reads. WM session showed empty graphs and "no rows" empty states on half the pages. | Each simulator solves one slice (devices, assets, inventory) and they were never composed into a single coherent demo seed. |
| The four simulators don't run for long — they're one-shot or short-loop. The tenant goes idle within minutes. | None of them are designed as long-running services; no rate caps, no shift patterns, no docker-compose profile. |
| §55.C / §56.B / §57.G measurement items keep getting deferred sprint after sprint. | Same blocker: realistic, continuously-fed tenant data hasn't existed. Lighthouse Perf on a 3-row asset list isn't comparable to anything. |
| First demo with WM was conducted against this empty tenant. Their terminology + nav feedback is muddied by "and there was nothing on the page" confounder. | No demo-tenant tooling, so we shipped the focus-group session with whatever the default seed gave us. |

## Hard constraints

1. **Compose, don't rewrite.** The four existing scripts stay where they are; new code wraps them via subprocess or imports. We do not refactor `simulate_devices.py` mid-sprint to be "more modular." If a gap genuinely can't be filled by composition, add a narrow shim — don't open the simulator's internals.
2. **Idempotent.** Running `make demo-tenant` twice produces the same tenant, not double the rows. Same EPCs, same product/lot codes, same site/zone names. Re-runs reuse `simulate_assets.py`'s existing reuse semantics and `simulate_inventory.py`'s stable serials.
3. **Rate-capped by default.** The continuous simulator must not be able to blow the per-tenant rate limit. Defaults conservative; document how to raise.
4. **One-command teardown.** `make demo-tenant-reset` or `docker compose --profile sim down --volumes` brings everything back to zero so the next review starts clean.
5. **No new API endpoints.** If the demo needs an alert type the engine can't trigger naturally, seed it via existing write paths (or via direct SQL through `scripts/azd-job.sh`) — do not extend the rule engine to make the demo prettier. (A query param added to an existing route — see Q1's `?backfill=true` resolution — does not violate this; the constraint targets new routes / new resource surfaces, not flag-shaped behaviour toggles on existing ones.)
6. **Dev-only Container Apps job.** The continuous simulator runs in `dev` only. Never `staging`, never `prod`. Gated by `ENV` check in the job script.

## Three concrete deliverables (the user-visible outcomes)

1. **`make demo-tenant`** — repeatable one-shot seed that brings up a "WM Distribution Center" tenant: 1 site, 6–8 zones, 8–12 readers, 4–6 named products with lots, ~3 days of historical reads, 3–5 open alerts, 2–3 resolved alerts, 1 transfer in flight, 1 low-stock product. Idempotent. Companion `make demo-tenant-reset` for clean re-runs.
2. **Continuous simulator** — long-running orchestrator that drives the four simulators on realistic schedules. Local: `docker compose --profile sim up -d`. Dev: `scripts/azd-job.sh dev sim_loop.py`. Configurable rate caps; ships with sane defaults.
3. **Baseline measurement capture** — run §55.C stopwatch + §57.G Lighthouse against the demo tenant on the Sprint 54-kickoff SHA AND on current `main`. Commit numbers to `docs/measurements/sprint-58-baseline.md`. Flip §55.C / §56.B / §57.G to `[shipped]` with cross-links.

## Architecture sketch

```
                            +--------------------------------------------+
                            |            make demo-tenant                |
                            |  (one-shot, composes the four simulators)  |
                            +----------------------v---------------------+
                                                   |
                                                   v
                            +--------------------------------------------+
                            |       scripts/seed_demo_tenant.py          |
                            |  1. ensure tenant + api key                |
                            |  2. simulate_devices.py    (one pass)      |
                            |  3. simulate_inventory.py  (one pass)      |
                            |  4. simulate_assets.py     (one pass)      |
                            |  5. backfill_history.py    (NEW: ~3d)      |
                            |  6. seed_alerts.py         (NEW: 3-5)      |
                            |  7. seed_transfer.py       (NEW: 1)        |
                            +----------------------v---------------------+
                                                   |
                                                   v  (tenant demo-ready in <= 5 min)
                                                   |
                                                   v
                            +--------------------------------------------+
                            |   docker compose --profile sim up -d       |
                            |   -or-  scripts/azd-job.sh dev sim_loop    |
                            +----------------------v---------------------+
                                                   |
                                                   v
                            +--------------------------------------------+
                            |       scripts/sim_loop.py (long-running)   |
                            |  - shift schedule: peaks 0800/1300 local   |
                            |  - 5%/min: one reader briefly offline      |
                            |  - 1/15min: alert-triggering condition     |
                            |  - rate cap: 200 reads/min/tenant          |
                            +--------------------------------------------+
```

The two **NEW: ...** items (plus `backfill_history.py`) in the seed bundle are the only genuinely new code paths. Everything else is `subprocess.run([sys.executable, "scripts/simulate_devices.py", ...])` style composition.

## Decisions to lock in Phase A (before any code lands in B/C)

### D1. Orchestrator: long-running Python or scheduled cron?

**Decision: long-running Python process.** A single `sim_loop.py` with `asyncio` + an internal `apscheduler`-style tick loop, NOT a cron of one-shot scripts.

- Rationale: we want stateful behaviour (a reader that goes offline stays offline for 3–8 min, not just one tick), shift-peak smoothing across minutes, and the ability to react to its own writes (after an alert is triggered, don't trigger another in the same zone for 10 min). Cron-of-oneshots loses all that state and produces a "twitchy" tenant.
- Cost: one always-on container/job. Acceptable in dev; explicitly never in staging/prod.

### D2. Demo tenant identity: hardcoded or per-run UUID?

**Decision: hardcoded tenant slug `demo-wm-dc`, deterministic UUID derived via `uuid5(NAMESPACE_DNS, "demo-wm-dc.tagpulse.local")`.**

- Rationale: idempotency. `make demo-tenant` rerun against a partially-seeded state must converge to the same tenant, same API key. Deterministic UUID means the tenant row is reusable without lookup.
- API key: rotated on each `make demo-tenant` run (caller can override with `DEMO_KEEP_KEY=1` to preserve).

### D3. Historical backfill: replay through HTTP, or direct DB insert?

**Decision: replay through HTTP, NOT direct DB insert.** Use the existing tag-reads ingest endpoint with `observed_at` timestamps in the past.

- Rationale: keeps the simulator code path identical to live ingest (same validation, same enrichment pipeline, same telemetry rollups, same hypertable inserts). Direct DB insert would skip the asset-zone enrichment that the dashboard summary depends on.
- Cost: ~3 days of reads × ~200 reads/min = ~860 K rows. At even modest batch sizes this completes in single-digit minutes locally. Acceptable.
- Backfill-vs-alerts coupling: resolved under [Q1 below](#open-questions--resolved) — Phase B adds `?backfill=true` to the existing tag-reads ingest route to skip rule evaluation while keeping the rest of the ingest pipeline.

### D4. Alert seeding: trigger naturally or insert directly?

**Decision: hybrid.** A subset of "natural" alerts get triggered by feeding the simulator a deliberately alert-shaped read sequence (so they appear with the right rule attribution and message). The remaining "resolved" alerts get inserted via direct SQL through `scripts/azd-job.sh` — we don't need them to be live, we need them to render in the alert-history list.

- Rationale: trigger-naturally is preferable for fidelity but flaky to time-control; for the few we just need on screen, direct insert is simpler than choreographing the simulator to produce + resolve them.

### D5. Rate-cap mechanism

**Decision: token bucket in `sim_loop.py`, defaulting to 200 reads/min/tenant.** Configurable via `SIM_RATE_PER_MIN` env. Hard ceiling at 600 reads/min (enforced by the loop) to prevent fat-fingered overrides from saturating dev infra.

- Rationale: the existing per-tenant rate limit (Sprint 38) is around 1 K/min for unsubscribed tenants; we want to sit well below to leave room for real test traffic on the same dev cluster.
- We do not reuse `scripts/load_test.py`'s `--ramp` machinery here — that's a stress profile, not a demo profile.

### D6. Container Apps job vs. long-running Container App service

**Decision: Azure Container Apps Job (manual-trigger, no schedule), invoked through `scripts/azd-job.sh dev sim_loop.py -- --duration 8h`.** NOT a perpetually-running Container App service.

- Rationale: cost. A demo tenant doesn't need 24/7 reads; we want explicit "I'm doing a demo today" invocation with a built-in 8 h ceiling so it can't run forever if forgotten.
- Operator workflow: invoke the job ~30 min before the review session; tenant is alive for 8 h; job self-terminates.

**ACA Job configuration (specifics, so Phase C Bicep is unambiguous):**

| Aspect | Value |
|---|---|
| Job trigger type | Manual (`triggerType: Manual`) — not Schedule, not Event |
| Container image | Reuse the existing `tools-job` image (same one driven by `smoke_setup.py` / `rotate-key`) — no new ACR push, no separate Dockerfile |
| `replicaTimeout` | `28800` (8 h, matches the ceiling above; ACA Jobs cap at 7 days so well within limits) |
| `parallelism` / `replicaCompletions` | `1` / `1` — exactly one `sim_loop` at a time per dev env |
| Env vars | `TAGPULSE_API_URL` + `TAGPULSE_API_KEY` from existing Key Vault secrets (already wired into the tools-job; no new KV plumbing) |
| Egress | HTTP only to the dev `api` Container App (per Q3 working assumption); same NSG / private-endpoint path the existing tools jobs already use; **no MQTT broker access needed from the job** |
| Invocation surface | `scripts/azd-job.sh dev sim_loop.py -- --duration 8h --rate 200` — same calling convention as the existing tools jobs |
| `ENV` guard | Script aborts with non-zero if `ENV != dev` (defence-in-depth on the "dev-only" constraint from Hard Constraint 6) |
| Cost shape | Billed per execution-second only while running. 8 h on the tools-job CPU/memory profile is on the order of single-digit dollars per session, not hundreds |

The local `docker compose --profile sim up -d` path is the *only* deviation from ACA: locally we want `up -d` / `down` semantics with no per-execution timeout, so it's a regular compose service under a profile (not a separate job runner).

### D7. Demo tenant in CI?

**Decision: no.** The demo tenant exists only in local dev and in the `dev` Azure environment. CI continues to use the existing per-test fixture flow. Adding the demo seed to CI would add 5 min to every PR for no gain.

## Phases

- **A — 58.1 Audit + design (this doc).** Lock D1–D7 above. Inventory what each existing simulator covers and what gaps remain. Pass bar: this doc reviewed; design decisions ratified or amended; no scope expansion mid-sprint without an OOS exception in the PR description.
- **B — 58.2 `make demo-tenant` seed bundle.** Build `scripts/seed_demo_tenant.py` composing the four existing simulators + three NEW shims (`backfill_history.py`, `seed_alerts.py`, `seed_transfer.py`). Add `make demo-tenant` + `make demo-tenant-reset` targets. Add a "Demo tenant" section to `docs/operator-quickstart.md`. **Sub-task (per Q1):** add `?backfill=true` query param to the existing tag-reads ingest endpoint that skips rule evaluation; covered by a focused unit test. Pass bar: clean local `docker compose up` → `make demo-tenant` produces a demo-ready tenant in ≤ 5 min; re-run is idempotent; reset returns to zero; backfill query param tested.
- **C — 58.3 Continuous simulator service.** Build `scripts/sim_loop.py` per D1, D5. Add a `sim` profile to `docker-compose.yml`. Add the `scripts/azd-job.sh dev sim_loop.py` invocation path per D6. Pass bar: runs ≥ 1 h without crash; default rate doesn't trip per-tenant limits; one Makefile target each for start/stop/status.
- **D — 58.4 Baseline capture + closeout.** Run the §55.C stopwatch protocol (5 tasks × 3 runs, drop high/low) against `main` on the demo tenant; run §57.G Lighthouse (Perf ≥ 90, A11y ≥ 95 on Dashboard, Assets, Devices, Alerts in both themes). Commit numbers to `docs/measurements/sprint-58-baseline.md`. Flip §55.C / §56.B / §57.G to `[shipped]` in `docs/roadmap.md` with cross-links. Pass bar: numbers committed; three roadmap items closed.
- **E — 58.5 WM pre-Sprint-59 baseline session.** Provide WM with demo-tenant access + the 5 stopwatch tasks. Capture their "before" timing for Sprint 59's terminology + nav rework. Pass bar: timing log on file (recording or written); pain-points enumerated in a Sprint 59 kickoff brief stub at `docs/design/sprint-59-kickoff-brief.md`.

## Risks

- **R1 — Simulator drift breaks composition.** If `simulate_devices.py` changes its `--api-key` flag between now and the next sprint, the bundle breaks silently. Mitigation: pin the CLI contracts in `scripts/seed_demo_tenant.py` with explicit args (no `**kwargs` pass-through); cover with one smoke test in `tests/integration/test_seed_demo_tenant.py` that runs the seed against the dev-stack docker-compose.
- **R2 — Demo tenant pollutes dev tenant list.** The demo tenant will show up in any "list all tenants" admin call. Mitigation: tag tenant slug `demo-wm-dc` consistently; admin list UI already supports tenant search.
- **R3 — Container Apps job costs more than expected.** 8 h of an always-on container with HTTP egress isn't free. Mitigation: 8 h hard ceiling per D6; default `MAX_JOB_DURATION_H=8` env in `scripts/azd-job.sh`; document cost expectation in `docs/operator-quickstart.md`.
- **R4 — §55.C baseline-capture-at-Sprint-54-SHA is invalid.** The Sprint 54-kickoff SHA may no longer build cleanly against current `dev` infra (DB schema has moved). Mitigation: if rebuilding the Sprint-54 SHA against current dev fails, capture baseline only at current `main` and document the constraint in the measurement doc. Acceptable because we have WM's qualitative "this was painful before" feedback to anchor against.
- **R5 — WM unavailable for Phase E within sprint.** Calendar slippage. Mitigation: Phase E does not block sprint sign-off — flip §55.C / §56.B / §57.G to `[shipped]` based on D4 alone; track WM session as a follow-up issue if it slips.

## Out of scope

- **WM-specific scenarios beyond a generic "distribution center."** Their specific SKUs / zones / shift patterns land in Sprint 59 or later if the demo flushes out a real need.
- **Terminology renames** (`Device` → `Reader`, `Telemetry` → ?) — Sprint 59.
- **Nav rework** beyond what Sprints 54 / 56 already shipped — Sprint 59.
- **New device types**, **new chart types**, **rule engine changes**, **new API endpoints**.
- **i18n / RTL**, **phone responsive (<768 px)**, **WCAG audit beyond Lighthouse**.
- **Load testing at scale** — `scripts/load_test.py` continues to own this unchanged.
- **A demo-tenant CI fixture** — explicitly declined in D7.
- **Production / staging continuous simulators** — D6 ceiling at `dev`.

## Open questions — resolved

- **Q1. Should backfilled reads bypass alert-rule evaluation? — RESOLVED: YES.** Add a `?backfill=true` query param on the existing tag-reads ingest endpoint as a Phase-B sub-task. Behaviour: when `backfill=true`, skip the rule-evaluation step but otherwise run the full ingest path (validation, enrichment, hypertable insert, telemetry rollups). This is a query param on an existing route, not a new endpoint, so it does not break the "no new API endpoints" hard constraint. Without this, the seed step would race itself — the historical reads it emits would trigger alerts in parallel with the 3–5 curated ones we want on screen.
- **Q2. Separate API key for continuous simulator vs. seed bundle? — DEFERRED TO PHASE C.** Working assumption stays "same key" for Phase B (the seed bundle uses one key). Revisit during the ACA Job wiring in Phase C: if a second key drops in trivially via the existing KV plumbing, give the sim_loop its own key for cleaner rate-cap attribution; otherwise reuse. Not a blocker for Phase B.
- **Q3. ACA Job emit MQTT or HTTP-only? — RESOLVED IN D6: HTTP-ONLY.** Subsumed by D6's egress row. MQTT path stays covered by the existing `mqtt_canary.py` on its independent schedule.
