# ADR-028: Tags as a first-class entity

- Status: **Implemented (Sprint 50, May 2026)** — all five open questions resolved inline at v0.1–v0.5; full Phase A–E implementation landed under Sprint 50 (`sprint-50/tag-registry` branch). See [Decision history](#decision-history) v1.0 for the per-phase commit / CHANGELOG cross-references.
- Supersedes in part: the "no `tags` table" position documented in
  [docs/data-models.md §"Where is the tag?"](../data-models.md#where-is-the-tag-and-why-theres-no-tags-table).
  That position remains correct for **read ingestion**; this ADR adds a
  registry layer on top of it for **inventory, ownership, and lifecycle**
  concerns that the bindings-only model cannot answer.
- Reopens: gap 2.14 (tag registry) in
  [docs/design/reference-design-remediation.md](../design/reference-design-remediation.md).
  Previously marked **Defer** with the note "a read-only Tags page can
  be served from `asset_tag_bindings` + `tag_reads`". The SME review
  surfaced four use cases that page cannot serve — see Context below.
- Related: ADR [019 Categories](019-categories.md) (which already
  references the "deferred tag-registry concept" — §Context bullet 4),
  ADR [022 Soft assets](022-soft-assets.md) (interacts with the
  unowned-tag question), ADR [026 Presence model](026-presence-model.md)
  (`tag_presence` is per-`(tenant, device, epc)` — this ADR governs
  the `(tenant, epc)` identity layer above it).

## Context

TagPulse today resolves a tag value (an EPC / TID / device-emitted
string) into business meaning through three independent layers:

| Layer | Stores | Lifecycle |
|---|---|---|
| `tag_reads.tag_id` (+ `epc_hex`) | Every observation. | Append-only hypertable. |
| `asset_tag_bindings.binding_value` | The **active** mapping tag → asset. | One row per (tenant, tag, asset, bound window). |
| `tag_presence.(tenant_id, device_id, epc)` | Current live presence at one producer. | Synchronous reconcile on snap (ADR 026). |

The data-models doc enumerates three trade-offs that motivated keeping
the tag out of the schema:

1. Ingest stays cheap — no SELECT-or-INSERT round-trip per read.
2. A tag without context is meaningless.
3. Tags are rebindable — binding-history rows preserve the audit trail.

All three remain valid for **the ingest path**. What changed is the
operator-facing surface area. SME review of an industry reference design
for an RFID-label cloud platform surfaced four concrete capabilities the
current schema cannot answer:

1. **Owned inventory before first read.** An operator receives a reel
   of 5 000 labels from the manufacturer, scans the reel-range CSV into
   the platform, and expects the platform to know they own 5 000 tags
   that have not yet been observed. Today there is no row for a tag
   until `tag_reads` records a read, and `asset_tag_bindings` requires
   an asset. A tag that was never read and is not bound to anything has
   no representation at all.
2. **Ownership / transfer between tenants.** Moving a reel of tags from
   tenant A to tenant B is currently impossible to express. Bindings
   are per-tenant (correctly), and `tag_reads` is tenant-scoped by
   ingestion, but the physical tag has no row that can be moved across
   tenants with audit history.
3. **Stray-read attribution.** A reader hears an EPC. Is that EPC ours,
   a neighbour's, a returned-goods tag, or noise? Today every EPC that
   appears on a tenant's MQTT topic is treated as "theirs" by virtue of
   topic ownership. With a registry we can mark unknown EPCs as such
   and gate downstream behaviour (alerts, soft-asset auto-creation,
   presence rows) on tag ownership rather than topic ownership.
4. **Bulk operations.** "Recall reel 008rT from production — disable
   all 5 000 tags" or "the labels in shipment X are damaged, mark them
   `defective`" have no batch handle today. Each tag would have to be
   processed through the asset → binding path even though the change is
   about the physical tag, not its asset.

The previous "read-only Tags page from bindings + reads" answer only
covers retrospective questions ("what tags have we seen?"). It cannot
answer prospective inventory questions ("what tags do we own that we
have not yet seen?") or ownership-mutation questions ("transfer these
tags"). The four capabilities above are not retrospective.

## Decision

Introduce a tenant-scoped `tags` table as a thin **identity and
ownership registry**. The table holds one row per `(tenant_id,
epc_hex)`, materializes optional reel/batch metadata, and carries a
lifecycle status. It does **not** replace any of the three existing
layers and is **not** on the per-read ingest hot path.

```sql
CREATE TABLE tags (
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    epc_hex         TEXT NOT NULL,                   -- canonical: uppercase hex, no separators
    gs1_uri         TEXT NULL,                       -- parsed GS1 URI (urn:epc:id:sgtin:… / grai:…), NULL for non-GS1 or unparseable
    status          VARCHAR(16) NOT NULL,            -- see status enum below
    source          VARCHAR(16) NOT NULL,            -- 'csv_import' | 'api' | 'backfill' | 'transfer_in'
    first_seen_at   TIMESTAMPTZ NULL,                -- denormalized from earliest tag_reads observation
    last_seen_at    TIMESTAMPTZ NULL,                -- denormalized; updated by a worker, not the ingest path
    metadata        JSONB NULL,                      -- freeform per-tenant attributes
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, epc_hex)
);
CREATE INDEX ON tags (tenant_id, gs1_uri) WHERE gs1_uri IS NOT NULL;
-- RLS: USING (tenant_id = current_setting('app.current_tenant_id')::uuid)
--
-- Batch grouping uses ADR 020 entity_labels with reserved key namespace
-- (see §"Batches: labels, not a table" below). No tag_batches table.

CREATE TABLE tag_transfers (
    id                  UUID PRIMARY KEY,
    request_id          UUID NOT NULL,               -- groups all rows of one transfer request
    from_tenant_id      UUID NOT NULL REFERENCES tenants(id),
    to_tenant_id        UUID NOT NULL REFERENCES tenants(id),
    epc_hex             TEXT NOT NULL,
    status              VARCHAR(16) NOT NULL,        -- 'requested' | 'completed' | 'failed'
    failure_reason      TEXT NULL,
    requested_by        UUID NOT NULL REFERENCES users(id),
    requested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ NULL
);
-- Indexed on (request_id), (from_tenant_id, requested_at DESC), (to_tenant_id, requested_at DESC).
```

### Status enum

`status ∈ { registered | active | retired | defective | transferred_out }`

- `registered` — owned by the tenant, never observed. Set on CSV
  import for tags that have not been read yet.
- `active` — owned, observed at least once. Set by the registrar
  worker on the first `tag_reads` write for a `registered` tag.
  Reads for EPCs not in `tags` do **not** auto-create rows (see
  OQ 3 resolution below) — they land in `tag_reads` with
  `tag_known = FALSE`.
- `retired` — operator-initiated soft delete. Read events still
  ingest into `tag_reads` (historical fidelity) but downstream
  gating treats the tag as out-of-fleet.
- `defective` — operator-initiated, distinguished from `retired` for
  reporting (e.g. supplier returns).
- `transferred_out` — terminal state on the source tenant after a
  successful transfer. A new `tags` row exists on the destination
  tenant with `source='transfer_in'`.

`status` transitions are validated in the service layer, not the DB
(matches ADR 019's `category_type` immutability pattern).

### Hot-path interaction (the critical constraint)

This ADR preserves the data-models trade-off #1 (ingest stays cheap).
**The MQTT ingest path does not read or write the `tags` table.**
Specifically:

- `tag_reads` inserts proceed unchanged — they do **not** look up
  `tags` first.
- `tag_presence` upserts (ADR 026) proceed unchanged — they do
  **not** look up `tags` first.
- A separate, asynchronous **tag registrar worker** consumes the
  internal event bus (`ingestion.tag_read` or an equivalent topic to
  be selected during implementation) and is the *only* code path
  that mutates `tags.first_seen_at` / `last_seen_at` and promotes
  `registered → active` on first read. The worker does **not**
  auto-create rows for unknown EPCs (OQ 3 resolution). It can lag
  the ingest path by seconds without affecting any user-visible
  behaviour.

This means a `tag_reads` row for an EPC not yet in `tags` is legal
and expected. The denormalized `last_seen_at` is eventually-consistent
on the order of the registrar worker's lag.

### Gating: `tag_known` on `tag_reads`

`tag_reads` carries a denormalized nullable boolean `tag_known`
populated by the registrar worker (never by the ingest path):

```sql
ALTER TABLE tag_reads
    ADD COLUMN tag_known BOOLEAN NULL;          -- NULL = not yet evaluated by registrar
```

Semantics:

- `NULL` — the registrar worker has not processed this read yet
  (transient state, bounded by worker lag, typically seconds).
- `TRUE` — EPC was in `tags` with `status ∈ {registered, active}`
  at the time the registrar processed the read.
- `FALSE` — EPC was either absent from `tags`, or present with a
  non-owning status (`retired`, `defective`, `transferred_out`).

The ingest hot path is **unchanged** — it writes `tag_reads` with
`tag_known = NULL` and moves on. No lookup, no cache, no join.
Dashboards and filters explicitly model the three-valued logic
(`?known=true|false|unknown` on `GET /v1/tenants/{slug}/tag-reads`).
Rules and presence behaviour ignore `tag_known` in v1 — it is a
dashboard/filter signal only.

The soft-asset auto-create worker (ADR 022) is the one consumer that
checks the registry directly (not via `tag_known`), because it runs
on its own worker path and the live registry state is the
authoritative answer at that point. This means soft assets are
**not** materialised for unowned/stray tags once the registry is
populated — which substantially reduces ADR 022's cost concern
(gap 2.4's deferral rationale).

### Batches: labels, not a table

Batch grouping (reels, shipments, supplier lots) reuses the existing
[ADR 020 labels](020-labels-first-class.md) catalog instead of a
dedicated `tag_batches` table. ADR 020's `entity_labels` is already
tenant-scoped, polymorphic over `entity_type`, indexed on
`(entity_type, entity_id, label_key)` for O(1) reverse lookup, and
supports the deep-object filter syntax (`?labels[batch]=reel-008rT`)
that the registry needs. Adding `tag_batches` as a typed table would
duplicate that machinery and contradict ADR 028's own non-goal of
"rigid hierarchy above batches — use labels."

**Reserved label-key namespace.** The following label keys are
contract-level (registered in the labels catalog on tenant
provisioning, not free-form):

| Key | Value type | Meaning |
|---|---|---|
| `batch` | string | Operator-defined batch identifier (`reel-008rT`, `ship-2026-05-23`). The bulk-op API requires either this or `epc_list[]`. |
| `batch.received_at` | ISO-8601 date | When the batch was received — enables "batches received this week" queries. |
| `batch.description` | string | Free-form operator note. |
| `batch.supplier` | string | Optional supplier identifier for recall scoping. |

Operators may add further `batch.*` keys via the standard ADR 020
catalog flow; bulk operations only require `batch` itself to exist.

**Tag → batch lookup** is a single indexed query against
`entity_labels WHERE entity_type='tag' AND entity_id=? AND
label_key='batch'`. **Batch → tag list** is the symmetric query.
Reverse lookup performance is equivalent to a `batch_id` FK column.

**Range optimization (not in v1).** Some batches (manufacturer
reels with sequential SGTIN serials) are inherently contiguous and
could be modelled as `(min_serial, max_serial)` on a batch row
instead of one `entity_labels` row per tag. We are explicitly
**not** shipping this in v1 — it only saves index storage (roughly
80 bytes per tag on `entity_labels`), it doesn't generalize to
non-contiguous batches (custom EPCs, mixed shipments, returns), and
it's purely additive when a customer's cardinality justifies it.
YAGNI applies.

### API surface

```
GET    /v1/tenants/{slug}/tags                       (list, viewer; filter ?status / ?labels[batch]= / ?epc_prefix / ?bound)
GET    /v1/tenants/{slug}/tags/{epc_hex}             (read one, viewer)
POST   /v1/tenants/{slug}/tags                       (single register, editor)
POST   /v1/tenants/{slug}/tags/import                (CSV bulk register, editor; max 10 000 rows, 10/hr per tenant — see OQ 4 resolution)
PATCH  /v1/tenants/{slug}/tags/{epc_hex}             (update status / metadata, editor; batch is set via labels API)
DELETE /v1/tenants/{slug}/tags/{epc_hex}             (admin; equivalent to status='retired' — no hard delete)

POST   /v1/tenants/{slug}/tag-transfers              (initiate, admin; body lists EPCs + destination tenant)
GET    /v1/tenants/{slug}/tag-transfers              (history, admin)
GET    /v1/tenants/{slug}/tag-transfers/{request_id} (one transfer's per-EPC outcomes, admin)
```

Transfer initiation requires Admin role on **both** tenants (matches
the SME reference behaviour and our ADR-008 multi-tenancy model — there
is no "platform admin" surface above the tenant). Active bindings on
transferred tags are rejected at request time (the operator must
disassociate first); we do not implicitly unbind on transfer.

### Governance & blast-radius controls

At scale (millions of tags per tenant, single bulk ops touching tens of
thousands of rows) the failure modes are operational, not algorithmic:
wrong-tenant CSV, wrong-filter bulk mutate, wrong-destination transfer,
slow drift between registry and physical reality. The following
controls are **binding decisions** of this ADR — they are not optional
hardening to be added later, because retrofitting them changes the API
shape.

1. **Default to `status='registered'` on import, never `active`.**
   Activation is a separate explicit operator action after the import
   is visually verified. This single rule catches "imported tenant A's
   reel into tenant B" before any downstream behaviour can take effect.
2. **Every bulk op is dry-run-first with a confirmation token.**
   `POST …?dry_run=true` returns `{ affected, sample, token,
   expires_in }`; `POST …?confirm=<token>` applies. The token binds
   the preview to the action — a second CSV cannot be confirmed with
   the first preview's token. Applies to `tags/import`, bulk `PATCH`,
   `tag-transfers`, and any future bulk endpoint.
3. **Scope-required filters on bulk mutations.** Bulk `PATCH` and
   bulk retire **must** carry either `labels[batch]=<value>` or
   `epc_list[]` with `len ≤ 1000`. There is deliberately no "PATCH
   all tags in tenant" surface. Operators think in batches; the
   API enforces it.
4. **Two-person rule above a tenant-configurable threshold.**
   `tenants.tag_bulk_two_person_threshold` (default 10 000). Bulk ops
   over the threshold create a `pending` request that a second admin
   must approve before it executes. Transfers already model this via
   `tag_transfers.status`; generalise the pattern.
5. **Reconciliation reports, not auto-reconciliation.** A scheduled
   job emits exception views: registered-but-unread-for-N-days,
   reading-but-unregistered EPCs, bindings-on-retired-tags. The job
   never mutates state — operators decide. Drift is the slow-burn
   failure mode at scale; surface it.
6. **Soft state only for lifecycle transitions.** `retired`,
   `defective`, `transferred_out` keep the row. Recovery from any
   error is `PATCH status=active`. No hard deletes via the registry
   API.
7. **Single audit log keyed on `(actor, action, batch, count,
   request_id)`** spans every bulk op (import, PATCH, retire,
   transfer). When something goes wrong the first question is always
   "who did what, when, to which batch" — one table must answer it.

These controls are what make a future opt-in category-scoped
auto-binding action (the use case the SME reference's auto-association
wizard addresses — see Not in scope below) acceptable without
reopening this ADR: it would reuse the same dry-run + confirm + scope
+ audit machinery.

### Backfill

A one-shot Alembic data migration enumerates every distinct EPC in
`asset_tag_bindings` and `tag_reads` per tenant and inserts a `tags`
row with `source='backfill'` and `status='active'`. Run-time is bounded
by the `(tenant, epc)` cardinality, not the read volume; expected to
finish well under existing migration budgets.

### Not in scope (explicit non-goals)

- **Per-read tag-existence gating.** No ingest-time policy of
  "drop reads for unknown EPCs". The ingest path stays unchanged
  (see hot-path constraint above). Whether and where the
  registry's `status` should gate downstream behaviour
  (presence rows, soft-asset auto-creation, alerts) is
  Open Question 1.
- **Reel-range auto-association.** Gap 2.16 in the remediation
  tracker (auto-creating one asset per tag in a reel). The
  registry is a prerequisite, but auto-association is a separate
  decision — keep 2.16 as "Drop" for now and revisit only if a
  customer asks.
- **Cross-tenant tag history.** A transferred tag's `tag_reads`
  history stays on the source tenant. The destination tenant
  starts with an empty observation history. This matches the
  ownership-boundary contract — readings made under tenant A's
  observations belong to tenant A.
- **Hardware-level provisioning (TID, certs).** This ADR is about
  ownership of an *EPC value*, not about chip-level identity.
  TID-based binding stays available via the existing
  `binding_kind='tid'` path on `asset_tag_bindings`.
- **Rigid hierarchy above `batch`** (reel → shipment → PO →
  supplier as nested tables). Use [ADR 020 labels](020-labels-first-class.md)
  on `tags` for operator-defined grouping (`supplier=…`,
  `po=…`, `shipment=…`). Labels flex per tenant; a hardcoded
  hierarchy does not. `batch` is the only contract-level grouping
  key because it's the unit operators *act on* (register / retire
  / transfer as a group) — and even `batch` is stored as a label,
  not a table (OQ 5 resolution).
- **Per-tag → per-asset auto-binding wizards.** Generic
  auto-association across the platform is rejected (it inverts
  TagPulse's many-tags-over-one-asset-lifetime model — see
  remediation tracker gap 2.16). If a real customer surfaces an
  item-level use case (1 tag = 1 disposable asset), ship it as a
  **category-scoped** action gated by an explicit category flag,
  reusing the dry-run + confirm + audit controls above. Not a
  platform primitive.

## Consequences

**Positive:**

- Operators can pre-register inventory and answer "how many tags do
  we own?" without needing reads to have happened.
- Cross-tenant transfer is expressible with audit history.
- Stray-read diagnosis becomes a query (`status IS NULL` on the
  registry view, joined to `tag_reads`).
- Bulk reel-level lifecycle changes (recall, retire, mark defective)
  are O(1) batch operations.
- ADR 019's "deferred tag-registry concept" reference (§Context
  bullet 4 — "cannot enforce the required-tag-count contract") is
  unblocked.

**Negative / costs:**

- New table grows with owned-EPC cardinality per tenant. For a
  large operator (10 M tags), `tags` is still well within
  Postgres comfort. Not a hypertable — the row count is bounded
  by what the operator buys, not by event volume. Batch labels
  add roughly one `entity_labels` row per tag (~80 bytes indexed)
  — negligible against `tag_reads` volume.
- New eventually-consistent surface: the registrar worker's lag
  means `last_seen_at` on a `tags` row may trail the truth in
  `tag_reads` by seconds. Documented as the API contract.
- Two new admin workflows (CSV import, transfer) need UI before
  the registry is operator-usable. Backend can ship first; UI is
  a separate sprint.

## Open questions

> These need answers before Sprint 50 implementation begins. They are
> design decisions, not implementation details — defer to the SME /
> product call.

**OQ 1 — RESOLVED 2026-05-23: soft gating via `tag_known`.**

Chose option (b): denormalize a nullable `tag_known` boolean on
`tag_reads`, populated asynchronously by the registrar worker. See
§"Gating: `tag_known` on `tag_reads`" in the Decision section.
Rationale: gives dashboards and filters a useful signal without
touching the ingest hot path, and unblocks the ADR 022 soft-asset
cost concern by letting the soft-asset worker skip unowned tags.
Hard gating (option c) explicitly rejected — we keep the
event-ledger guarantee that every read lands in `tag_reads`.

**OQ 2 — RESOLVED 2026-05-23: option (c), `epc_hex` PK + nullable `gs1_uri`, lenient parse.**

The `tags` table carries `gs1_uri TEXT NULL` populated by the
registrar worker (or at import time for CSV-supplied rows) from
the existing [`urn:epc:id:…`](../../src/tagpulse/rfid/epc.py)
parser. `(tenant_id, gs1_uri)` is partially indexed for search.
Non-GS1 EPCs and unparseable values keep `gs1_uri = NULL` and
import with a structured warning surfaced in the import response
(matches the soft-asset philosophy: don't lose data, surface the
exception). The natural key remains `(tenant_id, epc_hex)` so the
schema is encoding-agnostic; GS1 features (search by GTIN, group
by company prefix) light up automatically for tags that parse.

**OQ 3 — RESOLVED 2026-05-23: option (b), never auto-register.**

The registrar worker does not create `tags` rows for unknown EPCs.
Reads for unknown EPCs land in `tag_reads` with `tag_known = FALSE`
and are visible via the strays filter. Rationale: TagPulse is
pre-GA with no production install base, so backward compatibility
is not a constraint. Defaulting to auto-register would silently
undermine the registry's "owned" semantics by absorbing every
neighbour read into the fleet, and would render the `tag_known`
signal (OQ 1) useless. The one-shot backfill seeds existing dev
tenants' historical EPCs as `active`, so the change is
non-disruptive at migration time.

**Onboarding contract:** new tenants must import their inventory
(CSV or API) before reads will be attributed to owned tags. This
is an explicit operator step, documented in the onboarding
runbook. A tenant-level `tag_auto_register` setting may be added
later if a customer specifically asks for topic-ownership
semantics — not shipped speculatively.

**OQ 4 — RESOLVED 2026-05-23: 10 000 row cap, reject above, 10 imports/hour per-tenant, all-or-nothing validation.**

- **Per-request cap:** 10 000 rows. Round number, comfortable
  headroom over the SME reference (6 000), fits in a single
  transaction at TagPulse's row size. Final number subject to
  Sprint 50 Phase A load-test confirmation but the API contract
  treats the cap as fixed.
- **Behaviour above cap:** `413 Payload Too Large`. No
  server-side auto-chunking — it complicates the dry-run +
  confirmation-token flow (which token belongs to which chunk?).
  Operators with very large imports use a scripted client that
  chunks deliberately.
- **Rate limit:** 10 imports/hour per tenant, configurable via
  a tenant setting. Generous for honest workflows, catches a
  runaway script.
- **Validation:** all-or-nothing. Any invalid row rejects the
  whole CSV with a per-line error report. Partial imports are
  explicitly rejected because the resulting reconciliation
  burden ("did row 4732 land?") outweighs the convenience. The
  dry-run preview already surfaces validation errors before
  commit, so the retry cost is minimal.

**OQ 5 — RESOLVED 2026-05-23: no `tag_batches` table; use ADR 020 labels with reserved `batch.*` key namespace.**

Batch grouping (reels, shipments, lots) is modelled as ADR 020
labels on `tags` rows rather than a dedicated `tag_batches` table.
Reserved keys: `batch` (required by the bulk-op API),
`batch.received_at`, `batch.description`, `batch.supplier`.
Rationale: ADR 020's `entity_labels` already provides O(1) reverse
lookup, tenant scoping, polymorphic entity support, and the
`?labels[batch]=…` filter shape — a dedicated table would duplicate
that machinery and contradict ADR 028's own non-goal of rigid
hierarchy above batches. Range-based optimization for sequential
reels (one `(min_serial, max_serial)` row instead of N
`entity_labels` rows) is explicitly deferred under YAGNI — it only
saves index storage, doesn't generalize to non-contiguous batches,
and is purely additive when needed. See §"Batches: labels, not a
table" in the Decision section for full details.

## Decision history

- v0 (this version, **Proposed**): introduce `tags` and
  `tag_transfers` as identity + ownership layer above bindings
  and reads. Five open questions deferred to SME / product call
  before Sprint 50 implementation. Hot-path ingest behaviour
  explicitly preserved.
- v0.1 (2026-05-23, still **Proposed**): OQ 1 resolved — chose
  soft gating (option b). Added `tag_reads.tag_known BOOLEAN NULL`
  column populated by the registrar worker, three-valued filter
  surface on `GET /tag-reads`, and explicit note that the
  soft-asset worker checks the registry directly (not via
  `tag_known`) which reduces ADR 022's cost concern.
- v0.2 (2026-05-23, still **Proposed**): OQ 2 resolved — chose
  option (c). Added `tags.gs1_uri TEXT NULL` with partial index;
  lenient parse (unparseable EPCs import with `NULL` + warning,
  not rejected). Natural key remains `(tenant_id, epc_hex)`.
- v0.3 (2026-05-23, still **Proposed**): OQ 3 resolved — chose
  option (b), never auto-register. Justified by pre-GA status
  (no production install base whose workflows would break).
  Dropped `'first_read'` from the `source` enum; onboarding
  contract requires explicit inventory import before reads
  attribute to owned tags. Tenant-level opt-in setting deferred
  until a customer asks for it.
- v0.4 (2026-05-23, still **Proposed**): OQ 4 resolved —
  10 000-row cap per CSV import, `413` above the cap (no
  server-side auto-chunking), 10 imports/hour per-tenant rate
  limit (configurable), all-or-nothing validation with per-line
  errors surfaced in the dry-run response.
- v0.5 (2026-05-23, still **Proposed**): OQ 5 resolved — dropped
  the `tag_batches` table; batches modelled as ADR 020 labels
  with reserved `batch.*` key namespace (`batch`,
  `batch.received_at`, `batch.description`, `batch.supplier`).
  API surface removed `/tag-batches` endpoints; bulk-op scope
  filter switched from `batch_id=` to `labels[batch]=`.
  Range-optimization for sequential reels deferred under YAGNI.
  **All five open questions now resolved — ADR is ready for
  promotion to Accepted.**
- v1.0 (2026-05-23, **Implemented**): Sprint 50 Phases A–E
  shipped on branch `sprint-50/tag-registry`; ADR promoted from
  Accepted to Implemented under Phase F.
  - **Phase A — Schema & migration.** `tags`, `tag_transfers`,
    RLS, `tag_reads.tag_known`, reserved `batch.*` label-key
    namespace with collision-refusal migration. Migrations
    043 (`tag_registry`), 044 (`tag_reads_tag_known`), 045
    (`tag_label_namespace`). Collision runbook:
    [reserved-label-key-collision.md](../runbooks/reserved-label-key-collision.md).
  - **Phase B — Read/write API.** `GET/POST/PATCH/DELETE
    /v1/tenants/{slug}/tags`, `GET /v1/tenants/{slug}/tags/{epc_hex}`,
    `POST /v1/tenants/{slug}/tag-transfers`. Service layer in
    `src/tagpulse/services/tags.py`. List filters: `?status`,
    `?labels[batch]=`, `?epc_prefix`, `?bound`. PATCH does not
    accept `batch_id` (batches go through `entity_labels`).
  - **Phase C — Bulk import + governance.** `POST
    /v1/tenants/{slug}/tags/import` (CSV, 10 000-row cap → 413,
    per-tenant rate limit `tenants.tag_bulk_import_rate_limit`,
    dry-run + confirmation token, two-person approval above
    `tenants.tag_bulk_two_person_threshold` via
    `pending_bulk_operations`, scope-required filters on bulk
    mutations, unified audit log entries
    `tags.import` / `tags.bulk_patch` / `tags.bulk_retire` /
    `tag-transfers.request`). Migrations 046–048.
  - **Phase D — Registrar worker + `tag_known` population.**
    `src/tagpulse/workers/tag_registrar_worker.py` is the
    sole writer of `tag_known`; promotes `registered → active`
    on first matching read; never auto-creates `tags` rows.
    Soft-asset worker (ADR 022) reads the registry directly.
  - **Phase E — Reconciliation reports (governance §5).**
    `GET /v1/tenants/{slug}/tags/reconciliation/{view}` with
    JSON + CSV export for the three views
    (`registered-unread`, `unregistered-reading`,
    `bindings-on-retired`). Service module
    `src/tagpulse/services/tag_reconciliation.py`. Operator
    guide: [runbooks/tag-registry-operations.md](../runbooks/tag-registry-operations.md).
  - **Phase F — Docs (this entry).** ADR 028 status flip
    Accepted → Implemented; [reference-design-remediation.md](../design/reference-design-remediation.md)
    row 2.14 flipped to Done; [docs/roadmap.md](../roadmap.md)
    Sprint 50 marked `(shipped)`;
    [docs/data-models.md](../data-models.md) gained the
    "Tag registry (Sprint 50+)" section plus `tags` /
    `tag_transfers` / `pending_bulk_operations` table entries
    and the `tag_reads.tag_known` column note; operator
    runbook [tag-registry-operations.md](../runbooks/tag-registry-operations.md)
    added.
  - **Phase G — Validation (deferred to a follow-up sprint).**
    Registrar-worker integration test, reconciliation worker
    emitting Prometheus gauges, and live-Postgres integration
    tests for the bulk-import + two-person flow remain
    out-of-scope for Sprint 50. The unit-test suite (1335
    passing) covers the schema, service layer, governance
    invariants, and reconciliation queries.
- v1.1 (2026-05-23, **Audit remediation**): Post-ship audit on
  commit `d776b8a` closed two API-surface gaps that the per-phase
  test suites missed because they were holes between modules
  rather than bugs inside one. (1) Migration 045's binding
  docstring requires the labels API to refuse user-initiated
  CREATE / UPDATE / DELETE on any key in the reserved `batch.*`
  namespace **regardless of `entity_type`**, but the route layer
  had no such guard — an admin could shadow `batch.foo` under
  `entity_type='asset'`, or DELETE the seeded `(tag, batch)` row
  and wipe their tenant's batch grouping. Closed by adding
  `RESERVED_LABEL_KEYS` + `is_reserved_label_key()` to
  [src/tagpulse/services/tags.py](../../src/tagpulse/services/tags.py)
  (single source of truth, also reusable by tenant-bootstrap
  paths) and a `_refuse_if_reserved()` 403-guard in
  [src/tagpulse/api/routes/labels.py](../../src/tagpulse/api/routes/labels.py)
  fired from `create_label`, `update_label` (against the
  existing row's stored key — PATCH has no rename surface),
  and `delete_label` (against the loaded row before any
  deletion). Per-entity *association* endpoints
  (`POST /{entity_segment}/{id}/labels`) are deliberately NOT
  guarded — operators must be able to bind `batch=reel-008rT`
  values to tags via the seeded reserved row, which is the
  whole point of the reservation. (2) The labels-route URL →
  DB `entity_type` mapping `_ENTITY_TYPE_FROM_URL` was never
  widened when migration 045 added `'tag'` to the CHECK
  constraint, so the ADR §"Batches: labels, not a table" flow
  (`POST /tags/{id}/labels`) returned 404 "Unknown entity
  kind" end-to-end — closed by adding `"tags": "tag"` to the
  map. Tests:
  [tests/unit/test_labels_reserved_keys.py](../../tests/unit/test_labels_reserved_keys.py)
  (16 cases). `make check` clean: 1351 passed, 1 skipped (+16
  from v1.0's 1335).
