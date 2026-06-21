# Sprint 75 — Excel-like uniform column sort & filter

- Status: **In progress** (2026-06-21). Backend [#151](https://github.com/9owlsboston/TagPulse/pull/151) + UI [#112](https://github.com/9owlsboston/TagPulse-UI/pull/112).
- Owner: UI consumer + small backend contract additions, cross-repo.
- Related: [ADR-030 (list-page column filters)](../adr/030-list-page-column-filters.md),
  [Sprint 70 design (wildcard column box)](sprint-70-table-filter.md),
  `src/components/ColumnSearchFilter.tsx` + `src/lib/wildcard.ts` (UI),
  `src/tagpulse/api/filters.py` (`wildcard_to_ilike`, backend).
- Sibling: **Sprint 76** — server-side distinct-value **facets** (checkbox lists
  on the low-cardinality columns of the two server-paginated tables) + server
  sort. Sprint 75 deliberately stops at the editbox/sort/range tier so it ships
  standalone.

## 1. Goal

WM operators asked for an **Excel-AutoFilter** experience: **every** column
header carries the same affordance — **sort asc/desc**, plus a filter that is
**a type-to-search editbox for free-text columns and a searchable checkbox list
for small-set columns** — *regardless of the column's data type*. Today this is
fragmented:

- Sort (`sorter`) is uniform, but **client-only on the two server-paginated
  tables** (Tag Reads, Assets), so it silently sorts only the loaded page.
- The Sprint 70 wildcard editbox (`columnSearchFilter`) is on **5 pages only**,
  often a single column (Tag Reads = `Tag ID` only).
- Enum checkbox `filters` exist on a handful of columns but **without
  `filterSearch`** and (on server tables) **client-only**, so they too only act
  on the loaded page.

Sprint 75 makes the **client-side** list tables fully Excel-like and widens the
two **server** tables' editbox + sort coverage. Sprint 76 adds the server
checkbox facets.

Non-goals (this sprint): the server distinct-value facet endpoint (→ Sprint 76),
saved filters, cross-column global search, raw regex.

## 2. The unified helper — `excelColumn<T>()`

A single column-factory in `src/components/ExcelColumn.tsx` that returns AntD
`ColumnType<T>` props. It composes the **existing** pieces (Sprint 70 wildcard
editbox + AntD native `filters`/`filterSearch` + `sorter`) and **auto-selects**
the control by column nature, so callers write one line per column.

### 2.1 Client mode (fully-loaded tables) — auto

```ts
{ title: 'Name', dataIndex: 'name', ...excelColumn<Row>({ rows, accessor: r => r.name }) }
```

The helper inspects the data and picks:

| Column nature (auto-detected) | Control rendered | Mechanism |
|---|---|---|
| **Text, distinct-count > `FACET_MAX` (default 30)** | type-to-search **editbox** | `columnSearchFilter` client (`matchWildcard`) |
| **Text/enum, distinct-count ≤ `FACET_MAX`** | **searchable checkbox list** | AntD `filters` (auto-derived distinct values) + `filterSearch` + `onFilter` |
| **Numeric / date** | **sort + optional range** | `sorter` (auto comparator) + optional `filterDropdown` range |

Sort is **always** attached (auto `sorter`: numeric subtract, `Date` epoch, or
`localeCompare`), unless the caller passes `sortable: false`.

Callers may override the auto-pick: `kind: 'text' | 'enum' | 'number' | 'date'`,
an explicit `options` list (enum), or `facetMax`.

### 2.2 Server mode (paginated tables) — explicit

```ts
{ title: 'EPC', ...excelColumn<Row>({ mode: 'server', kind: 'text',
    value: epcQ, onSearch: setEpcQ }) }
```

Server mode never auto-derives values (the page is incomplete). It supports:

- `kind: 'text'` → editbox → `onSearch(pattern)` pushes a wildcard query param
  (server compiles via `wildcard_to_ilike`). Active state via `filteredValue`.
- `kind: 'enum'` with a **static** `options` list → checkbox list bound to a
  server param (used now for Assets Status/Source which have fixed enums; the
  *dynamic* facets come in Sprint 76).
- `kind: 'number' | 'date'` → sort (server `sort`/`order`) + optional range.

> **ADR-030 rule #2 stays law:** server-paginated tables never `onFilter`
> client-side. The helper enforces it — `mode: 'server'` omits `onFilter`.

## 3. Scope — the two essential tables, after Sprint 75

### 3.1 Tag Reads (server)
| Column | Sprint 75 control |
|---|---|
| Tag ID | editbox (existing `tag_q`) |
| Asset | editbox → new `asset_q` |
| EPC / EPC (hex) / TID / User Memory | editbox → new `epc_q` / `tid_q` (one combined identifier param; see §4) |
| Scheme / Device / Antenna | **deferred to Sprint 76** (checkbox facets) |
| Timestamp | sort (server) + existing start/end range |
| Signal / Temp / Humidity / Lat / Long | sort (server) |

### 3.2 Assets (server)
| Column | Sprint 75 control |
|---|---|
| Name | editbox → existing `q` (relabelled from the toolbar box onto the column) |
| External Ref | editbox → `q` already spans external_ref |
| Category | **deferred to Sprint 76** (dynamic facet); side-panel CategorySelect stays for now |
| Status / Location(source) | checkbox list bound to existing static enums, **server-side** |
| Last seen / Registered | sort (server) + range |
| Temperature | sort (server) |

The Assets **toolbar** `Search by name, external ref, or tag` box is folded into
the **Name** column editbox (same `q` param). One relearn item, documented in
the user guide + CHANGELOG.

## 4. Backend additions (small, additive)

All reuse `wildcard_to_ilike()` and the existing query services. No migration.

- **Tag Reads** (`GET /tag-reads`): add `epc_q` (matches `epc`/`epc_hex`/`tid`
  via `OR` of escaped `ILIKE`) and `asset_q` (matches the resolved bound-asset
  name — joins the Sprint 74 binding→asset path). Both optional; absent → no-op.
- **Server sort** for both endpoints: add `sort` (column whitelist) + `order`
  (`asc`/`desc`) params so the AntD header sort is whole-dataset correct.
  Whitelist only the sortable columns above; unknown column → 422.
- **Assets** (`GET /assets`): `status`/`source` already filterable; ensure both
  accept the multi-value form the checkbox list emits (`status in (...)`).

`openapi.json` regenerated; the UI `generate-api` picks up the new params.

## 5. UI migration plan

1. Build `excelColumn` + unit tests (auto-pick, client/server, range).
2. Migrate **client** list tables (no backend dep): Rules, Transfers, Stock
   Movements, Products, Users, Telemetry Models, Integrations, Delivery Log,
   Stock Levels, Lot Expiry Queue, Tag Data Mappings, Reconciliation. Add
   `filterSearch` to the pages that already hand-roll `filters`.
3. Wire the **server** editbox/sort columns on Tag Reads + Assets to the new
   params.
4. `npm run check` (lint + typecheck + tokens + vitest).

## 6. Acceptance

- Every **client** list table: each column header has a `▾` with sort + the
  type-correct filter; filtering/sorting is correct over the full dataset.
- Tag Reads + Assets: editbox on the identifier/name columns, server sort on the
  sortable columns — whole-dataset correct, not page-local.
- Existing tests stay green; new helper has unit coverage; CHANGELOGs + roadmap
  Sprint 75 section + user-guide note updated.
