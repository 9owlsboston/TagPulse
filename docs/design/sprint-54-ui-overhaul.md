# Sprint 54 + 55 — UI overhaul: design tokens, sectioned nav, dashboard rewrite, uniform list pattern

- Status: **planned** (Sprint 54 kickoff — backend PR #74, UI PR #62)
- Sprint numbers: **54 (foundation + dashboard + 3 list-page proof)** → **55 (3 remaining ops list pages + polish + measurement)**
- Cross-repo: this is the first sprint that exercises the `--with-ui` kickoff flow shipped in PR #73 / UI #61. Backend changes are minimal (one new endpoint); the bulk of the work is in `TagPulse-UI`.
- Related ADR: [029 UI design tokens](../adr/029-ui-design-tokens.md) (this sprint).

## Theme

Replace the flat 45-route navigation + customizable-but-bland Dashboard + page-by-page list inconsistencies with a coherent operator-first information architecture: **sectioned single left nav + titled KPI tiles with click-through + uniform list-page pattern, dual-theme (light + dark) via design tokens up front**.

Roughly **80% UX polish + 20% Dashboard rewrite**.

## Primary users

- **Warehouse / floor operator** (daily): triage the newest open alert, diagnose an offline reader, find an asset by EPC. Tablet + desk.
- **Inventory / asset manager** (daily): check inventory level for a product, scan transfer queue, start a tag import. Desk.

Explicitly **not** optimised for: occasional admin users (covered post-sprint-55), executives needing dashboards-as-reports, mobile-phone form factor (<768 px).

## Problem

| Symptom | Root cause |
|---|---|
| New users open `/` and don't know where to go next. | 45 flat routes in the sidebar with no grouping. The Dashboard is a customizable grid of widgets but provides no narrative path to the next action. |
| List pages feel inconsistent. | Each page (`AssetList`, `TagList`, `AlertHistory`, `DeviceList`, `ProductList`, `StockLevels`, …) was built independently. Different filter affordances, different empty states, different add-button placements, different result-count conventions. |
| Visual quality trails commercial IoT portals. | AntD defaults + per-component overrides. No semantic-token layer; theming retrofitted page-by-page. |
| Light/dark theming is incomplete. | ThemeProvider supports both modes but pages contain hardcoded hex values that don't switch. |

## Hard constraints

1. **Single sidebar** — no secondary nav rail, no top app-bar tabs. Sectioned categories inside the one sider only.
2. **Dual theme via tokens up front** — zero `!important`, zero hardcoded hex in components/pages. AntD `ConfigProvider` consumes the same token catalog.
3. **Vendor-neutral docs** — no third-party product or vendor names in any committed file (PR body, CHANGELOG, ADR, design doc, code comments). Reference material is consulted privately during planning.
4. **Tablet floor at ≥768 px** — desktop + tablet is in-scope; phone (<768 px) is explicitly out of scope.
5. **Backend-first when contract changes** — the one new endpoint (`GET /dashboard/summary`) ships in a backend PR that merges before the UI PR that consumes it; UI records the backend SHA `openapi.json` was regenerated against.

## Three concrete deliverables (the user-visible outcomes)

1. **Sectioned single left nav** — 45 routes mapped into ≤4 sections (Operations, Inventory, Configuration, Admin) plus ≤2 ungrouped top items (Dashboard, Search).
2. **Titled KPI tiles** on a static-grid Dashboard with **click-through to pre-filtered list pages** (e.g., the "Open alerts (24h)" tile links to `/alerts?status=open&since=24h`). User can pin/unpin and reorder tiles; order persists in LocalStorage. **No more `react-grid-layout`**.
3. **Uniform list-page pattern** — shared `<ListPageShell>` component: page title + result count + global search + Add button + collapsible filter drawer with faceted filters + standardised empty/loading/error states. Three pages convert in Sprint 54 (Assets, Tags, Alerts); three more in Sprint 55 (Devices, Products, StockLevels).

## Success metrics

### Primary — stopwatch task times (operator persona)

Five canonical tasks, timed on the current `main` (baseline) and on the sprint-55 final commit (after):

1. **Find an asset by EPC** — start on `/`, end on the asset detail page.
2. **Triage the newest open alert** — start on `/`, end with the alert acknowledged.
3. **Diagnose an offline reader** — start on `/`, end on the device-detail page of an offline reader.
4. **Check inventory for a product** — start on `/`, end on the stock-level view for that product.
5. **Start a tag import** — start on `/`, end with the bulk-import form filled and the Submit button focused.

Protocol: **3 runs per task**, drop high + low, compare medians. Same browser, same window size (1440×900 desktop). Baseline is recorded at the `sprint-54/ui-overhaul-foundation` kickoff commit SHA; after-numbers recorded at the sprint-55 final commit SHA. **Pass bar: new is ≥30% faster on 4 of 5 tasks AND none is slower by >10%.**

Mitigates N=1 noise; the brief stopwatch is deliberately lightweight (no SUS, no recruit, no recording infra) to fit a 2-sprint budget.

### Secondary — Lighthouse

Run Lighthouse on the Dashboard + Assets + Devices + Alerts pages, in **both themes**. **Pass bar: Performance ≥90, Accessibility ≥95 on every page in both themes.**

## Out of scope (explicit)

- Internationalisation / RTL. UI strings remain English-only.
- Custom illustrations or motion design beyond AntD defaults.
- Onboarding tour, product tour, contextual help overlays.
- Swapping the chart library or building new chart types.
- WCAG audit beyond what Lighthouse covers.
- **Phone responsive (<768 px).** Floor is tablet.
- **14 admin-area list pages** (audit logs, tenants, role assignments, webhook subscriptions, etc.) — recorded in [docs/backlog.md](../backlog.md) and pre-scheduled as a future **Sprint 56** entry. Sprint 55 only converts the 3 remaining ops list pages.
- Database / API redesign. The one new endpoint is purely a presentation aggregation.

## Backend dependencies

**One new endpoint** in this backend repo:

`GET /dashboard/summary` — tenant-scoped aggregator returning 8 counts for the new Dashboard tiles:

| Field | Source |
|---|---|
| `devices_online` | `devices` filtered by recent heartbeat |
| `devices_total` | `devices` count |
| `alerts_open_24h` | `alerts` where status='open' AND created_at > now() - 24h |
| `reads_per_hour_now` | `tag_reads` count in last 60 min |
| `assets_active` | `assets` where retired_at IS NULL |
| `transfers_in_flight` | `tag_transfers` where status IN ('pending','in_transit') |
| `recon_backlog` | sum of `tag_reconciliation` row counts across 3 views |
| `low_stock_count` | `stock_levels` rows under threshold |

Constraints: tenant-scoped via existing RLS, p95 ≤200 ms on dev data, integration test + contract test, regenerates `openapi.json` in the backend PR.

No other backend changes. Three existing endpoints already expose `rows_total` for the list pages (`/admin/usage/summary`, `/stock-levels`, `/tag-transfers`, `/tags/reconciliation/{view}`) — `<ListPageShell>` will read `rows_total` when present and fall back to client-side count otherwise.

## Phases — Sprint 54

| # | Phase | Pass bar |
|---|---|---|
| 54.1 | **Design tokens + ThemeProvider rework.** Define the semantic token layer (see ADR-029): colour, spacing, radius, shadow, font scale. Wire AntD `ConfigProvider` to consume them. Ship `/dev/tokens` debug page showing the live catalog in both themes. | Token catalog at `/dev/tokens` renders. Zero hardcoded hex / zero `!important` in `src/components/` + `src/pages/`. |
| 54.2 | **Sectioned left nav.** Map 45 routes into ≤4 sections + ≤2 ungrouped top items. Update `Layout.tsx` sider, collapsible sections, active-route highlight. | All 45 routes still reachable. Section grouping reviewed against ops-user task flow. |
| 54.3 | **Backend `GET /dashboard/summary` (backend PR merges FIRST).** Service + route + integration test + contract test. Regenerate `openapi.json`. | p95 ≤200 ms on dev data. UI PR records backend commit SHA `openapi.json` was generated against. |
| 54.4 | **Dashboard rewrite.** Remove `react-grid-layout`. Static 4-col (desktop) / 2-col (tablet) grid. 8 titled KPI tiles fed by `/dashboard/summary`. Click-through to pre-filtered list pages. Pin-to-personalize via LocalStorage. **Record stopwatch baseline against `main` BEFORE merging this phase.** Audit URL-param prefilter support on target list pages; add the small handful of `?status=…` / `?since=…` parsers needed. | 8 tiles render in both themes. All click-throughs land on a list page pre-filtered to the tile's slice. Pin order persists across reload. Stopwatch baseline recorded in PR body. |
| 54.5 | **Convert 3 list pages using shared `<ListPageShell>`.** AssetList, TagList, AlertHistory. Extract the shell + facet-filter primitives into reusable components. | All 3 pages match the pattern (count + search + Add + drawer). Tablet visual review at 1024×768 + 768×1024. Lighthouse a11y ≥95 on all 3 in both themes. |

## Phases — Sprint 55

| # | Phase | Pass bar |
|---|---|---|
| 55.1 | **Convert 3 remaining ops list pages.** DeviceList, ProductList, StockLevels — same `<ListPageShell>` pattern. | All 3 pages match. Tablet visual review. |
| 55.2 | **Polish.** Empty-state copy + iconography, loading skeletons, error copy. Tablet sweep across all converted pages. | Visual review checklist green. No layout overflow at 768×1024. |
| 55.3 | **Stopwatch (new) + Lighthouse.** Re-run the 5 stopwatch tasks. Run Lighthouse on Dashboard + Assets + Devices + Alerts in both themes. | Primary pass bar (4 of 5 tasks ≥30% faster, none >10% slower) and secondary (Perf ≥90, A11y ≥95) both met, recorded in sprint-55 PR body. |
| 55.4 | **Backlog drain + Sprint 56 entry.** Add the 14 unconverted admin list pages to `docs/backlog.md` (already done in sprint kickoff) and create a Sprint 56 placeholder in `docs/roadmap.md` for admin list-page conversion. | Roadmap and backlog reflect what's done and what's next. |

## Risks + mitigations

| # | Risk | Mitigation |
|---|---|---|
| 1 | Removing `react-grid-layout` loses customizability some users rely on. | Pin-to-personalize replaces it. Users can still hide / reorder tiles, just on a fixed grid. |
| 2 | IA design (which routes go in which section) is meaty and could overrun Phase 54.2. | Phase 54.2 owns the IA decision; if it slips, Phase 54.3 (backend) can run in parallel because it has no UI dependency. |
| 3 | Reference UI material is vanilla JS + Express, not React; only tokens / IA / tile structure transfer. | Tokens are designed up front (Phase 54.1) using semantic naming, not copied CSS. ADR-029 codifies the layer. |
| 4 | URL-param prefilter support is uneven across target list pages. | Audit folded into Phase 54.4 pass bar; missing parsers are added inline as part of the Dashboard work, not deferred. |
| 5 | N=1 stopwatch is noisy. | 3 runs / drop high+low / median compare; same browser, same window, same dev data fixture. |
| 6 | Baseline drift between baseline and after-numbers if `main` advances. | Baseline pinned to the `sprint-54-kickoff` commit SHA, recorded in the PR body. After-numbers taken on the sprint-55 final commit. |
| 7 | Mobile (<768 px) scope creep. | Hard out-of-scope. Floor is 768 px wide. |
| 8 | List-page `rows_total` gap (only 3 endpoints expose it today). | Solved by the new `/dashboard/summary` endpoint for the Dashboard counts; `<ListPageShell>` reads `rows_total` when present and falls back to client-side count otherwise. No new backend endpoints required for list pages. |
| 9 | Partial pattern conversion (6 of 20 list pages this sprint set) leaves 14 admin pages on the old pattern. | Explicit Sprint 56 entry in `docs/roadmap.md` + `docs/backlog.md` entry; `<ListPageShell>` is left as a reusable primitive so Sprint 56 can be a mechanical conversion. |

## Cross-repo plan

- **Backend** (this repo): ADR-029, this design doc, roadmap entries, backlog entry, the one `/dashboard/summary` endpoint, regenerated `openapi.json`.
- **UI** (`TagPulse-UI`): everything else — design tokens, `Layout` sectioned nav, Dashboard rewrite, `<ListPageShell>`, 6 list-page conversions, polish, measurement.
- **OpenAPI**: changes in Phase 54.3 backend PR. UI PR records the backend SHA `openapi.json` was generated against in its PR body checklist.
- **Merge order**: 54.3 backend PR merges first; the rest of the UI work rebases onto the regenerated `openapi.json`. Phases 54.1, 54.2, and the UI side of 54.4 / 54.5 can land on the UI branch in any order before 54.3 merges, but the Dashboard fetch hook stays mocked until 54.3 is live.
