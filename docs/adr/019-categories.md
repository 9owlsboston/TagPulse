# ADR-019: Categories as a First-Class Entity

- Status: **Completed** (Sprint 41 close-out, [PR #54](https://github.com/9owlsboston/TagPulse/pull/54) — accepted Sprint 34, May 2026; compatibility window closed Sprint 41, Phase H)
- Implements: gap 2.1 (and unblocks 2.8) in the external schema/API audit notes (held locally)
- Related: [reference-design-remediation plan](../design/reference-design-remediation.md), [data-models.md §assets](../data-models.md), ADR [005 rules engine](005-embedded-rules-engine.md), ADR [021 Configurable Sensing Events](021-configurable-sensing-events.md) (downstream consumer)

## Context

Today `assets.asset_type` is a free-form `VARCHAR(50)` per
[`models/database.py`](../../src/tagpulse/models/database.py). The reference
design treats Categories as a first-class entity: every asset must belong to
exactly one Category, Category declares the sensing-event capability template
and the required-tag count, and Configurable Sensing Events (ADR 021) scope
themselves per `(category, event_type)`.

> **Terminology note.** TagPulse's domain term for an RFID transponder
> is **tag** — see [`docs/data-models.md` §"Where is the tag?"](../data-models.md#where-is-the-tag-and-why-theres-no-tags-table)
> for the why. All TagPulse-owned schema (column names, enum values,
> API fields) uses this vocabulary, regardless of what any external
> reference design happens to call it.

Without Categories:

- Sensing Events (ADR 021) cannot scope correctly — every config would have to
  list every asset.
- The UI cannot offer a meaningful filter on Asset lists beyond free-text
  type.
- The outbound event envelope (gap 2.9) cannot carry `categoryId`.
- The deferred tag-registry concept (gap 2.14) cannot enforce the
  required-tag-count contract.

## Decision

Introduce a new tenant-scoped `categories` table and add a nullable FK
`assets.category_id`.

```sql
CREATE TABLE categories (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    name VARCHAR(255) NOT NULL,
    sku_upc VARCHAR(64),
    description TEXT,
    category_type VARCHAR(32) NOT NULL,   -- liquid_container | reference_tag | rti_container | object
    required_tags SMALLINT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);
-- RLS: USING (tenant_id = current_setting('app.current_tenant_id')::uuid)

ALTER TABLE assets ADD COLUMN category_id UUID REFERENCES categories(id);
```

Rules:

- `category_type` is **immutable after create** (matches reference; enforced
  in API layer, not DB).
- `categories` cannot be deleted while any asset references them (FK is
  `ON DELETE RESTRICT`; API returns 409 with a list of referencing asset IDs).
- During migration, every existing `assets.asset_type` string became a
  default `category_type='object'` row per tenant; `assets.category_id` was
  back-filled (Sprint 34, migration `037`). `assets.asset_type` stayed in
  place for one release as a compatibility shadow and was **dropped in
  Sprint 41 Phase H** (migration `041`), at which point `category_id` was
  promoted to `NOT NULL`.

API surface:

```
GET    /v1/tenants/{slug}/categories               (list, viewer)
POST   /v1/tenants/{slug}/categories               (create, editor)
GET    /v1/tenants/{slug}/categories/{id}          (read, viewer)
PATCH  /v1/tenants/{slug}/categories/{id}          (update, editor; rejects category_type changes)
DELETE /v1/tenants/{slug}/categories/{id}          (delete, admin; 409 if referenced)
```

UI: new sidebar item "Categories" under the top-level section; CRUD page with
columns Name · SKU/UPC · ID · Description · Type · # required tags.

## Alternatives considered

1. **Status quo (free-form `asset_type`)** — rejected; can't scope Sensing
   Events, can't propagate `categoryId` in outbound events.
2. **Tag-based categorisation via Labels (ADR 020)** — rejected; Categories
   carry behavioural metadata (`required_tags`, `category_type`) that
   Labels deliberately don't.
3. **Categories as JSONB array on `tenants`** — rejected; doesn't scale, no
   FK integrity, no per-row audit.

## Consequences

- **Positive:** unblocks ADR 021 (Sensing Events scoping); enables `categoryId`
  in outbound envelope (gap 2.9); cleaner Assets UI filter.
- **Migration risk:** existing `asset_type` strings vary in casing. Need a
  pre-migration sweep that normalises case + de-duplicates per tenant.
- **API breakage:** new required field for `POST /assets` after the
  compatibility window closes (one release). Document in CHANGELOG.
- **No cost impact:** Categories are low-cardinality (tens per tenant).

## Open questions for Sprint 34 — resolved

- **Should `category_type` be DB-enforced (CHECK constraint) or app-enforced
  (Pydantic enum only)?** — **DB-enforced.** Migration
  [`037_categories.py`](../../migrations/versions/037_categories.py) creates
  `ck_categories_type CHECK (category_type IN (...))`. App-side enum stays
  too for 4xx error messages.
- **Should `required_tags` be inferred from `category_type` (like the
  reference design) or operator-set?** — **Operator-set**, default `1`,
  validated `>= 1` both in the DB (`ck_categories_required_tags_positive`)
  and in `CategoryCreate` / `CategoryUpdate`. UI suggests a per-type
  default; backend doesn't enforce one.
- **Cross-tenant import path for category catalogs?** — Deferred. Will
  revisit once a customer asks.

## Deviations from the proposal

- **URL shape.** ADR drafted `/v1/tenants/{slug}/categories`. Shipped as
  `/categories` with `Depends(get_current_tenant)` to match the
  established pattern (see `tenant_branding.py`). No path versioning is
  used in TagPulse today. Documented in the router docstring.
- **DELETE 409 payload.** ADR said "list of referencing asset IDs".
  Shipped as `{"message": "...", "asset_count": N}` — the UI uses the
  count to gate the confirmation flow; listing every referencing asset id
  could be unbounded. Asset listing is one `GET /assets?category_id=` away
  if the UI ever needs it.
