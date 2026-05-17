# ADR-022: Soft Assets — Auto-Creation Policy

- Status: Proposed (Sprint 33, May 2026)
- Implements: gap 2.4 in `local reference notes/IMPLEMENTATION-GAPS.md`
- Related: [reference-design-remediation plan](../design/reference-design-remediation.md), ADR [003 TimescaleDB storage](003-timescaledb-storage.md) (`tag_reads` as event-ledger source-of-truth), [data-models.md §"Where is the tag?"](../data-models.md#where-is-the-tag-and-why-theres-no-tags-table), [design/tracking-modes.md](../design/tracking-modes.md) (the asset-mode / inventory-mode split this fits into)

## Context

Reference design "Soft Assets": when a read arrives from a Tag that has no
asset association, **and** the originating Location has
`soft_assets_enabled=true`, the platform auto-creates a Soft Asset so no
telemetry is lost. A Soft Asset converts to a full Asset on first manual
association of the same tag, preserving all historical sensing data via a
`previously_soft_asset=true` label.

Per-zone exclusion: even inside a soft-asset-enabled Location, operators can
flag staging/transit zones as `soft_assets_excluded=true` so we don't churn
out one Soft Asset per tag passing through the loading dock.

TagPulse already records every read in `tag_reads` regardless of binding
(ADR 003 event-ledger guarantee). The data is captured. What's missing is the
**entity surface** — operators have no UI representation of "all the tags
showing up inside warehouse-A that aren't associated to anything yet."

Inventory mode has an analogous auto-create flow (`stock_items` on first SGTIN
read matching a `tag_data_mapping`), but it requires a registered SKU and
operates per-EPC. Soft Assets are simpler — just "binding-less reads in a
soft-asset-enabled location should materialise an Asset row."

## Decision (proposed — to be ratified in Sprint 39)

Add toggle columns + an ingest-side hook that materialises a placeholder
asset on the first qualifying unassociated read. Mark the new asset with
`origin='soft'` so it can be hidden/filtered separately from manually-created
assets.

```sql
ALTER TABLE sites
    ADD COLUMN soft_assets_enabled BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE zones
    ADD COLUMN soft_assets_excluded BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE assets
    ADD COLUMN origin VARCHAR(16) NOT NULL DEFAULT 'manual';   -- manual | soft
```

Ingest pipeline change (worker, runs after dedupe + parse, before
rule/sensing-event evaluation):

```text
for read in batch:
  if read has matching active asset_tag_bindings row:
    continue
  resolve site + zone for the read (via reader_bound or geofence membership)
  if site is None or not site.soft_assets_enabled:
    continue
  if zone is not None and zone.soft_assets_excluded:
    continue
  upsert asset (tenant_id, name=f"Soft Asset {tag_id[:8]}", origin='soft',
                category_id=tenant's default soft-asset category)
  insert asset_tag_bindings (asset_id=new.id, binding_value=tag_id, binding_kind='epc')
  emit audit_logs entry (action='soft_asset_auto_created')
```

Conversion flow (operator creates a real Asset for a tag that is already a
Soft Asset):

```text
POST /v1/tenants/{slug}/assets { external_ref: "...", category_id, binding_value: "..." }
  → resolve existing asset by binding_value
  → if existing.origin == 'soft':
      patch existing.origin = 'manual'
      patch existing.external_ref, name, category_id, ... from request
      add label "previously_soft_asset" = "true"
      return existing.id (200 OK + X-Asset-Origin: converted-from-soft header)
  → else: standard create-new-asset path (201 Created)
```

UI:

- New Soft Assets column on the Locations list (Enabled / Disabled toggle).
- Per-zone "Exclude Soft Assets" toggle on Zone edit modal.
- Soft Assets surface on Asset list — `origin='soft'` rows shown with a
  badge, filterable separately; default Asset list filter hides them.
- "Convert to Asset" button on a Soft Asset's detail page (one-click form
  that reuses the conversion endpoint above).

## Alternatives considered

1. **Always auto-create on unbound read** — rejected; cost-prohibitive in
   ingestion-heavy tenants (one row per unique stray tag could 10–100× asset
   row count). Operator opt-in via the Site toggle is essential.
2. **Lazy materialisation on query** — rejected; the operator UI affordance
   ("show me Soft Assets I should triage") requires real rows so they can be
   listed, filtered, sorted, label-tagged, and converted.
3. **Tag aggregate counts in a separate `soft_asset_candidates` table** —
   rejected; doesn't allow Label associations or Sensing Event scoping on
   Soft Assets. Reuse `assets` with `origin` discriminator.

## Consequences

- **Cost:** one extra `assets` row + one `asset_tag_bindings` row per unique
  unassociated tag observed in a soft-asset-enabled location. Bounded by the
  operator's opt-in scope. Add a Prometheus counter
  `tagpulse_soft_assets_created_total{tenant}` so we can monitor blast
  radius.
- **Storage tax:** small. Soft assets carry no telemetry-model-specific
  history beyond the binding's `tag_reads` rows which would exist anyway.
- **Risk of accidental opt-in:** mitigated by defaulting both new columns to
  `FALSE` and surfacing the toggle in the Locations UI with a "what does this
  mean?" tooltip.
- **Conversion semantics are subtle.** The same physical tag ID can map to
  the same asset (Soft → Manual upgrade) — we preserve `asset_id` so all
  historical `tag_reads` remain reachable by binding history. Document this
  prominently in the conversion flow's docstrings.

## Open questions for Sprint 39

- Should Soft Assets count against any per-tenant Asset quota? Lean no, until
  a customer asks.
- Garbage-collect Soft Assets that have no reads for > N days? Lean no
  initially — let the operator manage explicitly. Add cleanup as a follow-up
  if Soft Assets accumulate.
- Default category for auto-created Soft Assets — per-tenant configurable,
  or hard-coded "Unknown"? Lean per-tenant configurable (new column on
  `tenants`).
- Should `previously_soft_asset` be a Label (ADR 020) or a column on
  `assets`? Lean Label — it's user-visible metadata, fits the catalog
  pattern.
