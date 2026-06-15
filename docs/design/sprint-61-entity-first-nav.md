# Sprint 61 — Entity-first IA + nav placement mechanism

> Status: **Planning** — kicked off on `sprint-61/entity-first-nav` (backend PR
> [#99](https://github.com/9owlsboston/TagPulse/pull/99), UI PR
> [#83](https://github.com/9owlsboston/TagPulse-UI/pull/83)). Design-doc-first
> per the 3+-component convention. Extends [ADR-032](../adr/032-configurable-ui.md)
> (the `nav` leaf) with a new `placement` concept; an ADR-032 `v1.2` amendment
> records the schema change.

## Why now

The June 2026 WM focus-group wireframes are consistently **entity-first**: the
top-level menu is a list of the domain nouns — *Assets, Tags, Readers, Alerts* —
with one operational catch-all (*Data Management*). The current information
architecture mixes grouping styles:

- **Activity-named** sections: *Asset Tracking*.
- **Entity-ish** sections: *Devices & Telemetry* (already skinned to *Readers &
  Telemetry* for WM).
- A **catch-all** that has grown too broad: *Data Management* currently holds
  Tag operations (Import, Transfers, Reconciliation) that belong with the Tag
  entity.

Sprint 60 shipped the [ADR-032](../adr/032-configurable-ui.md) `nav` leaf
(`hidden` / `order`, restrict-only) and proved per-tenant menu configuration.
But two of the WM asks surfaced a capability the `nav` leaf **cannot express**:
moving an item from one parent to another (e.g. *Tag Reads* under *Tags* vs.
pinned top-level). That is this sprint's core new mechanism.

This sprint does **not** change any behavior, routes, or data — it is an IA
restructure plus one additive presentation-config leaf. The
[ADR-032](../adr/032-configurable-ui.md) §1 invariant holds: **configure
presentation, never behavior.**

## Sprint goal

Three sequenced deliverables, smallest-shippable-first:

1. **IA restructure** (UI-only) — regroup `NAV_SECTIONS` / `NAV_TOP` entity-first.
   Default order is unchanged in *spirit* but the groupings change; every route
   stays reachable; mode-gating (`tracking_modes`) and role-gating are untouched.
2. **`nav.placement` mechanism** (cross-repo) — a new presentation-config leaf
   that pins a *movable* item to one of its **candidate parents**, with
   mutually-exclusive resolution. Backs the WM "Tag Reads under Tags" and
   "Locations under Assets vs. its own section" asks.
3. **Preferences "Menu" toggles** (UI) — mirror the dashboard-card
   check/uncheck pattern: a user (or admin, per layer) hides/shows menu entries
   and chooses placement for movable items, persisted via the existing
   `PUT /ui-config/{me,tenant,role}` paths.

## The target IA

Entity-first. Each entity owns its primary views; *Data Management* shrinks to
genuine cross-cutting reference data + bulk I/O.

| Top-level | Type | Children (default) |
|---|---|---|
| **Dashboard** | page | — |
| **Assets** | section | Assets · Locations · Map *(movable → Locations section)* |
| **Tags** | section | Tags · Tag Reads *(movable → top-level)* · Tag Transfers · Tag Reconciliation · Tag Import |
| **Readers** | section | Readers · Telemetry · Telemetry Models · Integrations |
| **Inventory** | section *(hidable)* | Products · Lot Expiry · Stock Levels · Stock Movements |
| **Alerts** | section | Alerts · Rules |
| **Data Management** | catch-all | Categories · Labels · Inventory CSV Import |

Decisions locked with the product owner (2026-06-14):

- **Tag Reads** default placement: **under Tags** (movable to top-level).
- **Locations / Map** default placement: **under Assets** (movable to a
  dedicated *Locations* section).
- **Tag operations** (Transfers, Reconciliation, Import) → **Tags**.
- **Rules** → **Alerts**.
- **Inventory** stays a real section, **hidable** per-tenant via `nav.hidden`
  (not via `tracking_modes`, so the cold-chain data/pages survive and the hide
  is reversible). For the WM demo tenant it will be hidden.

### What does *not* move into entities

*Data Management* keeps only **reference data + bulk I/O** that genuinely spans
entities: **Categories**, **Labels** (the label registry, distinct from the
ADR-032 label *skin*), and **Inventory CSV Import**. These are admin/operational
surfaces, not a single entity's primary views.

## The new mechanism: `nav.placement`

### Problem

The `nav` leaf today is `{ hidden: string[], order: string[] }`. It can hide an
item and reorder siblings, but it **cannot relocate** an item to a different
parent. "Tag Reads top-level **xor** under Tags" is a *placement* choice, not a
hide/order choice.

### Design

Introduce a curated registry of **movable items**, each with an enumerated set
of **candidate parents** and a **default**:

```jsonc
// code-side registry (UI nav.tsx + backend ui_config.py, kept in lock-step)
MOVABLE_ITEMS = {
  "tag-reads":  { candidates: ["sec-tags", "top"],        default: "sec-tags" },
  "locations":  { candidates: ["sec-assets", "sec-locations"], default: "sec-assets" },
  "map":        { candidates: ["sec-assets", "sec-locations"], default: "sec-assets" },
}
```

The config document gains a `placement` sub-leaf under `nav`:

```jsonc
{
  "nav": {
    "hidden": ["sec-inventory"],
    "order": ["sec-tags", "sec-assets", "sec-readers", "sec-alerts", "sec-data-management"],
    "placement": { "tag-reads": "top", "locations": "sec-locations" }
  }
}
```

### Resolution rules (deterministic, validated)

1. **Closed vocabulary.** A `placement` entry is valid only if the item is in
   `MOVABLE_ITEMS` and the chosen parent is in that item's `candidates`.
   Anything else is a `ValidationError` → **422** on the write paths (same
   posture as the curated `labels` / `theme` catalogues).
2. **Mutual exclusivity is structural, not config'd.** Each movable item renders
   in **exactly one** parent — its resolved placement (override → default).
   There is never a "checked in two places" state to reconcile; the UI renders
   the item once, at its resolved parent.
3. **Per-leaf merge (unchanged).** `placement` deep-merges like every other
   leaf across System → Tenant → Role → User. A user who moves Tag Reads
   inherits every other placement default.
4. **`top` is a reserved parent token** meaning "ungrouped top-level page"
   (renders in `NAV_TOP`, above the sections — consistent with the existing
   Layout constraint that top items precede sections).
5. **Hidden wins over placement.** If an item is in `nav.hidden`, it does not
   render regardless of `placement` (no orphan).

### Honest constraint we are *accepting*, not fixing

Layout builds the menu as `[...topItems, ...sections]`, so **top-level pages
always render above all sections**. The literal wireframe order ends with
*Alerts* as the last entry *below* Data Management — but Alerts is (now) a
section, and Dashboard/movable-Tag-Reads are top-level pages, so a top-level
page cannot sort *below* a section by config alone. We **keep this constraint**:
`order` reorders sections-among-sections and top-items-among-top-items, not
across the two bands. Making a page sortable below sections is explicitly
**out of scope** (a deeper Layout change with little real payoff — the entity
sections are what the wireframe is really about).

## Rollout order (sequenced PRs)

1. **PR-A (UI): IA restructure.** Regroup `NAV_SECTIONS` / `NAV_TOP`
   entity-first; move Tag operations → Tags, Rules → Alerts, Locations/Map →
   Assets; add an `sec-locations` section definition that is *empty by default*
   (only populated when a tenant moves Locations/Map into it). Pure
   reorganization: every route still reachable, `nav.test.ts` route-reachability
   smoke stays green, default render unchanged for items that didn't move
   parents. No config change.
2. **PR-B (backend): `nav.placement` schema + resolver.** Add `MOVABLE_ITEMS`,
   the `placement` field on the `nav` leaf model, the closed-vocabulary
   validator, and resolver fold. `openapi.json` regenerated. Unit tests:
   valid/invalid placement, default fall-through, mutual-exclusion invariants,
   four-layer merge.
3. **PR-C (UI): consume `placement`.** `applyNavConfig` honors resolved
   placement to render each movable item at its parent (or `top`). Tests pin
   "Tag Reads under Tags by default; moves to top-level under `{tag-reads:
   top}`; Locations under Assets by default; moves to Locations section."
4. **PR-D (UI): Preferences "Menu" panel.** Mirror the dashboard-card UX: a
   checkbox list (hide/show, writing `nav.hidden`) plus, for movable items, a
   small radio/segmented "where should this live" control (writing
   `nav.placement`). Persists via `useUpdateMyUiConfig()` (`PUT /ui-config/me`);
   "Reset to team default" already clears the row.
5. **PR-E (backend): WM demo seed.** Extend `WM_DEMO_PRESENTATION` with the
   reconciled WM nav: `hidden: ["sec-inventory"]`, the entity-first section
   `order`, and any WM placement choices. Demo seed data only.

Backend-first merge order where a PR touches the contract (B before C/D);
`openapi.json` regenerated in the same PR; UI rebases onto it.

## Out of scope

- **Making a top-level page sort below sections** (the literal "Alerts last"
  wireframe order) — accepted constraint, see above.
- **Arbitrary drag-and-drop nav builder** — placement is a *curated* movable
  set, not free-form tree editing (keeps the presentation-only, testable
  invariant).
- **`tracking_modes` changes** — Inventory is hidden via the presentation
  `nav.hidden` leaf, not the capability flag; no behavior/data change.
- **New routes or pages** — this is purely how existing destinations are
  grouped and placed.

## Acceptance

- Every existing route is reachable from the restructured nav (route-reachability
  smoke green).
- `nav.placement` rejects unknown items/parents with 422; valid placements fold
  through the four-layer merge; each movable item renders in exactly one parent.
- Preferences "Menu" panel hides/shows entries and relocates movable items,
  persisted per-user; "Reset to team default" reverts.
- WM demo tenant renders: entity-first sections, Inventory hidden, Tag Reads
  under Tags, Data Management present — matching the wireframe (modulo the
  accepted top-band/section-band ordering constraint).
- `make check` (backend) and `npm run check` (UI) green; `openapi.json`
  regenerated for the schema change.
