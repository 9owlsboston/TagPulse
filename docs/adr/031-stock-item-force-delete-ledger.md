# ADR-031: Force-delete of stock items never orphans the movement ledger

- Status: **Accepted (sprint-59/demo-scenarios, June 2026)**
- Scope: `TagPulse` backend. `DELETE /stock-items/{id}` semantics, the
  `delete_stock_item` service path, and the structured 409 contract returned to
  clients. Closes roadmap §59.6.
- Related: [ADR-008 (multi-tenancy)](008-multi-tenancy-strategy.md),
  migration [`021_inventory_hardening`](../../migrations/versions/021_inventory_hardening.py)
  (the `stock_movements.stock_item_id` `ON DELETE RESTRICT` FK),
  `src/tagpulse/api/services/inventory_service.py`,
  `src/tagpulse/api/routes/inventory.py`.

## Context

`DELETE /stock-items/{id}?force=true` returned a **500** (and dropped the DB
connection / aborted the transaction) whenever the targeted unit had ever
moved.

Why: the `stock_movements` ledger references `stock_items.id` via a
**`ON DELETE RESTRICT`** foreign key, introduced in migration `021` so "the
ledger can never be orphaned". The repository `delete()` enforced only a
*state* guard — `in_stock` items require `?force=true`. With `force=true`, the
state guard was bypassed and the code went straight to
`session.delete(row)` → `flush()`, at which point the RESTRICT FK fired an
**uncaught `IntegrityError`**. The route mapped `ValueError` → 409 but not
`IntegrityError`, so it surfaced as a 500 with a poisoned transaction.

The deeper question the bug exposed: *what should `force=true` actually
override?* Three options were considered:

| Option | Behaviour | Verdict |
|---|---|---|
| **A. Cascade the ledger** | `force=true` deletes the item *and* its `stock_movements` rows | ✗ Rejected — destroys the immutable audit ledger; contradicts migration `021`'s explicit intent. |
| **B. Soft-delete / retire** | A moved unit is never hard-deleted; it is retired via `state=consumed`, preserving history | ✓ Chosen. |
| **C. Just catch `IntegrityError` → 409** | Map the FK violation to 409 at the route | △ Necessary symptom fix but insufficient: a failed `flush()` poisons the session transaction, and the error reaches the route *after* DB work. Better to pre-check. |

## Decision

**`force=true` bypasses the *state* guard only — never the ledger.**

A unit that has any `stock_movements` history is immutable: it can be
**retired** (soft-deleted via `PATCH /stock-items/{id}` with `state=consumed`)
but never hard-deleted. Hard delete remains available only for units with an
empty ledger (e.g. created by mistake and never moved).

### Hard rules

1. **The ledger guard is unconditional.** `delete_stock_item` counts referencing
   `stock_movements` *before* any delete and raises `StockItemLedgerError`
   (a `ValueError` subclass carrying `movement_count`) when the count is > 0 —
   regardless of `force` and regardless of the item's `state`. This guarantees
   the RESTRICT FK is never reached, so the transaction is never poisoned.
2. **The guard lives in the service, not the route or the repo.** It uses the
   existing `TimescaleStockMovementRepository.count_for_stock_item`, mirroring
   the lot-delete pre-check precedent. Keeping it in the service makes it unit
   testable against the in-memory fakes (no DB required) and keeps the domain
   exception in the service layer alongside `ProductNotFoundError`.
3. **The route returns a structured 409.** For `StockItemLedgerError` the
   `detail` is a JSON object — `{ error: "stock_item_has_ledger",
   message, movement_count, remediation }` — so the UI can branch on `error`
   and surface the retire-instead remediation. The plain *state*-guard
   `ValueError` keeps its existing string-detail 409 (a never-moved `in_stock`
   item deleted without `force`).
4. **Force still bypasses the state guard** for a never-moved `in_stock` unit:
   that delete succeeds and is audited (`stock_item.deleted`).

## Consequences

- No API surface change: the endpoint, the 409 status, and the `force` query
  param are unchanged. The only observable difference is 500 → structured 409
  for moved units, and a typed `detail` object for that case.
- Operators clean up demo/seed data by **retiring** moved units
  (`state=consumed`), which `scripts/cleanup_demo_stock_items.py` already does.
- The `stock_movements` audit ledger remains append-only and complete.
