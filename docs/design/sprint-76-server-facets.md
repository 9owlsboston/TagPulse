# Sprint 76 — Server facets, sort & `asset_q`

- Status: **Shipped** (2026-06-21). Backend [#153](https://github.com/9owlsboston/TagPulse/pull/153) + UI [#113](https://github.com/9owlsboston/TagPulse-UI/pull/113).
  The **backend** shipped in full (asset_q, server sort, facets endpoint,
  multi-select `epc_schemes`/`reader_antennas`, assets multi-`statuses` + sort).
  The **UI** consumes the Tag Reads side fully (asset_q editbox + Scheme/Antenna
  checkbox facets). The **Assets** UI consumption (Status checkbox, Category
  checkbox, server sort) is deferred to a **Sprint 77** follow-up because the
  Assets list has a dual fetch path (positional generated client + raw
  `request()`) that is risky to rewire late; the backend params are live and
  tested, so the follow-up is UI-only.
  **Audit (2026-06-21):** the new tag-reads SQL (`asset_q` correlated `EXISTS`,
  `facets` distinct) is covered only by the in-memory fake (which can't model the
  binding join) — consistent with the Sprint 70 `tag_q` precedent (no DB-backed
  repo test harness exists; only `tests/integration/test_migration_round_trip.py`).
  Tracked as a backlog item; behaviour was verified by code review + the live
  `openapi.json` contract.
- Sibling of [Sprint 75 (Excel-like columns)](sprint-75-excel-column-filters.md):
  Sprint 75 delivered the uniform helper + editbox/sort/range on client tables;
  Sprint 76 lights up the **server** side for the two essential paginated tables
  (Tag Reads, Assets) — checkbox facets, server-side sort, and `asset_q`.

## Goal

Make the two server-paginated essential tables fully Excel-like and
**whole-dataset correct**: low-cardinality columns get searchable checkbox
lists (server-filtered), sort runs server-side (not page-local), and Tag Reads
can be filtered by the **bound asset name**.

## Scope

### Backend (additive, no migration)
- **`asset_q`** on `GET /tag-reads` — wildcard match on the *bound asset name*.
  Implemented as an `EXISTS` over `asset_tag_bindings` (active) → `assets`,
  matching the tag form columns (`tag_id`/`epc`/`epc_hex`/`tid`) against the
  binding value and the asset `name` via `wildcard_to_ilike`.
- **Server sort** on `GET /tag-reads` and `GET /assets`: `sort` (whitelisted
  column) + `order` (`asc`/`desc`). Unknown column → 422. Whitelists:
  - tag-reads: `timestamp` (default desc), `signal_strength`, `reader_antenna`.
  - assets: `name`, `created_at` (default desc), `last_seen`, `status`.
- **Facets** for Tag Reads: `GET /tag-reads/facets` → distinct `epc_scheme` and
  `reader_antenna` values for the tenant (small sets; bounded by a `LIMIT`).
- **Assets multi-status**: `GET /assets` accepts repeated `?statuses=` (the
  checkbox list emits multiple) → `status IN (...)`. Single `?status=` kept for
  back-compat.

### UI
- **Tag Reads:** server sort on the sortable columns; `asset_q` editbox on the
  Asset column; checkbox facets on **Scheme** + **Antenna** (from `/tag-reads/
  facets`); **Device** checkbox sourced from `useDevices` (the reader set is
  already known client-side, no facet scan needed).
- **Assets:** server sort; **Status** searchable checkbox (server `statuses`);
  **Category** checkbox sourced from the categories list (server `category_ids`).

## Out of scope
- **Assets UI consumption** (Status/Category checkboxes + server sort) — backend
  is shipped; the UI wiring is a **Sprint 77** follow-up (dual fetch path risk).
- Assets **Location/source** column facet — it is derived from current-location
  data, not an `assets` row column; stays client-side for now.
- The three other server-paginated tables (Transfers, Stock Levels,
  Reconciliation) — tracked as a follow-up; they reuse the same server-sort +
  facet patterns once prioritised.
- `pg_trgm` indexes (Sprint 70 O3) — still deferred until a paginated table
  trips the p95 SLO.
