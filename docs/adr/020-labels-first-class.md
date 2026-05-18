# ADR-020: Labels as a First-Class Entity

- Status: **Accepted (Sprint 35, May 2026)** — ratified at the start of the implementing sprint; the Open Questions section is closed inline below.
- Implements: gap 2.2 in `~/ws/TagPulse-Design/IMPLEMENTATION-GAPS.md`
- Related: [reference-design-remediation plan](../design/reference-design-remediation.md), [data-models.md](../data-models.md) (`metadata` JSONB on every tenant table), ADR [019 Categories](019-categories.md) (sibling catalog pattern; implementation template), ADR [021 Configurable Sensing Events](021-configurable-sensing-events.md) (downstream consumer — Label filters in event scoping), ADR [023 Outbound Connections](023-outbound-connections-mqtt-kafka.md) (downstream consumer — envelope `labels[]` field, gap 2.9)

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

## Decision

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

-- Enforce 30-per-entity cap via a BEFORE INSERT trigger as a backstop;
-- the API layer also enforces the cap with an early reject so the trigger
-- only fires on bypass paths (direct SQL, future bulk-associate jobs).
CREATE FUNCTION enforce_label_cap() RETURNS TRIGGER AS $$
BEGIN
  IF (SELECT count(*) FROM entity_labels WHERE entity_id = NEW.entity_id) >= 30 THEN
    RAISE EXCEPTION 'label cap exceeded' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
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

## Open questions — closed at Sprint 35 ratification

- **Q1: `entity_type` as DB enum vs free string?** → **CHECK constraint with a
  literal list** (`'asset','site','zone','device','category'`). Matches the
  Categories `category_type` precedent in ADR 019. Avoids enum-add-value
  migrations later; the list grows by re-issuing the CHECK.
- **Q2: Pre-seed common keys (`location`, `owner`, `priority`) per tenant on
  signup?** → **Defer.** No UX signal yet that this is friction; re-evaluate
  after one cycle of operator feedback once the chip UI ships.
- **Q3: Cross-entity label propagation (asset inherits its site's labels)?**
  → **Out of scope.** Will land as its own follow-up ADR if a customer asks.
  The shape of `entity_labels` doesn't preclude it (we'd add a
  `propagation_policy` enum on the catalog row).

## API path deviation from the original draft

The draft above shows `/v1/tenants/{slug}/...` paths. **The TagPulse codebase
does not version routes** and threads tenant scope through
`get_current_tenant` (Categories in ADR 019 already deviated the same way;
see its route docstring). Final shipped paths:

```
GET    /labels?entity_type=asset              (viewer+)
POST   /labels                                (editor/admin)
GET    /labels/{id}                           (viewer+)
PATCH  /labels/{id}                           (editor/admin; rename/recolour)
DELETE /labels/{id}                           (admin; 409 + association_count payload)
GET    /{entity_type}/{id}/labels             (viewer+)
POST   /{entity_type}/{id}/labels             (editor/admin; 409 on cap, 400 on bad value)
DELETE /{entity_type}/{id}/labels/{label_id}  (editor/admin)
```

The per-entity `/search` endpoint from the draft is **collapsed into a
filter on the existing list endpoints** — see next section. This keeps the
API shape consistent (one list endpoint per entity type with growing
filter surface) and avoids a parallel `/search` per entity.

## Filter encoding — `?labels[...]=...` deep-object

Extend `GET /assets`, `GET /sites`, `GET /zones`, `GET /devices` with a
single `labels` query parameter using **OpenAPI `style: deepObject,
explode: true`** encoding. Same shape as Prometheus/Loki/Grafana label
selectors; operators recognize it.

**Single pair (most common)**

```
GET /assets?labels[location]=warehouse-a
```

**Multiple keys → AND across keys**

```
GET /assets?labels[location]=warehouse-a&labels[priority]=high
```

**Multiple values for one key → OR within key (comma-separated)**

```
GET /assets?labels[location]=warehouse-a,warehouse-b
```

**Combined**

```
GET /assets?labels[location]=warehouse-a,warehouse-b&labels[priority]=high&labels[owner]=alice,bob
```

Semantics: AND across distinct keys; OR within values of the same key.
The single-pair form is the degenerate case.

**Guard rails (return 400 if exceeded):**

- ≤ 5 distinct keys per request.
- ≤ 20 values per key.
- Each value still matches the `^[A-Za-z0-9_.]{1,64}$` regex from the
  schema validators below.

**Rejected alternative — repeated parallel params** (`?label.key=X&label.value=Y&label.key=Z&label.value=W`):
query-param order isn't guaranteed across all middleware/proxies, so
positional pairing is fragile. Don't use.

**SQL shape — one correlated `EXISTS` per key**

```sql
SELECT a.* FROM assets a
WHERE a.tenant_id = current_setting('app.current_tenant_id')::uuid
  AND EXISTS (
    SELECT 1 FROM entity_labels el JOIN labels l ON el.label_id = l.id
    WHERE el.entity_id = a.id
      AND l.tenant_id = a.tenant_id          -- RLS join, enforced by policy too
      AND l.entity_type = 'asset'
      AND l.key = 'location'
      AND el.value IN ('warehouse-a','warehouse-b')
  )
  AND EXISTS (
    SELECT 1 FROM entity_labels el JOIN labels l ON el.label_id = l.id
    WHERE el.entity_id = a.id
      AND l.tenant_id = a.tenant_id
      AND l.entity_type = 'asset'
      AND l.key = 'priority'
      AND el.value IN ('high')
  );
```

Index-friendly: each `EXISTS` is a fast probe via `entity_labels(label_id,
entity_id)` PK + `labels(tenant_id, entity_type, lower(key))` unique
index.
