# Reserved label-key collision — `batch.*` namespace

**Triggered by:** Alembic migration `045_tag_label_namespace.py` aborts with:

```
RuntimeError: reserved label-key collision detected.
Tenants {slug-a, slug-b, ...} hold labels under the batch.* namespace
reserved by ADR 028 for Sprint 50 tag-batch grouping.
See docs/runbooks/reserved-label-key-collision.md for the rename
procedure. Migration refused — re-run after reconciliation.
```

**Source design:** [ADR 028 — Tags as a first-class entity](../adr/028-tags-as-first-class-entity.md)
(§"Batches: labels, not a table"), [Sprint 50 roadmap entry](../roadmap.md)
("Risks / open items entering the sprint", item 2).

## Why this happens

Sprint 50 reserves four label keys under `entity_type='tag'` for tag-batch
grouping:

| Reserved key       | Purpose                                                 |
|--------------------|---------------------------------------------------------|
| `batch`            | Per-tag batch identifier (`acme-2026Q1-r017`, etc.).    |
| `batch.received_at`| Date the batch landed at the operator's facility.       |
| `batch.description`| Freeform supplier description (PO number, reel size).   |
| `batch.supplier`   | Supplier name / GLN.                                    |

The migration refuses to run if **any** label across **any** `entity_type`
(asset / site / zone / device / category / tag) for **any** tenant has a
key equal to `batch` or starting with `batch.` (case-insensitive). This
is the locked Sprint 50 policy: **refuse + manual intervention** — no
auto-rename, no silent coexistence. The reservation is namespace-wide on
purpose, even though only `entity_type='tag'` will actively use the keys —
operators expect a label keyed `batch` to mean the same thing everywhere.

## What you do

### Step 1 — enumerate the colliding rows

Connect as the migration role (the one that owns the `labels` table) and
run:

```sql
SELECT t.slug              AS tenant_slug,
       l.entity_type,
       l.key,
       l.id                AS label_id,
       COUNT(el.entity_id) AS associations
  FROM labels l
  JOIN tenants t       ON t.id = l.tenant_id
  LEFT JOIN entity_labels el ON el.label_id = l.id
 WHERE lower(l.key) = 'batch'
    OR lower(l.key) LIKE 'batch.%'
 GROUP BY t.slug, l.entity_type, l.key, l.id
 ORDER BY t.slug, l.entity_type, l.key;
```

For each row, you have two options.

### Step 2a — rename (preserves operator data)

If the colliding label is real customer data, rename the key. The label
catalog API enforces the same `^[A-Za-z0-9_.+$]{3,24}$` regex from
migration 039 — pick something unambiguous like `legacy_batch` or
`shipment_batch`:

```sql
UPDATE labels
   SET key = 'legacy_batch', updated_at = now()
 WHERE id = '<label_id from step 1>';
```

If the new key collides with an existing label on the same
`(tenant_id, entity_type)`, you'll get a unique-index error — pick a
different name. Run a tenant communication after the rename: any UI
filters, saved searches, exports, or webhook payloads that referenced
the old key by name now need updating on the operator's side.

### Step 2b — delete (if the label is unused)

If `associations = 0` (no entities bound to this label), it is safe to
delete outright:

```sql
DELETE FROM labels WHERE id = '<label_id from step 1>';
```

The `entity_labels` ON DELETE RESTRICT FK (migration 039) makes this
fail safely if there are bindings — fall back to step 2a in that case.

### Step 3 — re-run the migration

```bash
alembic upgrade head
```

The collision detector re-runs from scratch each time, so the migration
either succeeds or surfaces a fresh list of remaining collisions to
work through. Repeat steps 1-3 until upgrade succeeds.

### Step 4 — operator-facing changelog note

Add a one-line entry to the customer-facing release notes naming each
renamed key per tenant. The operator needs to know to update any
dashboards or integrations that filtered on the old name.

## Anti-patterns

- **Do not** comment out the collision check in the migration to "just
  ship it". The Sprint 50 policy was locked at planning precisely
  because silent coexistence creates two labels with the same
  semantic-looking key (`batch` on `asset` vs `batch` on `tag`) that
  mean entirely different things — confusing for operators, broken for
  reporting.
- **Do not** auto-rename via a UPDATE-all script. Per the locked policy,
  this is the operator's decision per-tenant; the rename may cascade
  into downstream systems we don't control.
- **Do not** widen the reservation (e.g. also reserving `shipment.*`)
  in this runbook. That requires an ADR amendment and a new migration —
  surface the request to the architecture review.

## Related

- [ADR 028](../adr/028-tags-as-first-class-entity.md) — the reservation.
- [ADR 020](../adr/020-labels-first-class.md) — label semantics this
  collision report builds on (key uniqueness is per
  `(tenant_id, entity_type, lower(key))`).
- Migration [`045_tag_label_namespace.py`](../../migrations/versions/045_tag_label_namespace.py)
  — the code that surfaces this failure.
