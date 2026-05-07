# TagPulse Roadmap

---

## Sprint 1 — Core Ingestion Pipeline

> Design: [docs/architecture.md](architecture.md), [docs/data-models.md](data-models.md)
> ADRs: [001 Python+FastAPI](adr/001-python-fastapi-backend.md), [002 MQTT](adr/002-mqtt-device-connectivity.md), [003 TimescaleDB](adr/003-timescaledb-storage.md)

- [done] MQTT subscriber — connect to broker, subscribe to device topics
- [done] HTTP ingestion endpoint — REST push path for devices that can't speak MQTT
- [done] Tag read message schema — Pydantic model (tag ID, reader ID, timestamp, signal strength, optional sensor payload)
- [done] TimescaleDB schema — hypertable for tag reads, table for device registry
- [done] Message validation + persistence — parse incoming messages, write to TimescaleDB
- [done] Database migrations — Alembic setup with initial schema

## Sprint 2 — Device Registry & Configuration

> Design: [docs/data-models.md](data-models.md), [docs/architecture.md](architecture.md)
> ADRs: [002 MQTT](adr/002-mqtt-device-connectivity.md)

- [done] CRUD API for readers — register, list, get, update, decommission readers
- [done] Device configuration profiles — per-device settings, connection credentials, metadata
- [done] Device status tracking — last seen, connection state, firmware version
- [done] Telemetry model definitions — per-device-type schema (what metrics a device type reports, units, expected ranges)
- [done] MQTT topic convention — `devices/{device_id}/tag-reads`, `devices/{device_id}/status`

## Sprint 3 — Query & Telemetry Monitoring API

> Design: [docs/data-models.md](data-models.md), [docs/user-guide.md](user-guide.md)

- [done] Tag read query endpoint — filter by reader, tag, time range, with pagination
- [done] Basic aggregations — reads per reader per hour, unique tags per time window
- [done] Live telemetry API — recent reads per device, current device status summary
- [done] Device health API — connectivity status, last-seen, error rates

## Sprint 4 — Dev & Ops Baseline

> Design: [docs/quickstart.md](quickstart.md), [CONTRIBUTING.md](../CONTRIBUTING.md)
> ADRs: [009 Containerization](adr/009-containerization-local-dev.md)

- [done] Docker Compose — app + TimescaleDB + Mosquitto for local dev
- [done] CI pipeline — GitHub Actions with lint + typecheck + test
- [done] CONTRIBUTING.md + CHANGELOG.md
- [done] Structured logging — JSON formatter, request ID correlation

## Sprint 5 — Multi-Tenancy & Usage Metering

> Design: [docs/design/storage-strategy.md](design/storage-strategy.md)
> ADRs: [008 Multi-tenancy](adr/008-multi-tenancy-strategy.md)

- [done] Tenant data model — `tenants`, `tenant_usage_detail`, `tenant_quotas` tables, Alembic migrations
- [done] Add `tenant_id` to all tables — `devices`, `tag_reads` with FK + indexes
- [done] Row-Level Security — RLS policies on all tenant-scoped tables
- [done] Tenant auth dependency — `get_current_tenant` FastAPI dependency (API key header → tenant_id)
- [done] UsageMeter service — in-process buffered counter, 60s flush to `tenant_usage_detail`
- [done] Tenant-scoped routes — all endpoints require X-Tenant-ID, filter by tenant_id
- [done] Quota enforcement — `check_quota()` inline, configurable `throttle | reject | alert_only`
- [done] Billing API — `GET /admin/usage`, `GET /admin/usage/summary` (tenant-scoped)
- [done] MQTT topic restructuring — `tenants/{tenant_id}/devices/{device_id}/tag-reads`
- [done] Metering middleware — record api_read/api_write/ingestion dimensions per request

## Sprint 6 — Rules & Alerts Engine

> Design: [docs/design/alerts-anomaly-detection.md](design/alerts-anomaly-detection.md)
> ADRs: [005 Embedded rules engine](adr/005-embedded-rules-engine.md)

- [done] Rule configuration API — CRUD for user-defined rules (conditions, actions, schedules)
- [done] Rule evaluation engine — evaluate conditions against incoming telemetry stream
- [done] Built-in conditions — threshold breach, absence detection ("tag not seen for N min"), rate change
- [done] Alert routing — deliver alerts via webhook, email, or internal notification queue
- [done] Alert history — log of triggered alerts with context (which rule, which device, what data)
- [done] Rules metering — record `rule_evaluations` and `alerts_fired` dimensions per tenant

## Sprint 7 — Analytics Module Framework

> Design: [docs/design/analytics-module-framework.md](design/analytics-module-framework.md)
> ADRs: [004 Monolith + plugin analytics](adr/004-monolith-plugin-analytics.md)

- [done] Plugin interface — base class for analytics modules, registration, lifecycle
- [done] First module: read frequency analytics — reads/min per reader, anomaly flagging
- [done] Background task runner — async worker for analytics that don't block ingestion

## Sprint 8 — Integration & Export Layer

> Design: [docs/design/integration-export-layer.md](design/integration-export-layer.md)
> ADRs: [006 Webhook-first integration layer](adr/006-webhook-integration-layer.md)

- [done] Outbound webhooks — push events to external systems on configurable triggers
- [done] Event streaming endpoint — SSE or WebSocket feed for real-time consumers
- [planned] Scheduled data exports — periodic CSV/JSON export to object storage or email
- [done] External API — documented REST API for third-party system integration
- [done] Integration configuration API — CRUD for webhook/export targets
- [done] Integration metering — record `webhook_deliveries`, `sse_connections`, `export_volume` per tenant

## Sprint 9 — Admin UI

> Design: [docs/design/admin-ui.md](design/admin-ui.md)
> ADRs: [007 Admin UI technology](adr/007-admin-ui-technology.md)

- [done] Technology decision — React 19 + TypeScript + Vite SPA in separate repo (see ADR-007, design/admin-ui.md)
- [done] TagPulse-UI repo bootstrapped — quality gates passing (lint + typecheck + test)
- [done] Overview dashboard — KPI tiles (devices, reads, alerts, anomalies) with auto-refresh + SSE live counter
- [done] Device management views — register, configure, monitor device fleet (list + detail + register)
- [done] Telemetry dashboard — live and historical telemetry visualization per device/group
- [done] Data Explorer — form-based ad-hoc query builder for tag reads with CSV export
- [done] Telemetry model management — list, create, delete per-device-type metric schemas
- [done] Rule & alert management — 4-step rule wizard, alert history with acknowledge
- [done] Integration management — type-specific webhook/SSE/export config, delivery log
- [done] Usage & billing dashboard — daily usage bar chart, quota progress bars

## Sprint 10 — Production Hardening

> Design: [docs/design/production-hardening.md](design/production-hardening.md)

- [done] Health checks — deep health (DB connectivity, MQTT broker status)
- [done] Graceful shutdown — drain EventBus queues, flush pending writes
- [done] Retry + dead letter — dead_letter_events table, admin API for retry/abandon
- [done] Audit logging — audit_logs table with tenant-scoped query API
- [done] Dockerfile + deployment config — HEALTHCHECK, multi-worker, labels
- [done] `docs/runbooks/` foundation — README + first two runbooks (`device-token-rotation.md` from Sprint 16, `geofence-postgis-trigger.md` from Sprint 17a). Future runbooks land alongside the sprint that needs them.

## Sprint 11 — Observability

> Design: [docs/design/observability.md](design/observability.md)
> ADRs: [010 Internal event bus](adr/010-internal-event-bus.md)

- [done] Platform metrics — OpenTelemetry SDK, Prometheus /metrics endpoint, ingestion + API counters
- [done] Device telemetry metrics — devices_online gauge
- [done] Rule engine metrics — rule_evaluations + alerts_fired counters
- [done] EventBus metrics — published/consumed/dropped counters, queue size
- [done] Integration metrics — webhook_deliveries, sse_connections, dead_letters counters
- [done] Auto-instrumentation — FastAPI, SQLAlchemy, httpx, logging (trace ID correlation)

## Sprint 12 — Identity & Device Provisioning

> Design: [docs/design/identity-device-provisioning.md](design/identity-device-provisioning.md), [docs/design/iot-central-gap-analysis.md](design/iot-central-gap-analysis.md)

- [done] User & role management — users table, admin/editor/viewer roles, tenant-scoped
- [done] API key generation — SHA-256 hashed keys, per-user, revocable, prefix-based lookup
- [done] API key authentication — Bearer token auth with backward-compatible X-Tenant-ID
- [done] Role enforcement — require_role() dependency, per-route permission matrix
- [done] Device self-registration — provisioning endpoint with pre-shared key auth
- [done] Device approval flow — admin approves/rejects pending devices

## Sprint 13 — UI Authentication & Session Management

> Design: [docs/design/ui-authentication.md](design/ui-authentication.md)

- [done] Login page — email + API key authentication (replaces tenant-ID-only gate)
- [done] Session management — JWT tokens with expiry, secure cookie storage
- [done] Role-aware UI — show/hide actions based on user role (admin/editor/viewer)
- [done] User profile — view current user info, role, tenant in header
- [done] API key login flow — backend endpoint to exchange API key for session token
- [done] Logout + session expiry — clear tokens, redirect to login
- [done] Auth guard updates — protect routes based on role, redirect unauthenticated users
- [done] User management page — admin-only list, create, edit, deactivate users
- [done] API key management UI — generate and revoke keys from user detail view
- [done] Register Device button — add CTA on device list page for discoverability

## Sprint 13b — Multi-tier Foundations

> Design: [docs/design/storage-strategy.md](design/storage-strategy.md), [docs/adr/008-multi-tenancy-strategy.md](adr/008-multi-tenancy-strategy.md)
> Goal: ship the per-request DB-routing seam and the time-bucketed metrics abstraction that subsequent sprints build on. v1 ships with one shared pool; the seam makes future sovereign-tenant onboarding a config change, not a refactor.

- [done] `db_session_var: ContextVar[AsyncSession]` in `tagpulse.core.context`; `tenant_context()` async helper for non-request code (background jobs, scripts); refactor existing `get_session()` dependency to populate the contextvar. Per [storage-strategy.md §6 Q2](design/storage-strategy.md).
- [done] `PoolRegistry` built once at startup from `config/database.yaml`; v1 ships with single `shared_default` entry.
- [done] `tenants.db_pool_key VARCHAR(64) NOT NULL DEFAULT 'shared_default'` column + Alembic migration (023); middleware reads it per request, fetches a session from the matching pool, sets `app.current_tenant_id` for shared-pool tenants (RLS).
- [done] `AdminRepository` in `src/tagpulse/repositories/admin.py` for cross-tenant operations gated by admin role at the route layer (will be the home for the `GET /admin/tag-collisions` endpoint added in Sprint 15).
- [done] `MetricsRepository` abstraction (deterministic; both backends first-class) in `src/tagpulse/repositories/metrics.py`. Selected once at startup from `DATABASE_BACKEND` config (`timescale` \| `postgres`). **Timescale impl** uses `time_bucket` so a continuous aggregate can be plugged in transparently; **PG impl** uses `date_trunc` paired with periodic matview refresh. First method: `tag_reads_hourly_by_reader`. Per [storage-strategy.md §6 Q1](design/storage-strategy.md).
- [done] CI integration tests for both `MetricsRepository` backends (SQL-dialect assertions + factory selection); review rule: any new method requires both implementations in the same PR.
- [done] PG-mode scaling ceiling benchmark — `scripts/benchmark_pg_metrics.py` harness + results table in [storage-strategy.md §6.1](design/storage-strategy.md#61-pg-mode-scaling-ceiling). Floor numbers on dev hardware: cold raw-table path crosses 1 s between 100 and 500 devices/tenant; matview path is sub-second through 2k devices and ~700 ms p99 at 5k. Operational ceiling ~2k devices/tenant on dev hardware (expected ~5–10k on tuned ops hardware) before matview refresh becomes the bottleneck and TimescaleDB continuous aggregates are required.

## Sprint 14 — Telemetry & Location Foundations

> Design: [docs/design/telemetry-and-location.md](design/telemetry-and-location.md), [docs/design/rfid-tag-data-model.md](design/rfid-tag-data-model.md)
> Goal: first-class location on tag reads + dedicated sensor telemetry stream + extended MQTT topic taxonomy + structured RFID tag identity (TID/EPC/user memory) and tag-borne sensor capture.

- [done] Add `latitude`, `longitude`, `location_accuracy_m`, `location_source` to `tag_reads` (Alembic migration 016)
- [done] Add RFID identity columns to `tag_reads`: `epc`, `epc_hex`, `epc_scheme`, `epc_decoded`, `tid`, `user_memory_hex`, `tag_data`, `reader_antenna` (migration 016)
- [done] EPC decoder module `tagpulse.rfid.epc` — SGTIN-96/198, SSCC-96, GIAI-96/202, GRAI-96/170, raw fallback
- [done] Ingestion: auto-decode `epc_hex` → `epc`/`epc_scheme`/`epc_decoded`; `tag_id` defaults to `epc` when absent
- [done] Tag-borne sensor mirror: declared `tag_data` numeric keys also written as `device_telemetry` rows with `metadata.source='tag'` and `metadata.tag_read_id`
- [done] **`tag_data` size cap** — 4 KB inline cap on the JSONB blob; on overflow, silently truncate and set `tag_data._truncated=true`. Quarantine remains for malformed/unknown data, not oversized. OTel counter `tag_data_truncations_total{tenant}` so unexpected truncation rates are visible. Per [rfid-tag-data-model.md §9 Q2](design/rfid-tag-data-model.md).
- [done] Extend `TagReadCreate` Pydantic with optional `Location`, `Identity` (epc/tid/user_memory), and `tag_data` sub-models
- [done] New `device_telemetry` hypertable (tenant_id, device_id, timestamp, metric_name, metric_value, unit, metadata) + RLS
- [done] `POST /telemetry` HTTP ingestion endpoint (tenant-scoped, batched)
- [done] MQTT topic `tenants/{tenant_id}/devices/{device_id}/telemetry` subscriber branch
- [done] MQTT topic `tenants/{tenant_id}/devices/{device_id}/location` subscriber branch
- [done] MQTT topic `tenants/{tenant_id}/devices/{device_id}/events` subscriber branch (device-side events)
- [done] Validate telemetry against existing `telemetry_models`; quarantine unknown metrics; emit `telemetry.out_of_range` for rules engine
- [done] Update `scripts/simulate_devices.py` to publish location, standalone telemetry, and at least one sensor-tag profile (temperature embedded in `tag_data`)
- [done] `clients/pi/`: wire `submit_telemetry` / `submit_location` end-to-end through MqttTransport; add `epc`/`tid`/`tag_data` fields on `submit_tag_read`
- [done] Metering: new dimension `telemetry_ingestion`
- [done] **UI:** Device detail "Location" tab — last known lat/lon + Leaflet mini-map
- [done] **UI:** Device detail "Telemetry" tab — per-metric line chart with time-range picker; "source: tag" badge for tag-borne readings, click-through to originating read
- [done] **UI:** Telemetry Models page — quarantined readings panel
- [done] **UI:** Data Explorer — `epc`, `tid`, `latitude`, `longitude` columns; "has location" filter; EPC scheme filter
- [done] **UI:** Device detail "Last read" panel — surface `epc`, `tid`, decoded scheme, `tag_data` keys
- [done] **UI:** Overview dashboard — "Devices reporting location" KPI tile

## Sprint 15 — Assets & Zones (asset-tracking mode)

> Design: [docs/design/assets-and-zones.md](design/assets-and-zones.md), [docs/design/tracking-modes.md](design/tracking-modes.md), [docs/design/mobile-carriers-and-manifests.md](design/mobile-carriers-and-manifests.md)
> Goal: track *what* is being scanned for the **asset-tracking** domain (returnable, durable items). Reader-bound zones unblock first geofence-style alerts without GPS. Mobile readers + carrier manifests (truck-with-cargo) ride on the same substrate. Inventory mode lands separately in Sprint 15b.

- [done] `assets` table + CRUD API `/assets` (tenant-scoped, RLS)
- [done] `assets.parent_asset_id` for carrier containment per [mobile-carriers-and-manifests.md](design/mobile-carriers-and-manifests.md)
- [done] `devices.mobility` flag (`fixed` \| `mobile`); ingestion skips fixed-zone lookup for mobile readers (migration 020a) — column + check constraint shipped in migration 017; ingestion enrichment hot-path (Phase B.2) honours the flag and skips `get_zone_for_reader()` when `mobility != 'fixed'`
- [done] `asset_tag_bindings` table — historical tag-to-asset mappings; column **named `binding_value` from day one** (no deprecation dance — the table is new in this sprint); `binding_kind` ∈ {`epc`,`tid`,`device`} per [rfid-tag-data-model.md](design/rfid-tag-data-model.md) and [mobile-carriers-and-manifests.md](design/mobile-carriers-and-manifests.md)
- [done] **Tag-collision admin tooling** — non-unique global index on `asset_tag_bindings(binding_value) WHERE unbound_at IS NULL`; admin-only `GET /admin/tag-collisions?binding_value=…` (returns count of other tenants with an active binding, never tenant identities); OTel counter `tag_collisions_global_total`. Bulk-import preflight wires up alongside CSV import in Phase E. Per [assets-and-zones.md §11 Q3](design/assets-and-zones.md).
- [done] **`external_locations` hypertable** (migration 019) — `(tenant_id, asset_id, latitude, longitude, recorded_at, source, accuracy_meters?, speed_kph?, heading_deg?, metadata)`; RLS by `tenant_id`; hypertable on `recorded_at`. Per [mobile-carriers-and-manifests.md §10 Q5](design/mobile-carriers-and-manifests.md). (Compression/retention parity with `device_telemetry` deferred to ops policy work in Sprint 17.)
- [done] **`POST /assets/{asset_id}/external-position`** endpoint (editor+) — generic ingestion for non-RFID carriers; emits `Topic.EXTERNAL_LOCATION_RECORDED`; OTel counter `tagpulse_external_locations_recorded_total`. Tenant rate-limit deferred to backlog (alongside global API rate-limit middleware). TMS-specific adapters land in backlog.
- [done] `POST /assets/{id}/load`, `POST /assets/{id}/unload`, `GET /assets/{id}/manifest` for carrier semantics — idempotent; emits `Topic.ASSET_LOADED` / `Topic.ASSET_UNLOADED`; manifest built via recursive CTE on `assets.parent_asset_id`; OTel counter `tagpulse_asset_load_operations_total`.
- [done] `asset_current_location` SQL view (migration 024) — latest tag read per active binding, **UNION** with the latest `external_locations` row; `latest_position_source` column lets the UI render "via Samsara" vs "via Reader-12". Powers `GET /assets/{id}/current-location`.
- [done] `sites` table — physical locations (name, address, default_timezone) — **shared substrate, used by both modes**
- [done] `zones` table — reader-bound (polygon nullable, deferred to Sprint 17) — **shared substrate**
- [done] `/sites` and `/zones` CRUD APIs
- [done] Ingestion emits `subject.zone_changed` event (with `subject_kind='asset'`) when reader transition crosses zone boundary — process-local last-zone cache; multi-worker durability deferred to Sprint 17
- [done] **Phase B.3** Batch ingest enrichment (`IngestionService.ingest_batch`) — batch path now mirrors `ingest()`: per-read binding/zone resolution, `subject.zone_changed` events, inventory enrichment, device last-seen + connection updates. `insert_batch` returns rows so events can carry `tag_read_id`.
- [done] **Phase B.3** `asset_current_location` SQL view + `GET /assets/{id}/current-location` and `GET /assets/{id}/path` endpoints — single migration that unions RFID and `external_locations` per [mobile-carriers-and-manifests.md §10 Q5](design/mobile-carriers-and-manifests.md).
- [done] Repository: `get_assets_in_zone()`, `get_asset_path()`, `get_current_location()` (`TimescaleAssetLocationRepository`); `GET /zones/{zone_id}/assets` route.
- [done] Simulator: `scripts/simulate_assets.py` — binds tag IDs to named assets; randomises reader hops to drive zone transitions.
- [done] **UI:** Assets page — list, search by external_ref/tag, detail with current location/zone/binding history.
- [done] **UI:** Sites & Zones page (admin/editor) — site list + zone editor with reader picker; new "Occupants" drawer per zone backed by `useAssetsInZone`.
- [done] **UI:** Asset detail — server-merged path via `useAssetPath`, current-location card via `useAssetCurrentLocation`, badged by source (RFID vs external/TMS).
- [done] **UI:** Sidebar — Assets + Sites entries with role guards (visible when `tenants.tracking_modes` includes `asset`).
- [done] **UI:** Device detail — "Covers Zones" panel on the Overview tab listing zones whose `fixed_reader_ids` include this device. ([TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI/blob/main/src/pages/devices/DeviceDetail.tsx))

## Sprint 15b — Inventory Tracking (sibling to Sprint 15)

> Design: [docs/design/tracking-modes.md](design/tracking-modes.md)
> Goal: serve the **inventory-tracking** domain (count of stock per SKU/lot/zone, expiration, in/out movements). Sits on the same substrate as Sprint 15; runs in parallel and can ship in either order.

- [done] `tenants.tracking_modes` JSONB column — `['asset']` default; `'inventory'` opt-in (Sprint 15 Phase A)
- [done] `products` table + CRUD API `/products` (SKU, GTIN, name, category, unit, attributes) — Sprint 15b Phase D
- [done] `lots` table + nested API `/products/{id}/lots` (lot_code, manufactured_at, expires_at) — Sprint 15b Phase D
- [done] `stock_items` table — per-tag inventory unit; column `binding_value` from day one — Sprint 15b Phase D (auto-creation by ingestion deferred to Phase D.2)
- [done] `stock_items.parent_stock_item_id` — case/pallet containment (migration 028); self-FK with `ON DELETE SET NULL`, partial index on non-null values, mirrored on the ORM model and `StockItemCreate` / `StockItemUpdate` / `StockItemResponse` schemas. Per [mobile-carriers-and-manifests.md §4.3](design/mobile-carriers-and-manifests.md). Manifest API (recursive CTE mirroring `assets.parent_asset_id`) ships when the first customer needs SSCC → SGTIN traversal.
- [done] **`tag_data_mappings` table** — per-(tenant, device_type, product) mapping from `tag_data` keys to semantic fields; most-specific scope wins (Sprint 15b Phase D). Ingestion read path lands with Phase D.2.
- [done] `stock_movements` hypertable — append-only ledger (enter/exit/transfer/consume) — Sprint 15b Phase D (writes from ingestion deferred to Phase D.2)
- [done] `stock_levels` SQL view — live count per (product, lot, zone) — Sprint 15b Phase D
- [done] **Phase D.2** — Ingestion inventory branch — SKU lookup by GTIN, lot inference from `tag_data` via `tag_data_mappings`, emit `subject.zone_changed` with `subject_kind='stock_item'` and append `stock_movements` rows on zone transitions (Sprint 15b Phase D.5)
- [done] APIs: `/stock-items`, `/stock-levels`, `/stock-movements` (filter by product/lot/zone/state/time) — Sprint 15b Phase D
- [done] Rules engine: `stock.below_threshold`, `stock.expiring_within`, `stock.unexpected_in_zone` — Sprint 15b Phase E (`src/tagpulse/rules/evaluator.py`, `workers/inventory_rule_worker.py`)
- [done] Periodic workers — below-threshold scan (60 s), expiring-soon scan (daily) — Sprint 15b Phase E (`workers/inventory_rule_worker.py`)
- [done] CSV import endpoints for products / lots / stock_items (bulk onboarding) — Sprint 15b Phase E (`api/routes/inventory_imports.py`)
- [done] Simulator: inventory profile — register sample products, emit SGTIN tag streams across zones, simulate consume / expire — Sprint 15b Phase E (`scripts/simulate_inventory.py`)
- [done] Metering: new dimensions `inventory_movements`, `stock_items_active` — Sprint 15b Phase E
- [done] **UI:** Products page — catalog list, SKU detail with stock-by-zone bar chart — Sprint 15b Phase F (`pages/inventory/ProductList.tsx`, `ProductDetail.tsx`)
- [done] **UI:** Lots sub-page — expiry queue, lot detail — Sprint 15b Phase F (`pages/inventory/LotExpiryQueue.tsx` + per-product lots in `ProductDetail.tsx`; backed by new cross-product `GET /lots`)
- [done] **UI:** Stock Levels page — pivot grid (product × zone), CSV export — Sprint 15b Phase F (`pages/inventory/StockLevels.tsx`)
- [done] **UI:** Stock Movements page — chronological ledger filter (product / zone / time) — Sprint 15b Phase F (`pages/inventory/StockMovements.tsx`)
- [done] **UI:** Rule wizard — inventory condition step — Sprint 15b Phase F (`pages/rules/RuleEditor.tsx` extended with `stock.*` condition types)
- [done] **UI:** Sidebar — Products / Stock Levels / Stock Movements / Lot Expiry entries (gated by `tenants.tracking_modes`) — Sprint 15b Phase F (`components/Layout.tsx` reads `useTenantConfig` and filters by `requires`)
- [done] **UI:** Tenant settings page — admin toggle for `tracking_modes`; **"Sensor metrics"** sub-tab (declared telemetry keys mirrored to `device_telemetry`); **"Tag data fields"** sub-tab (editor for `tag_data_mappings`) — Sprint 15b Phase F mitigation (`pages/admin/TenantSettings.tsx`, backed by new `GET`/`PATCH /tenant/config`). Per [admin-ui.md §10](design/admin-ui.md).

## Sprint 16 — Edge Contract & Identity Hardening

> Design: [docs/design/edge-device-contract.md](design/edge-device-contract.md), ADR-011
> Goal: codify the wire contract `clients/pi/` enforces; tighten device identity before fleets get bigger.

- [done] `docs/design/edge-device-contract.md` — dedup, ENTER/EXIT, batching, clock rules, heartbeat
- [done] ADR-011 — device identity roadmap (token rotation → mTLS → TPM)
- [done] Backend ingestion middleware: reject events older than 24h or >5min in future; metering dimension `events_rejected_clock` (`src/tagpulse/ingestion/clock.py`, dead-lettered to `dead_letter_events.topic = 'tag_read.rejected_clock'`)
- [done] `POST /device-registry/{id}/rotate-token` (admin only) — revoke previous token, audit log entry (`device.token_rotated`), Alembic `025_device_tokens.py` adds `devices.token_hash` / `token_prefix` / `token_rotated_at`
- [done] Provisioning metering: `device_token_rotations` dimension + `tagpulse_device_token_rotations_total` OTel counter
- [done] Edge client doc — README in `clients/pi/` linked to contract spec
- [done] **UI:** Device detail "Security" panel — token last-rotated, rotate button (admin), copy-once token reveal modal
- [done] **UI:** Device detail "Heartbeat" panel — connection state, firmware version, last-seen, mobility, configuration JSON (uptime/queue depth deferred — surface when device publishes them on `…/status`)
- [done] **UI:** Devices list — admin-only "Last Rotated" column (per design §7)
- [done] **UI:** Audit log — admin-only `/admin/audit-logs` page (`src/pages/admin/AuditLog.tsx` in TagPulse-UI) with `Segmented` preset selector. "Device security events" preset filters server-side via `actions=device.token_rotated,device.cert_attached,device.approved,device.rejected`; backend extended (`AuditLogger.list_logs(actions=…)` + `?actions=` query param on `/admin/audit-logs`).
- [done] **Audit follow-up:** observe-mode flag for clock enforcement (`settings.ingest_clock_enforce`), explicit `MAX_INGEST_PAYLOAD_BYTES` middleware (256 KB), edge-client `TokenRevokedError`, conformance harness scaffold under `tests/conformance/`, operator runbook for first rotation (`docs/runbooks/device-token-rotation.md`)

## Sprint 17a — Geofencing & Map UI

> Design: [docs/design/geofencing-and-map.md](design/geofencing-and-map.md), [docs/design/tracking-modes.md](design/tracking-modes.md), [docs/design/mobile-carriers-and-manifests.md](design/mobile-carriers-and-manifests.md)
> Goal: polygon zones + map-based situational awareness in the admin UI — supporting both asset markers, inventory stock-density layers, and moving carriers (trucks/forklifts) with click-through manifests.

- [done] Store polygon as GeoJSON on `zones.polygon_geojson` with denormalized bbox columns (migration 026) and partial index `ix_zones_bbox` on `(min_lat, max_lat, min_lon, max_lon) WHERE polygon_geojson IS NOT NULL`
- [done] Spatial query: `tagpulse.geo` module — pure-Python ray-casting `point_in_polygon`, `validate_polygon` (single ring, ≤500 vertices, closed, valid lat/lon), `compute_bbox`, `bbox_contains`. No PostGIS dependency. **Tenant-level 30s TTL cache** on geofence-zone list per design §4.1 (`_GEOFENCE_CACHE`, write-through invalidation on zone create/update/delete).
- [done] **OTel instrumentation for PostGIS-trigger threshold** — histograms `geofence_evaluation_duration` (s) and `geofence_candidates_per_evaluation` (1) emitted by the geofence eval path; counters `geofence_transitions_counter`, `dwell_evaluations_counter`, `dwell_alerts_counter`. Prometheus alert rules ([ops/prometheus/alerts.yml](../ops/prometheus/alerts.yml), `tagpulse.geofence` group) fire after 1h sustained breach (p99 > 10ms OR p95 candidates > 50). Operator runbook: [docs/runbooks/geofence-postgis-trigger.md](runbooks/geofence-postgis-trigger.md).
- [done] **`MapConfigResolver` abstraction** — `tenants.tile_provider JSONB NULL` (NULL → OSM public default); `GET /tenant/map-config` (any role) + admin-only `PATCH /tenant/map-config`; `src/tagpulse/services/map_config.py` with builders for `osm`, `mapbox`, `maptiler`, `self_hosted`. Validation gates persistence.
- [done] Rules engine: `zone.entered`, `zone.exited`, `zone.dwell_exceeded` condition types with `subject_kinds` filter (`asset` \| `stock_item` \| `device`) and per-rule per-subject cooldown (`cooldown_s`)
- [done] Ingestion: emits `subject.zone_changed` for geofence transitions in addition to reader-bound — gated by `settings.geofence_evaluation_enabled` (default `false`); separate `_LAST_GEOFENCE_BY_*` caches so reader and geofence transitions don't clobber each other. **Both reader-bound and geofence emits now include `zone_kind` in the payload** so downstream consumers can route per design §4.1.
- [done] **DwellWorker** — periodic background task (interval `settings.dwell_worker_interval_s`, default 60s) snapshots in-process tracker, fires `zone.dwell_exceeded` alerts when threshold elapsed; per-rule per-subject cooldown. **`DwellTracker` is write-through to `subject_current_zone` (migration 027)** and hydrates from the table on startup so dwell state survives worker restart and works in multi-worker deployments per design §5.2.
- [done] **UI:** Map page (Leaflet + react-leaflet, **provider-agnostic tiles** via `MapConfigResolver`) — live asset markers + stock-density heat tiles, layer toggle (Assets / Zones / Stock density), zone polygon overlay, 24h time-slider path replay, mobility ring for `metadata.mobility==='mobile'`. UI footer renders the resolver's `attribution` string; OSM-default footer note shown when tile provider is unset. ([TagPulse-UI#4](https://github.com/9owlsboston/TagPulse-UI/pull/4))
- [done] **UI:** Zone editor — polygon-draw mode (custom `PolygonDraw` click-to-vertex editor; emits valid closed GeoJSON Polygon — implemented without leaflet-draw to keep bundle lean). ([TagPulse-UI#4](https://github.com/9owlsboston/TagPulse-UI/pull/4))
- [done] **UI:** Rule wizard — geofence step (three new condition types `zone.entered` / `zone.exited` / `zone.dwell_exceeded` with subject-kind filter + cooldown). ([TagPulse-UI#4](https://github.com/9owlsboston/TagPulse-UI/pull/4))
- [done] **UI:** Carriers render with mobility ring + click-through manifest pop-out (recursive AntD `Tree` of `GET /assets/{id}/manifest`). ([TagPulse-UI#4](https://github.com/9owlsboston/TagPulse-UI/pull/4))
- [done] Simulator: `scripts/simulate_devices.py --with-gps` — synthetic GPS tracks crossing geofence polygons
- [deferred → Sprint 17c] **Antimeridian-crossing polygons** — explicitly out of v1; ray-casting + bbox prefilter assume polygons do not cross ±180°.

## Sprint 17b — mTLS for MQTT (A6 Phase 2)

> Design: [docs/adr/012-mtls-for-mqtt.md](adr/012-mtls-for-mqtt.md)
> Goal: production-grade per-device cryptographic identity.

- [done] **ADR-012** — Mosquitto 2.x + smallstep step-ca sidecar + per-tenant intermediate CA + 90-day leaf certs + `mosquitto-go-auth` HTTP backend → TagPulse `/internal/mqtt-auth`. Backward-compat dual-auth (token + cert) during migration. EMQX deferred to ADR-014.
- [done] `devices.cert_thumbprint` + `devices.cert_subject` columns (migration 026) with unique partial index `ix_devices_cert_thumbprint`
- [done] `POST /device-registry/{device_id}/cert` (admin only) — accepts PEM, parses via `cryptography` (lazy import), stores SHA-256(DER) thumbprint + RFC 4514 subject (PEM is **not** persisted), audits as `device.cert_attached`, increments `device_cert_attachments_counter`
- [deferred → Sprint 17c] **Broker enforcement & dual-auth** — Mosquitto `tls_version`, listener `cafile`/`certfile`/`keyfile` config; `mosquitto-go-auth` HTTP backend pointing at `/internal/mqtt-auth`; `tenants.require_mtls` flag for opt-in enforcement. Cert scaffolding ships now; broker-side rollout is its own sprint.
- [deferred → Sprint 17c] **step-ca Helm chart + per-tenant intermediate CA bootstrap**
- [done] **UI:** Device detail Security tab — cert thumbprint + subject display, admin-only "Attach cert" PEM upload modal (POST /device-registry/{id}/cert). ([TagPulse-UI#4](https://github.com/9owlsboston/TagPulse-UI/pull/4))

## Sprint 17c — mTLS Broker Rollout & Geofence Edge Cases (planned)

> Design: [docs/adr/012-mtls-for-mqtt.md](adr/012-mtls-for-mqtt.md), [docs/design/geofencing-and-map.md](design/geofencing-and-map.md)
> Goal: finish the mTLS story end-to-end at the broker, and close the explicit geofence edge cases deferred from 17a.

- [planned] Mosquitto 2.x listener config — `tls_version`, `cafile`/`certfile`/`keyfile`; per-tenant SNI or listener split (TBD in ADR-012 follow-up)
- [planned] `mosquitto-go-auth` HTTP backend → TagPulse `/internal/mqtt-auth` shim — verifies cert thumbprint against `devices.cert_thumbprint`, enforces tenant scope on topic ACL
- [planned] `tenants.require_mtls` flag (Alembic) — opt-in enforcement; dual-auth (token + cert) honoured while `false`
- [planned] step-ca Helm chart + per-tenant intermediate CA bootstrap; 90-day leaf rotation; revocation list publication
- [planned] **Antimeridian-crossing polygons** — split-at-±180° storage + dual-bbox prefilter; covers Pacific-spanning fleets
- [planned] Operator runbook — broker rollout, dual-auth → cert-only cutover, revocation procedure

## Sprint 18 — Subject-Scoped Telemetry: Schema & Back-Compat

> Design: [docs/design/subject-scoped-telemetry.md](design/subject-scoped-telemetry.md), [docs/adr/013-telemetry-subject-scoping.md](adr/013-telemetry-subject-scoping.md)
> Goal: introduce the `telemetry_readings` hypertable keyed on `(subject_kind, subject_id)` without changing ingest behaviour. Read-only sprint — every existing query, dashboard, and rule keeps working byte-for-byte. De-risks the multi-subject ingest cutover in Sprint 19.

- [done] **Migration: new `telemetry_readings` hypertable** (`(tenant_id, subject_kind, subject_id, metric_name, timestamp)` PK component) with RLS policy `tenant_isolation_telemetry_readings`; indexes `ix_telemetry_readings_subject` and partial `ix_telemetry_readings_device` (`WHERE device_id IS NOT NULL`). `subject_kind` enum CHECK ∈ `device|asset|lot|stock_item|zone`. ([migrations/versions/030_subject_scoped_telemetry.py](../migrations/versions/030_subject_scoped_telemetry.py))
- [done] **Migration: rename `device_telemetry` → `telemetry_readings_legacy_device`**; expose backwards-compat SQL view `device_telemetry` (`SELECT … WHERE subject_kind='device'`). All existing repositories, analytics modules, and Grafana dashboards keep working unchanged.
- [done] **One-shot back-fill** copying every legacy row → `telemetry_readings` with `subject_kind='device'`, `subject_id=device_id`, `source='device'`. Idempotent (`ON CONFLICT DO NOTHING`); a `DO $$ … $$` block raises if `legacy_count <> migrated_count`.
- [done] **`telemetry_models` extension** — `subject_kind` column (default `device`) added; `device_type` is now nullable with a CHECK enforcing `(subject_kind='device' AND device_type IS NOT NULL) OR (subject_kind<>'device' AND device_type IS NULL)`; uniqueness replaced by `(tenant_id, subject_kind, COALESCE(device_type,''))`. Pydantic `TelemetryModelCreate` validator enforces the same rule client-side. ([src/tagpulse/models/schemas.py](../src/tagpulse/models/schemas.py))
- [done] **`telemetry_quarantine` extension** — nullable `subject_kind` + `subject_id` columns added; legacy rows leave them NULL.
- [done] **Repository layer split** — new `TimescaleTelemetryReadingsRepository.insert(subject_kind=…)` writes to the new table; existing `TimescaleTelemetryRepository` API preserved unchanged but internally re-routed to `telemetry_readings WHERE subject_kind='device'`, marked `@deprecated:: Sprint 18`. ([src/tagpulse/repositories/timescaledb/telemetry.py](../src/tagpulse/repositories/timescaledb/telemetry.py))
- [done] **`downgrade()` round-trip implemented** — drops view, copies any post-upgrade `subject_kind='device'` rows back into the legacy table, renames it back, then drops the new schema in strict LIFO. Manual round-trip verified.
- [deferred → Sprint 19] **CI harness for `alembic upgrade → downgrade -1 → upgrade head` on a populated DB.** Sprint 18's acceptance criteria referenced this as "existing convention" but no such test existed in the repo for any prior migration; promoted into Sprint 19 scope rather than scope-creeping 18.
- [done] **CHANGELOG + ADR-013** documenting the rename-not-drop strategy, the SQL-view back-compat decision, the repo split, and the Sprint 18→19→20 deprecation window. ([docs/adr/013-telemetry-subject-scoping.md](adr/013-telemetry-subject-scoping.md))
- [done] **Acceptance:** every Sprint 14 telemetry test (`tests/unit/test_telemetry_*`) passes unmodified; `make check` clean (364 tests, ruff clean, mypy 91 files); back-fill row-count parity asserted in-migration.

> **Out of scope this sprint** (deferred to 19/20): multi-subject ingest emission, new APIs, UI surfaces, rules engine `subject_kind` branch. Schema-only by design so it can be reverted cleanly if the cutover hits an unforeseen issue.

## Sprint 19 — Subject-Scoped Telemetry: Multi-Subject Ingest & APIs

> Design: [docs/design/subject-scoped-telemetry.md](design/subject-scoped-telemetry.md) §4–§5, [docs/adr/013-telemetry-subject-scoping.md](adr/013-telemetry-subject-scoping.md), [docs/adr/014-telemetry-multi-subject-ingest.md](adr/014-telemetry-multi-subject-ingest.md)
> Goal: turn the Sprint 18 schema on. Telemetry that today gets attributed only to the reporting device starts emitting additional subject-scoped rows for the asset / lot / stock_item the tag resolves to, and a new HTTP/MQTT API surface lets callers query and ingest by subject directly. Still no UI or rules changes — those land in Sprint 20.

- [done] **Server-side subject resolution in the ingest pipeline.** `IngestionService._mirror_tag_borne_sensors` resolves each tag-read into `(subject_kind, subject_id)` tuples — `device` from `read.device_id`, `asset` via cached `asset_tag_bindings.get_active_by_value`, `stock_item` via `stock_items.get_active_by_binding(kind="epc"/"tid")`, `lot` derived from the resolved stock item — and writes one `telemetry_readings` row per opted-in subject × tag-borne metric (`source="tag"`). The legacy `TelemetryService.ingest_reading` device path is preserved byte-for-byte. Misses log `telemetry.subject_unresolved` at INFO and skip silently. ([src/tagpulse/ingestion/service.py](../src/tagpulse/ingestion/service.py))
- [done] **HTTP API: `GET /telemetry/readings`** — paginated subject-scoped query (`subject_kind`, `subject_id`, `metric_name`, `start`, `end`, `limit`); existing `/telemetry/*` routes unchanged. ([src/tagpulse/api/routes/telemetry.py](../src/tagpulse/api/routes/telemetry.py))
- [done] **HTTP API: `GET /telemetry/aggregates`** — bucketed avg/min/max/count, served from `cagg_telemetry_1m` (60s), `cagg_telemetry_1h` (3600s), or live `time_bucket(make_interval(secs => :secs))` for arbitrary widths. Returns 400 when `end <= start`. ([src/tagpulse/api/routes/telemetry.py](../src/tagpulse/api/routes/telemetry.py), [src/tagpulse/repositories/timescaledb/telemetry.py](../src/tagpulse/repositories/timescaledb/telemetry.py))
- [done] **`POST /telemetry/readings/ingest` (admin/editor only)** — direct write of pre-resolved external observations via `TimescaleTelemetryReadingsRepository.insert(source="external")`. *Spec deviation:* path uses `/ingest` (slash) instead of `:ingest` (colon) to stay consistent with the rest of the FastAPI surface. ([src/tagpulse/api/routes/telemetry.py](../src/tagpulse/api/routes/telemetry.py))
- [done] **MQTT topic surface** — `tenants/{tenant_id}/subjects/{subject_kind}/{subject_id}/telemetry`; subject identity comes from the topic, not the body. Helper `_parse_subject_topic` validates the 6-segment shape, kind whitelist, and both UUIDs. Legacy `tenants/{tid}/devices/{did}/telemetry` keeps working unchanged. ([src/tagpulse/ingestion/mqtt_subscriber.py](../src/tagpulse/ingestion/mqtt_subscriber.py))
- [done] **Embedded `latest_telemetry`** on `GET /assets/{id}` and `GET /lots/{id}` — up to 5 latest readings (one per metric) via `DISTINCT ON (metric_name) … ORDER BY metric_name, timestamp DESC`, gated on tenant opt-in. *Spec deviation:* the 30s server-side cache is deferred to Sprint 20 because a single hypertable scan capped to N metrics is cheap enough at current scale. ([src/tagpulse/api/services/asset_service.py](../src/tagpulse/api/services/asset_service.py), [src/tagpulse/api/services/inventory_service.py](../src/tagpulse/api/services/inventory_service.py))
- [done] **Telemetry-models admin: subject_kind selector.** New `GET /telemetry-models/{subject_kind}/{key}` (validates `subject_kind ∈ {device, asset, lot, stock_item, zone}`); legacy `GET /telemetry-models/{device_type}` returns `301` to `device/{device_type}` (sunset Sprint 20). ([src/tagpulse/api/routes/telemetry_models.py](../src/tagpulse/api/routes/telemetry_models.py))
- [done] **Continuous aggregates `cagg_telemetry_1m` and `cagg_telemetry_1h`** keyed on `(tenant_id, subject_kind, subject_id, metric_name, bucket)` with `avg`/`min`/`max`/`count`. Refresh policies: 1m cagg refreshes last 2h every 1m; 1h cagg refreshes last 31d every 15m. DDL runs inside `op.get_context().autocommit_block()` because Timescale's `add_continuous_aggregate_policy` cannot run in a transaction. ([migrations/versions/031_telemetry_subject_kinds_and_caggs.py](../migrations/versions/031_telemetry_subject_kinds_and_caggs.py))
- [done] **CI: alembic round-trip harness.** `tests/integration/test_migration_round_trip.py` runs `alembic upgrade head → downgrade -1 → upgrade head` against a real TimescaleDB. Gated on `TAGPULSE_INTEGRATION_DB_URL` env var so `make test` (unit only) stays hermetic. New `make migration-check` target. *Implementation note:* did not pull in `pytest-docker` — the harness reuses any running TimescaleDB (compose, CI service container, or local). ([tests/integration/test_migration_round_trip.py](../tests/integration/test_migration_round_trip.py), [Makefile](../Makefile))
- [done] **Tenant opt-in flag `tenants.telemetry_subject_kinds JSONB DEFAULT '["device"]'`** + `TenantConfig.telemetry_subject_kinds` admin field with per-field audit diff. *Spec deviation:* a dedicated column is used instead of extending `tracking_modes` JSONB shape — `tracking_modes` is a flat `list[str]` consumed by several services that would all have to learn a new nested shape. Documented in [ADR-014 §1](adr/014-telemetry-multi-subject-ingest.md). ([migrations/versions/031_telemetry_subject_kinds_and_caggs.py](../migrations/versions/031_telemetry_subject_kinds_and_caggs.py), [src/tagpulse/api/routes/tenant_config.py](../src/tagpulse/api/routes/tenant_config.py))
- [done] **Acceptance:** Sprint 14 + Sprint 18 telemetry tests pass unmodified; 10 new unit tests in [tests/unit/test_sprint19_subject_telemetry.py](../tests/unit/test_sprint19_subject_telemetry.py) cover (a) fan-out into asset+lot+stock_item subjects, (b) opt-out skips fan-out, (c) unresolved subjects log + skip, (d) MQTT subject topic parser (4 cases), (e) `AssetService.get_asset` embed gating, (f) `/telemetry-models/{device_type}` 301 redirect. `make check` clean (374 tests).

> **Sprint 20 carry-overs:** rules-engine `subject_kind` branch, all UI surfaces (Asset Telemetry tab, Lot Cold-chain card, Assets-list temperature column, Devices→Telemetry `subject_kind` filter), `lot.cold_chain_breach` rule template, simulator extension, retiring the `device_telemetry` view + legacy table, the `/telemetry-models/{device_type}` 301 redirect, the 30s server-side `latest_telemetry` cache, and cross-process invalidation for `_TELEMETRY_SUBJECT_KINDS`.

## Sprint 20 — Subject-Scoped Telemetry: Rules & Templates (done — May 2026)

> Design: [docs/design/subject-scoped-telemetry.md](design/subject-scoped-telemetry.md) §3.4 + §6, [docs/adr/013-telemetry-subject-scoping.md](adr/013-telemetry-subject-scoping.md), [docs/adr/015-telemetry-rules-and-deprecation.md](adr/015-telemetry-rules-and-deprecation.md)
> Goal: surface subject-scoped telemetry to operators (rules + templates) and stage the Sprint 18 deprecation sunset behind a documented retention-cycle gate.

- [done] **Rules engine: new `telemetry.threshold` condition type.** *Spec deviation:* the spec called for extending the existing `threshold` rule with a `subject_kind` branch; we instead introduced a separate condition type to keep the Sprint 14 `TAG_READ_CREATED` payload byte-for-byte stable (the new path fires on a new `Topic.TELEMETRY_RECORDED` event with a different shape). Rationale documented in [ADR-015 §1](adr/015-telemetry-rules-and-deprecation.md). Pydantic `TelemetryThresholdCondition` validates `subject_kind ∈ {device, asset, lot, stock_item, zone}`, `operator ∈ {gt, lt, gte, lte, eq}`, optional `subject_id` pin, and `cooldown_s` (default 300). ([src/tagpulse/models/rule_schemas.py](../src/tagpulse/models/rule_schemas.py), [src/tagpulse/rules/evaluator.py](../src/tagpulse/rules/evaluator.py))
- [done] **`Topic.TELEMETRY_RECORDED` published by all four producers** \u2014 ingestion fan-out (`source=\"tag\"`, non-device subjects), `POST /telemetry/readings/ingest` (`source=\"external\"`), `MqttSubscriber._handle_subject_telemetry` (`source=\"external\"`), and (added during the Sprint 18/19/20 cross-sprint audit) `TelemetryService._process_reading_with_response` + `ingest_location` (`source=\"device\"`). The fourth producer closes a silent contract gap where the schema accepted `subject_kind='device'` in `telemetry.threshold` rules but no event ever matched. MQTT path collects tuples during the transaction and emits events after `session.commit()` so subscribers can re-read the row. ([src/tagpulse/events/protocol.py](../src/tagpulse/events/protocol.py), [src/tagpulse/api/services/telemetry_service.py](../src/tagpulse/api/services/telemetry_service.py), [src/tagpulse/ingestion/service.py](../src/tagpulse/ingestion/service.py), [src/tagpulse/api/routes/telemetry.py](../src/tagpulse/api/routes/telemetry.py), [src/tagpulse/ingestion/mqtt_subscriber.py](../src/tagpulse/ingestion/mqtt_subscriber.py))
- [done] **Built-in rule templates: `lot.cold_chain_breach` + `asset.high_temperature`** — convenience templates exposed via `GET /rule-templates` and `GET /rule-templates/{key}`. *Spec deviation:* templates live in code (`src/tagpulse/rules/templates.py`), not in a DB table — handful of templates expected, no per-tenant ownership model needed yet. `requires_subject_kind` is a UI hint (the backend does not gate). ([src/tagpulse/rules/templates.py](../src/tagpulse/rules/templates.py), [src/tagpulse/api/routes/rules.py](../src/tagpulse/api/routes/rules.py))
- [done] **Per-(tenant, rule, subject) cooldown via shared `_RULE_COOLDOWN_UNTIL` table.** Repeat breach for the same subject is suppressed within `cooldown_s`; distinct subjects under the same rule get independent windows. Same trade-off the Sprint 17a zone rules made (in-process; cross-process cap at N alerts per window where N = worker count).
- [done] **Simulator: `--cold-chain` flag** ([scripts/simulate_devices.py](../scripts/simulate_devices.py)). Provisions a synthetic milk product / lot / stock item idempotently, then drifts `temperature_c` via `POST /telemetry/readings/ingest` so the new rule template fires end-to-end without manual setup. `--cold-chain-period` controls cycle pacing.
- [done] **Operator runbook + ADR-015 + 20 unit tests** — [docs/runbooks/subject-scoped-telemetry.md](runbooks/subject-scoped-telemetry.md) covers tenant opt-in, rule authoring (template + by-hand), end-to-end validation, and the deprecation-sunset checklist. [docs/adr/015-telemetry-rules-and-deprecation.md](adr/015-telemetry-rules-and-deprecation.md) records every design deviation. [tests/unit/test_sprint20_telemetry_rules.py](../tests/unit/test_sprint20_telemetry_rules.py) covers the matcher (every operator + subject_id pin), the full `on_telemetry_recorded` path (fires/skips/cooldown), schema validation, and the template registry.
- [deferred to Sprint 21 — TagPulse-UI repo] **UI items**: Asset detail Telemetry tab, Lot detail Cold-chain card, Assets-list Temperature column, Devices→Telemetry `subject_kind` filter, `subject_kind` selector in the Rules editor, template gallery, alert detail context. The backend ships everything those surfaces need (APIs, templates, opt-in column, alert context). ([ADR-015 §5](adr/015-telemetry-rules-and-deprecation.md))
- [deferred to Sprint 21 — gated on retention cycle past Sprint 18 cutover] **Sprint 18 deprecation sunset**: drop `device_telemetry` view + `telemetry_readings_legacy_device` hypertable, remove `TimescaleTelemetryRepository` + `DeviceTelemetryModel` from `src/`, remove `GET /telemetry-models/{device_type}` 301. Trigger: slowest tenant's `telemetry_retention_days` cycled past first non-`device` opt-in. Precondition checks (zero `pg_stat_user_tables` reads on legacy hypertable for one full retention window, no Grafana dashboards reference the view, `grep` of `src/` clean) documented in the runbook. ([ADR-015 §6](adr/015-telemetry-rules-and-deprecation.md))
- [done] **Acceptance:** new tests pass (394 unit total, up from 374); Sprint 14 device-keyed rules untouched (regression-tested via the existing `test_zone_rules_and_dwell.py` and `test_threshold_rule.py` suites); `make check` clean (ruff + mypy + pytest).

---

## Sprint 21 — Subject-Scoped Telemetry: UI & Deprecation Sunset (done — May 2026)

> Design: [docs/design/subject-scoped-telemetry.md](design/subject-scoped-telemetry.md), [docs/adr/013-telemetry-subject-scoping.md](adr/013-telemetry-subject-scoping.md), [docs/adr/015-telemetry-rules-and-deprecation.md](adr/015-telemetry-rules-and-deprecation.md)
> Goal: ship the operator-facing UI that consumes Sprint 20 endpoints, close the Sprint 18 deprecation window, and clear two Sprint 19 carry-overs.

- [done] **All Sprint 20 UI items** shipped in [TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI) on the `sprint-17/geofencing-and-map` branch (PR #4 follow-up): Asset detail Telemetry tab, Lot detail page + Cold-chain card (new `/inventory/lots/:id` route), Assets-list opt-in Temperature column, Devices → Telemetry `subject_kind` filter, Rules editor `telemetry.threshold` condition + `subject_kind` selector + template gallery (consumes `GET /rule-templates`), Alert history subject context column + expandable context JSON, tenant settings subject-scoped telemetry opt-in card. Shared `<SubjectTelemetryTab>` component + `useSubjectTelemetry` hook back the asset / lot views; UI is gated on the new `tenant.telemetry_subject_kinds` array. Test count 42 → 44 (`LotDetail.test.tsx`). ([TagPulse-UI CHANGELOG](https://github.com/9owlsboston/TagPulse-UI/blob/main/CHANGELOG.md))
- [done] **Sprint 18 deprecation sunset** ([migration 031 → 032](../migrations/versions/032_drop_legacy_device_telemetry.py)). Drops the `device_telemetry` back-compat view + `telemetry_readings_legacy_device` hypertable + RLS policy + lookup index. The deprecated `TimescaleTelemetryRepository` and `DeviceTelemetryModel` are removed; `TimescaleTelemetryReadingsRepository` now owns both the subject-aware surface (`insert` / `query_by_subject` / `latest_per_metric` / `aggregate`) and the Sprint 14 device-shaped surface (`insert_reading` / `query` / `quarantine` / `list_quarantine`) consumed by `TelemetryService`. The 301 redirect on `GET /telemetry-models/{device_type}` becomes a `410 Gone` with a migration hint pointing at the subject-scoped form. *Spec deviation:* the ADR-015 §6 retention-cycle gate (slowest tenant's `telemetry_retention_days` past first non-`device` opt-in) is documented in the [runbook](runbooks/subject-scoped-telemetry.md); operators run the precondition checks before applying migration 032 in production. ([src/tagpulse/repositories/timescaledb/telemetry.py](../src/tagpulse/repositories/timescaledb/telemetry.py), [src/tagpulse/api/services/telemetry_service.py](../src/tagpulse/api/services/telemetry_service.py), [src/tagpulse/api/routes/telemetry_models.py](../src/tagpulse/api/routes/telemetry_models.py))
- [done] **30 s server-side `latest_telemetry` cache** on `GET /assets/{id}` / `GET /lots/{id}` — Sprint 19 carry-over. New `tagpulse.core.ttl_cache.TTLCache` primitive; `LATEST_TELEMETRY_CACHE` keyed on `(tenant_id, subject_kind, subject_id)` coalesces the `DISTINCT ON (metric_name)` lookup so an F5-mashed asset detail page does not hammer the hypertable. ([src/tagpulse/core/ttl_cache.py](../src/tagpulse/core/ttl_cache.py), [src/tagpulse/core/telemetry_caches.py](../src/tagpulse/core/telemetry_caches.py))
- [done] **Cross-process invalidation for `_TELEMETRY_SUBJECT_KINDS` cache** — Sprint 19 carry-over, resolved per ADR-015 §5. The unbounded process-local dict was replaced with the shared `SUBJECT_KINDS_CACHE` (30 s TTL); `PATCH /tenant/config` calls `invalidate_subject_kinds(tenant_id)` so the writing worker sees the new opt-in immediately, and sibling workers converge within one TTL. *Spec deviation:* Redis pub/sub was the alternative — short TTL was picked because the wrong outcome on a flip is "operator waits 30 s", not data loss; re-open if operators report the settle time as a problem.
- [done] **Acceptance:** legacy view + hypertable dropped without test breakage; full unit suite green at 408 tests (was 395; +13 from `tests/unit/test_sprint21_caches.py` covering `TTLCache` semantics, `SUBJECT_KINDS_CACHE` invalidation, and `LATEST_TELEMETRY_CACHE` coalescing on `AssetService.get_asset`). `make check` clean (ruff + mypy + pytest). UI smoke-test (firing `lot.cold_chain_breach` end-to-end against the simulator) is gated on the TagPulse-UI repo landing the rules editor.

---

## Sprint 22 — Cloud Readiness (Azure first, multi-cloud-shaped)

> Design: ADR-016 (this sprint), [docs/adr/008-multi-tenancy-strategy.md](adr/008-multi-tenancy-strategy.md) (tenant-export shape), [docs/adr/002-mqtt-device-connectivity.md](adr/002-mqtt-device-connectivity.md) (broker target)
> Goal: close the 12 must-fix gaps that block first cloud deploy. Land a per-provider IaC layout with Azure as the v1 target; keep the data layer portable so cross-cloud migration is a `pg_dump --where=tenant_id` + Helm-replay drill, not a rewrite. EMQX broker cutover, mTLS broker rollout (Sprint 17c), and AWS/GCP IaC implementations stay deferred — Sprint 22 ships only the **structure** for the latter two so they aren't a refactor when scheduled.

### Phase A — Config & runtime hardening (no cloud account required; ship first)

- [done] **A1 — Strip dev defaults from `Settings`.** `jwt_secret` and `database_url` lose their hardcoded fallbacks; new `environment: Literal["dev","staging","production"] = "dev"` field; `Settings` validator raises at import time when `environment != "dev"` and either secret is missing or matches a known dev value. Closes the "every cloud deployment shares the same JWT key if env var unset" foot-gun. ([src/tagpulse/core/config.py](../src/tagpulse/core/config.py))
- [done] **A2 — CORS hardening.** `allow_methods` + `allow_headers` become explicit lists in `Settings`; wildcard `*` origin rejected at startup when `environment != "dev"`. ([src/tagpulse/api/main.py](../src/tagpulse/api/main.py))
- [done] **A3 — Geofence flag default audit.** Kept default `False`; startup emits a WARN when `environment != "dev"` boots with geofence evaluation off, and the flag now appears in `/health/ready`'s config snapshot for cloud operators. ([src/tagpulse/api/main.py](../src/tagpulse/api/main.py), [src/tagpulse/api/routes/health.py](../src/tagpulse/api/routes/health.py))
- [done] **A4 — Global rate-limit middleware.** In-process token bucket keyed on `(tenant_id, route_class)`; routes classified as `ingest` / `read` / `write` / `admin` with separate per-tenant limits in `Settings`. Per-tenant overrides via new `tenants.rate_limit_overrides JSONB` (Alembic migration 033) and a `PATCH /tenant/config` extension that invalidates the limiter cache. ([src/tagpulse/core/rate_limit.py](../src/tagpulse/core/rate_limit.py), [migrations/versions/033_tenant_rate_limit_overrides.py](../migrations/versions/033_tenant_rate_limit_overrides.py), [src/tagpulse/api/routes/tenant_config.py](../src/tagpulse/api/routes/tenant_config.py))
- [done] **A5 — `MAX_INGEST_PAYLOAD_BYTES` surfaced in `/health/ready`.** `/health/ready` and `/health/detail` now embed a `config` snapshot (environment, max-payload bytes, clock-enforce, geofence flag, rate-limit-enabled, strict-migration-check) so cloud operators can verify env-var wiring without shelling into the container. ([src/tagpulse/api/routes/health.py](../src/tagpulse/api/routes/health.py))
- [done] **A6 — `/health/live` vs `/health/ready` split.** Container Apps / k8s convention: `/health/live` = process up; `/health/ready` = DB reachable + MQTT subscriber connected + `alembic_version == head`. Existing single `/health` keeps working (delegates to liveness). ([src/tagpulse/api/routes/health.py](../src/tagpulse/api/routes/health.py))
- [done] **A7 — Startup migration-version assertion.** API refuses to boot if `alembic_version` ≠ `head` when `strict_migration_check=True` (forced by the strict-mode validator in staging/production, opt-in in dev). `/health/ready` reports the same comparison. ([src/tagpulse/core/migration_check.py](../src/tagpulse/core/migration_check.py), [src/tagpulse/api/main.py](../src/tagpulse/api/main.py))

### Phase B — Container & migration pipeline

- [done] **B1 — Split Dockerfile into three images.** Multi-target Dockerfile with stages `api` (HTTP only, `WORKERS_INLINE=false`), `worker` (MQTT subscriber + `DwellWorker` + `inventory_rule_worker` + alert/analytics/webhook, `WORKERS_INLINE=true`), and `migrations` (one-shot `alembic upgrade head`). New `workers_inline` Settings flag gates worker startup in `lifespan`; `event_bus` and `usage_meter` remain unconditional because HTTP routes / SSE depend on them. `docker-compose.yml` now boots api + worker + migrations as separate services. ([Dockerfile](../Dockerfile), [src/tagpulse/core/config.py](../src/tagpulse/core/config.py), [src/tagpulse/api/main.py](../src/tagpulse/api/main.py), [docker-compose.yml](../docker-compose.yml))
- [done] **B2 — `deploy/common/migrations-job.yaml`.** Provider-agnostic k8s `Job` running the `tagpulse-migrations` image to completion (`restartPolicy: OnFailure`, `backoffLimit: 2`, `ttlSecondsAfterFinished: 3600`) before API/Worker rollouts. Container Apps Job equivalent will reuse the same image + env-var contract in Phase C. ([deploy/common/migrations-job.yaml](../deploy/common/migrations-job.yaml))
- [done] **B3 — GitHub Actions `build-and-push.yml`.** Matrix builds `tagpulse-{api,worker,migrations}` from the matching Dockerfile targets, tags `ghcr.io/9owlsboston/tagpulse-{component}:{git-sha}` + `:latest` on `main`; provenance attestation via `actions/attest-build-provenance`. PRs build-only (no push), pushes to `main` and version tags publish. ([.github/workflows/build-and-push.yml](../.github/workflows/build-and-push.yml))
- [done] **B4 — `deploy/common/helm/tagpulse/` chart.** Provider-agnostic Helm chart deploying api Deployment + Service + HPA + PDB, worker Deployment (single replica, `Recreate` strategy so MQTT never double-subscribes), pre-rollout migrations Job (Helm `pre-install,pre-upgrade` hook), ServiceMonitor (optional, Prometheus Operator clusters), ServiceAccount, and a shared `_helpers.tpl` env-var block. Values overlay per cloud; chart is the canonical "what runs where" spec for AWS/GCP later. ([deploy/common/helm/tagpulse/](../deploy/common/helm/tagpulse/))

### Phase C — Azure deployment (first provider)

- [planned] **C1 — `deploy/azure/bicep/` modules.** Subscription-scope `main.bicep` orchestrating:
  - **postgres** — Azure Database for PostgreSQL Flexible Server, TimescaleDB extension via `azure.extensions`, Entra ID admin, private endpoint into VNet, geo-redundant backup, `pgvector` enabled (future-proofs AI Phase 1 backlog).
  - **container-apps** — three apps (api, worker, ui) + one job (migrations); managed identity for Postgres + Key Vault; KEDA scaling on HTTP for api, static `1` worker for v1 (MQTT-queue-depth scaler deferred).
  - **keyvault** — JWT secret, Postgres admin password (until passwordless), MQTT broker creds; managed identity → `Key Vault Secrets User`.
  - **acr** — container registry; Container Apps pulls via managed identity.
  - **log-analytics + app-insights** — destination for the existing Sprint 11 OTel exporter; Container Apps native integration.
  - **mqtt** — parameterized: (a) ACI-hosted Mosquitto for v1 (~$15/mo, single-node, no HA), or (b) external EMQX Cloud subscription wired in via `keyvault` (managed, ~$50+/mo, HA). Pick at deploy time; module signature identical.
  - **front-door + storage-static-site** — static SWA for [TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI) + Front Door for TLS/WAF in front of Container Apps.
- [planned] **C2 — `azd up` integration.** `deploy/azure/azure.yaml` + service hooks; `azd up` from a clean Azure subscription provisions everything, runs the migrations job, deploys all three images.
- [planned] **C3 — OTel → App Insights wiring.** `OTEL_EXPORTER_OTLP_ENDPOINT` env var sourced from App Insights connection string; existing Sprint 11 OTel SDK does the rest.
- [planned] **C4 — `deploy-azure.yml` GHA workflow.** Continuous deployment for ACA. Trigger: push of a `v*` tag or manual `workflow_dispatch` (no auto-deploy on `main` push). Steps: OIDC federated login to Azure (no long-lived secrets) → `az containerapp job start` for `tagpulse-migrations` and wait for completion → `az containerapp update --image ghcr.io/9owlsboston/tagpulse-api:<tag>` → same for worker. Reuses images published by `build-and-push.yml` (B3) — no rebuild, just promotion. Environment-protected via GitHub `production` environment so a manual approval gate sits before any prod rollout. Rollback = re-run with the previous tag.

### Phase D — Portable data layer (the "easy migration between clouds" deliverable)

- [planned] **D1 — `deploy/portable/data-migration/export_tenant.py`.** Wraps `pg_dump --table=… --where="tenant_id='…'"` for every tenant-scoped table in FK-dependency order, plus the `tenants` row + `tenant_quotas` + `users` + `api_keys`. Output: a single `.tar.zst` per tenant + a JSON manifest with row counts and source schema version. Builds directly on the ADR-008 Tier-2 sovereign-tenant promotion runbook that's been a backlog item since Sprint 13b.
- [planned] **D2 — `deploy/portable/data-migration/import_tenant.py`.** Validates manifest schema-version matches `alembic_version head` of the target, optionally remaps `tenant_id` (UUID collision avoidance), restores in dependency order, runs `COUNT(*)` parity checks against the manifest. Refuses to import partial/corrupt archives.
- [planned] **D3 — Cross-cloud DR runbook `docs/runbooks/cross-cloud-migration.md`.** Azure → AWS step-by-step (provision target → run migrations → export from source → upload archive → import to target → DNS cutover → decommission source). The actual cross-cloud drill is Sprint 23+ work; the runbook is the deliverable for Sprint 22.
- [planned] **D4 — `tagpulse.storage` blob abstraction.** Protocol `BlobStore` with implementations for Azure Blob, S3, GCS, local FS. Used by D1/D2 immediately; unblocks the Sprint 8 backlog "scheduled CSV exports" item without a second abstraction. Selected at runtime via `STORAGE_BACKEND` + provider-specific env vars.

### Phase E — Observability & operations

- [planned] **E1 — Prometheus scrape wiring.** `ServiceMonitor` template in the Helm chart (B4); for Container Apps, the Azure Monitor managed Prometheus add-on with a scrape config targeting the api + worker `/metrics` endpoints. `ops/prometheus/alerts.yml` ported into Helm values + Azure Managed Grafana dashboard JSON checked into `deploy/azure/grafana/`.
- [planned] **E2 — First-deploy runbook `docs/runbooks/azure-first-deploy.md`.** Prerequisites (subscription, az CLI, azd, GitHub PAT for ACR), `azd up` walkthrough, smoke test using `scripts/smoke_setup.py --full` against the deployed instance, troubleshooting top-10. Skeleton mirrors at `docs/runbooks/aws-first-deploy.md` + `gcp-first-deploy.md` (TODO-only — gated on Sprint 23+).
- [planned] **E3 — CI gating on migration round-trip.** Promote the existing `TAGPULSE_INTEGRATION_DB_URL` round-trip test (Sprint 19) into the GitHub Actions matrix with a TimescaleDB service container so `make migration-check` blocks merge to `main`. Closes the Sprint 19 carry-over flagged in the cloud-readiness review.

### Phase F — Other-cloud skeletons (no implementation, just structure)

- [planned] **F1 — `deploy/aws/` skeleton.** README + Terraform module skeleton listing the 1:1 mapping (Container Apps → ECS Fargate or EKS, Azure DB → RDS Postgres + Timescale extension *or* Aiven, Front Door → CloudFront + WAF, Key Vault → Secrets Manager, App Insights → CloudWatch + AMP, ACR → ECR). Stub `main.tf` is `# TODO Sprint 23+`; no resources provision.
- [planned] **F2 — `deploy/gcp/` skeleton.** Same shape (Cloud Run / GKE → Cloud SQL → Cloud Armor + Cloud Load Balancing → Secret Manager → Cloud Logging → Artifact Registry).

### Phase G — Documentation

- [planned] **G1 — ADR-016 — Multi-cloud deployment strategy.** Records the "Helm chart is the portable spec, IaC is per-provider, data layer is `pg_dump --where=tenant_id`" decision; pins the Azure-first ordering; documents the rate-limit-middleware design choice (in-process vs Redis); locks in the `tagpulse.storage` blob abstraction shape.
- [planned] **G2 — `docs/architecture.md` updated** with deployment topology diagram (Azure first; per-provider variants stubbed).
- [planned] **G3 — `CHANGELOG.md` Sprint 22 section** + per-phase commits.

### Acceptance criteria

- `azd up` from a clean Azure subscription stands up the working stack; `python scripts/smoke_setup.py --full` green against the deployed instance.
- `python deploy/portable/data-migration/export_tenant.py --tenant <id> --out azure-blob://…` round-trips into a second Azure deployment via `import_tenant.py` cleanly. (Cross-cloud — Azure → AWS — drill is deferred to Sprint 23 once Phase F has real implementations.)
- Phase A items shipped; `make check` clean; `make migration-check` runs in CI and blocks merge.
- No regression for `ENVIRONMENT=dev` developer workflow — `make run` + `scripts/smoke_setup.py` still work without setting any of the new strict-mode env vars.
- Helm chart (B4) deploys cleanly against `kind` (local) for portability sanity-check, even though k8s isn't the v1 production target.

### Deferred to Sprint 23+

- Real AWS / GCP IaC (only skeletons in Sprint 22).
- EMQX Cloud production cutover (separate ADR — mTLS broker rollout per Sprint 17c is the natural pairing).
- Cross-cloud DR drill (real export-from-Azure → import-to-AWS dry run).
- Passwordless Postgres via Entra ID (Phase C1 wires the secret today; flip to passwordless once first deployment is stable).
- KEDA scaling of `tagpulse-worker` on MQTT queue depth (static `replicas=1` for v1).
- `slowapi` / Redis-backed distributed rate limiter (A4 ships in-process; revisit when first multi-replica API tier hits rate-limit drift).

---

## Backlog (not scheduled)
- Cloud-to-device commands (reader configuration push via MQTT) (G8)
- Bulk device operations / jobs (G9)
- **Database-per-tenant for data residency (ADR-008 Tier 2).** Routing mechanism (`db_pool_key`, `PoolRegistry`, `tenant_context()`) ships in v1 (Sprint 13b); backlog covers per-customer pool provisioning, regional cluster setup, and the shared→sovereign promotion runbook (`pg_dump` filtered by `tenant_id` + one row update on `tenants.db_pool_key`). Gated on first sovereign / regulated customer.
- Edge gateway with store-and-forward (G10)
- Scheduled data exports with croniter (G12)
- Data export transformations (G11)
- Customizable drag-and-drop dashboards (react-grid-layout, Sprint 9+) (G13)
- Device type-specific UI views (G14)
- Second device type support (beyond RFID readers)
- Mobile app for field technicians
- MQTT connection metering via broker plugin/proxy
- ~~Pallet-of-cases hierarchy (`stock_items.parent_stock_item_id`) for SSCC → SGTIN containment~~ — **promoted to Sprint 15b** per [mobile-carriers-and-manifests.md](design/mobile-carriers-and-manifests.md)
- Inventory cycle counts and reconciliation workflows
- ~~Non-RFID carrier integration (truck without onboard reader; location pushed via integration layer / TMS)~~ — **resolved as the generic `external_locations` endpoint in Sprint 15** per [mobile-carriers-and-manifests.md §10 Q5](design/mobile-carriers-and-manifests.md). Replaced by:
- **TMS vendor adapters (Samsara, Geotab, Motive, etc.)** — paid integration tier in `src/tagpulse/integrations/tms/`; each adapter is a thin polling service that calls `POST /assets/{id}/external-position` (the generic endpoint shipped in Sprint 15). Gated on first paying customer requesting a specific vendor; nothing built in v1 is wasted.
- Route adherence / ETA analytics module (depends on Sprint 17a `subject.zone_changed` for `subject_kind='device'`)
- Kit / BOM models (one stock item composed of N child SKUs)
- Cross-mode subject hierarchy (asset *containing* stock_items — e.g., truck-as-asset carries inventory)
- **AI Phase 1** — read-only LLM Q&A: NL Data Explorer, alert / incident summarization, **Ask** panel in UI. Server-side, tool-calling against existing service layer. Per [llm-integration-strategy.md](design/llm-integration-strategy.md) §7. Sprint-scoped post-Sprint 17.
- **AI Phase 2** — authoring assist: NL → Rule DSL draft, NL → Zone polygon draft, both confirm-required. Per [llm-integration-strategy.md](design/llm-integration-strategy.md) §7.
- **AI Phase 3** — proactive summarization (auto incident reports, scheduled shift reports, anomaly explanation on alerts). Customer-demand-gated.
- **AI Phase 4 (parking lot)** — edge-resident SLM. Not planned; gated on a real customer scenario per [llm-integration-strategy.md](design/llm-integration-strategy.md) §3.2 / §6.
- **Gen2v2 Authenticate (cryptographic tag authentication).** Gated on **(a)** at least one customer with a contractual or compliance-driven anti-counterfeit requirement, **(b)** customer commitment to source Gen2v2-capable tags and readers (~2–10× tag price; reader-firmware support varies), **(c)** a parallel decision on the broader serialization / EPCIS compliance feature set (DSCSA, EU FMD) so anti-counterfeit ships as a coherent story. When triggered: ADR for key-management architecture (server-side vs reader-side, optional HSM), edge-contract extension for reader Authenticate passthrough, new `tag_credentials` table + commissioning workflow. Threat-model framing established in [rfid-tag-data-model.md §4.4](design/rfid-tag-data-model.md).
- **ADR-013 — PostGIS adoption.** Triggered automatically by either OTel-alert condition shipped in Sprint 17a (p99 evaluation > 10 ms OR p95 candidates-per-evaluation > 50, sustained 1h for any tenant). When triggered: schema rewrite `zones.polygon_geojson` → `zones.polygon GEOMETRY(Polygon, 4326)` + GIST index, replace Shapely with `ST_Contains`/`ST_DWithin`, add PostGIS to integration-test containers, update portability gate. Per [geofencing-and-map.md §11 Q5](design/geofencing-and-map.md).
- **ADR-014 — production tile provider choice.** Triggered by first paying customer or public demo. Options: self-hosted (TileServer GL + OpenMapTiles or Protomaps), managed (Mapbox / MapTiler / Stadia), or per-customer mix. `MapConfigResolver` abstraction (Sprint 17a) makes the eventual decision a settings change. Per [geofencing-and-map.md §11 Q4](design/geofencing-and-map.md).
- **TileServer GL container + OpenMapTiles/Protomaps tooling.** Gated on Option-A choice in ADR-014 above. Cost profile (per design discussion): regional extract ~$2–5/mo cloud-hosted; planet extract ~$30–400/mo depending on data subscription. Free for customer-hosted on-prem deployments.
