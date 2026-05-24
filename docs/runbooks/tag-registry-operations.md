# Tag registry operations

**Audience:** tenant admins + on-call operators running TagPulse deployments
that have opted into the Sprint 50 tag registry.

**Source design:** [ADR 028 — Tags as a first-class entity](../adr/028-tags-as-first-class-entity.md),
[Sprint 50 roadmap entry](../roadmap.md#sprint-50--tag-registry-v1-shipped--implements-adr-028).

This runbook covers the four day-to-day registry workflows:

1. [Bulk CSV import](#1-bulk-csv-import) — register a batch of EPCs.
2. [Status lifecycle](#2-status-lifecycle) — promote, retire, transfer.
3. [Two-person rule for large bulk ops](#3-two-person-rule-for-large-bulk-ops).
4. [Reconciliation reports](#4-reconciliation-reports) — the three exception
   views and how to bind them to spreadsheets.

The registry is **operator-driven**: TagPulse never auto-registers an EPC
on first read (ADR 028 OQ 3). Tags appear in the registry only via CSV
import, API call, backfill, or cross-tenant transfer.

The ingest hot path is **unchanged** — `tag_reads` writes touch zero rows
in `tags`. The link is the `tag_reads.tag_known` column, populated
asynchronously by the registrar worker.

---

## 1. Bulk CSV import

**Endpoint:** `POST /v1/tenants/{slug}/tags/import` (admin role).

**Hard limits** (locked at planning, ADR 028 OQ 4):

| Limit | Value | Behaviour above |
|---|---|---|
| Rows per CSV | 10 000 | `413 Payload Too Large`. No server-side chunking — split the file client-side. |
| Imports per hour per tenant | 10 (default, configurable via `tenants.tag_bulk_import_rate_limit`) | `429 Too Many Requests` with `{"limit_per_hour": N}`. |
| Validation mode | All-or-nothing | Any per-row error fails the whole import; full error list returned in the dry-run response. |

### CSV format

```
epc_hex,status,source,metadata
30340789BB000000A1B2C3D4,registered,csv_import,{"reel":"R-2026-017"}
30340789BB000000A1B2C3D5,registered,csv_import,
30340789BB000000A1B2C3D6,registered,csv_import,
```

- `epc_hex` — required. Canonical uppercase hex, 16–128 chars, `[0-9A-F]` only.
- `status` — required. One of `registered` / `active` / `retired` / `defective` / `transferred_out`. Operators normally import as `registered`; the registrar worker promotes to `active` on first read.
- `source` — required. One of `csv_import` / `api` / `backfill` / `transfer_in`. Use `csv_import` for this path.
- `metadata` — optional JSON blob (escape commas with quotes).

### Two-step flow: dry-run → confirm

Every bulk op is dry-run first; the response includes a single-use
`confirmation_token` scoped to the previewed payload's content hash. The
apply step won't accept a token from a different payload.

```bash
# Step 1 — dry-run (no rows inserted; full per-row validation)
curl -sS -X POST \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: text/csv" \
  --data-binary @reel-2026-017.csv \
  "https://$HOST/v1/tenants/$SLUG/tags/import?dry_run=true"
# → { "row_count": 6000, "errors": [], "content_hash": "sha256:…",
#     "confirmation_token": "tok_…", "expires_in": 600,
#     "requires_two_person_approval": false }

# Step 2 — apply (token must match the dry-run payload hash)
curl -sS -X POST \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: text/csv" \
  -H "X-Confirmation-Token: $TOKEN" \
  --data-binary @reel-2026-017.csv \
  "https://$HOST/v1/tenants/$SLUG/tags/import"
# → { "inserted": 6000, "request_id": "…", "audit_log_id": "…" }
```

If the dry-run response sets `requires_two_person_approval: true`, the
apply step lands in `pending_bulk_operations` instead of executing —
see [§3](#3-two-person-rule-for-large-bulk-ops).

### Assigning a batch label at import time

There is **no `tag_batches` table** — batches are modelled as
[ADR 020 labels](../adr/020-labels-first-class.md) under the reserved
`batch.*` namespace. After import, attach the batch label to the rows
through the existing `entity_labels` API:

```bash
curl -sS -X POST \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{ "key": "batch", "value": "acme-2026Q1-r017",
        "entity_ids": [...epc_hex list from import response...] }' \
  "https://$HOST/v1/tenants/$SLUG/entity-labels?entity_type=tag"
```

The reserved keys are `batch`, `batch.received_at`, `batch.description`,
`batch.supplier`. Collisions on existing tenants are caught by migration
`045_tag_label_namespace.py` — see
[runbooks/reserved-label-key-collision.md](reserved-label-key-collision.md).

---

## 2. Status lifecycle

| From → To | Triggered by | Notes |
|---|---|---|
| (none) → `registered` | Bulk import / API POST | Operator action. |
| `registered` → `active` | Registrar worker | First matching read after registration. **Worker is the sole writer of this transition.** |
| `active` → `retired` | Admin PATCH (single) or bulk-retire endpoint | Soft retirement — the row stays for audit. `tag_known` flips to `FALSE` on next worker pass. |
| `*` → `defective` | Admin PATCH | Tag failed QC; doesn't auto-retire bindings. |
| `*` → `transferred_out` | `POST /tag-transfers` (admin) | Cross-tenant transfer; pairs with a `transferred_in` row on the receiving tenant. |

The `tag_known` signal on `tag_reads`:

- `NULL` — not yet classified (worker is behind).
- `TRUE` — EPC present in `tags` with status ∈ `{registered, active}`.
- `FALSE` — EPC unknown OR in a terminal status OR a null-EPC read.

Operators should rarely see `NULL` — the worker p95 lag SLI is 10 s. If
`NULL` rows persist, check the registrar worker dashboard for lag /
restart loops.

---

## 3. Two-person rule for large bulk ops

When a bulk op's row count meets or exceeds the tenant's
`tag_bulk_two_person_threshold` (default 10 000), the apply step does
**not** execute. It writes a row into `pending_bulk_operations` and
returns:

```json
{ "pending_id": "…", "row_count": 12345, "content_hash": "sha256:…",
  "expires_at": "…", "sample": ["30340789...", "30340789...", "..."] }
```

A second admin (different `users.id` from the requester) must call the
approve endpoint with the same `pending_id`. The approver sees the
content hash + a sample of EPCs before deciding. The path:

```bash
# As a second admin:
curl -sS -X POST \
  -H "Authorization: Bearer $SECOND_ADMIN_TOKEN" \
  "https://$HOST/v1/tenants/$SLUG/tags/pending/$PENDING_ID/approve"
# → executes the bulk op, returns the same shape as a direct apply.
```

State machine: `pending → approved → executed` (happy path),
`pending → rejected` (deny), `pending → expired` (lazy sweep on approve).

Every bulk op — direct or two-person — writes an `audit_logs` row keyed
`(actor, action, batch, count, request_id)` with `action ∈ {tags.import,
tags.bulk_patch, tags.bulk_retire, tag-transfers.request}`.

---

## 4. Reconciliation reports

**Endpoint:** `GET /v1/tenants/{slug}/tags/reconciliation/{view}`
(viewer role — read-only, exposing them is a monitoring concern).

The three views surface discrepancies the registrar worker can detect
but cannot self-heal. **Never mutate state.**

| View | What it lists | `?days=` honoured? |
|---|---|---|
| `registered-unread` | Tags with `status ∈ {registered, active}` whose `last_seen_at` is `NULL` or older than `?days=N` (default 30, max 365). | **Yes** — staleness cutoff. |
| `unregistered-reading` | Distinct `tag_id` values appearing in `tag_reads` over the last `?days=N` whose EPC is absent from `tags` or carries a terminal status. | **Yes** — bounds the read scan. |
| `bindings-on-retired` | `stock_items` rows whose EPC binding points at a tag in `retired` / `defective` / `transferred_out`. | **No — point-in-time.** Param accepted for URL uniformity, ignored. |

### Query

```bash
# Default — JSON, last 30 days, first 100 rows.
curl -sS \
  -H "Authorization: Bearer $VIEWER_TOKEN" \
  "https://$HOST/v1/tenants/$SLUG/tags/reconciliation/registered-unread"

# Override lookback + pagination.
curl -sS \
  -H "Authorization: Bearer $VIEWER_TOKEN" \
  "https://$HOST/v1/tenants/$SLUG/tags/reconciliation/unregistered-reading?days=7&limit=500&offset=0"

# CSV export (Content-Disposition: attachment; filename="tags-{view}.csv").
curl -sS -o reg-unread.csv \
  -H "Authorization: Bearer $VIEWER_TOKEN" \
  "https://$HOST/v1/tenants/$SLUG/tags/reconciliation/registered-unread?format=csv&limit=1000"
```

Pagination bounds: `limit ∈ [1, 1000]`, `offset ≥ 0`.

### Spreadsheet binding contract

CSV column order is **stable** per view (`src/tagpulse/services/tag_reconciliation.py`,
`_HEADERS`). An empty result set still emits the header row so
spreadsheets see a stable schema.

| View | Columns (in order) |
|---|---|
| `registered-unread` | `tag_id`, `epc_hex`, `status`, `source`, `first_seen_at`, `last_seen_at`, `created_at` |
| `unregistered-reading` | `tag_id`, `last_seen_at`, `read_count` |
| `bindings-on-retired` | `stock_item_id`, `epc_hex`, `product_id`, `lot_id`, `stock_item_state`, `tag_id`, `tag_status`, `tag_updated_at` |

**Adding columns is allowed** (appended on the right). **Renaming or
reordering columns is a breaking change** — bump the route path
(`/v2/...`) if ever needed.

Datetime cells are ISO-8601 with timezone; `None` renders as an empty
cell.

### How to interpret rising row counts

| View | Rising row count usually means | Escalation |
|---|---|---|
| `registered-unread` | Reels imported but never deployed; a reader site went silent; tag attrition. | Cross-check `last_seen_at` against deployment dates. If a *whole batch* hasn't moved, check the customer's onboarding workflow. |
| `unregistered-reading` | Stray reads from another tenant's tags or a reel that shipped but was never imported. | Confirm EPCs aren't from a sister facility / supplier. If legitimate, run the bulk import for that reel. |
| `bindings-on-retired` | Stock items still pointing at retired tags — likely the inventory side missed a retire / re-bind. | Re-bind via the assets API; the binding row's history is preserved. |

### Known gotchas

- **`unregistered-reading` depends on the registrar worker classification window.** If the worker is behind, rows that would otherwise resolve to `tag_known=TRUE` may still appear here. Confirm worker lag before treating a spike as real.
- **`registered-unread` includes both `registered` and `active`.** A tag picked up months ago that's now gone silent (e.g., reader site decommissioned) surfaces here alongside never-deployed tags. Filter on `first_seen_at IS NULL` client-side to isolate the "never deployed" subset.
- **`bindings-on-retired` is point-in-time** — `?days=` is accepted only so callers can use a uniform URL shape; the underlying query has no time window.

---

## Cross-links

- [ADR 028 — Tags as a first-class entity](../adr/028-tags-as-first-class-entity.md) (governance §§1–7, status enum, hot-path constraint).
- [data-models.md §Tag registry (Sprint 50+)](../data-models.md#tag-registry-sprint-50) (schema reference).
- [reserved-label-key-collision.md](reserved-label-key-collision.md) (migration 045 collision handling).
- [roadmap.md §Sprint 50](../roadmap.md#sprint-50--tag-registry-v1-shipped--implements-adr-028) (per-phase change log + Phase G deferral).
- Code: `src/tagpulse/services/tags.py`, `src/tagpulse/services/tag_reconciliation.py`, `src/tagpulse/workers/tag_registrar_worker.py`, `src/tagpulse/api/routes/tags.py`.
