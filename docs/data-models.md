# TagPulse Data Models & Schemas

This document is the single reference for all database tables, Pydantic API schemas, and their relationships. Source of truth for column types lives in [`src/tagpulse/models/database.py`](../src/tagpulse/models/database.py) (ORM) and the `src/tagpulse/models/` schema files (API contracts).

> **New here?** Start with [guides/domain-concepts-101.md](guides/domain-concepts-101.md) for a plain-English primer on devices, tags, assets, lots, stock items, bindings, and how location is derived. This document goes deeper on schema.
>
> **RFID domain primer:** what an RFID tag actually carries (TID, EPC, user memory, sensor data) and how those fields land in `tag_reads` and `device_telemetry` is captured in [design/rfid-tag-data-model.md](design/rfid-tag-data-model.md).
>
> **Mobile readers / carriers:** the `devices.mobility` flag, `assets.parent_asset_id` / `stock_items.parent_stock_item_id` containment columns, and the `binding_kind='device'` extension to `asset_tag_bindings` are specified in [design/mobile-carriers-and-manifests.md](design/mobile-carriers-and-manifests.md). All landed across migrations 017–019 and 028.

---

## Entity-Relationship Overview

```
tenants
  |-- 1:N -- devices
  |-- 1:N -- users
  |-- 1:N -- tag_reads
  |-- 1:N -- device_telemetry
  |-- 1:N -- telemetry_quarantine
  |-- 1:N -- external_locations               — TMS-pushed positions
  |-- 1:N -- assets                           — asset-tracking mode
  |           |-- 1:N -- asset_tag_bindings
  |-- 1:N -- products                         — inventory mode
  |           |-- 1:N -- lots
  |                       |-- 1:N -- stock_items   (parent_stock_item_id self-FK)
  |                                       |-- 1:N -- stock_movements
  |-- 1:N -- sites                            — shared substrate
  |           |-- 1:N -- zones                — reader_bound | geofence (polygon)
  |-- 1:N -- subject_current_zone             — durable dwell-tracker state
  |-- 1:N -- tag_data_mappings                — tag_data key → semantic field
  |-- 1:N -- rules
  |           |-- 1:N -- alerts
  |-- 1:N -- telemetry_models
  |-- 1:N -- integrations
  |           |-- 1:N -- integration_deliveries
  |-- 1:N -- analytics_results
  |-- 1:N -- tenant_usage_detail
  |-- 1:N -- tenant_quotas
  |-- 1:N -- audit_logs
  |-- 1:N -- dead_letter_events
```

> **Tracking modes:** TagPulse supports two domain layers — **asset tracking** (`assets`) and **inventory tracking** (`products` / `stock_items`) — sitting on the same shared substrate (`tag_reads`, `sites`, `zones`, `subject.zone_changed` events, edge contract). A tenant can enable one or both via `tenants.tracking_modes`. Full design: [design/tracking-modes.md](design/tracking-modes.md).

All tenant-scoped tables enforce isolation via:
1. `tenant_id` FK + index
2. Row-Level Security (RLS) policies using `current_setting('app.current_tenant_id')`

---

## Where is the "tag"? (and why there's no `tags` table)

> **Sprint 50 update (ADR 028).** This section described the pre-Sprint-50 design. A `tags` table now exists as an **identity / ownership** layer above the bindings + reads model below — operator-driven (CSV import, lifecycle status, cross-tenant transfers), **never on the ingest hot path**. See [Tag registry (Sprint 50+)](#tag-registry-sprint-50) below and [ADR 028](adr/028-tags-as-first-class-entity.md). The "no Tag entity on the ingest hot path" trade-offs in this section remain accurate — `tag_reads` writes still touch zero rows in `tags`.

A common new-contributor question: *we have devices, assets, sites, zones, telemetry — where do RFID tags live?*

**The TagPulse ingest path has no first-class `Tag` entity.** A "tag" is a *value* (an EPC / TID / device-emitted string), not a row. It's resolved into business meaning through three independent layers, each with its own lifecycle:

| Where the tag value lives | What it is | Lifecycle |
|--------------------------|------------|-----------|
| `tag_reads.tag_id` | The raw string the reader emitted on each scan. | One row per read, append-only TimescaleDB hypertable. Every read is recorded **whether or not** the tag is bound to anything — the event ledger is the source of truth. |
| `asset_tag_bindings.binding_value` (+ `binding_kind ∈ {epc, tid, device}`) | The **active** mapping `tag_id → asset_id` for asset tracking. | One row per (tenant, tag, asset, bound-window). `bound_at` / `unbound_at` give a full history; the same physical tag can be re-stuck on a new asset (slap-and-ship) without losing the audit trail. |
| `stock_items.epc` | One unique SGTIN-96 EPC = one inventory unit. | Auto-materialized on **first read** when a `tag_data_mapping` decodes the EPC into `(product, lot)`. Subsequent reads of the same EPC update `current_zone_id` / `last_seen_at` instead of inserting. |
| `tag_data_mappings` | Tenant-level **rules** that parse fields out of the tag-data blob (e.g. company-prefix → product GTIN, byte-range → `lot_code`). | Catalog rule, not per-tag. Reads inherit the parse at ingest time. |

### Why no `tags` table?

Three deliberate trade-offs:

1. **Ingest stays cheap.** Every read is a single append to `tag_reads` (a TimescaleDB hypertable, see [ADR 003](adr/003-timescaledb-storage.md)). Requiring a `tags` row to exist first would add a SELECT-or-INSERT round-trip per read and halve ingest throughput.
2. **A "tag" without context is meaningless.** `urn:epc:id:sgtin:0614141.123456.7890` only matters if you can resolve it to *what* (product + lot) or *which* (asset). Different resolution paths have different lifecycles, so collapsing them into one table would over-couple them.
3. **Tags are rebindable.** A tag peeled off pallet A and stuck on pallet B is the same physical object but a different business meaning. Binding-history rows (rather than mutating a `tags` row in place) preserve the audit trail.

### How the three "what is this tag?" questions get answered

| Question | Answer source |
|---|---|
| Which asset is this tag attached to **right now**? | `asset_tag_bindings WHERE binding_value=… AND unbound_at IS NULL` |
| What product/lot does this EPC represent? | `tag_data_mappings` → decode → `products` + `lots` (auto-creates `stock_items` row on first read) |
| Where has this tag physically been? | `tag_reads` filtered by `tag_id`, ordered by `recorded_at` (each row carries `device_id` + optional `location`) |
| What carrier's manifest contains it? | binding → asset → recursive `parent_asset_id` walk via `GET /assets/{id}/manifest` |

### Mental model

> **Devices** *emit* tag reads. **Tag reads** *carry* tag IDs. **Bindings** turn a tag ID into an **asset**. **Mappings** turn an EPC into a **stock item** (product + lot). **Sites + Zones** are the spatial frame everything is observed against. **Telemetry** is the parallel non-tag sensor stream from the same devices.

### UI sidebar → underlying entities

| Sidebar page | Entities |
|---|---|
| **Devices** | `devices` (+ `device_health`) |
| **Assets** | `assets` + `asset_tag_bindings` + `asset_current_location` view |
| **Sites & Zones** | `sites` + `zones` |
| **Telemetry / Telemetry Models** | `telemetry_models` + `telemetry_readings` |
| **Products / Lot Expiry / Stock Levels / Stock Movements** | `products` + `lots` + `stock_items` + `stock_movements` |
| **Admin → Tag Data Mappings** | `tag_data_mappings` (the closest thing to a "tag config" UI) |
| **Tag Reads** (under Devices) | `tag_reads` — the raw event log |
| **Rules / Alerts** | `rules` + `alerts` |
| **Map** | `assets` ⨝ `asset_current_location` ⨝ `zones` (geofence polygons) |

For the deeper EPC/TID/sensor-data primer (what each chunk of an RFID tag carries on the wire) see [design/rfid-tag-data-model.md](design/rfid-tag-data-model.md).

### Tag registry (Sprint 50+)

Sprint 50 added a `tags` table on top of the three layers above. It does **not** replace any of them — ingest still appends to `tag_reads` with zero lookups, and asset attribution still flows through `asset_tag_bindings`. The registry answers operator-facing questions the bindings-only model could not: *"What EPCs does this tenant own?"*, *"Is this reading from a tag we registered or a stray?"*, *"Did the supplier transfer this reel to us yet?"*. Per [ADR 028](adr/028-tags-as-first-class-entity.md).

| Table / column | Purpose | Hot path? |
|---|---|---|
| [`tags`](#tags) | Per-`(tenant, epc_hex)` identity + lifecycle status (`registered` → `active` → `retired`/`defective`/`transferred_out`). Populated by CSV import, API, or transfer-in. | **No** — never queried on ingest. |
| [`tag_transfers`](#tag_transfers) | Append-only cross-tenant transfer audit log (one row per EPC; `request_id` groups a batch). | No — admin-only flow. |
| `tag_reads.tag_known` | Three-valued (`NULL` / `TRUE` / `FALSE`) classification written **only** by the background registrar worker. | **Read-only on hot path** (write happens off the ingest critical path). |
| `pending_bulk_operations` | Two-person-approval staging for bulk ops above the tenant's `tag_bulk_two_person_threshold` (default 10 000). | No. |
| `audit_logs.action ∈ {tags.import, tags.bulk_patch, tags.bulk_retire, tag-transfers.request}` | Unified bulk-op audit trail (governance §7). | No. |
| **Reserved label keys** (`batch`, `batch.received_at`, `batch.description`, `batch.supplier`) under `entity_type='tag'` | Batch grouping via [ADR 020 labels](adr/020-labels-first-class.md) — **no `tag_batches` table**. The reservation is namespace-wide across all entity types; migration 045 enforces it. Collision handling: see [runbooks/reserved-label-key-collision.md](runbooks/reserved-label-key-collision.md). | No. |

Operator workflows for the registry (CSV bulk import, dry-run + two-person flow, reconciliation reports) live in [runbooks/tag-registry-operations.md](runbooks/tag-registry-operations.md).

---

## Database Tables

### tenants

Organization accounts on the platform.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `name` | VARCHAR(255) | NOT NULL | Display name |
| `slug` | VARCHAR(100) | NOT NULL, UNIQUE | URL-safe identifier, lowercase alphanumeric + hyphens |
| `plan` | VARCHAR(50) | NOT NULL, default `'standard'` | Billing plan tier |
| `status` | VARCHAR(50) | NOT NULL, default `'active'` | `active`, `suspended` |
| `provisioning_key_hash` | VARCHAR(255) | NULLABLE | SHA-256 hash of device provisioning key |
| `provisioning_key_prefix` | VARCHAR(10) | NULLABLE | First 10 chars for O(1) lookup |
| `tracking_modes` | JSONB | NOT NULL, default `'["asset"]'` | Array of `asset` \| `inventory`; controls which domain layer is exposed |
| `db_pool_key` | VARCHAR(64) | NOT NULL, default `'shared_default'` | Routing key into the startup-built `PoolRegistry`. Most tenants share `'shared_default'` (RLS-isolated); sovereign tenants get a dedicated key pointing at a region-specific cluster. See [adr/008-multi-tenancy-strategy.md](adr/008-multi-tenancy-strategy.md) and [design/storage-strategy.md §6 Q2](design/storage-strategy.md). |
| `tile_provider` | JSONB | NULLABLE | Per-tenant map tile provider override. Shape: `{"kind": "osm" \| "mapbox" \| "maptiler" \| "self_hosted", "config": {...}}`. NULL = system default (OSM public for POC). Resolved by `MapConfigResolver`; switching providers is a settings change, not a code change. See [design/geofencing-and-map.md §11](design/geofencing-and-map.md) Q4. |
| `ui_config` | JSONB | NULLABLE | Per-tenant Configurable-UI **presentation** defaults (the tenant + role layers). NULL = pure system default. Tenant-default leaves (`labels` / `theme` / `nav` / `cards` / `columns` / `tables`) live at the top level; the per-role layer is keyed under a reserved `roles` sub-object, e.g. `{"theme": {...}, "roles": {"viewer": {"columns": {...}}}}`. Split into resolve layers and folded `System → Tenant → Role → User` by `tagpulse.services.ui_config` (never queried directly). Reuses the tenant-JSONB precedent above. See [adr/032-configurable-ui.md](adr/032-configurable-ui.md). |
| `position_strategy` | JSONB | NULLABLE | Sprint 59 ([ADR-024](adr/024-position-estimation.md)): per-tenant indoor-position estimator config — the RSSI-weight formula varies company-to-company, so it is config, never hardcoded. **Read by the Sprint 66 `rssi_weighted_centroid` estimator** (`half_life_s` τ, `recompute_interval_s`, `lookback_s`, `min_antennas`, `rssi_floor_dbm`). NULL = tenant not opted in. The estimator worker is **off by default** (`position_estimator_enabled`). |
| `fusion_strategy` | JSONB | NULLABLE | Sprint 71 ([ADR-034](adr/034-asset-state-consolidation.md)): per-tenant **asset-state consolidation** config — generalises `position_strategy` to govern the `read_count × recency` fusion of an asset's bound-tag reads into one zone + environment answer. **Read by the consolidation worker** (`FusionStrategy`: `half_life_s` τ, `recompute_interval_s`, `lookback_s`, `rssi_floor_dbm`, `min_reads`). Sprint 72 adds an optional `sla` sub-block (`temp_min_c`/`temp_max_c`/`humidity_max`/`excursion_tolerance_s`) scoring transit-leg cold chain. NULL = tenant not opted in. The worker is **off by default** (`consolidation_enabled`). |
| `logo_url` | TEXT | NULLABLE | Tenant branding logo for the 240px expanded sidebar header. Either an `https://` URL or a size-capped inline base64 `data:` URL (cap enforced at the API layer, not the DB — Sprint 60 widened this from `VARCHAR(2048)` to `TEXT` to hold a data URL). NULL = system default. |
| `logo_collapsed_url` | TEXT | NULLABLE | Sprint 60: second branding logo — a square mark for the 64px collapsed sidebar rail. Same `https://`-or-`data:` rule as `logo_url`. NULL = no second logo (fall back to `logo_url` / system default). |
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |

**Migration:** 005, 014, 017 (tracking_modes), 023 (db_pool_key), 026 (tile_provider), 036 (logo_url — tenant branding), 051 (position_strategy), 053 (ui_config), 054 (logo_url → TEXT + logo_collapsed_url), 058 (fusion_strategy)

---

### devices

Registered readers and IoT devices.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `name` | VARCHAR(255) | NOT NULL | Human-readable label |
| `device_type` | VARCHAR(50) | NOT NULL, default `'rfid_reader'` | |
| `status` | VARCHAR(50) | NOT NULL, default `'active'` | `active`, `pending`, `decommissioned`, `rejected` |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL, indexed | |
| `metadata` | JSONB | NULLABLE | Freeform key-value metadata |
| `configuration` | JSONB | NULLABLE | Per-device settings |
| `firmware_version` | VARCHAR(50) | NULLABLE | |
| `connection_state` | VARCHAR(50) | NOT NULL, default `'unknown'` | `online`, `offline`, `unknown` |
| `last_seen` | TIMESTAMPTZ | NULLABLE | Updated on each ingestion |
| `mobility` | VARCHAR(16) | NOT NULL, default `'fixed'` | `fixed` \| `mobile`. Drives zone resolution: fixed → reader_bound / floor-polygon; mobile → geofence via the read's lat/lon. |
| `site_id` | UUID | FK → sites.id, ON DELETE SET NULL, NULLABLE, indexed | Sprint 64: the site/floor a fixed reader physically lives on. NULL for mobile/un-assigned readers. Enables floor-polygon zone resolution. |
| `token_hash` | VARCHAR(255) | NULLABLE | SHA-256 hash of per-device token |
| `token_prefix` | VARCHAR(10) | NULLABLE | First chars for O(1) lookup |
| `token_rotated_at` | TIMESTAMPTZ | NULLABLE | Last rotation timestamp |
| `cert_thumbprint` | VARCHAR(128) | NULLABLE | X.509 mTLS cert thumbprint |
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL, auto-updated | |

**RLS:** Yes (migration 007)
**Migration:** 001, 002, 003, 005, 017 (mobility), 025 (tokens), 026 (cert_thumbprint), 055 (site_id)

---

### tag_reads (hypertable)

Time-series RFID tag read events. Partitioned by `timestamp` via TimescaleDB.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK (composite with `timestamp`) | |
| `device_id` | UUID | NOT NULL, indexed | Source reader |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL, indexed | |
| `tag_id` | TEXT | NOT NULL, indexed | Application-facing identifier (defaults to `epc`); see [design/rfid-tag-data-model.md](design/rfid-tag-data-model.md) |
| `epc` | VARCHAR(256) | NULLABLE, indexed | Decoded EPC URI form |
| `epc_hex` | VARCHAR(128) | NULLABLE | Raw wire-format EPC hex |
| `epc_scheme` | VARCHAR(32) | NULLABLE | `sgtin-96` \| `sgtin-198` \| `sscc-96` \| `giai-96` \| `giai-202` \| `grai-96` \| `grai-170` \| `raw` |
| `epc_decoded` | JSONB | NULLABLE | Parsed parts: company_prefix, item_ref, serial, … |
| `tid` | VARCHAR(64) | NULLABLE, indexed | Factory-programmed Tag Identifier hex |
| `user_memory_hex` | TEXT | NULLABLE | Bank-11 raw hex (truncated to first 4 KB) |
| `tag_data` | JSONB | NULLABLE | Decoded user memory + inline sensor mirrors (e.g. `temperature_c`, `batch`, `expiry`) |
| `reader_antenna` | SMALLINT | NULLABLE | Antenna / port number, 0–255 |
| `timestamp` | TIMESTAMPTZ | NOT NULL, indexed | When the read occurred |
| `signal_strength` | FLOAT | NULLABLE | RSSI or dBm value |
| `latitude` | DOUBLE PRECISION | NULLABLE | WGS84 |
| `longitude` | DOUBLE PRECISION | NULLABLE | WGS84 |
| `location_accuracy_m` | DOUBLE PRECISION | NULLABLE | Reported accuracy in meters |
| `location_source` | VARCHAR(20) | NULLABLE | `gps` \| `fixed` \| `inferred` |
| `sensor_data` | JSONB | NULLABLE | Optional sensor payload |
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()` | When ingested |
| `tag_known` | BOOLEAN | NULLABLE | Sprint 50 / ADR 028. Three-valued: `NULL` = not yet classified; `TRUE` = EPC present in `tags` with `status ∈ {registered, active}`; `FALSE` = unknown / terminal-status / null-EPC read. Sole writer is the tag-registrar worker (`src/tagpulse/workers/tag_registrar_worker.py`); ingest hot path never reads or writes this column. |

**RLS:** Yes (migration 007)
**Migration:** 001, 003, 005, 016, 044

---

### tags

Per-`(tenant_id, epc_hex)` identity / ownership row (Sprint 50, [ADR 028](adr/028-tags-as-first-class-entity.md)). Operator-driven — created via CSV import, API, backfill, or transfer-in; **never** auto-created on first read.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL | |
| `epc_hex` | VARCHAR(128) | NOT NULL | Canonical uppercase hex, no separators. `ck_tags_epc_hex_format`: `^[0-9A-F]{16,128}$`. |
| `gs1_uri` | TEXT | NULLABLE | Denormalized lenient GS1 parse (e.g. `urn:epc:id:sgtin:…`); `NULL` for raw / proprietary / unparseable EPCs. Partial index `ix_tags_tenant_gs1_uri` covers the populated subset. |
| `status` | VARCHAR(16) | NOT NULL | `registered` \| `active` \| `retired` \| `defective` \| `transferred_out`. Transitions enforced in `tagpulse.services.tags`. |
| `source` | VARCHAR(16) | NOT NULL | `csv_import` \| `api` \| `backfill` \| `transfer_in`. (No `first_read` — ADR 028 OQ 3 resolution.) |
| `first_seen_at` | TIMESTAMPTZ | NULLABLE | First read after registration; written by registrar worker. |
| `last_seen_at` | TIMESTAMPTZ | NULLABLE | Most recent read; written by registrar worker. |
| `metadata` | JSONB | NULLABLE | Free-form operator metadata (not used by the platform). |
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL, auto | |

**Unique constraint:** `uq_tags_tenant_epc` `(tenant_id, epc_hex)`
**RLS:** Yes
**Migration:** 043

### tag_transfers

Cross-tenant transfer audit log (Sprint 50, [ADR 028](adr/028-tags-as-first-class-entity.md) §"Cross-tenant transfer"). Append-only — one row per EPC; all rows from one operator request share `request_id`. The row names both sides (`from_tenant_id`, `to_tenant_id`) and the RLS policy `tenant_isolation_tag_transfers` lets either side see it.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `request_id` | UUID | NOT NULL | Groups a batch of EPCs from one operator action. |
| `from_tenant_id` | UUID | FK → tenants.id, NOT NULL | |
| `to_tenant_id` | UUID | FK → tenants.id, NOT NULL | |
| `epc_hex` | VARCHAR(128) | NOT NULL | Same canonical form as `tags.epc_hex`. |
| `status` | VARCHAR(16) | NOT NULL | `requested` \| `completed` \| `failed`. Cross-column invariants enforced by `ck_tag_transfers_terminal_failure_reason` and `ck_tag_transfers_completed_at`. |
| `failure_reason` | TEXT | NULLABLE | NOT NULL when `status='failed'`. |
| `requested_by` | UUID | FK → users.id, NOT NULL | Initiating admin. |
| `requested_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |
| `completed_at` | TIMESTAMPTZ | NULLABLE | NOT NULL when `status='completed'`. |

**RLS:** Yes (both sides can read)
**Migration:** 043

### pending_bulk_operations

Two-person-approval staging table for tag bulk ops above the tenant's `tag_bulk_two_person_threshold` (Sprint 50 Phase C3, ADR 028 §Governance #4). Generic — `operation` discriminates (currently `tags.import`; `tags.bulk_patch` / `tags.bulk_retire` planned). Happy path: `pending → approved → executed`; deny path: `pending → rejected`; timeout: lazy sweep to `expired` on `approve`. The row stashes the raw CSV `payload` plus the dry-run `content_hash` so the approver verifies the stored bytes still match what the first admin previewed.

**Migration:** 047

---

### users

Individual user accounts within a tenant.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL, indexed | |
| `email` | VARCHAR(255) | NOT NULL | Unique per tenant |
| `name` | VARCHAR(255) | NOT NULL | |
| `role` | VARCHAR(50) | NOT NULL, default `'viewer'` | `admin`, `editor`, `viewer` |
| `status` | VARCHAR(20) | NOT NULL, default `'active'` | `active`, `inactive` |
| `api_key_hash` | VARCHAR(255) | NULLABLE | SHA-256 hash |
| `api_key_prefix` | VARCHAR(10) | NULLABLE | First 10 chars for lookup |
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |
| `last_login` | TIMESTAMPTZ | NULLABLE | |

**Unique constraint:** `(tenant_id, email)`
**Migration:** 014

---

### user_ui_prefs

Per-user Configurable-UI **presentation** overrides — the *user* layer of the `System → Tenant → Role → User` resolve ([adr/032-configurable-ui.md](adr/032-configurable-ui.md) §3). Sibling of `users` (same `user_id` PK grain), so — like `users` — it carries **no RLS**: the request path scopes by the globally-unique `user_id` PK, not the `app.current_tenant_id` GUC.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `user_id` | UUID | PK, FK → users.id (`ON DELETE CASCADE`) | One row per user. "Reset to team default" has two scopes: clear the whole layer (`PUT /ui-config/me` with `{}` → fall through to role/tenant/system for every leaf) or drop one leaf (`DELETE /ui-config/me/columns/{page}`, Sprint 63 — just that page re-inherits the floor). |
| `tenant_id` | UUID | FK → tenants.id (`ON DELETE CASCADE`), NOT NULL | Stored for audit + scoping, not isolation (the `user_id` PK already pins one user → one tenant). |
| `prefs` | JSONB | NOT NULL, default `'{}'` | The **sparse** per-leaf override (a subset of the ADR-032 §4 leaf document — missing keys fall through to the layer below). Written via `PUT /ui-config/me` (replace whole layer) or `PATCH /ui-config/me` (deep-merge a sparse body per leaf; lists replace wholesale — Sprint 63); validated against the §4 schema on every write. |
| `updated_at` | TIMESTAMPTZ | NOT NULL, default `now()` | Touched on every upsert. |

**Migration:** 052

---

### rules

User-defined automation rules evaluated against incoming telemetry.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL, indexed | |
| `name` | VARCHAR(255) | NOT NULL | |
| `description` | TEXT | NULLABLE | |
| `condition_type` | VARCHAR(50) | NOT NULL | 10 legacy values (`threshold`, `absence`, `rate_change`, `stock.*`, `zone.*`, `telemetry.threshold`) + 12 signaling values (`signaling.<event_type>.<trigger>`) per ADR-021 v2 |
| `condition_config` | JSONB | NOT NULL | Type-specific parameters (see below) |
| `action_type` | VARCHAR(50) | NOT NULL | `webhook`, `email`, `notification` |
| `action_config` | JSONB | NOT NULL | Type-specific parameters |
| `scope_device_id` | UUID | NULLABLE | Restrict to single device |
| `enabled` | BOOLEAN | NOT NULL, default `true` | |
| `event_type` | VARCHAR(32) | NULLABLE | ADR-021 v2: `location` / `geolocation` / `temperature` / `geofencing`. NULL = legacy rule. |
| `trigger` | VARCHAR(32) | NULLABLE | ADR-021 v2: `on_change` / `periodic` / `on_inactivity` / `on_inference` / `on_entry` / `on_exit`. Valid pairs constrained by `SIGNALING_VALID_PAIRS` (Pydantic + regex). |
| `processor` | VARCHAR(32) | NULLABLE | ADR-021 v2: `isolated_zones` / `overlapping_zones`. |
| `confidence_threshold` | NUMERIC(3,2) | NOT NULL, default `0.0` | ADR-021 v2: `0.0` (All) / `0.5` / `0.75`. |
| `category_ids` | UUID[] | NOT NULL, default `'{}'` | ADR-021 v2: empty = all categories. |
| `asset_label_filters` | JSONB | NULLABLE | ADR-021 v2: `[{key, value_in: [...]}]` AND-ed; per ADR-020 evaluation. |
| `zone_label_filters` | JSONB | NULLABLE | ADR-021 v2: same shape as `asset_label_filters`. |
| `site_label_filters` | JSONB | NULLABLE | ADR-021 v2: same shape as `asset_label_filters`. |
| `integration_ids` | UUID[] | NULLABLE | ADR-021 v2: empty/NULL = broadcast (legacy); populated = per-rule routing. |
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL, auto-updated | |

**RLS:** Yes (migration 007)
**Migration:** 006 (initial), 040 (Sprint 41 signaling-event columns + `idx_rules_signaling_active` partial index)
**Partial index:** `idx_rules_signaling_active ON rules (tenant_id, event_type, trigger) WHERE enabled = true AND event_type IS NOT NULL` — keeps the signaling-event evaluator hot path narrow.

#### Condition config shapes

**threshold:**
```json
{ "field": "signal_strength", "operator": "gt|lt|gte|lte|eq", "value": -50.0 }
```

**absence:**
```json
{ "tag_id": "TAG123", "minutes": 30 }
```

**rate_change:**
```json
{ "window_minutes": 60, "change_percent": 25.0 }
```

**signaling.\*.periodic** (cadence config; processor-specific config in `processor_config` per ADR-021 v2):
```json
{
  "cadence_minutes": 60,
  "processor_config": {
    "aggregation_window_s": 60,
    "min_rssi_dbm": -80,
    "aging_weight": 0.5
  }
}
```

---

### alerts (hypertable)

Triggered alert history. Partitioned by `triggered_at`.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK (composite with `triggered_at`) | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL, indexed | |
| `rule_id` | UUID | FK → rules.id, NOT NULL, indexed | Which rule triggered |
| `device_id` | UUID | NULLABLE | Which device triggered (if applicable) |
| `severity` | VARCHAR(20) | NOT NULL, default `'warning'` | `info`, `warning`, `critical` |
| `message` | TEXT | NOT NULL | Human-readable description |
| `context` | JSONB | NOT NULL | Snapshot of data that triggered the alert |
| `status` | VARCHAR(20) | NOT NULL, default `'open'` | `open`, `acknowledged` |
| `triggered_at` | TIMESTAMPTZ | NOT NULL, indexed | |

**RLS:** Yes (migration 007)
**Migration:** 006

---

### telemetry_models

Per-device-type metric schema definitions.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL, indexed | |
| `device_type` | VARCHAR(50) | NOT NULL | e.g. `rfid_reader` |
| `metrics` | JSONB | NOT NULL | Array of `MetricDefinition` objects |
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL, auto-updated | |

**RLS:** Yes (migration 007)
**Migration:** 004

#### MetricDefinition shape
```json
{ "name": "signal_strength", "unit": "dBm", "min_value": -100, "max_value": 0, "description": "..." }
```

---

### integrations

Outbound integration target configurations.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL, indexed | |
| `name` | VARCHAR(255) | NOT NULL | |
| `type` | VARCHAR(20) | NOT NULL | `webhook`, `sse`, `export` |
| `events` | JSONB | NOT NULL | List of subscribed event types |
| `config` | JSONB | NOT NULL | Type-specific config (URL, headers, etc.) |
| `enabled` | BOOLEAN | NOT NULL, default `true` | |
| `status` | VARCHAR(20) | NOT NULL, default `'active'` | `active`, `paused`, `failed` |
| `health_status` | VARCHAR(20) | NOT NULL, default `'unknown'` | `healthy`, `degraded`, `unhealthy`, `unknown` |
| `filters` | JSONB | NULLABLE | Event field filters (operator-based) |
| `enrichments` | JSONB | NULLABLE | Key-value enrichment fields |
| `consecutive_failures` | INTEGER | NOT NULL, default `0` | |
| `last_triggered` | TIMESTAMPTZ | NULLABLE | |
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL, auto-updated | |

**Migration:** 010, 011

---

### integration_deliveries

Delivery log for webhook and export attempts.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `integration_id` | UUID | FK → integrations.id, NOT NULL, indexed | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL, indexed | |
| `event_type` | VARCHAR(50) | NOT NULL | e.g. `tag_read.created` |
| `payload` | JSONB | NOT NULL | Full event payload sent |
| `status` | VARCHAR(20) | NOT NULL, default `'pending'` | `pending`, `delivered`, `failed`, `dead_letter` |
| `attempts` | INTEGER | NOT NULL, default `0` | |
| `last_attempt_at` | TIMESTAMPTZ | NULLABLE | |
| `response_code` | INTEGER | NULLABLE | HTTP status from target |
| `error_message` | TEXT | NULLABLE | |
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()`, indexed | |

**Migration:** 010

---

### analytics_results

Computed results from analytics modules.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL, indexed | |
| `module_name` | VARCHAR(100) | NOT NULL, indexed | e.g. `read_frequency` |
| `device_id` | UUID | NOT NULL, indexed | |
| `metric_name` | VARCHAR(100) | NOT NULL | e.g. `reads_per_minute`, `anomaly_flag` |
| `metric_value` | FLOAT | NOT NULL | |
| `computed_at` | TIMESTAMPTZ | NOT NULL, indexed | |

**RLS:** Yes (migration 009)
**Migration:** 008, 009

---

### tenant_usage_detail

Daily per-dimension usage counters for billing.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `tenant_id` | UUID | PK, FK → tenants.id | |
| `usage_date` | TIMESTAMPTZ | PK | Day bucket |
| `dimension` | VARCHAR(50) | PK | `ingestion`, `api_read`, `api_write`, `rule_evaluations`, `alerts_fired`, `webhook_deliveries`, `sse_connections` |
| `quantity` | BIGINT | NOT NULL, default `0` | |
| `unit` | VARCHAR(50) | NOT NULL | e.g. `requests`, `events`, `connections` |

**Migration:** 005

---

### tenant_quotas

Per-dimension usage limits per tenant.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `tenant_id` | UUID | PK, FK → tenants.id | |
| `dimension` | VARCHAR(50) | PK | Matches `tenant_usage_detail.dimension` |
| `max_quantity` | BIGINT | NOT NULL | |
| `period` | VARCHAR(20) | NOT NULL, default `'daily'` | `daily`, `monthly` |
| `action_on_exceed` | VARCHAR(20) | NOT NULL, default `'throttle'` | `throttle`, `reject`, `alert_only` |

**Migration:** 005

---

### dead_letter_events

Failed events that exhausted retry attempts.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `tenant_id` | UUID | NULLABLE | May be null if tenant couldn't be determined |
| `topic` | VARCHAR(50) | NOT NULL | EventBus topic |
| `payload` | JSONB | NOT NULL | Original event data |
| `error_message` | TEXT | NOT NULL | |
| `retry_count` | INTEGER | NOT NULL, default `0` | |
| `status` | VARCHAR(20) | NOT NULL, default `'pending'` | `pending`, `retried`, `abandoned` |
| `failed_at` | TIMESTAMPTZ | NOT NULL, default `now()`, indexed | |

**RLS:** Yes (migration 013)
**Migration:** 012, 013

---

### audit_logs

Configuration change audit trail.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL, indexed | |
| `user_id` | UUID | NULLABLE | Who made the change |
| `action` | VARCHAR(20) | NOT NULL | `create`, `update`, `delete` |
| `resource_type` | VARCHAR(50) | NOT NULL | e.g. `device`, `rule`, `integration` |
| `resource_id` | UUID | NOT NULL | |
| `changes` | JSONB | NULLABLE | Before/after diff |
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()`, indexed | |

**RLS:** Yes (migration 012)
**Migration:** 012, 015

---

### device_telemetry (hypertable — removed Sprint 21)

> **Sprint 21 update:** the back-compat `device_telemetry` view + the underlying `telemetry_readings_legacy_device` hypertable were dropped by [migration 032](../migrations/versions/032_drop_legacy_device_telemetry.py) (closes [ADR-015 §6](adr/015-telemetry-rules-and-deprecation.md)). All telemetry now lives in `telemetry_readings` (keyed on `(tenant_id, subject_kind, subject_id, metric_name, timestamp)`) accessed via `TimescaleTelemetryReadingsRepository`. The Sprint 14 device-shaped surface (`insert_reading` / `query` / `quarantine` / `list_quarantine`) is preserved on the same repository — callers swap the class without API changes. The columns below describe the **pre-Sprint 18** schema kept for historical reference.

Time-series sensor metric stream, decoupled from `tag_reads`. Partitioned by `timestamp`. See [design/telemetry-and-location.md](design/telemetry-and-location.md) and [design/subject-scoped-telemetry.md](design/subject-scoped-telemetry.md).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK (composite with `timestamp`) | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL | |
| `device_id` | UUID | FK → devices.id, NOT NULL | |
| `timestamp` | TIMESTAMPTZ | NOT NULL | When the metric was sampled |
| `metric_name` | VARCHAR(100) | NOT NULL | Must match a `MetricDefinition.name` |
| `metric_value` | DOUBLE PRECISION | NOT NULL | |
| `unit` | VARCHAR(20) | NULLABLE | Enriched from telemetry model when present |
| `metadata` | JSONB | NULLABLE | Free-form per-reading context |

**Index:** `(tenant_id, device_id, metric_name, timestamp DESC)`
**RLS:** Yes
**Migration:** 016

---

### telemetry_quarantine

> **Sprint 18 update:** [migration 030](../migrations/versions/030_subject_scoped_telemetry.py) added nullable `subject_kind` + `subject_id` columns. Legacy back-filled rows leave them NULL; multi-subject ingest (Sprint 19) populates them so reviewers see *what* the failed reading was meant to describe.

Readings rejected by validation (unknown metric, out of range, unit mismatch). Capped retention 7 d.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `tenant_id` | UUID | NOT NULL | |
| `device_id` | UUID | NOT NULL | |
| `received_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |
| `metric_name` | VARCHAR(100) | NOT NULL | |
| `metric_value` | DOUBLE PRECISION | NULLABLE | |
| `raw_payload` | JSONB | NOT NULL | |
| `reason` | VARCHAR(40) | NOT NULL | `unknown_metric` \| `out_of_range` \| `unit_mismatch` |

**RLS:** Yes
**Migration:** 016

---

### assets

The physical thing being tracked, distinct from the reader. See [design/assets-and-zones.md](design/assets-and-zones.md).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL | |
| `external_ref` | VARCHAR(255) | NULLABLE | ERP / WMS asset code. URL-unsafe characters rejected at the API layer (see [ADR-019](adr/019-categories.md), gap 2.8). |
| `name` | VARCHAR(255) | NOT NULL | |
| `category_id` | UUID | FK → categories.id, ON DELETE RESTRICT, NOT NULL | Sprint 34 (added nullable); promoted to `NOT NULL` and the legacy `asset_type` shadow column was dropped in Sprint 41 Phase H (migration `041`, [ADR-019](adr/019-categories.md) close-out). |
| `status` | VARCHAR(20) | NOT NULL, default `'active'` | `active` \| `retired` \| `lost` |
| `metadata` | JSONB | NULLABLE | |
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL, auto-updated | |

**Unique constraint:** `(tenant_id, external_ref)`
**RLS:** Yes
**Migration:** 017 (base table); 037 adds `category_id`; 041 promotes `category_id` to `NOT NULL` and drops `asset_type`

---

### asset_tag_bindings

Historical mapping of RFID tag IDs to assets. Bindings carry an open or closed lifetime. The `tag_id` value may be the EPC or the TID depending on `binding_kind` — see [design/rfid-tag-data-model.md](design/rfid-tag-data-model.md).

> **Naming note.** This table is new in Sprint 15 and ships with the column named `binding_value` from day one (see roadmap Sprint 15). The legacy `tag_id` examples below predate that decision; treat them as `binding_value` in the actual schema. The `binding_kind='device'` extension and the `external_locations` table land in the same sprint per [design/mobile-carriers-and-manifests.md §10](design/mobile-carriers-and-manifests.md).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `asset_id` | UUID | FK → assets.id, ON DELETE CASCADE | |
| `tag_id` | VARCHAR(256) | NOT NULL | EPC URI or TID hex per `binding_kind` |
| `binding_kind` | VARCHAR(8) | NOT NULL, default `'epc'` | `epc` \| `tid` |
| `bound_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |
| `unbound_at` | TIMESTAMPTZ | NULLABLE | NULL = currently active |
| `tenant_id` | UUID | NOT NULL | Denormalized for RLS |

**PK:** `(asset_id, tag_id, bound_at)`
**Partial unique index:** `(tenant_id, binding_kind, tag_id) WHERE unbound_at IS NULL` — a tag can have at most one active binding per tenant per kind
**RLS:** Yes
**Migration:** 017

---

### sites

Physical locations or mobile carriers (Sprint 15; Sprint 34 added kind + geolocation + structured address as gap 2.7 of the [reference-design remediation plan](design/reference-design-remediation.md)).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL | |
| `name` | VARCHAR(255) | NOT NULL | |
| `kind` | VARCHAR(16) | NOT NULL, default `'site'`, CHECK | One of `site` \| `transporter`. Drives the Site/Transporter icon column in the Locations UI. Mutable (a parked transporter can be reclassified). |
| `address` | TEXT | NULLABLE | **Deprecated free-form fallback.** Retained this release as a compatibility shadow; new code should write the structured columns below and may also mirror the formatted string here. |
| `street_line1` | VARCHAR(255) | NULLABLE | Structured address (gap 2.7). |
| `street_line2` | VARCHAR(255) | NULLABLE | |
| `city` | VARCHAR(128) | NULLABLE | |
| `region` | VARCHAR(128) | NULLABLE | State / province / county. |
| `postal_code` | VARCHAR(32) | NULLABLE | |
| `country` | CHAR(2) | NULLABLE, CHECK `~ '^[A-Z]{2}$'` | ISO 3166-1 alpha-2; API normalises to uppercase. |
| `latitude` | DOUBLE PRECISION | NULLABLE, CHECK `-90..90` | Both-or-neither with `longitude` (DB CHECK `ck_sites_latlon_paired`). |
| `longitude` | DOUBLE PRECISION | NULLABLE, CHECK `-180..180` | |
| `default_timezone` | VARCHAR(64) | NOT NULL, default `'UTC'` | IANA tz |
| `coord_system` | JSONB | NULLABLE | Sprint 64 ([ADR-024](adr/024-position-estimation.md)): floor coordinate frame — `units` (`meters`\|`feet`), `extent_x`/`extent_y`, `origin_anchor`, `rotation_deg`, optional `geo_anchor` (the unified-overlay seam) and optional `floorplan_image` (inline `data:` URL or `https://`, ≤~2 MB). **NULL ⇒ geographic-only** (lat/lon); set ⇒ the site renders as a floor plan. |
| `metadata` | JSONB | NULLABLE | |
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |

**Unique constraint:** `(tenant_id, name)`
**RLS:** Yes
**Migrations:** 017 (base), 038 (kind + geolocation + structured address — Sprint 34 gap 2.7), 051 (coord_system)

---

### categories

First-class tenant-scoped categorisation for `assets` (Sprint 34, [ADR-019](adr/019-categories.md)). Replaces the free-form `assets.asset_type` string. Carries behavioural metadata (`category_type`, `required_tags`) that downstream Signaling Events (ADR 021) scope themselves against. Cannot be deleted while any asset references it (`FK ON DELETE RESTRICT`).

> **Terminology.** TagPulse uses **`required_tags`** rather than any vendor-specific term — see [§"Where is the tag?"](#where-is-the-tag-and-why-theres-no-tags-table) for the why.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL | |
| `name` | VARCHAR(255) | NOT NULL | |
| `sku_upc` | VARCHAR(64) | NULLABLE | Optional SKU / UPC for catalogue lookup. |
| `description` | TEXT | NULLABLE | |
| `category_type` | VARCHAR(32) | NOT NULL, CHECK | One of `liquid_container` \| `reference_tag` \| `rti_container` \| `object`. **Immutable after create** (API-enforced, 400 on attempted PATCH). |
| `required_tags` | SMALLINT | NOT NULL, default `1`, CHECK `>= 1` | Operator-set; UI suggests a per-type default. |
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |

**Unique constraint:** `(tenant_id, name)`
**CHECK constraints:** `ck_categories_type`, `ck_categories_required_tags_positive`
**RLS:** Yes (`tenant_isolation_categories`)
**Migration:** 037

---

### zones

Reader-bound or geofence zones inside a site.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL | |
| `site_id` | UUID | FK → sites.id, ON DELETE CASCADE, NOT NULL | |
| `name` | VARCHAR(255) | NOT NULL | |
| `kind` | VARCHAR(20) | NOT NULL | `reader_bound` \| `geofence` |
| `fixed_reader_ids` | JSONB | NULLABLE | Array of device UUIDs (reader_bound only) |
| `polygon_geojson` | JSONB | NULLABLE | GeoJSON Polygon (geofence only) |
| `bbox_min_lat` | DOUBLE PRECISION | NULLABLE | (Sprint 17a) Bbox prefilter |
| `bbox_max_lat` | DOUBLE PRECISION | NULLABLE | (Sprint 17a) |
| `bbox_min_lon` | DOUBLE PRECISION | NULLABLE | (Sprint 17a) |
| `bbox_max_lon` | DOUBLE PRECISION | NULLABLE | (Sprint 17a) |
| `metadata` | JSONB | NULLABLE | |
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL, auto-updated | |

**Unique constraint:** `(site_id, name)`
**Check constraint:** `(kind='reader_bound' AND fixed_reader_ids IS NOT NULL) OR (kind='geofence' AND polygon_geojson IS NOT NULL)`
**RLS:** Yes
**Migration:** 017, 026 (polygon + bbox columns)

> **Floor-polygon zones (Sprint 64).** On a site whose `coord_system` is set, a
> zone's `polygon_geojson` is interpreted as **floor `(x, y)`** coordinates (not
> lat/lon). The coordinate-agnostic `point_in_polygon` engine resolves a fixed
> read to its floor zone by testing the read's antenna position — the *accurate*
> path, preferred over the coarse `reader_bound` membership. No new column or
> `kind`; a zone on a floor-site simply *is* a floor polygon.

---

### antennas

Per-antenna position within a device's site coordinate frame (Sprint 59 schema; Sprint 64 API). **Port 0 is the reader's nominal location**; ports 1..N are individual radiators. No `tenant_id` — isolation flows through the `device_id` FK (devices are tenant-scoped).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `device_id` | UUID | FK → devices.id, ON DELETE CASCADE, NOT NULL | |
| `port` | SMALLINT | NOT NULL | Antenna port (matches `tag_reads.reader_antenna`, 0–255). Port 0 = reader nominal location. |
| `x` | NUMERIC | NULLABLE | Floor-frame X (in the site `coord_system` units). |
| `y` | NUMERIC | NULLABLE | Floor-frame Y. |
| `z` | NUMERIC | NULLABLE | Mount height (optional; estimator-only). |
| `label` | VARCHAR(64) | NULLABLE | |
| `gain_dbi` | NUMERIC | NULLABLE | |

**Unique constraint:** `(device_id, port)`
**RLS:** None (via `device_id` FK)
**API:** `GET/PUT/DELETE /devices/{device_id}/antennas[/{port}]` (viewer read / admin write)
**Migration:** 051

---

### asset_current_location (view)

Latest known position per active binding, **frame-aware** (Sprint 69 A1 —
[migration 056](../migrations/versions/056_frame_aware_current_location.py); the
Sprint 15 view was geographic-only). Picks the newer of the latest geo fix vs the
latest floor `(x, y)` fix, and reports a **true `last_seen_at`** from the newest
read of any kind (so a fixed-reader/floor asset isn't "never seen").

| Column | Type | Notes |
|--------|------|-------|
| `tenant_id` | UUID | |
| `asset_id` | UUID | |
| `last_seen_at` | TIMESTAMPTZ | Newest `tag_read` for any active binding, **regardless of lat/lon** (NULL only if never read) |
| `kind` | TEXT | `geo` \| `floor` \| `none` — which frame the current position is in |
| `recorded_at` | TIMESTAMPTZ | The chosen position's time (NULL when `kind='none'`) |
| `latitude` / `longitude` | DOUBLE PRECISION | Geo frame only (`kind='geo'`) |
| `accuracy_meters` | FLOAT | Geo frame only |
| `x` / `y` | NUMERIC | Floor frame only (`kind='floor'`), from `asset_positions` |
| `site_id` | UUID | Floor frame only |
| `device_id` | UUID | Geo fix's reader, else the last-seen reader |
| `latest_position_source` | TEXT | `rfid` \| `external`/vendor \| `computed` \| `precomputed` (NULL when `kind='none'`) |

Base set = union of assets with any read, geo fix, or floor fix, so a
read-but-unpositioned asset still appears (`kind='none'`, populated
`last_seen_at`). **Inherits RLS** from the underlying tables.

---

### asset_positions (hypertable)

Per-asset indoor `(x, y)` position fixes in a site's floor `coord_system`. Landed
headless in Sprint 59 ([migration 051](../migrations/versions/051_spatial_foundation.py),
[ADR-024](adr/024-position-estimation.md)). **Sprint 65 (Phase 1) added the first
writer**: `source='precomputed'` rows via `POST /assets/{id}/position` (BYO — an
external location engine pushes a resolved floor fix). **Sprint 66 (Phase 2)
added the `computed` writer** — the homegrown `rssi_weighted_centroid` estimator
worker, **off by default** (`position_estimator_enabled`). The `zone` retrieval
fallback remains deferred ([floor-position-estimation.md](design/floor-position-estimation.md)).
Zone-level "where is X" is still answered from `tag_presence` + `subject_current_zone`.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK (with `time`), default `gen_random_uuid()` | |
| `time` | TIMESTAMPTZ | NOT NULL, hypertable partition key | Fix timestamp (`recorded_at`; server `now()` if the writer omits it). |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL | RLS-scoped (`tenant_isolation_asset_positions`); always server-stamped. |
| `asset_id` | UUID | NOT NULL, **no FK** | Hypertable — matches the ADR-013/014 no-FK convention (cf. `external_locations`). |
| `site_id` | UUID | NOT NULL | The site whose `coord_system` frames `(x, y)`; validated to belong to the tenant on write (422 otherwise). |
| `x` | NUMERIC | NOT NULL | Floor-frame X (site `coord_system` units). |
| `y` | NUMERIC | NOT NULL | Floor-frame Y. |
| `z` | NUMERIC | NULLABLE | Height (optional). |
| `confidence` | NUMERIC(3,2) | NOT NULL, `BETWEEN 0 AND 1` | Fix confidence (vendor-supplied for `precomputed`; estimator-scored for `computed`). |
| `source` | VARCHAR(16) | NOT NULL, `IN ('precomputed','zone','computed')` | Origin of the fix. `precomputed` (Sprint 65 BYO) + `computed` (Sprint 66 estimator, off by default); `zone` deferred. |
| `metadata` | JSONB | NULLABLE | Free-form per-fix detail. |

**Indexes:** `ix_asset_positions_by_asset` (`tenant_id, asset_id, time DESC`), `ix_asset_positions_by_site` (`tenant_id, site_id, time DESC`).
**RLS:** enabled — `tenant_id = current_setting('app.current_tenant_id')`.
**API:** `POST /assets/{id}/position` (admin/editor write, `source='precomputed'`), `GET /assets/{id}/floor-path` (viewer read, ascending time, `since`/`until`/`source`/`limit` filters) — Sprint 65.
**Migration:** 051 (spatial foundation).

---

### asset_state_history (hypertable)

One **fused per-asset state snapshot** per consolidation tick (Sprint 71,
[migration 058](../migrations/versions/058_asset_state_consolidation.py),
[ADR-034](adr/034-asset-state-consolidation.md)). The consolidation worker
(**off by default** — `consolidation_enabled`) fuses an asset's bound-tag reads
over the look-back window into one **location** answer (a `read_count × recency`-weighted
zone *vote* — `frame` + `zone_id`/`site_id` + position) and one **environment**
answer (a `read_count × recency`-weighted *mean* of temperature/humidity). "Is" =
latest row per asset; "was" = range query. Frames are mostly temporally exclusive
(`reader`/`floor`/`geo`/`none`); a `frame` change between ticks emits a
`Topic.ASSET_CUSTODY_CHANGED` custody event.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK (with `time`), default `gen_random_uuid()` | |
| `time` | TIMESTAMPTZ | NOT NULL, hypertable partition key | Tick time (server-ingest frame). |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL | RLS-scoped (`tenant_isolation_asset_state_history`); always server-stamped. |
| `asset_id` | UUID | NOT NULL, **no FK** | Hypertable — matches the ADR-013/014 no-FK convention (cf. `asset_positions`). |
| `frame` | VARCHAR(16) | NOT NULL, `IN ('reader','floor','geo','none')` | Resolution frame of the voted location. `geo` with NULL `zone_id` = "in transit". |
| `zone_id` | UUID | NULLABLE, **no FK** | Voted zone (`reader`/`floor`/geofence); NULL for a zoneless geo fix. |
| `site_id` | UUID | NULLABLE, **no FK** | Owning site of the voted zone. |
| `lat` / `lon` | DOUBLE PRECISION | NULLABLE | Weighted-centroid GPS fix (geo frame). |
| `x` / `y` | DOUBLE PRECISION | NULLABLE | Weighted-centroid floor position (floor frame; Phase 2). |
| `temperature_c` | DOUBLE PRECISION | NULLABLE | Weighted-mean temperature. |
| `humidity_pct` | DOUBLE PRECISION | NULLABLE | Weighted-mean humidity. |
| `sample_count` | INTEGER | NOT NULL, default 0 | Reads that fed this tick. |
| `tag_count` | INTEGER | NOT NULL, default 0 | Distinct bound tags that contributed. |
| `confidence` | DOUBLE PRECISION | NULLABLE, `NULL OR BETWEEN 0 AND 1` | Location share × mean freshness. |

**Index:** `ix_asset_state_history_by_asset` (`tenant_id, asset_id, time DESC`).
**RLS:** enabled — `tenant_id = current_setting('app.current_tenant_id')`.
**API:** `GET /assets/{id}/state` (viewer read, latest snapshot — "is"), `GET /assets/{id}/state/history` (viewer read, newest-first, `since`/`limit` — "was") — Sprint 71.
**Migration:** 058 (asset state consolidation).

---

### asset_legs

Transit **legs** — the `geo`-frame interval between two facility frames (Sprint 72,
[migration 059](../migrations/versions/059_asset_legs.py), [ADR-034](adr/034-asset-state-consolidation.md)
Phase 2). Opened/closed by the `AssetLegTracker` from Phase-1 `ASSET_CUSTODY_CHANGED`
events (open on `facility → geo`, close on `… → facility`); the env envelope +
cold-chain SLA are computed on close from `asset_state_history` over the leg window
per the tenant's `fusion_strategy.sla`. Regular tenant-scoped table (not a
hypertable — low cardinality); **off by default** (`consolidation_enabled`).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK, default `gen_random_uuid()` | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL | RLS-scoped (`tenant_isolation_asset_legs`). |
| `asset_id` | UUID | NOT NULL, **no FK** | Matches the ADR-013/014 no-FK convention. |
| `status` | VARCHAR(8) | NOT NULL, `IN ('open','closed')` | `open` = in transit; `closed` = arrived. |
| `origin_zone_id` / `origin_site_id` | UUID | NULLABLE | Facility departed (from the custody event's `from_*`). |
| `dest_zone_id` / `dest_site_id` | UUID | NULLABLE | Facility arrived (null while open). |
| `departed_at` | TIMESTAMPTZ | NOT NULL | Leg start (the `facility → geo` event). |
| `arrived_at` | TIMESTAMPTZ | NULLABLE | Leg end (null while open). |
| `last_lat` / `last_lon` | DOUBLE PRECISION | NULLABLE | Reserved for the in-transit fix (live fix served from `/state` in v1). |
| `temp_min_c` / `temp_max_c` / `temp_mean_c` | DOUBLE PRECISION | NULLABLE | Leg temperature envelope (on close). |
| `humidity_min` / `humidity_max` | DOUBLE PRECISION | NULLABLE | Leg humidity envelope (on close). |
| `excursion_s` | INTEGER | NULLABLE | Longest contiguous out-of-SLA run (seconds). |
| `in_range_pct` | DOUBLE PRECISION | NULLABLE | Share of leg samples within the SLA envelope. |
| `sla_breached` | BOOLEAN | NULLABLE | `excursion_s` > `excursion_tolerance_s` (null = no SLA configured). |

**Indexes:** `ix_asset_legs_by_asset` (`tenant_id, asset_id, departed_at DESC`); `ix_asset_legs_open` partial-unique (`tenant_id, asset_id) WHERE status='open'` (one open leg per asset).
**RLS:** enabled — `tenant_id = current_setting('app.current_tenant_id')`.
**API:** `GET /assets/{id}/legs` (viewer read, newest-first, `status`/`limit`); the **open** leg is also attached to `GET /assets/{id}/state` as `open_leg` — Sprint 72.
**Migration:** 059 (asset legs).

---

### products

SKU catalog — the **inventory** domain layer's anchor entity. See [design/tracking-modes.md](design/tracking-modes.md).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL | |
| `sku` | VARCHAR(64) | NOT NULL | Tenant-unique |
| `gtin` | VARCHAR(14) | NULLABLE | GS1 GTIN-14; used to auto-bind SGTIN reads |
| `name` | VARCHAR(255) | NOT NULL | |
| `category` | VARCHAR(64) | NULLABLE | |
| `unit` | VARCHAR(20) | NOT NULL, default `'each'` | `each` \| `case` \| `pallet` |
| `attributes` | JSONB | NULLABLE | |
| `created_at` / `updated_at` | TIMESTAMPTZ | | |

**Unique constraint:** `(tenant_id, sku)`
**RLS:** Yes
**Migration:** 020

---

### lots

Production batch / expiration grouping under a product.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL | |
| `product_id` | UUID | FK → products.id, NOT NULL | |
| `lot_code` | VARCHAR(64) | NOT NULL | Manufacturer batch code |
| `manufactured_at` | TIMESTAMPTZ | NULLABLE | |
| `expires_at` | TIMESTAMPTZ | NULLABLE | Drives `stock.expiring_within` rules |
| `metadata` | JSONB | NULLABLE | |

**Unique constraint:** `(tenant_id, product_id, lot_code)`
**RLS:** Yes
**Migration:** 020

---

### stock_items

Per-tag inventory unit. One row per RFID-tagged item; auto-created by ingestion when an SGTIN read matches a registered product.

> **Naming note.** This table is new in Sprint 15b and ships with the column named `binding_value` from day one (see roadmap Sprint 15b). The legacy `tag_id` examples below predate that decision; treat them as `binding_value` in the actual schema.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL | |
| `product_id` | UUID | FK → products.id, NOT NULL | |
| `lot_id` | UUID | FK → lots.id, NULLABLE | NULL when product is not lot-tracked |
| `tag_id` | VARCHAR(256) | NOT NULL | Typically EPC URI (SGTIN) |
| `binding_kind` | VARCHAR(8) | NOT NULL, default `'epc'` | `epc` \| `tid` |
| `state` | VARCHAR(20) | NOT NULL, default `'in_stock'` | `in_stock` \| `in_transit` \| `consumed` \| `expired` \| `lost` |
| `current_zone_id` | UUID | NULLABLE | Maintained by ingestion |
| `first_seen_at` | TIMESTAMPTZ | NOT NULL | |
| `last_seen_at` | TIMESTAMPTZ | NOT NULL | |
| `consumed_at` | TIMESTAMPTZ | NULLABLE | Set when state → consumed |
| `parent_stock_item_id` | UUID | FK → stock_items.id, NULLABLE | Self-FK for SSCC → SGTIN containment (case/pallet hierarchy). Per [design/mobile-carriers-and-manifests.md](design/mobile-carriers-and-manifests.md). |

**Partial unique:** `(tenant_id, binding_kind, tag_id) WHERE state NOT IN ('consumed','expired','lost')`
**Index:** `(tenant_id, product_id, lot_id, current_zone_id)`; partial `ix_stock_items_parent` on `(tenant_id, parent_stock_item_id) WHERE parent_stock_item_id IS NOT NULL`
**RLS:** Yes
**Migration:** 020, 028 (parent_stock_item_id)

---

### stock_movements (hypertable)

Append-only ledger of inventory movements. Partitioned by `occurred_at`.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK (composite with `occurred_at`) | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL | |
| `stock_item_id` | UUID | FK → stock_items.id, NOT NULL | |
| `from_zone_id` | UUID | NULLABLE | NULL = first appearance |
| `to_zone_id` | UUID | NULLABLE | NULL = exit (consumed/lost) |
| `movement_type` | VARCHAR(20) | NOT NULL | `enter` \| `exit` \| `transfer` \| `consume` |
| `quantity` | INTEGER | NOT NULL, default `1` | Reserved for future case/pallet aggregation |
| `device_id` | UUID | NULLABLE | Source reader |
| `occurred_at` | TIMESTAMPTZ | NOT NULL, indexed | |

**RLS:** Yes
**Migration:** 020

---

### stock_levels (view)

Live count of in-stock items per (product, lot, zone). Defined in [design/tracking-modes.md](design/tracking-modes.md) §4.5.

| Column | Type | Notes |
|--------|------|-------|
| `tenant_id` | UUID | |
| `product_id` | UUID | |
| `lot_id` | UUID | NULL allowed |
| `current_zone_id` | UUID | NULL = unzoned |
| `quantity` | BIGINT | COUNT of `stock_items` where `state='in_stock'` |

**Inherits RLS** from underlying tables.

---

### external_locations (hypertable)

Positions pushed by external systems (TMS adapters, mobile-carrier apps) for assets without an onboard reader. Partitioned by `recorded_at`. UNION'd with reader-derived positions in the `asset_current_location` view (Sprint 15 Phase C).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK (composite with `recorded_at`) | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL | |
| `asset_id` | UUID | FK → assets.id, NOT NULL, indexed | |
| `recorded_at` | TIMESTAMPTZ | NOT NULL, indexed | When the position was sampled at the source |
| `received_at` | TIMESTAMPTZ | NOT NULL, default `now()` | When TagPulse ingested it |
| `latitude` | DOUBLE PRECISION | NOT NULL | WGS84 |
| `longitude` | DOUBLE PRECISION | NOT NULL | WGS84 |
| `accuracy_m` | DOUBLE PRECISION | NULLABLE | |
| `source` | VARCHAR(64) | NOT NULL | Adapter / vendor identifier (e.g. `samsara`, `geotab`) |
| `metadata` | JSONB | NULLABLE | |

**RLS:** Yes
**Migration:** 019

---

### tag_data_mappings

Per-tenant / per-scope (device-type or product) mappings from `tag_reads.tag_data` keys to semantic fields (`lot`, `expiry`, `batch`, `mfg_date`, `serial`, …). Drives ingestion-time enrichment without code changes. Per [design/tracking-modes.md §11](design/tracking-modes.md).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL | |
| `scope_kind` | VARCHAR(20) | NOT NULL | `tenant` \| `device_type` \| `product` |
| `scope_id` | TEXT | NULLABLE | NULL when `scope_kind='tenant'`; device_type string or product UUID otherwise |
| `tag_data_key` | VARCHAR(64) | NOT NULL | Key inside `tag_reads.tag_data` JSONB |
| `semantic_field` | VARCHAR(32) | NOT NULL | `lot` \| `expiry` \| `batch` \| `mfg_date` \| `serial` |
| `value_format` | VARCHAR(32) | NULLABLE | Optional parser hint (e.g. `iso8601`, `yyyymmdd`) |

**Check constraint:** `ck_tag_data_mappings_scope_kind` (scope_kind enum)
**Check constraint:** `ck_tag_data_mappings_scope_id_consistency` (scope_id NULL iff scope_kind='tenant')
**Unique constraint:** `uq_tag_data_mappings_scope_field` `(tenant_id, scope_kind, scope_id, semantic_field)`
**RLS:** Yes
**Migration:** 020

---

### subject_current_zone

Durable dwell-tracker state. One row per `(tenant_id, subject_kind, subject_id)`; upserted on every `subject.zone_changed` event. Replaces the in-process `DwellTracker._state` map so `zone.dwell_exceeded` rules survive worker restarts and work in multi-worker deployments. Per Sprint 17a §5.2.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `tenant_id` | UUID | FK → tenants.id ON DELETE CASCADE, NOT NULL | |
| `subject_kind` | VARCHAR(32) | NOT NULL | `asset` \| `stock_item` \| `device` |
| `subject_id` | UUID | NOT NULL | |
| `zone_id` | UUID | NULLABLE | NULL = subject not currently in any zone |
| `zone_kind` | VARCHAR(32) | NULLABLE | `reader_bound` \| `geofence` |
| `entered_at` | TIMESTAMPTZ | NOT NULL | When the subject entered the current zone |
| `updated_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |

**PK:** `(tenant_id, subject_kind, subject_id)`
**Partial index:** `ix_subject_current_zone_zone` on `(tenant_id, zone_id) WHERE zone_id IS NOT NULL`
**RLS:** Yes
**Migration:** 027

---

## API Schemas (Pydantic)

All schemas are defined in `src/tagpulse/models/` and enforce validation at the API boundary.

### Tag Reads — `schemas.py`

| Schema | Purpose | Key Fields |
|--------|---------|------------|
| `TagReadCreate` | Ingest request (HTTP + MQTT) | `device_id` (UUID), `tag_id` (str, 1-256), `timestamp`, `signal_strength?`, `sensor_data?`, `reader_antenna?` (0–255), `location?`, `identity?` |
| `TagReadResponse` | API response | All DB fields **+ `location`** (Sprint 64 query-time descriptor) |
| `LocationDescriptor` | Resolved location on `TagReadResponse.location` | `kind` (`geo`\|`floor`\|`none`), `lat?`, `lon?`, `accuracy_m?`, `source?`, `zone_id?`, `zone_name?`. `geo` = mobile read lat/lon; `floor` = resolved zone (floor-polygon, else `reader_bound`); `none` = unresolved. |

### Devices — `schemas.py`

| Schema | Purpose | Key Fields |
|--------|---------|------------|
| `DeviceCreate` | Register device | `name` (1-255), `device_type?`, `metadata?`, `configuration?`, `firmware_version?`, `site_id?` |
| `DeviceUpdate` | Partial update | All fields optional (incl. `site_id` — set to assign, null to clear) |
| `DeviceResponse` | API response | All DB fields (incl. `mobility`, `site_id`) |
| `DeviceStatusUpdate` | MQTT status message | `connection_state`, `firmware_version?` |

### Floor positions — `schemas.py`

| Schema | Purpose | Key Fields |
|--------|---------|------------|
| `FloorPositionCreate` | `POST /assets/{id}/position` body — BYO precomputed `(x, y)` (Sprint 65) | `site_id` (UUID, tenant-validated → 422 if foreign), `x`, `y` (float, finite — `allow_inf_nan=False`), `z?`, `confidence` (0–1), `recorded_at?` (server `now()` when omitted), `metadata?`. Persisted with `source='precomputed'`. |
| `FloorPositionResponse` | `POST /assets/{id}/position` result **and** each `GET /assets/{id}/floor-path` point | `id`, `tenant_id`, `asset_id`, `site_id`, `recorded_at`, `x`, `y`, `z?`, `confidence`, `source`, `metadata?` |

### Spatial — `schemas.py` (Sprint 64)

| Schema | Purpose | Key Fields |
|--------|---------|------------|
| `CoordSystem` | A site's floor frame (`SiteCreate/Update/Response.coord_system`) | `units` (`meters`\|`feet`), `extent_x>0`, `extent_y>0`, `origin_anchor` (`nw_corner`\|`sw_corner`\|`device_id`), `origin_device_id?`, `rotation_deg` (-360..360), `geo_anchor?`, `floorplan_image?`. `extra="forbid"`. |
| `GeoAnchor` | Pin a floor point to lat/lon (seam) | `lat` (-90..90), `lng` (-180..180), `x`, `y` |
| `AntennaUpsert` | `PUT /devices/{id}/antennas/{port}` body | `x?`, `y?`, `z?`, `label?`, `gain_dbi?`. `extra="forbid"` |
| `AntennaResponse` | Antenna API response | `id`, `device_id`, `port` (0–255), `x?`, `y?`, `z?`, `label?`, `gain_dbi?` |

### Telemetry Models — `schemas.py`

| Schema | Purpose | Key Fields |
|--------|---------|------------|
| `MetricDefinition` | Single metric spec | `name`, `unit`, `min_value?`, `max_value?`, `description?` |
| `TelemetryModelCreate` | Create model | `device_type`, `metrics` (list, min 1) |
| `TelemetryModelResponse` | API response | All DB fields |

### Query / Aggregations — `schemas.py`

| Schema | Purpose | Key Fields |
|--------|---------|------------|
| `ReadsPerHour` | Read count per time bucket (default hourly; bucket width set by the endpoint's `bucket_minutes`) | `bucket`, `device_id`, `read_count` |
| `UniqueTagsPerWindow` | Unique tags per window | `bucket`, `device_id`, `unique_tags` |
| `DeviceHealthSummary` | Health snapshot | `device_id`, `name`, `status`, `connection_state`, `last_seen`, `reads_last_hour`, `error_rate` |

### Rules & Alerts — `rule_schemas.py`

| Schema | Purpose | Key Fields |
|--------|---------|------------|
| `ThresholdCondition` | Threshold config | `field`, `operator` (gt/lt/gte/lte/eq), `value` |
| `AbsenceCondition` | Absence config | `tag_id?`, `minutes` |
| `RateChangeCondition` | Rate change config | `window_minutes`, `change_percent` |
| `RuleCreate` | Create rule | `name`, `condition_type`, `condition_config`, `action_type`, `action_config`, `scope_device_id?`, `enabled?` |
| `RuleUpdate` | Partial update | All fields optional |
| `RuleResponse` | API response | All DB fields |
| `AlertResponse` | API response | All DB fields |

### Tenants — `tenant_schemas.py`

| Schema | Purpose | Key Fields |
|--------|---------|------------|
| `TenantCreate` | Create tenant | `name`, `slug` (lowercase, alphanumeric + hyphens), `plan?` |
| `TenantResponse` | API response | id, name, slug, plan, status, created_at |
| `UsageRecord` | Daily usage row | `tenant_id`, `usage_date`, `dimension`, `quantity`, `unit` |
| `UsageSummary` | Aggregated usage | `tenant_id`, `dimension`, `total_quantity`, `unit` |

### Integrations — `integration_schemas.py`

| Schema | Purpose | Key Fields |
|--------|---------|------------|
| `IntegrationCreate` | Create target | `name`, `type` (webhook/sse/export), `events` (list), `config`, `filters?`, `enrichments?` |
| `IntegrationUpdate` | Partial update | All fields optional |
| `IntegrationResponse` | API response | All DB fields |
| `DeliveryResponse` | Delivery log entry | `id`, `integration_id`, `event_type`, `status`, `attempts`, `response_code?`, `error_message?` |

### Users — `user_schemas.py`

| Schema | Purpose | Key Fields |
|--------|---------|------------|
| `UserCreate` | Create user | `email`, `name`, `role` (admin/editor/viewer) |
| `UserUpdate` | Partial update | `name?`, `role?`, `status?` |
| `UserResponse` | API response | All DB fields (excluding key hash) |
| `ApiKeyResponse` | One-time key reveal | `api_key`, `prefix`, `message` |

---

## Migrations

| # | File | Tables Affected | Description |
|---|------|-----------------|-------------|
| 001 | `001_initial_schema.py` | `devices`, `tag_reads` | Initial tables + hypertable |
| 002 | `002_device_config_status.py` | `devices` | Add `configuration`, `connection_state`, `firmware_version`, `last_seen` |
| 003 | `003_tag_reads_device_fk.py` | `tag_reads` | Device FK relationship |
| 004 | `004_telemetry_models.py` | `telemetry_models` | Per-device-type metric schemas |
| 005 | `005_multi_tenancy.py` | `tenants`, `devices`, `tag_reads`, `tenant_usage_detail`, `tenant_quotas` | Multi-tenancy + tenant_id FKs |
| 006 | `006_rules_alerts.py` | `rules`, `alerts` | Rules engine + alert history |
| 007 | `007_telemetry_tenant_rls.py` | `devices`, `tag_reads`, `rules`, `alerts`, `telemetry_models` | RLS policies |
| 008 | `008_analytics_results.py` | `analytics_results` | Analytics module output |
| 009 | `009_analytics_rls.py` | `analytics_results` | RLS policy |
| 010 | `010_integrations.py` | `integrations`, `integration_deliveries` | Integration targets + delivery log |
| 011 | `011_integration_health_enrichments.py` | `integrations` | Add health_status, filters, enrichments |
| 012 | `012_dead_letter_audit_logs.py` | `dead_letter_events`, `audit_logs` | Dead letter + audit trail + RLS |
| 013 | `013_dead_letter_rls.py` | `dead_letter_events` | RLS policy |
| 014 | `014_users_provisioning.py` | `users`, `tenants` | Users table + provisioning keys on tenants |
| 015 | `015_audit_user_id.py` | `audit_logs` | Add `user_id` column |
| 016 | `016_telemetry_location_rfid.py` | `tag_reads`, `device_telemetry`, `telemetry_quarantine` | Sprint 14: location columns on `tag_reads` (`latitude`, `longitude`, `location_accuracy_m`, `location_source`); RFID identity columns (`epc`, `epc_hex`, `epc_scheme`, `epc_decoded`, `tid`, `user_memory_hex`, `tag_data`, `reader_antenna`); new `device_telemetry` and `telemetry_quarantine` hypertables with RLS |
| 017 | `017_sites_zones_tracking_modes.py` | `tenants`, `devices`, `sites`, `zones` | Sprint 15 Phase A: shared substrate — `sites`, `zones` (reader-bound + geofence kinds), `tenants.tracking_modes` JSONB flag, `devices.mobility` |
| 018 | `018_assets_bindings.py` | `assets`, `asset_tag_bindings` | Sprint 15 Phase B: assets + tag bindings with `binding_kind` (`epc`/`tid`/`device`) and `binding_value`; partial-unique index on active bindings; RLS |
| 019 | `019_external_locations.py` | `external_locations` | Sprint 15 Phase C: external_locations hypertable for TMS-pushed positions; feeds the `asset_current_location` view union |
| 020 | `020_inventory.py` | `products`, `lots`, `stock_items`, `stock_movements`, `tag_data_mappings`, view `stock_levels` | Sprint 15b: inventory domain layer; `stock_movements` hypertable; `tag_data_mappings` (per-tenant/scope mappings of `tag_data` keys → semantic fields) |
| 021 | `021_inventory_hardening.py` | `stock_items`, `stock_movements` | Sprint 15b Phase D hardening: post-audit constraint and index follow-ups |
| 022 | `022_phase_abc_hardening.py` | Sprint 15 tables | Sprint 15 Phase A–C audit mitigations |
| 023 | `023_tenant_db_pool_key.py` | `tenants` | Sprint 13b: `db_pool_key` column for the multi-tier `PoolRegistry` |
| 024 | `024_asset_current_location.py` | view `asset_current_location` | Sprint 15: `asset_current_location` SQL view (latest position per active binding); recursive `assets.parent_asset_id` path support |
| 025 | `025_device_tokens.py` | `devices` | Sprint 16 (A6 Phase 1): per-device rotatable tokens — `token_hash`, `token_prefix`, `token_rotated_at` |
| 026 | `026_geofence_tile_provider_cert.py` | `zones`, `tenants`, `devices` | Sprint 17a: geofence storage (`polygon_geojson`, bbox columns), `tenants.tile_provider`, `devices.cert_thumbprint` |
| 027 | `027_subject_current_zone.py` | `subject_current_zone` | Sprint 17a §5.2: durable dwell-tracker state; one row per `(tenant_id, subject_kind, subject_id)`; survives worker restarts |
| 028 | `028_stock_item_parent.py` | `stock_items` | Sprint 15b: `parent_stock_item_id` self-FK + partial index `ix_stock_items_parent` for SSCC → SGTIN case/pallet containment |
| 029 | `029_zones_virtual_kind.py` | `zones` | Sprint 17a follow-up: virtual zone kind |
| 030 | `030_subject_scoped_telemetry.py` | `telemetry_readings`, `telemetry_readings_legacy_device` (renamed from `device_telemetry`), `device_telemetry` (now a view), `telemetry_models`, `telemetry_quarantine` | Sprint 18: subject-scoped telemetry hypertable keyed on `(tenant_id, subject_kind, subject_id, metric_name, timestamp)`; rename + back-fill of `device_telemetry`; back-compat view; `subject_kind` added to `telemetry_models` (with CHECK enforcing `device_type` only for `device` kind) and `telemetry_quarantine` |
| 031 | `031_telemetry_subject_kinds_and_caggs.py` | `tenants`, `cagg_telemetry_1m`, `cagg_telemetry_1h` | Sprint 19: `tenants.telemetry_subject_kinds JSONB DEFAULT '["device"]'` opt-in column; two TimescaleDB continuous aggregates (1m / 1h) over `telemetry_readings` keyed on `(tenant_id, subject_kind, subject_id, metric_name, bucket)` with `avg`/`min`/`max`/`count`; cagg DDL runs in `op.get_context().autocommit_block()` because `add_continuous_aggregate_policy` cannot run in a transaction |
| 032 | `032_drop_legacy_device_telemetry.py` | `device_telemetry` (view), `telemetry_readings_legacy_device` (hypertable), `tenant_isolation_device_telemetry` (RLS), `ix_device_telemetry_lookup` (index) | Sprint 21: closes the Sprint 18 deprecation window per [ADR-015 §6](adr/015-telemetry-rules-and-deprecation.md). Drops the back-compat view, the legacy hypertable, the legacy RLS policy, and the Sprint 14 lookup index. Downgrade re-creates empty shells — **not data-reversible**; rollback is restore-from-backup. Pre-flight: `pg_stat_user_tables.idx_scan = 0` for one full retention window, `grep -rn 'device_telemetry' src/` clean, no Grafana / external SQL clients pointing at the view. |

---

## Row-Level Security

RLS is enabled on all tenant-scoped tables. Policies use:

```sql
USING (tenant_id = current_setting('app.current_tenant_id')::uuid)
```

| Table | Policy Name | Migration |
|-------|-------------|-----------|
| `devices` | `tenant_isolation_devices` | 007 |
| `tag_reads` | `tenant_isolation_tag_reads` | 007 |
| `rules` | `tenant_isolation_rules` | 007 |
| `alerts` | `tenant_isolation_alerts` | 007 |
| `telemetry_models` | `tenant_isolation_telemetry_models` | 007 |
| `analytics_results` | `tenant_isolation_analytics_results` | 009 |
| `audit_logs` | `tenant_isolation_audit_logs` | 012 |
| `dead_letter_events` | `tenant_isolation_dead_letter_events` | 013 |
| `device_telemetry` | `tenant_isolation_device_telemetry` | 016 (renamed to `telemetry_readings_legacy_device` in 030; **dropped in 032**) |
| `telemetry_readings` | `tenant_isolation_telemetry_readings` | 030 |
| `telemetry_quarantine` | `tenant_isolation_telemetry_quarantine` | 016 |
| `assets` | `tenant_isolation_assets` | 017 |
| `asset_tag_bindings` | `tenant_isolation_asset_tag_bindings` | 017 |
| `sites` | `tenant_isolation_sites` | 017 |
| `zones` | `tenant_isolation_zones` | 017 |
| `products` | `tenant_isolation_products` | 020 |
| `lots` | `tenant_isolation_lots` | 020 |
| `stock_items` | `tenant_isolation_stock_items` | 020 |
| `stock_movements` | `tenant_isolation_stock_movements` | 020 |
| `tag_data_mappings` | `tenant_isolation_tag_data_mappings` | 020 |
| `external_locations` | `tenant_isolation_external_locations` | 019 |
| `subject_current_zone` | `subject_current_zone_tenant_isolation` | 027 |

---

## Role-Based Access Control

| Role | Read | Create/Update | Delete | Admin Ops |
|------|------|---------------|--------|-----------|
| `admin` | All | All | All | Dead letter, audit logs, user management |
| `editor` | All | Devices, rules, integrations, telemetry models, alert ack | — | — |
| `viewer` | All | — | — | — |

Authentication: Bearer API key (`Authorization: Bearer tp_{slug}_{hex}`) or backward-compatible `X-Tenant-ID` header (viewer-only).

---

## MQTT Topic Structure

```
tenants/{tenant_id}/devices/{device_id}/tag-reads   → TagReadCreate
tenants/{tenant_id}/devices/{device_id}/status       → DeviceStatusUpdate
tenants/{tenant_id}/devices/{device_id}/telemetry    → TelemetryReading      
tenants/{tenant_id}/devices/{device_id}/location     → LocationUpdate        
tenants/{tenant_id}/devices/{device_id}/events       → DeviceEvent           
```

Full wire contract: [design/edge-device-contract.md](design/edge-device-contract.md).

---

## EventBus Topics

| Topic | Publisher | Consumers |
|-------|----------|-----------|
| `tag_read.created` | Ingestion service | Rules engine, analytics modules, integration layer |
| `device.status_changed` | MQTT subscriber | Integration layer |
| `alert.triggered` | Rules engine | Alert delivery, integration layer |
| `device.registered` | Device service | Integration layer |
| `device.decommissioned` | Device service | Integration layer |
| `device.token_rotated` | Device service | Audit log, integration layer |
| `telemetry.received` | Ingestion service | Rules engine, analytics modules, integration layer |
| `telemetry.out_of_range` | Telemetry validator | Rules engine, integration layer |
| `subject.zone_changed` | Ingestion service | Rules engine, integration layer; payload carries `subject_kind` (`asset` \| `stock_item`) |
| `stock.movement_recorded` | Inventory ingestion branch | Analytics, integration layer |
