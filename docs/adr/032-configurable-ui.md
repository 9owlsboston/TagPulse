# ADR-032: Configurable UI — presentation config as a per-viewer projection of one engine

- Status: **Accepted (sprint-60/configurable-ui, June 2026); amended v1.3 (sprint-63/column-visibility-tier2, June 2026)**
- Scope: **backend + `TagPulse-UI`.** The backend owns the config storage,
  resolution, and `GET /ui-config` contract; `TagPulse-UI` consumes the
  resolved document. Recorded in the backend repo per the standing arrangement
  (backend owns the roadmap + ADR series, same as
  [ADR-029](029-ui-design-tokens.md) / [ADR-030](030-list-page-column-filters.md)).
- Related: [ADR-029 (UI design tokens)](029-ui-design-tokens.md) — the
  theme/card-style seam this ADR drives; [ADR-030 (list-page column
  filters)](030-list-page-column-filters.md) — the column show/hide + sort +
  search convention this ADR persists; [ADR-008 (multi-tenancy)](008-multi-tenancy-strategy.md)
  — the tenant scoping the config layers ride on; [ADR-021 (configurable
  sensing events)](021-configurable-sensing-events.md) — the *behavior* config
  boundary this ADR deliberately does **not** cross.

## Context

WM focus-group feedback (June 2026 wireframes) from non-technical operators
asked for a simpler interface: hide plumbing columns (TID, the `metadata`
JSONB), hide dashboard cards they don't use, sparkline-style card visuals,
sort-by-header, and free-text search. The naïve reading is "build a simpler
app." That is the wrong reading.

TagPulse is deliberately a **sophisticated, multi-device-type engine**
(ADR-008 multi-tenancy, the subject-scoped telemetry model, the signaling
rules engine). WM operators are **one persona** looking at it; the next
customer may want the full grid. Shipping a stripped-down fork — or bolting
one-off "hide this column" edits onto individual pages — would either fork the
product per customer or accrete exactly the per-page drift ADR-029 and ADR-030
were created to stop.

Two capabilities already exist and point at the answer:

- **ADR-030** already standardized sort-by-header + free-search +
  column-filter across the ~28 list pages. The WM "sort + search" ask is
  *already a decided convention* — what's missing is **persisting** a user's
  choice of which columns/sort to default to.
- **ADR-029** already established a semantic design-token layer. "Sparkline
  cards / 2–3 approved card styles" is a token-driven theme variant, not
  free-form styling.

What's missing is the connective tissue: a single, curated, per-viewer
**presentation** configuration — label skins, column presets, card/widget
visibility, theme variant, table defaults — resolved across tenant / role /
user, stored once, served resolved.

## Decision

Introduce a **UI config contract**: one schema-validated presentation document,
resolved server-side across four layers, stored on the existing tenant-JSONB
precedent, and served already-merged to the UI via a single endpoint.

### 1. The governing invariant

> **Configure _presentation_ (visibility, order, density, theme, presets),
> never _behavior / semantics_ (what a rule means, how ingest works, what a
> status implies).**

Presentation config is infinitely safe to expand — every leaf is a view,
testable as a projection. Behavior config is where flexibility becomes
untestable code paths and per-customer forks (the line ADR-021 already draws
for sensing-event *behavior*). Every WM ask is 100% presentation, which is
precisely why all of it can be granted without touching the engine. **New
personas ship as presets (data), not forks (code).**

### 2. Scope resolution — four layers, deep-merged per leaf

Effective config is the deep-merge of four layers, last writer wins **per leaf
key** (not whole-object replace):

```
System default  →  Tenant default  →  Role default  →  User override
   (code)            (tenants)         (tenants)        (user_ui_prefs)
```

- **Per-leaf merge.** A user who hides one column inherits every other layer's
  choices; a missing key falls through to the layer below. This is the exact
  semantics `tenants.rate_limit_overrides` already uses ("any subset of keys;
  missing keys fall back to Settings").
- **"Reset to team default"** comes in two scopes (both keep the four-layer
  floor reachable, never an unrecoverable corner state):
  - **Whole layer** = clear the user-override row (`PUT /ui-config/me` with an
    empty body `{}`) → the user falls back to role / tenant / system for
    *every* leaf.
  - **One leaf** = remove a single sub-tree without touching the rest
    (`DELETE /ui-config/me/columns/{page}`, Sprint 63) → just that page's
    column override re-inherits the floor while the user's other choices
    (other pages, `cards`, `nav`, …) stand. This is distinct from a user
    *showing every column* by setting `columns.<page>.hidden = []` (a list-leaf
    replace that overrides the floor's hides — see the write-semantics note).
- **Write semantics — `PUT` replaces, `PATCH` deep-merges (Sprint 63).** A
  `PUT /ui-config/me` replaces the caller's **whole** user layer (the original
  single-write-surface path). A `PATCH /ui-config/me` deep-merges a *sparse*
  body into the stored prefs per leaf — nested dicts recurse, a **list is a
  leaf and replaces wholesale** (so `columns.assets.hidden` is set, not
  unioned). This lets independent write surfaces compose instead of clobbering
  each other: the column chooser writing `columns.<page>` no longer wipes the
  Preferences page's `cards` / `nav` choices, even with no endpoint to read the
  user's own unmerged layer. The merged result is re-validated against the §4
  schema on every write so a stored doc can never drift out of shape.
- **`locked: true`** on a tenant/role leaf prevents lower layers from
  overriding it (e.g. a compliance-mandated column hide that a user may not
  re-enable).

### 3. Storage — reuse the tenant-JSONB precedent, no new pattern

Tenants already carry this exact shape: `tile_provider`,
`rate_limit_overrides`, and `position_strategy` are all "nullable JSONB, subset
of keys, fall back to system default." UI config is the same animal, so it
reuses the pattern rather than inventing a relational schema.

- **Tenant + role defaults** → a new nullable `ui_config JSONB` column on
  `tenants`. The role layer is keyed inside it (`{"roles": {"operator": {…}}}`).
  `NULL` = pure system default. This **resolves the roadmap's open D8 question**
  (tenant-settings JSONB vs. dedicated tables) **by precedent: JSONB**, until a
  leaf needs relational querying — none in this contract do.
- **User overrides** → a new small table
  `user_ui_prefs (user_id PK, tenant_id, prefs JSONB, updated_at)`. Per-user,
  low-cardinality, no history required.
- **System default** → a versioned, tested **code constant**, never in the DB.

### 4. The config document (leaf keys — all presentation)

```jsonc
{
  "labels":  { "device": "Reader", "telemetry": "Readings" },    // label skin
  "theme":   { "variant": "operator", "cardStyle": "sparkline" }, // ADR-029 tokens
  "nav":     { "hidden": ["data-management"], "order": ["..."] }, // menu system
  "cards":   { "dashboard": { "hidden": ["dead_letter"], "order": ["..."] } },
  "columns": {                                                    // ADR-030 surface
    "assets":    { "hidden": ["metadata"], "order": ["..."], "advanced": ["tid"] },
    "tag_reads": { "hidden": ["tid", "user_memory_hex"] }
  },
  "tables":  { "assets": { "defaultSort": { "key": "name", "dir": "asc" } } }
}
```

- **`columns.*.advanced`** is the key move for the TID / `metadata`-JSONB ask:
  default-OFF, revealed by an "Advanced columns" toggle. Power users keep
  access; operators are not overwhelmed. This is *default-hidden*, never
  deletion — the field still exists in the API and exports.
- **`labels`** is the label-skin surface (a per-tenant display override, **not**
  a schema/code rename — keeps the multi-device-type architecture intact). The
  WM-facing values are chosen during the terminology sprint; the mechanism
  lives here.
- **`theme.cardStyle`** rides ADR-029 tokens: 2–3 curated variants (sparkline
  is one), not unbounded styling knobs.
- **Sort / search are always-on capabilities** (ADR-030); only their *default*
  and a user's *persisted state* live here.

### 5. API surface — one endpoint family, contract-first

- `GET  /ui-config` → returns the **resolved** document for the caller; the
  server performs the four-layer merge so the UI never reconstructs it.
  Cacheable per `(tenant, role, user)`.
- `PUT  /ui-config/me` → **replace** the caller's `user_ui_prefs.prefs`
  wholesale (empty body `{}` = reset the whole user layer).
- `PATCH /ui-config/me` → **deep-merge** a sparse body into the caller's stored
  prefs per leaf (Sprint 63), so independent write surfaces compose (§2
  write-semantics). Lists replace wholesale; the merged result is re-validated.
- `DELETE /ui-config/me/columns/{page}` → granular reset (Sprint 63): drop just
  `columns.<page>` from the user layer so that page re-inherits the
  tenant/role/system floor; idempotent (resetting an unset page is a 200 no-op).
- `PUT  /ui-config/tenant` and `PUT /ui-config/role/{role}` → admin-gated
  tenant / role defaults.

Any new endpoint regenerates `openapi.json` in the same PR; the UI rebases onto
the regenerated contract (the standing cross-repo convention).

### 6. Guardrails baked into the contract

1. **Schema-validated leaves.** `ui_config` / `prefs` are validated by a
   Pydantic model on write; unknown keys are rejected. Config is a curated
   surface, not a free JSON dump.
2. **Presentation-only invariant (§1).** No leaf may alter behavior. Enforced by
   review and by the structural fact that nothing here feeds the rules / ingest
   / auth engines.
3. **Reference integrity.** Hiding a column never deletes data; `advanced`
   columns and all exports always expose the full field set.
4. **Graceful unknown keys on read.** The UI ignores leaves it doesn't
   recognize, so the catalogue can grow without breaking older clients.

### 7. Rollout order (smallest shippable increments)

1. **Server-resolved `GET /ui-config` + system defaults only** (no DB) — proves
   the merge and UI consumption with zero persistence risk.
2. **User overrides** (`user_ui_prefs` + `PUT /ui-config/me`) — delivers
   hide-column / hide-card / sort-default / advanced-toggle. **This increment
   alone satisfies the bulk of the WM ask.**
3. **Tenant + role defaults** (`tenants.ui_config`) — admins set the floor /
   persona; "Reset to team default" lights up.
4. **Label skins** consume `labels.*` (the terminology sprint picks values).
5. **Theme / cardStyle variants** on ADR-029 tokens (sparkline et al.).

## Consequences

### Positive

- One sophisticated engine serves simple *and* rich personas without a fork —
  the durable answer to "who knows what our next users will want."
- The WM feedback is satisfiable end-to-end as **presentation**, with no engine
  change and no integrity erosion.
- Resolves the open D8 storage question by precedent (tenant JSONB).
- Reuses ADR-029 (tokens) and ADR-030 (filters) rather than duplicating them;
  this ADR is the persistence + resolution layer they were missing.

### Negative / costs

- New backend surface: one column, one small table, one endpoint family, and a
  validated config model. Upfront work that produces no user-visible feature
  until increment 2 (the same trade-off ADR-029/030 accepted for their layers).
- A growing leaf catalogue needs discipline to stay presentation-only; the §1
  invariant is a review burden, not a compiler-enforced one.
- Deep-merge + `locked` semantics need careful tests (precedence, reset,
  forward-compat unknown keys).

### Out of scope for this ADR

- **Any behavior/semantics config** — rule logic, ingest, auth, retention.
  Those follow their own ADRs (e.g. ADR-021), never `ui_config`.
- **Saved / shareable named views** beyond a single per-user default (a later
  extension once the single-default path is proven).
- **The concrete WM-facing label values** (chosen in the terminology sprint;
  this ADR only fixes the mechanism).
- **Marketing/content surfaces** (e.g. the dashboard footer link strip) — those
  stay UI/CMS, not in the API.

## Decision history

- **v1.3 (sprint-63/column-visibility-tier2, June 2026)** — Amended. Added the
  two Sprint 63 write surfaces backing in-app configurable column visibility
  (Tier 2, cross-device) without changing the contract's shape: `PATCH
  /ui-config/me` **deep-merges** a sparse body into the user layer (vs. `PUT`'s
  wholesale replace) so the column chooser and the Preferences page compose
  instead of clobbering each other, and `DELETE /ui-config/me/columns/{page}`
  resets one list page's column override to the team default while leaving the
  rest of the user layer intact. §2 now spells out the **two reset scopes**
  (whole-layer via `PUT {}` vs. one-leaf via `DELETE`) and the `PUT`-replaces /
  `PATCH`-merges write semantics (list leaves replace wholesale; merged result
  re-validated against §4). §5 gains both endpoints; `openapi.json` regenerated.
  No schema change (reuses `user_ui_prefs`). The `locked` leaf-pin stays the one
  deferred §2 increment. `TagPulse-UI` consumes the new endpoints (retire the
  Tier 1 `localStorage` path) as the paired cross-repo follow-on.
- **v1.1 (sprint-60/configurable-ui, June 2026)** — Accepted. The full §7
  backend rollout shipped over Sprint 60 (steps 1–5): server-resolved
  `GET /ui-config` over system defaults, the `user_ui_prefs` user-override
  layer (`PUT /ui-config/me`), the `tenants.ui_config` tenant + role default
  layers (`PUT /ui-config/{tenant,role/{role}}`), the curated `labels`
  registry (`Device`→`Reader`), and the curated `theme` variant + card-style
  catalogues — all in `tagpulse.services.ui_config` behind the four-layer
  deep-merge. The `locked` leaf-pin remains the one deferred §2 increment
  (it earns its complexity once the tenant/role floor layers are in real use).
  `TagPulse-UI` consumption of the resolved document is the remaining
  cross-repo follow-on.
- **v1.0 (chore/configurable-ui-adr, June 2026)** — Proposed. Presentation-only
  UI config contract: the presentation-vs-behavior invariant, a
  System→Tenant→Role→User deep-merge with "Reset to team default" + `locked`,
  tenant-JSONB storage (`tenants.ui_config` + `user_ui_prefs`) resolving the D8
  question by precedent, the leaf-key document (labels / theme / nav / cards /
  columns / tables) with `columns.*.advanced` for the TID/metadata ask, a
  single `GET /ui-config` resolved-server-side endpoint family, and a 5-step
  rollout where user overrides (step 2) satisfy the bulk of the WM ask.
  Prompted by the June 2026 WM focus-group wireframes and grounded against the
  existing ADR-029/030 UI conventions and the tenant-JSONB precedent.
