# Sprint 77 — Excel filters on the remaining server-paginated tables

- Status: **In progress** (2026-06-21). Backend [#156](https://github.com/9owlsboston/TagPulse/pull/156) + UI [#114](https://github.com/9owlsboston/TagPulse-UI/pull/114).
- Closes the Excel-like column-filter initiative started in
  [Sprint 70](sprint-70-table-filter.md) and continued in
  [Sprint 75](sprint-75-excel-column-filters.md) /
  [Sprint 76](sprint-76-server-facets.md). After this sprint **every** list
  table has the uniform sort + per-column filter.

## Goal

Finish the rollout: wire the **Assets** table to the Sprint 76 backend (the
params shipped but the UI didn't consume them), and bring the three remaining
list tables — **Transfers**, **Stock Levels**, **Reconciliation** — up to the
same Excel-like bar.

## Scope

### 1. Assets table (UI only — backend shipped in Sprint 76)
Wire `AssetList` to the live params:
- **Status** column → searchable checkbox list bound to `statuses` (multi).
- **Category** column → checkbox list bound to `category_ids`.
- **Server sort** (`sort`/`order`: `name`/`created_at`/`status`).
- Care: `useAssets` has a **dual fetch path** (positional
  `AssetsService.listAssetsAssetsGet` + a raw `request()` fallback when
  `labels`/`category_ids` are set). Both must forward the new params, and the
  positional call is the known landmine (see repo memory).

### 2. Stock Levels (UI only — already client-side)
`StockLevels` fetches all levels and **pivots client-side** (Product rows × Zone
columns + Total), so it is a client table:
- **Product** column → wildcard editbox (client `excelColumn`).
- **Zone count / Total** columns → numeric sort (+ range).
No backend change.

### 3. Transfers (backend + UI)
`GET /transfers` is server-paginated (toolbar already has direction + status).
Add server params + UI:
- **Backend:** `epc_q` (wildcard over `epc_hex`), `statuses` (multi), `sort`/
  `order` (whitelist: `requested_at` default, `completed_at`, `status`).
- **UI:** EPC editbox + Status checkbox + server sort on the Transfers table.

### 4. Reconciliation (backend + UI)
`ReconciliationPage` has three server-paginated views (registered-but-unread,
unregistered-but-reading, bindings-on-retired-tags) with view-specific columns
(EPC / Tag ID / Status / Source / seen-times):
- **Backend:** per-view `q` (wildcard over the EPC/tag identifier) + `sort`/
  `order` (whitelist the seen-time + status columns) on the reconciliation
  endpoints.
- **UI:** identifier editbox + server sort on each view's table.

## Out of scope
- New checkbox **facet endpoints** beyond what the columns already enumerate
  (Transfers/Reconciliation status are fixed enums; no distinct-value scan
  needed). `pg_trgm` indexes (Sprint 70 O3) stay deferred.
- The DB-backed repo test harness for the correlated-join SQL (tracked
  separately in the backlog); new params here are single-column and covered by
  the existing fake-repo pattern.

## Acceptance
- All four tables carry the uniform column dropdown (sort + type-correct filter);
  the server-paginated ones filter/sort whole-dataset, not page-local.
- `make check` (backend) + `npm run check` (UI) green; CHANGELOGs + roadmap
  Sprint 77 section + user-guide note updated.
