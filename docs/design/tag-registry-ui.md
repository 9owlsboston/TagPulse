# Design Document: Tag Registry UI (Sprint 51)

**Date:** 2026-05-23
**Status:** proposed
**Related:** [ADR 028 — Tags as first-class entity](../adr/028-tags-as-first-class-entity.md), [Sprint 50 — Tag registry v1 (shipped)](../roadmap.md#sprint-50--tag-registry-v1-shipped--implements-adr-028), [Sprint 51 — Tag registry UI (planned)](../roadmap.md)
**Implementation repo:** [TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI) (React 19 + AntD 5, per [admin-ui.md](admin-ui.md))
**Runbook reference:** [runbooks/tag-registry-operations.md](../runbooks/tag-registry-operations.md)

---

## 1. Goal

Make the Sprint 50 tag registry backend operator-usable from the admin UI. Sprint 50 shipped every API endpoint ADR 028 contemplates — tag CRUD, CSV import with dry-run + confirmation tokens + two-person rule, cross-tenant transfers, three reconciliation views, the registrar worker — but the only operator path today is `curl` against the documented routes. ADR 028 §"Negative / costs" anticipated this gap: *"Two new admin workflows (CSV import, transfer) need UI before the registry is operator-usable. Backend can ship first; UI is a separate sprint."* This design covers that sprint.

**Non-goal: backend changes.** If a UI flow surfaces an API gap, the fix lands as Sprint 51b on this repo — Sprint 51 is UI-only by construction so the OpenAPI surface stays a stable contract for the duration. Scope creep into Sprint 51b is preferable to mid-sprint OpenAPI churn that breaks the generated TypeScript client.

---

## 2. Information Architecture

New top-level nav entry **"Tags"** in the admin sidebar, slotted between "Assets" and "Devices" so the operator's mental model (`device sees → tag is read → asset is bound`) lines up left-to-right.

| Route | Purpose | Role gate |
|-------|---------|-----------|
| `/tags` | Paginated list + filters (status, batch label, EPC prefix, bound/unbound) | viewer |
| `/tags/:epcHex` | Detail drawer / page — registry metadata, status history, labels, current binding | viewer (read); admin/editor (mutate) |
| `/tags/import` | CSV import wizard (upload → dry-run preview → confirm) | admin/editor |
| `/tags/bulk` | Bulk patch / retire wizard (label-batch or EPC list scope) | admin/editor (patch); admin (retire) |
| `/tags/transfers` | Cross-tenant transfer flow — outbound request + inbound approval queue | admin (both tenants) |
| `/tags/reconciliation/:view` | Three exception views, paginated + CSV download | viewer |
| `/admin/pending-bulk-operations` | Shared inbox for **all** pending two-person approvals (imports, bulk patch/retire, transfers) | admin |

The pending-approval inbox is deliberately **not** under `/tags` — it multiplexes three operation kinds today (`tags.import`, `tags.bulk_patch`, `tags.bulk_retire`, `tag.transfer`) and is designed to accept future non-tag bulk ops (Sprint 51+ device bulk ops will reuse `pending_bulk_operations.operation` as the discriminator). Building it under `/tags` would force a rename later.

---

## 3. Phase B — Tag List + Detail

### 3.1 List page (`/tags`)

Server-side paginated AntD `Table` driven by `GET /v1/tenants/{slug}/tags?status=…&labels[batch]=…&epc_prefix=…&bound=…&limit=&offset=`.

**Default columns:**

| Column | Source | Notes |
|--------|--------|-------|
| EPC (hex) | `tag.epc_hex` | Monospace; click → detail |
| Status | `tag.status` | AntD `Tag` color: `registered`=blue, `active`=green, `defective`=orange, `retired`=default, `transferred_out`=purple |
| Source | `tag.source` | `csv_import` / `first_read` / `manual` / `transfer` |
| Batch | resolved from `entity_labels` (key=`batch`) | Click → filter list by that batch |
| Last seen | `tag.last_seen_at` | Relative (`2m ago`); `null` → "Never" |
| Binding | resolved via parallel call to assets / stock_items by EPC | One badge per kind, click → owner detail |

**Filters** (top toolbar, all map to existing query params): status multi-select, batch text input (with debounce + autocomplete from `entity_labels` distinct values), EPC prefix text input, bound/unbound segmented control.

**`tag_known=NULL` UX policy — locked in this design.** The list never surfaces NULL — the registrar worker's p95 < 10 s lag SLI (Sprint 50 risk lock) means an operator-driven refresh effectively always sees a resolved value. Reads in the brief unclassified window do not appear in the `/tags` list at all (the list is over the `tags` table, not `tag_reads`). The unclassified state IS visible in the reconciliation view `unregistered-reading` — `tag_known=FALSE` is what Phase D's worker actively classifies as "EPC not in registry"; `NULL` reads are filtered out of that view too by design (Sprint 50 Phase E note: showing them would surface lag-induced false positives). This resolves the Phase-A open item from the roadmap entry.

### 3.2 Detail (`/tags/:epcHex`)

Drawer (from list-row click) or full page (from deep link / refresh). Sections:

1. **Header** — EPC hex (copyable), GS1 URI if denormalized, status `Tag` with a "Change status" CTA gated on admin/editor + the server's `validate_status_transition` (UI calls `PATCH /tags/{epc_hex}` with `{status: <new>}` and surfaces the 409 body on rejection).
2. **Metadata** — JSON viewer (read-only viewer role; AntD `Editor`-ish for admin/editor; PATCH is full-replace per Sprint 50 contract — UI warns "this replaces the whole metadata object" on the first edit).
3. **Labels** — chips for each `(key, value)` pair from `GET /tag/{id}/labels`. Add/remove via the existing labels API. **Reserved-key guard**: the four `batch.*` keys are visible as chips but their delete/edit affordances are disabled with tooltip *"Reserved by ADR 028 — managed at the catalog level"* (server enforces this via Sprint 50 audit remediation G-1; UI hides the wall so operators don't hit it).
4. **Status history** — timeline rendered from `GET /admin/audit-logs?subject_type=tag&subject_id={id}&actions=tag.status_changed,tag.created,tag.bulk_patched,tag.bulk_retired` (admin/editor role only — viewer cannot see audit logs per existing policy).
5. **Current binding** — link to the bound `asset` or `stock_items` row if any.

---

## 4. Phase C — CSV Import Wizard (`/tags/import`)

Three-step AntD `Steps`:

### Step 1 — Upload + client-side preview

- Drag-drop / file picker, accepts `.csv` only.
- Client-side parse with PapaParse, first 10 rows rendered in a preview `Table`.
- Hard limits surfaced **before** submit: file ≤ 8 MiB, rows ≤ 10 000, per-tenant limit 10 imports/hour (resolved at page load via `GET /tenant/config` — adds `tag_bulk_import_rate_limit` to the tenant config response if not already exposed; backend gap noted in §9).
- Validation surface: malformed CSV header (missing required `epc_hex` column), duplicate EPCs within the file, EPCs failing the canonical `_EPC_HEX_PATTERN` regex — all reported client-side before any network call.

### Step 2 — Dry-run preview

- Submit as `POST /v1/tenants/{slug}/tags/import?dry_run=true` with the file body.
- Render `TagBulkOperationResult`: matched count, would-import count, would-skip count (existing EPCs), sample of first 10 rows.
- Per-row errors from `TagBulkRowError[]` rendered in an expandable table — each row carries the operator's input line, the error string (e.g., `"epc_hex already exists with status=retired"`), and a copy-to-clipboard affordance for the EPC.
- Token + expiry displayed: *"This preview is valid for 15 minutes (expires at HH:MM)"*.

### Step 3 — Confirm

- `POST .../tags/import?confirm=<token>` with the same file body.
- **Two-person branch**: when the response is 202 `requires_approval=True`, the wizard transitions to a final "Submitted for approval" pane with: pending operation ID, link to `/admin/pending-bulk-operations`, copy-to-clipboard for the pending ID. **Do not** show success copy — the import has not happened yet.
- **Direct-execute branch**: response 200, render imported / skipped / failed counts. Link to the new tags filtered by import request_id (uses the C5-shipped `request_id` audit column — UI calls `GET /tags?source=csv_import` filtered to that import's window as a fallback if no direct filter exists; see §9 for the backend gap).

---

## 5. Phase D — Cross-tenant Transfer Flow (`/tags/transfers`)

Two surfaces sharing one page (segmented control: **Outbound** / **Inbound**).

### Outbound — request a transfer

- Recipient tenant selector (typeahead over `GET /tenants?role=admin_in` — UI shows only tenants where current user has the Admin role; server still 403s anyone who lies about this).
- EPC list: paste-in textarea OR label-batch picker (mutually exclusive, ≤ 1000 EPCs per Sprint 50 schema cap).
- Optional reason text (audit-log payload).
- Dry-run → confirm flow identical to imports (reuses C2 confirmation-token shape).

### Inbound — approval queue

- Lists `pending_bulk_operations WHERE operation='tag.transfer' AND target_tenant_id=current`.
- Per row: source tenant, EPC count, requester display name, request timestamp, sample of first 10 EPCs.
- **Approve / Reject** buttons hidden when `current_user.id == row.requested_by` (server 403s self-approval; UI suppresses the buttons rather than letting the operator hit the wall).
- Full audit trail visible via `?request_id=` filter on `/admin/audit-logs` (deep link from the row).

---

## 6. Phase E — Reconciliation Views

Three sibling pages at `/tags/reconciliation/:view` with `view ∈ {registered-unread, unregistered-reading, bindings-on-retired}`. Shared layout:

- Top toolbar: `?days=` selector (default 30, bounded 1–365; **hidden** on `bindings-on-retired` because that view is point-in-time per Sprint 50 Phase E contract), "Download CSV" button binding to `?format=csv`.
- Paginated `Table` with the column set matching the Pydantic row schema (`RegisteredUnreadRow`, `UnregisteredReadingRow`, `BindingOnRetiredRow`) — column order matches the CSV header contract so spreadsheet-trained operators read both surfaces identically.
- Empty state copy is per-view and instructive:
  - `registered-unread` → *"No imported tags have been silent for {days} days. Healthy registrar."*
  - `unregistered-reading` → *"No unregistered EPCs reading in the last {days} days. Either operators are diligent about importing, or no rogue tags are in range."*
  - `bindings-on-retired` → *"No active bindings reference terminal-status tags. Soft-asset / inventory invariants hold."*

**No mutation surfaces on these pages** — operators triage by jumping to the relevant tag detail or stock_items row. Read-only triage view is Phase E's contract.

---

## 7. Phase F — Bulk Patch + Retire Wizard + Pending Inbox

### 7.1 Bulk wizard (`/tags/bulk`)

Single wizard supporting both patch and retire via a top "Action" selector (mutually exclusive: **Patch status / metadata** OR **Retire**). Scope picker: label-batch (with autocomplete) OR EPC list (paste-in, capped client-side at 1000 to match server). Dry-run → confirm flow reuses the import wizard's component set; the only delta is the body shape (the existing `TagBulkPatchRequest` / `TagBulkRetireRequest` shapes generated from OpenAPI).

### 7.2 Pending-approval inbox (`/admin/pending-bulk-operations`)

Single inbox page for **all** pending two-person ops. Renderers registered per `operation` value:

```
const RENDERERS = {
  "tags.import": ImportPreviewCard,
  "tags.bulk_patch": BulkPatchPreviewCard,
  "tags.bulk_retire": BulkRetirePreviewCard,
  "tag.transfer": TransferPreviewCard,
};
```

Adding a future device-bulk op is a one-entry registry change, not a page rewrite. Each renderer receives the row's `payload` JSON + a deep-link callback. Approve / reject buttons sit at the inbox level (operation-agnostic) and call the existing `bulk_operations.py` admin endpoints.

---

## 8. Phase G — Docs + ADR Linkage

- ADR 028 status flipped Implemented (Sprint 50) → **Implemented + UI shipped (Sprint 51)** via a v1.2 decision-history entry citing the TagPulse-UI PR set.
- [runbooks/tag-registry-operations.md](../runbooks/tag-registry-operations.md) gets a new top section *"Operator UI quick reference"* mapping each runbook subsection to its new UI screen. The cURL examples stay (operators automating against the API still need them); the recommended path becomes the UI, matching the [domain-concepts-101.md](../guides/domain-concepts-101.md) §"Everything in this guide is doable from the UI" convention.
- [docs/roadmap.md](../roadmap.md) Sprint 51 entry flipped to `(shipped)` with the cross-repo PR list.

---

## 9. Backend Gaps Identified During Design

These are candidates for **Sprint 51b** (backend follow-up). None block UI work — workarounds documented in the relevant phase.

1. **`GET /tenant/config` should expose `tag_bulk_import_rate_limit` and `tag_bulk_two_person_threshold`.** Both are tenant-config knobs already on the `tenants` table (per Sprint 50 C1 + C3) but the UI needs them to render accurate limits in the import wizard and bulk wizard (*"You can submit X more imports this hour"*, *"This will require a second admin's approval"*). Today the UI would have to hard-code defaults or surface the limit only on submit failure — neither is acceptable for the wizard's first step.
2. **`GET /tags?import_request_id=<uuid>` filter** to deep-link from the import success pane to "the tags I just imported". Workaround: filter by `source=csv_import` + `created_at >= submission_time`, which approximates but isn't exact under concurrent imports.
3. **`GET /tenants?role=admin_in`** to populate the transfer-flow recipient typeahead with only tenants where the current user has Admin. Today the UI would need to call `/tenants` then per-tenant `/tenants/{slug}/users/me/roles` — N+1 problem. A single endpoint returning the filtered list is the right shape.

If any of these prove blocking in mid-sprint UI work, escalate to Sprint 51b and bump the TagPulse-UI OpenAPI client pin in a single coordinated commit.

---

## 10. Cross-repo PR Sequencing

1. **TagPulse `main`** ships Sprint 50 + this design doc (Sprint 51 Phase A). OpenAPI surface frozen for the sprint duration.
2. **TagPulse-UI `main`** pins its OpenAPI client to the Sprint 50 release tag at sprint kickoff. Any client regeneration during the sprint is a deliberate, coordinated commit (not an automatic CI step).
3. UI PRs land in TagPulse-UI per phase (B → C → D → E → F), each cross-linking back to this design doc and to the Sprint 51 roadmap entry.
4. Sprint 51 closes with a single TagPulse PR carrying Phase G changes (ADR 028 v1.2, runbook UI quick reference, roadmap `(shipped)` flip) + the cross-repo PR list as the per-phase commit references.

---

## 11. Decision History

- **v1 (2026-05-23) — proposed.** Initial design covering all six UI phases B–G. `tag_known=NULL` UX policy resolved to "never surface NULL in `/tags` list; reconciliation surface only" per §3.1. Three backend gaps identified and queued for Sprint 51b (§9).
