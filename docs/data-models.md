# TagPulse Data Models & Schemas

This document is the single reference for all database tables, Pydantic API schemas, and their relationships. Source of truth for column types lives in [`src/tagpulse/models/database.py`](../src/tagpulse/models/database.py) (ORM) and the `src/tagpulse/models/` schema files (API contracts).

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
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |

**Migration:** 005, 014, 017 (tracking_modes), 023 (db_pool_key), 026 (tile_provider)

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
| `token_hash` | VARCHAR(255) | NULLABLE | SHA-256 hash of per-device token |
| `token_prefix` | VARCHAR(10) | NULLABLE | First chars for O(1) lookup |
| `token_rotated_at` | TIMESTAMPTZ | NULLABLE | Last rotation timestamp |
| `cert_thumbprint` | VARCHAR(128) | NULLABLE | X.509 mTLS cert thumbprint |
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL, auto-updated | |

**RLS:** Yes (migration 007)
**Migration:** 001, 002, 003, 005, 017 (mobility), 025 (tokens), 026 (cert_thumbprint)

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

**RLS:** Yes (migration 007)
**Migration:** 001, 003, 005, 016

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

### rules

User-defined automation rules evaluated against incoming telemetry.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL, indexed | |
| `name` | VARCHAR(255) | NOT NULL | |
| `description` | TEXT | NULLABLE | |
| `condition_type` | VARCHAR(50) | NOT NULL | `threshold`, `absence`, `rate_change` |
| `condition_config` | JSONB | NOT NULL | Type-specific parameters (see below) |
| `action_type` | VARCHAR(50) | NOT NULL | `webhook`, `email`, `notification` |
| `action_config` | JSONB | NOT NULL | Type-specific parameters |
| `scope_device_id` | UUID | NULLABLE | Restrict to single device |
| `enabled` | BOOLEAN | NOT NULL, default `true` | |
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL, auto-updated | |

**RLS:** Yes (migration 007)
**Migration:** 006

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

### device_telemetry (hypertable)

Time-series sensor metric stream, decoupled from `tag_reads`. Partitioned by `timestamp`. See [design/telemetry-and-location.md](design/telemetry-and-location.md).

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
| `external_ref` | VARCHAR(255) | NULLABLE | ERP / WMS asset code |
| `name` | VARCHAR(255) | NOT NULL | |
| `asset_type` | VARCHAR(50) | NOT NULL | Free-form per tenant (e.g. `pallet`, `tool`) |
| `status` | VARCHAR(20) | NOT NULL, default `'active'` | `active` \| `retired` \| `lost` |
| `metadata` | JSONB | NULLABLE | |
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL, auto-updated | |

**Unique constraint:** `(tenant_id, external_ref)`
**RLS:** Yes
**Migration:** 017

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

Physical locations.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK | |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL | |
| `name` | VARCHAR(255) | NOT NULL | |
| `address` | TEXT | NULLABLE | |
| `default_timezone` | VARCHAR(64) | NOT NULL, default `'UTC'` | IANA tz |
| `metadata` | JSONB | NULLABLE | |
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |

**Unique constraint:** `(tenant_id, name)`
**RLS:** Yes
**Migration:** 017

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

---

### asset_current_location (view)

Latest `tag_read` per active binding. Defined in [design/assets-and-zones.md](design/assets-and-zones.md) §3.4.

| Column | Type | Notes |
|--------|------|-------|
| `asset_id` | UUID | |
| `tenant_id` | UUID | |
| `last_reader_id` | UUID | Source `tag_reads.device_id` |
| `last_seen_at` | TIMESTAMPTZ | |
| `latitude` | DOUBLE PRECISION | NULL when binding has no GPS reads |
| `longitude` | DOUBLE PRECISION | |
| `signal_strength` | FLOAT | |

**Inherits RLS** from underlying tables.

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
| `TagReadCreate` | Ingest request (HTTP + MQTT) | `device_id` (UUID), `tag_id` (str, 1-256), `timestamp`, `signal_strength?`, `sensor_data?` |
| `TagReadResponse` | API response | All DB fields |

### Devices — `schemas.py`

| Schema | Purpose | Key Fields |
|--------|---------|------------|
| `DeviceCreate` | Register device | `name` (1-255), `device_type?`, `metadata?`, `configuration?`, `firmware_version?` |
| `DeviceUpdate` | Partial update | All fields optional |
| `DeviceResponse` | API response | All DB fields |
| `DeviceStatusUpdate` | MQTT status message | `connection_state`, `firmware_version?` |

### Telemetry Models — `schemas.py`

| Schema | Purpose | Key Fields |
|--------|---------|------------|
| `MetricDefinition` | Single metric spec | `name`, `unit`, `min_value?`, `max_value?`, `description?` |
| `TelemetryModelCreate` | Create model | `device_type`, `metrics` (list, min 1) |
| `TelemetryModelResponse` | API response | All DB fields |

### Query / Aggregations — `schemas.py`

| Schema | Purpose | Key Fields |
|--------|---------|------------|
| `ReadsPerHour` | Hourly read count | `bucket`, `device_id`, `read_count` |
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
| `device_telemetry` | `tenant_isolation_device_telemetry` | 016 |
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
