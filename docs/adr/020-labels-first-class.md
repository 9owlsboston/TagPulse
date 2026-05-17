# ADR-020: Labels as a First-Class Entity

- Status: Proposed (Sprint 33, May 2026)
- Implements: gap 2.2 in `local reference notes/IMPLEMENTATION-GAPS.md`
- Related: [reference-design-remediation plan](../design/reference-design-remediation.md), [data-models.md](../data-models.md) (`metadata` JSONB on every tenant table), ADR [019 Categories](019-categories.md) (sibling catalog pattern), ADR [021 Configurable Sensing Events](021-configurable-sensing-events.md) (downstream consumer — Label filters in event scoping)

## Context

Every TagPulse entity (assets, sites, zones, devices, …) carries a free-form
`metadata` JSONB column. This is flexible but:

- No shared catalog. Two operators can spell the same concept differently
  (`location:warehouse-a` vs `loc:WAREHOUSE_A`) and there's no way to
  reconcile.
- No format validation. Free strings can include spaces, emojis, JSON
  injection.
- No per-entity cap. Bag-of-properties bloat is uncapped.
- No createdBy/updatedBy audit trail at the label level.
- No API to "list all labels in this account that match key=X" (today
  requires a full-table scan with JSONB extraction).

Reference design treats Labels as an account-scoped catalog with strict
format rules, a per-entity cap of 30, and per-label audit fields.

## Decision (proposed — to be ratified in Sprint 35)

Introduce a Labels catalog + association junction table. Coexist with
`metadata` JSONB rather than replace it.

```sql
CREATE TABLE labels (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    entity_type VARCHAR(32) NOT NULL,    -- asset | site | zone | device
    key VARCHAR(24) NOT NULL,            -- 3-24 chars, [A-Za-z0-9_.+$], no spaces
    color VARCHAR(7),                    -- '#RRGGBB' optional
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by UUID REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by UUID REFERENCES users(id),
    UNIQUE (tenant_id, entity_type, lower(key))
);

CREATE TABLE entity_labels (
    label_id UUID NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
    entity_id UUID NOT NULL,              -- polymorphic; matches labels.entity_type
    value VARCHAR(64) NOT NULL,           -- alphanumeric + _ + .
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by UUID REFERENCES users(id),
    PRIMARY KEY (label_id, entity_id)
);

-- Enforce 30-per-entity cap via deferred trigger (or app-layer).
-- RLS via JOIN to labels.tenant_id.
```

Validation rules:

- Key: 3–24 chars; `[A-Za-z0-9_.+$]`; no spaces; case-insensitive uniqueness
  per `(tenant, entity_type)`.
- Value: ≤64 chars; `[A-Za-z0-9_.]`.
- Cap: max 30 labels associated to any single entity (enforced in API layer
  with a `BEFORE INSERT` trigger as a backstop).
- Deleting an entity-label association is API-level disassociation only; the
  catalog row stays until explicitly deleted via the catalog endpoint.

API surface:

```
GET    /v1/tenants/{slug}/labels?entity_type=asset      (list catalog)
POST   /v1/tenants/{slug}/labels                        (create catalog row)
PATCH  /v1/tenants/{slug}/labels/{id}                   (rename / recolour)
DELETE /v1/tenants/{slug}/labels/{id}                   (delete; cascades to entity_labels)

POST   /v1/tenants/{slug}/{entity_type}/{id}/labels     (associate {key, value})
DELETE /v1/tenants/{slug}/{entity_type}/{id}/labels/{label_id}
GET    /v1/tenants/{slug}/{entity_type}/search?label.key=X&label.value=Y
```

Coexistence with `metadata`:

- `metadata` JSONB stays as the **escape hatch** for properties that don't
  fit the Label format (long values, structured nested data, integration-
  specific blobs).
- Labels are the canonical surface for: filtering, grouping, dashboards,
  Sensing Event scoping (ADR 021).
- A one-shot `scripts/migrate_metadata_to_labels.py` promotes top-level
  scalar keys that match the Label format into the new tables (idempotent;
  leaves the original `metadata` untouched as a back-out path).

UI: chip control replaces the raw `metadata` JSON textarea on entity detail
pages where Labels apply. `metadata` editor stays available behind an
"Advanced" accordion.

## Alternatives considered

1. **Keep `metadata` JSONB, add GIN indexes** — rejected; doesn't solve the
   no-catalog or no-cap problems, doesn't give audit trail.
2. **Single global `labels` table without `entity_type` partitioning** —
   rejected; key uniqueness should be per-entity-type (operators reasonably
   want `location` on Sites *and* `location` on Assets to mean different
   things).
3. **Use `audit_logs` for label-level audit** — partially adopted; we still
   wire `created_by` / `updated_by` columns for hot-path read performance,
   but mutations also emit existing `audit_logs` rows.

## Consequences

- **Positive:** enables Label chip UI, label-based filters everywhere, Sensing
  Event scoping; restores audit trail per label.
- **Migration risk:** existing `metadata` is heterogeneous. The migration
  script must be conservative — only promote keys matching `[A-Za-z0-9_.+$]`,
  skip everything else.
- **Storage:** two new tables. Cardinality bounded by `30 × entities × labels`;
  small.
- **API surface growth:** +9 endpoints. Acceptable cost for the capability.

## Open questions for Sprint 35

- Should `entity_type` be DB enum or a free string with app-layer validation?
  Lean enum (matches Categories `category_type` choice in ADR 019).
- Pre-seed common keys (`location`, `owner`, `priority`) per tenant on
  signup? Defer until UX feedback says it's needed.
- Cross-entity label propagation (asset inherits its site's labels)?
  Out-of-scope for this ADR; document as a follow-up.
