# Configurable column visibility — header toggle-off + "show all" reset

> Status: **Planning** — design-doc-first per the 3+-component convention.
> Extends [ADR-032](../adr/032-configurable-ui.md) (the `columns` leaf). Tier 2
> changes the `PUT /ui-config/me` write semantics and therefore needs an
> **ADR-032 amendment** (proposed `v1.3`) when it is scheduled. Implementation
> is split across two planned sprints (roadmap: Sprint 62 = Tier 1, Sprint 63 =
> Tier 2). This doc is the plan; no code ships on this branch.

## Why now

Sprint 60 shipped the [ADR-032](../adr/032-configurable-ui.md) `columns` leaf
(`hidden` / `advanced` / `order`) and the `Preferences` page that writes the
`cards` + `nav` leaves via `PUT /ui-config/me`. Operators can already *consume*
column config, but they can only **change** it through the API or the demo seed
— there is no in-app way to hide a column you don't want.

The ask is the familiar spreadsheet pattern (Excel / the Office grid, most data
tables): **hide a column directly from its header**, and a single **"show all"**
control to bring every hidden column back. This doc scopes what that takes,
because it turns out to hinge on one non-obvious write-path constraint.

The governing [ADR-032](../adr/032-configurable-ui.md) invariant still holds:
**configure presentation, never behavior.** Hiding a column never removes the
field from the API or CSV export — it is visibility only.

## What already exists (building blocks)

- **Data model.** `columns.<page>.hidden` / `.advanced` / `.order` leaves, per
  page, four-layer merged (System -> Tenant -> Role -> User). `ColumnGroup.hidden`
  is a free-form `list[str]`, so any column key is accepted — no schema change
  to hide an arbitrary column.
- **Consumption.** The UI's `applyColumnConfig` already filters columns by their
  stable `key` against `hidden` (and `advanced` behind the "Advanced columns"
  toggle), and `orderByKeys` applies `order`. Hiding a column is just "add its
  key to `hidden`."
- **Cross-device write path.** `PUT /ui-config/me` persists a per-user override
  (used today by the `Preferences` page for `cards` + `nav`).
- **Per-device precedent.** The Dashboard's "Customize" button already keeps a
  *per-browser* card-visibility choice in `localStorage`, layered beneath the
  server config. Column visibility can reuse this pattern for a zero-backend
  first cut.

## The core problem

Three properties of the current write path shape the whole design:

1. **`PUT /ui-config/me` replaces the user's prefs blob wholesale.** The
   repository (`UserUiPrefsRepository.upsert`) does an
   `on_conflict_do_update set prefs = :prefs` — it is a whole-document upsert,
   not a merge. Per-leaf merging happens **across layers** at resolve time, not
   **within** the user layer.
2. **There is no endpoint to read the user's *own* layer.** `GET /ui-config`
   returns the fully *resolved* (merged) document. A client cannot tell which
   `hidden` entries came from the user vs. the tenant/role floor.
3. **List leaves replace wholesale on merge.** `deep_merge` replaces a list-typed
   leaf (`hidden`, `order`, `advanced`) rather than unioning it. So a user-layer
   `columns.tag_reads.hidden = []` *overrides* a tenant/role hide and reveals
   every column.

Consequences:

- **Multi-writer clobber.** Today the `Preferences` page papers over (1) by
  PUTting `cards` + `nav` together, seeded from the resolved doc. If a new
  column-header writer naively PUTs only `{columns: ...}`, it **wipes the user's
  saved `cards`/`nav`** (and vice-versa). Any second write surface must either
  send the *full* reconstructed user override or we change the write semantics.
- **"Show all" is easy; "reset this table to team default" is not.** Because of
  (3), revealing everything is a one-liner (`hidden = []`). But surgically
  *removing* the user's `columns.<page>` leaf so they re-inherit the tenant/role
  floor needs either the missing read-own-layer endpoint or a granular reset.

## Two resets (both exist in Office, keep them distinct)

The user request — "overall reset to show all hidden" — is reset **A**:

- **A. Show all / Unhide all** = user override `columns.<page>.hidden = []`
  (and `advanced = []` if advanced columns should also show). Overrides the
  tenant/role floor; the user sees every column. This is the "Unhide All
  Columns" equivalent.
- **B. Reset to team default** = *remove* the user's `columns.<page>` leaf so
  they re-inherit tenant/role. Different outcome; needs Tier 2 plumbing.

Tier 1 ships **A**. Tier 2 adds **B** cleanly.

## Tier 1 — per-device, zero backend (MVP)

Smallest shippable, no API change, no clobber risk.

- A reusable **`ColumnChooser`** control in the list-page toolbar (next to the
  existing "Advanced columns" toggle): a "Columns" popover with a checkbox per
  addressable column + a **"Show all"** action. Optionally a per-header dropdown
  "Hide column" item (Ant `Dropdown` on the header cell) for the Office-like
  header affordance.
- Persist the hidden set to **`localStorage`**, keyed by page (mirrors the
  Dashboard "Customize" pattern). Layered beneath the server `columns` config so
  the server floor still applies.
- Generic and reusable — built once so Tag Reads, Assets, and every future list
  page inherit it, not a Tag-Reads-only fork.

Trade-off: choices do **not** follow the user across devices. That is the
explicit Tier 2 upgrade.

Touch points: UI only (`src/lib/columnConfig.ts`, a new `ColumnChooser`
component, the list-page shell, Tag Reads + Assets as first adopters).

## Tier 2 — cross-device, per-login (Office-grade)

Same UI control, now persisted per-user via `PUT /ui-config/me`, **plus** a
fix for the multi-writer clobber and a clean reset **B**. Two options:

- **Option 1 (recommended) — change the write semantics.** Add a merge-style
  write so each surface sends only its own leaf and they compose:
  - `PATCH /ui-config/me` -> deep-merge the body into the stored prefs (instead
    of wholesale replace), so the column writer and the `Preferences` writer no
    longer clobber each other.
  - A granular reset for **B**, e.g. `DELETE /ui-config/me/columns/{page}` (or a
    generic `DELETE /ui-config/me/{leaf}`), removing one leaf without touching
    the rest.
  - Keep `PUT /ui-config/me` as the explicit "replace my whole layer" verb;
    `{}` stays the global "reset to team default."
- **Option 2 — client reconstructs the full override.** No backend change: on
  every write, rebuild the entire user-layer override from the resolved doc and
  PUT it. Rejected as the primary path — it *freezes* tenant/role defaults into
  the user layer (future floor changes stop propagating for that user) and gets
  brittle as leaves grow. It also still requires refactoring the `Preferences`
  save to compose.

Option 1 needs an **ADR-032 amendment** (write-semantics change) and an
`openapi.json` regen in the backend PR; the UI rebases onto it (standard
cross-repo order: backend first).

Touch points: backend (`src/tagpulse/api/routes/ui_config.py`,
`UserUiPrefsRepository`, ADR-032, `openapi.json`), UI (the `ColumnChooser` write
path via `useUpdateMyUiConfig`, plus reworking the `Preferences` save to use the
new merge verb).

## Caveats / decisions to settle at kickoff

- **Stable, addressable keys.** Every toggleable column needs a stable `key`.
  Most already have one (`epc_scheme`, `tid`, `user_memory_hex`, ...); a few
  computed/unaddressable columns currently "always show" and would need keys
  added before they can be hidden.
- **`locked` floor not implemented.** ADR-032's `locked` leaf-pin is still
  deferred, so "show all" currently reveals even a tenant's compliance-mandated
  hide. If a hard floor is required, `locked` enforcement is the gate — decide
  whether Tier 2 depends on it or ships before it.
- **"Show all" scope.** Confirm "show all" reveals `hidden` only, or also
  `advanced` columns (recommendation: a single "Show all columns" that clears
  both, matching the spreadsheet mental model).
- **Per-device vs per-login precedence.** With both layers present (Tier 1
  `localStorage` + Tier 2 server), define precedence. Recommendation: server is
  the floor, the local layer is a per-device override on top (same shape as the
  Dashboard card precedent).
- **Relation to ADR-030 column *filters*.** This is column *visibility*, not the
  [ADR-030](../adr/030-list-page-column-filters.md) value-*filtering* work. They
  share the list-page toolbar but are independent; keep the controls visually
  distinct.

## Out of scope

- Arbitrary drag-and-drop column reordering UI (config supports `order`; an
  in-app reorder builder is a later, separable ask).
- Admin UI to edit tenant/role column defaults (tracked separately as the
  general "in-app admin UI for tenant/role config" tail item).
- `locked` enforcement itself (its own ADR-032 increment).
- Any change to what columns *exist* or to exports — visibility only.

## Sprint phasing

| Sprint | Tier | Shape | Repos |
|---|---|---|---|
| 62 | Tier 1 | Per-device `ColumnChooser` + "Show all", `localStorage`, no backend | UI only |
| 63 | Tier 2 | Cross-device via `PATCH /ui-config/me` merge + granular reset; ADR-032 amendment; `Preferences` save rework | backend + UI |

Tier 1 is independently shippable and delivers the Office-like UX immediately;
Tier 2 layers cross-device persistence and the clean "reset to team default"
once the write-semantics change is made.
