# Sprint 58 Baseline — UI Performance + API Latency Measurement

> **Purpose.** Capture concrete numbers for the three deferred measurement
> items §55.C (stopwatch), §56.B (re-run), §57.G (Lighthouse) so they can
> flip from `[deferred]` to `[shipped]`. Provides the "before" reference
> WM's Sprint 59 terminology + nav rework will be measured against.

| Field | Value |
|---|---|
| Sprint | 58 (Phase D — `[58.4]` per `docs/design/sprint-58-demo-and-simulation.md`) |
| Backend SHA | `998351e` (Phase C commit; Phase D drive-by fixes captured in the same PR) |
| Branch | `sprint-58/demo-data-and-simulation` (PR [#83](https://github.com/9owlsboston/TagPulse/pull/83)) |
| Demo tenant | `WM Distribution Center` — slug `demo-wm-dc` — id `241d9b81-59da-5fb7-8f78-f58200978566` |
| Stack | local `docker-compose` (api + worker + db + mqtt, no UI container) |
| Date captured | 2026-05-19 |
| Operator | velen |

---

## 1. Sprint-54 SHA re-measurement — NOT attempted (R4 escape hatch)

Per the Sprint 58 design doc §R4: rebuilding the `sprint-54/ui-overhaul-foundation`
kickoff SHA against current `dev` infra was **not attempted** for this
baseline. The DB schema has moved through Sprints 54 → 57 (Sprint 57
telemetry-charting migrations, Sprint 56 `<ListPageShell>` UI changes,
the Sprint 49 `?asset_type=` removal in `assets.py`, and the Sprint 50
tag-registry ADR-028 schema in particular all touch the surface area
the stopwatch tasks exercise). A Sprint-54 build against current
migrations would either fail outright on schema drift or — worse —
produce numbers that conflate "UI got better" with "DB shape changed".

Acceptable per the design doc: WM's qualitative "this was painful
before" feedback from the May focus-group session anchors the
before-state. The numbers below are therefore captured at the **current
`main` + Sprint 58 demo tenant** and serve as the "after" baseline that
Sprint 59 (terminology + nav rework) will measure against.

---

## 2. API-side latency baseline (this commit)

Captured with `scripts/measure_baseline.py --iterations 30`. Steady-state
HTTP-only — one warmup request per task, then 30 timed iterations. Local
docker-compose (no network hop, no TLS), so these are the **best-case
backend slice** of the human-observable stopwatch time. The same tasks
run over the real Azure dev hop will add ~50-150 ms of network +
SWA edge per call; that overhead is constant across the §55.C / Sprint-59
comparison and cancels out.

The 5 tasks come from `docs/roadmap.md` §55.C primary metric.

| Task | Endpoint | p50 (ms) | p95 (ms) | p99 (ms) | n | status |
|---|---|---:|---:|---:|---:|---|
| Task 1: find asset by EPC            | `GET /assets?q=sim-pallet-012&limit=25`  | 26.8 | 40.3 | 43.8 | 30 | 200×30 |
| Task 2: triage newest open alert     | `GET /alerts?status=open&limit=10`       | 30.3 | 44.7 | 51.8 | 30 | 200×30 |
| Task 3: diagnose offline reader      | `GET /device-registry?limit=100`         | 32.3 | 48.4 | 61.9 | 30 | 200×30 |
| Task 4: check inventory for product  | `GET /stock-levels?limit=100`            | 22.9 | 35.9 | 37.3 | 30 | 200×30 |
| Task 5: start tag import             | `GET /bulk-operations?limit=50`          | 31.0 | 33.2 | 37.2 | 30 | 200×30 |

**Dataset shape at capture time** (from the Phase B seed + Phase C
backfill bundle): 14 reader devices, 17 assets with bound EPCs, 4
zones, 4 lots, 1673 historical tag reads (3 days), 4 open + 3 resolved
alerts, 1 in-flight cross-tenant transfer.

**Observations.** All five hot-path endpoints sit comfortably under
65 ms p99 on a populated demo tenant. The widest spread is on
`/device-registry` (Task 3) because the list joins to the per-device
connection-state view; that's where a Sprint-59 nav simplification has
the most upside if a single round-trip lookup can replace navigating
through multiple list pages. Tasks 4 and 5 already finish well inside
the 100 ms budget you'd want for "click feels instant" — the human
stopwatch time on those will be dominated by render + paint, not API.

Raw per-iteration sample arrays live in `/tmp/sprint58-baseline.json`
(local-only, not committed). Re-generate any time via:

```bash
export TAGPULSE_API_KEY=$(your demo tenant key)
python scripts/measure_baseline.py --iterations 30 --json sprint58-baseline.json
```

---

## 3. UI-side stopwatch — TO BE FILLED BY HUMAN

The §55.C protocol is **explicitly a human-driven stopwatch test** — a
seasoned operator runs each of the 5 tasks 3 times on the demo tenant
in a real browser, drops high + low, compares the median. The agent
cannot drive a browser; this section is the template the human fills.

Tooling: phone stopwatch or laptop stopwatch app. Browser: Chrome
latest, default profile, no extensions. Demo tenant URL: local
http://localhost:5173 against the docker-compose backend OR the
Azure dev SWA URL — pick one and note below.

| Task | Run 1 (s) | Run 2 (s) | Run 3 (s) | Median (s) | Notes |
|---|---:|---:|---:|---:|---|
| Task 1: find asset by EPC (operator types `sim-pallet-012` into the Assets search box and clicks the result) | _TBD_ | _TBD_ | _TBD_ | _TBD_ | |
| Task 2: triage newest open alert (operator opens Alerts, clicks the topmost open row, acknowledges it) | _TBD_ | _TBD_ | _TBD_ | _TBD_ | |
| Task 3: diagnose offline reader (operator opens Devices, filters/sorts to find an offline reader, opens its detail page) | _TBD_ | _TBD_ | _TBD_ | _TBD_ | |
| Task 4: check inventory for product (operator opens Inventory, filters to `SKU-MILK-1L`, reads the on-hand count) | _TBD_ | _TBD_ | _TBD_ | _TBD_ | |
| Task 5: start tag import (operator opens Bulk Operations, clicks "New import", drops the file picker open) | _TBD_ | _TBD_ | _TBD_ | _TBD_ | |

**Pass criterion** (per §55.C / re-evaluated §56.B): this is the
**"before" baseline for Sprint 59**, not a pass/fail gate for Sprint 58.
The human values land here so Sprint 59's terminology + nav rework has
something concrete to compare against. The 30 %/4-of-5 criterion only
applies when comparing Sprint 59's after-numbers against this row.

---

## 4. Lighthouse pass — TO BE FILLED BY HUMAN

Per §57.G: Perf ≥ 90, A11y ≥ 95 on Dashboard / Assets / Devices / Alerts
in **both** themes. Run from Chrome DevTools → Lighthouse, default mobile
config, "Performance" + "Accessibility" categories only, demo tenant
logged in.

### Light theme

| Page | Perf | A11y | Notes |
|---|---:|---:|---|
| Dashboard | _TBD_ | _TBD_ | |
| Assets    | _TBD_ | _TBD_ | |
| Devices   | _TBD_ | _TBD_ | |
| Alerts    | _TBD_ | _TBD_ | |

### Dark theme

| Page | Perf | A11y | Notes |
|---|---:|---:|---|
| Dashboard | _TBD_ | _TBD_ | |
| Assets    | _TBD_ | _TBD_ | |
| Devices   | _TBD_ | _TBD_ | |
| Alerts    | _TBD_ | _TBD_ | |

**Pass criterion** (§57.G): every cell ≥ the threshold. If any page in
either theme falls below, file the specific score gap as a Sprint 59
follow-up.

---

## 5. Roadmap closeout state

This commit closes §58.4 "Baseline capture + closeout" partially:

- ✅ **API-side baseline captured** in §2 above (5 tasks × 30 iters,
  all p99 < 65 ms on demo tenant).
- ✅ **Measurement doc scaffolded** with §55.C + §57.G templates.
- ⏳ **UI-side stopwatch + Lighthouse pending human run.** See §3, §4.
- ⏳ **Roadmap items §55.C / §56.B / §57.G remain `[deferred]`** until
  the human fills §3 and §4. When that lands, flip the three items to
  `[shipped]` with a cross-link to this doc.

Phase E (§58.5 WM pre-Sprint-59 baseline session) is non-blocking per
R5 and is tracked separately.
