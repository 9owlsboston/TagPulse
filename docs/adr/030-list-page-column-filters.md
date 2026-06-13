# ADR-030: List-page column filters — standard checkbox + search pattern

- Status: **Proposed (chore/demo-data-fixes, June 2026)**
- Scope: `TagPulse-UI` repo. Recorded in the backend repo because the backend
  owns the roadmap and ADR series (same arrangement as
  [ADR-029](029-ui-design-tokens.md)); implementation lands in `TagPulse-UI`.
- Related: [ADR-029 (UI design tokens)](029-ui-design-tokens.md),
  `src/components/ListPageShell.tsx` (the canonical list scaffold).

## Context

The UI has ~28 list pages built on AntD `<Table>`. Column filtering has grown
ad-hoc and inconsistent:

- Some pages use AntD's built-in header **`filters` + `onFilter`** (client-side
  row filtering): `AssetList` (`STATUS_FILTERS`, `SOURCE_FILTERS`),
  `CategoryList`, `SitesZones`, `AlertHistory`, `DeviceList`, `LabelManagement`.
- Some pages are **server-paginated** with a free-text `q` param and have no
  per-column filtering at all: `ProductList`, `StockLevels`, `TagList`,
  `TransferList`.
- Each client-side page hand-rolls its own `filters` array (e.g.
  `STATUS_FILTERS`) inline, so the look, the empty state, and whether a search
  box appears above the checkboxes all differ page to page.

The trigger: operators asked to filter Products by `category`. The Products
list exposes only free-text search; `category` is a display-only column. Adding
a one-off category filter to `ProductList` would deepen the inconsistency
rather than fix it.

The pattern operators expect (and that AntD supports natively via
`filterSearch: true`) is a **column header dropdown with a free-text search box
above a checkbox list** of distinct values.

## Decision

Adopt a single, uniform column-filter convention across all list pages, driven
by **column cardinality and data source** rather than applied blindly:

| Column shape | Examples | Filter mechanism |
|---|---|---|
| Low-cardinality enum, **fully client-loaded** | asset status, category, unit, connection state | AntD header `filters` + `filterSearch` (checkbox + search) |
| Low-cardinality enum, **server-paginated** | product category, tag status | Header filter selection → **query param** to the API (not client `onFilter`) |
| High-cardinality / free text | SKU, GTIN, EPC, name, asset tag | Toolbar `Input.Search` → server `q` (never a checkbox list) |
| Continuous / range | expiry date, signal strength, quantity | Date/number range control in the toolbar |

### Hard rules

1. **Checkbox + search is the standard for low-cardinality enumerable
   columns.** Enable `filterSearch` whenever a filter list can exceed ~7
   values; below that AntD hides the search box and that is fine.
2. **A header filter on a server-paginated table MUST drive a query param**,
   not client-side `onFilter`. Client `onFilter` only filters the current page,
   which silently misreports the dataset — that is a correctness bug, not a
   style choice.
3. **High-cardinality and free-text columns stay on the toolbar `q` search.**
   Do not force identifiers (SKU, GTIN, EPC, names) into checkbox lists.
4. **One shared factory, not per-page arrays.** A helper next to
   `ListPageShell` returns the AntD column-filter config so every page renders
   identically:

   ```ts
   makeEnumFilterColumn({
     dataIndex,
     options,                 // { text, value }[]
     mode: 'client' | 'server',
     search?: boolean,        // defaults to options.length > 7
     onServerChange?,         // required when mode === 'server'
   })
   ```

   Client mode wires `filters` + `filterSearch` + `onFilter`; server mode wires
   `filters` + `filterSearch` and emits the selected keys to `onServerChange`
   (which updates the query param). No page hand-rolls its own `filters` array.
5. **`AssetList` is the reference implementation.** Other pages migrate to the
   factory incrementally; new list pages use it from day one.

## Consequences

### Positive

- Operators get one consistent filter affordance everywhere.
- The Products category-filter request is satisfied correctly (server-side,
  whole-dataset) instead of as a one-off page hack.
- Server-paginated lists stop silently lying about filtered counts.
- New list pages have a single obvious way to add a column filter.

### Negative / costs

- Touches ~10 existing list pages; migration is incremental and spans more than
  one UI sprint.
- Server-mode filters require each backend list endpoint to accept the relevant
  facet as a query param. Some (e.g. `GET /products?category=`) may not exist
  yet and need a small backend follow-up — surfaced per page during migration.
- The shared factory is upfront work that produces no user-visible feature on
  its own (same trade-off ADR-029 accepted for the token layer).

### Out of scope for this ADR

- Range / date-range filter controls (only the convention is noted here; the
  control library is decided during implementation).
- Saved / shareable filter views.
- Multi-column compound filter builders.

## Decision history

- **v1.0 (chore/demo-data-fixes, June 2026)** — Proposed. Standard checkbox +
  search header filter for low-cardinality columns, client-vs-server rule, a
  shared `makeEnumFilterColumn` factory, `AssetList` as the reference. Prompted
  by the Products category-filter request and the broader filter drift across
  the list pages.
