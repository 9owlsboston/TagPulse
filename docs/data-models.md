# TagPulse Data Models & Schemas

This document is the single reference for all database tables, Pydantic API schemas, and their relationships. Source of truth for column types lives in [`src/tagpulse/models/database.py`](../src/tagpulse/models/database.py) (ORM) and the `src/tagpulse/models/` schema files (API contracts).

---

## Entity-Relationship Overview

```
tenants
  |-- 1:N -- devices
  |-- 1:N -- users
  |-- 1:N -- tag_reads
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
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |

**Migration:** 005, 014

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
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()` | |
| `updated_at` | TIMESTAMPTZ | NOT NULL, auto-updated | |

**RLS:** Yes (migration 007)
**Migration:** 001, 002, 003, 005

---

### tag_reads (hypertable)

Time-series RFID tag read events. Partitioned by `timestamp` via TimescaleDB.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK (composite with `timestamp`) | |
| `device_id` | UUID | NOT NULL, indexed | Source reader |
| `tenant_id` | UUID | FK → tenants.id, NOT NULL, indexed | |
| `tag_id` | TEXT | NOT NULL, indexed | RFID tag identifier |
| `timestamp` | TIMESTAMPTZ | NOT NULL, indexed | When the read occurred |
| `signal_strength` | FLOAT | NULLABLE | RSSI or dBm value |
| `sensor_data` | JSONB | NULLABLE | Optional sensor payload |
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()` | When ingested |

**RLS:** Yes (migration 007)
**Migration:** 001, 003, 005

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
```

---

## EventBus Topics

| Topic | Publisher | Consumers |
|-------|----------|-----------|
| `tag_read.created` | Ingestion service | Rules engine, analytics modules, integration layer |
| `device.status_changed` | MQTT subscriber | Integration layer |
| `alert.triggered` | Rules engine | Alert delivery, integration layer |
| `device.registered` | Device service | Integration layer |
| `device.decommissioned` | Device service | Integration layer |
