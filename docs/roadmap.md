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

- [done] **C1 — `deploy/azure/bicep/` modules.** Subscription-scope [main.bicep](../deploy/azure/bicep/main.bicep) + [main.bicepparam](../deploy/azure/bicep/main.bicepparam) → resource group → [workload.bicep](../deploy/azure/bicep/workload.bicep) composing 10 modules: [acr](../deploy/azure/bicep/modules/acr.bicep) (Basic, no admin, no anon pull), [keyvault](../deploy/azure/bicep/modules/keyvault.bicep) (RBAC mode, seeds 3 secrets), [monitoring](../deploy/azure/bicep/modules/monitoring.bicep) (Log Analytics + workspace-based App Insights), [postgres](../deploy/azure/bicep/modules/postgres.bicep) (Flexible Server `Standard_B1ms` Burstable + TimescaleDB extension allow-list + `shared_preload_libraries`), [mqtt](../deploy/azure/bicep/modules/mqtt.bicep) (single-node Mosquitto on ACI with Azure Files-backed config + data shares), [container-apps-env](../deploy/azure/bicep/modules/container-apps-env.bicep), [identity](../deploy/azure/bicep/modules/identity.bicep) (UAMI + AcrPull + KV Secrets User), [container-app](../deploy/azure/bicep/modules/container-app.bicep) (parameterized api vs worker), [migrations-job](../deploy/azure/bicep/modules/migrations-job.bicep) (`Microsoft.App/jobs` manual-trigger), [static-web-app](../deploy/azure/bicep/modules/static-web-app.bicep) (Free tier, restricted region list). Hardening backlog (private endpoint for Postgres, Front Door + WAF, EMQX HA, passwordless Postgres, geo-redundant backup) deferred by design — see [deploy/azure/README.md](../deploy/azure/README.md).
- [done] **C2 — `azd up` integration.** [azure.yaml](../azure.yaml) wires three services (api/worker/migrations) to the matching Dockerfile targets pushing to ACR. **postprovision** captures `AZURE_ACR_LOGIN_SERVER` + `AZURE_IMAGE_TAG` from deployment outputs. **postdeploy** runs the migrations Container Apps Job and polls completion (10s × 60 = 10 min cap; `Failed`/`Degraded` exits 1) before api/worker revisions roll.
- [done] **C3 — OTel → App Insights wiring.** New `[azure]` extra in [pyproject.toml](../pyproject.toml) pins `azure-monitor-opentelemetry`; the [Dockerfile](../Dockerfile) build stage installs `pip install ".[azure]"`. [src/tagpulse/core/telemetry.py](../src/tagpulse/core/telemetry.py) soft-imports `configure_azure_monitor` and uses it when `APPLICATIONINSIGHTS_CONNECTION_STRING` is set, taking precedence over the OTLP path. The Container Apps Bicep wires the connection string + `OTEL_SERVICE_NAME` + `OTEL_RESOURCE_ATTRIBUTES`.
- [done] **C4 — `deploy-azure.yml` GHA workflow.** [.github/workflows/deploy-azure.yml](../.github/workflows/deploy-azure.yml). Trigger: `v*` tag push or `workflow_dispatch` (no auto-deploy on `main`). OIDC federation only (no PATs); `production` GitHub environment gates manual approval. Verifies all three images exist in ACR at the target tag → runs migrations job to completion → `az containerapp update` api + worker → smoke `https://${api-fqdn}/health/ready`. Rollback = re-run with the previous tag. Companion change: [build-and-push.yml](../.github/workflows/build-and-push.yml) now dual-pushes to GHCR (dev) + ACR (prod) when `vars.AZURE_ACR_NAME` is set.

### Phase D — Portable data layer (the "easy migration between clouds" deliverable)

- [planned] **D1 — `deploy/portable/data-migration/export_tenant.py`.** Wraps `pg_dump --table=… --where="tenant_id='…'"` for every tenant-scoped table in FK-dependency order, plus the `tenants` row + `tenant_quotas` + `users` + `api_keys`. Output: a single `.tar.zst` per tenant + a JSON manifest with row counts and source schema version. Builds directly on the ADR-008 Tier-2 sovereign-tenant promotion runbook that's been a backlog item since Sprint 13b.
- [planned] **D2 — `deploy/portable/data-migration/import_tenant.py`.** Validates manifest schema-version matches `alembic_version head` of the target, optionally remaps `tenant_id` (UUID collision avoidance), restores in dependency order, runs `COUNT(*)` parity checks against the manifest. Refuses to import partial/corrupt archives.
- [planned] **D3 — Cross-cloud DR runbook `docs/runbooks/cross-cloud-migration.md`.** Azure → AWS step-by-step (provision target → run migrations → export from source → upload archive → import to target → DNS cutover → decommission source). The actual cross-cloud drill is Sprint 23+ work; the runbook is the deliverable for Sprint 22.
- [planned] **D4 — `tagpulse.storage` blob abstraction.** Protocol `BlobStore` with implementations for Azure Blob, S3, GCS, local FS. Used by D1/D2 immediately; unblocks the Sprint 8 backlog "scheduled CSV exports" item without a second abstraction. Selected at runtime via `STORAGE_BACKEND` + provider-specific env vars.

### Phase E — Observability & operations

- [planned] **E1 — Prometheus scrape wiring.** `ServiceMonitor` template in the Helm chart (B4); for Container Apps, the Azure Monitor managed Prometheus add-on with a scrape config targeting the api + worker `/metrics` endpoints. `ops/prometheus/alerts.yml` ported into Helm values + Azure Managed Grafana dashboard JSON checked into `deploy/azure/grafana/`.
- [done] **E2 — First-deploy runbook [`docs/runbooks/azure-first-deploy.md`](runbooks/azure-first-deploy.md).** Six-phase checklist: prerequisites (CLI versions, RP registration, RBAC), per-env `.env.<env>` bootstrap, first `azd up` + MQTT broker file-share seeding, smoke tests (`/health/live`, `/health/ready` field-by-field, `smoke_setup.py --full`, App Insights traces, worker logs), per-environment CI/CD wiring (GitHub Environment + federated credential + role assignments + 5 Environment variables), and production cutover gates (backups, KV soft-delete, alerts, hardening-backlog review). Plus a top-10 common-failures table and a decommission recipe. Discoverable from [README.md](../README.md#deployment), [deploy/azure/README.md](../deploy/azure/README.md), and [docs/runbooks/README.md](runbooks/README.md). Skeleton mirrors at `docs/runbooks/aws-first-deploy.md` + `gcp-first-deploy.md` remain TODO-only — gated on Sprint 23+.
- [planned] **E3 — CI gating on migration round-trip.** Promote the existing `TAGPULSE_INTEGRATION_DB_URL` round-trip test (Sprint 19) into the GitHub Actions matrix with a TimescaleDB service container so `make migration-check` blocks merge to `main`. Closes the Sprint 19 carry-over flagged in the cloud-readiness review.

### Phase F — Other-cloud skeletons (no implementation, just structure)

- [planned] **F1 — `deploy/aws/` skeleton.** README + Terraform module skeleton listing the 1:1 mapping (Container Apps → ECS Fargate or EKS, Azure DB → RDS Postgres + Timescale extension *or* Aiven, Front Door → CloudFront + WAF, Key Vault → Secrets Manager, App Insights → CloudWatch + AMP, ACR → ECR). Stub `main.tf` is `# TODO Sprint 23+`; no resources provision.
- [planned] **F2 — `deploy/gcp/` skeleton.** Same shape (Cloud Run / GKE → Cloud SQL → Cloud Armor + Cloud Load Balancing → Secret Manager → Cloud Logging → Artifact Registry).

### Phase G — Documentation

- [shipped] **G1 — ADR-016 — Multi-cloud deployment strategy.** Records the "Helm chart is the portable spec, IaC is per-provider, data layer is `pg_dump --where=tenant_id`" decision; pins the Azure-first ordering; documents the rate-limit-middleware design choice (in-process vs Redis); locks in the `tagpulse.storage` blob abstraction shape.
- [planned] **G2 — `docs/architecture.md` updated** with deployment topology diagram (Azure first; per-provider variants stubbed).
- [shipped] **G3 — `CHANGELOG.md` Sprint 22 section** + per-phase commits.

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

## Sprint 23 — Network Hardening (KV private endpoint + VNet-integrated ACA)

> Design: ADR-017 (this sprint), [docs/runbooks/azure-first-deploy.md](runbooks/azure-first-deploy.md) (impacted), [deploy/azure/README.md](../deploy/azure/README.md) (hardening backlog → promoted)
> Goal: comply with the corporate "no public network access on Key Vault / Storage" policy that the platform team is enforcing tenant-wide. First deployment in Sprint 22-C exposed two problems: (1) the storage policy is `Modify`-mode and silently reverted `allowSharedKeyAccess=true`, breaking the Mosquitto Azure Files mount; (2) the KV policies are currently `Audit`-only but flagged for promotion to `Deny`. Sprint 23 lands the proper VNet + private-endpoint topology so neither service depends on public network access, *and* removes the Mosquitto Files dependency entirely.

### Phase A — Same-day mitigation (no VNet required)

> Ships within 24h to keep the Azure deploy unblocked while Phase B is built.

- [shipped] **A1 — Custom Mosquitto image with config baked in.**
  - New `docker/mosquitto.Dockerfile` (`FROM eclipse-mosquitto:2`).
  - **Replace** the existing dev-only [docker/mosquitto.conf](../docker/mosquitto.conf) (currently `allow_anonymous true`, listener 1883 only) with the hardened conf the bootstrap script used to materialize: `allow_anonymous false`, `password_file /mosquitto/config/mosquitto.passwd`, `persistence true`, `persistence_location /mosquitto/data/`, listener 1883. (Listener 8883 + TLS stays out of scope — TLS is the ADR-012 mTLS workstream.) Local `docker compose` keeps using the dev conf via a build arg or a separate compose-only conf so dev is unaffected.
  - New `docker/mosquitto-entrypoint.sh` materializes `/mosquitto/config/mosquitto.passwd` from `MOSQUITTO_USERNAME` + `MOSQUITTO_PASSWORD` env vars at boot via `mosquitto_passwd -b -c`, then `exec`s the upstream entrypoint. Fails fast if either env var is empty.
  - Add `mqtt` as a fourth service in [azure.yaml](../azure.yaml) so `azd deploy mqtt` builds + pushes to ACR. ACR repo: `tagpulse-mqtt`. Service `host: containerapp` is wrong for ACI — use `host: containerapp` is not applicable; declare it as a generic image-build service (`docker.path`/`docker.target`/`registry`/`image`/`tag` only, no `host`) and let the Bicep ACI consume the resulting `${ACR}/tagpulse-mqtt:${tag}` directly. Confirm against the azd schema during implementation; fall back to a manual `docker build && az acr login && docker push` step in `preprovision` if azd's image-only services are unsupported.
- [shipped] **A2 — Drop Azure Files dependency from `mqtt.bicep`.**
  - Remove `storage`, `fileService`, `share`, `configShare`, the `volumes` block, both `volumeMounts`, and `output mqttStorageAccountName`.
  - Add module params: `acrLoginServer string`, `imageTag string`, `userAssignedIdentityId string`, `useImagePlaceholders bool` (so Phase A respects the existing first-provision placeholder pattern — ACI placeholder = `mcr.microsoft.com/azuredocs/aci-helloworld:latest`).
  - Add `identity: { type: 'UserAssigned', userAssignedIdentities: { '${userAssignedIdentityId}': {} } }` on the container group **and** `imageRegistryCredentials: [{ server: acrLoginServer, identity: userAssignedIdentityId }]` — ACI requires both for managed-identity ACR pull. The existing UAMI already has `AcrPull` (Sprint 22 [identity.bicep](../deploy/azure/bicep/modules/identity.bicep)).
  - Update [workload.bicep](../deploy/azure/bicep/workload.bicep): drop the `mqttStorageName` variable, drop `output mqttStorageAccountName`, pass the new params into the `mqtt` module call, switch the image expression to the same `useImagePlaceholders ? aciPlaceholderImage : '${acr.outputs.loginServer}/tagpulse-mqtt:${imageTag}'` pattern used for ACA modules.
  - Update [scripts/azd-image-check.sh](../scripts/azd-image-check.sh) to also gate on `tagpulse-mqtt:$TAG` existing in ACR (require **all four** repos to be present before flipping placeholders off).
  - `git rm scripts/azd-bootstrap-mqtt.sh` (no longer reachable; remove the file outright).
- [shipped] **A3 — Trade-off documented.** ADR-017 §Phase A captures the loss of broker retained-message persistence across restarts (devices republish on reconnect — acceptable for v1; obviated by Sprint 24 EMQX cutover anyway).
- [shipped] **A4 — Update [docs/runbooks/azure-first-deploy.md](runbooks/azure-first-deploy.md)** Phase 2: drop the MQTT bootstrap step; **delete** the two now-stale rows from the common-failures table (`Mosquitto ACI fails: CannotAccessStorageAccount … 403` and `/health/ready shows checks.mqtt=='error' … bootstrap`); add a "if `allowSharedKeyAccess` policy is enforced in your subscription, Sprint 23 Phase A is mandatory" callout. Add one new row covering `MQTT password env var unset` (entrypoint fast-fails).
- [shipped] **A5 — Tests** ([tests/unit/test_sprint23_phase_a.py](../tests/unit/test_sprint23_phase_a.py)): assert `docker/mosquitto.Dockerfile` parses + bases on `eclipse-mosquitto`; assert hardened conf has `allow_anonymous false` + `password_file`; assert `azure.yaml` has a `mqtt` service that targets the new Dockerfile and pushes to ACR repo `tagpulse-mqtt`; assert `mqtt.bicep` no longer references `Microsoft.Storage` and includes `imageRegistryCredentials`; assert `azd-image-check.sh` references all four repo names.

### Phase B — VNet + private endpoints (the policy-recommended path)

- [shipped] **B1 — `network.bicep` module.** New `deploy/azure/bicep/modules/network.bicep`: VNet `tpdev-vnet` (10.10.0.0/16), three subnets — `aca-infra` (10.10.0.0/23, delegated to `Microsoft.App/environments`, NSG with default-deny + ACA service-tag allow), `pe` (10.10.2.0/27, no delegation, for private endpoints), `mgmt` (10.10.3.0/27, reserved for future Bastion). Service endpoints not used (we go full private-endpoint).
- [shipped] **B2 — VNet-integrated Container Apps environment.** Update [container-apps-env.bicep](../deploy/azure/bicep/modules/container-apps-env.bicep) to set `vnetConfiguration.infrastructureSubnetId` + `internal=false` (still public ingress for the api, just with controlled egress). **Breaking change:** ACA env is immutable on this property → Sprint 22 deployments must be torn down and recreated. Migration runbook in B6.
- [shipped] **B3 — Key Vault private endpoint.** New `deploy/azure/bicep/modules/private-endpoint-kv.bicep`. Toggle KV `publicNetworkAccess=Disabled` + `networkAcls.defaultAction=Deny`, gated on the **shared** Phase-B feature flag `disablePublicNetworkAccess` (bool, **default `false`** — opt-in, so subscriptions without the policy are not regressed). Add Private DNS Zone `privatelink.vaultcore.azure.net` linked to the VNet; A-record auto-created via the `privateDnsZoneGroups` block on the PE.
- [shipped] **B4 — Postgres private endpoint** (was already on the hardening backlog — promoted). Same shape as B3; zone `privatelink.postgres.database.azure.com`. When `disablePublicNetworkAccess=true`: set `network.publicNetworkAccess=Disabled` and **remove** the `AllowAllAzureIPs` firewall rule (the `fwAllowAzure` resource in [postgres.bicep](../deploy/azure/bicep/modules/postgres.bicep) becomes a conditional `if (!disablePublicNetworkAccess)`).
- [shipped] **B5 — ACR private endpoint.** Optional but cheap to ship now. Zone `privatelink.azurecr.io`. **ACR `publicNetworkAccess` stays `Enabled` and no firewall allow-list is added in Sprint 23** — `azd deploy` from a developer laptop and the GHA runner both push to ACR, and a public-IP allow-list for hosted runners is impractical (rotating ranges, no service tag for `GitHubActions`-egress on Azure side). Closing public ACR access is its own workstream tracked under Sprint 24+ and listed in the Deferred section below.
- [shipped] **B6 — Migration runbook `docs/runbooks/sprint-23-network-cutover.md`.** Order of operations for an existing Sprint 22 environment: (1) `azd env set AZURE_ENABLE_VNET true && azd env set AZURE_DISABLE_PUBLIC_NETWORK_ACCESS true`; (2) `azd down --purge --force` (ACA env recreate is destructive — KV state is preserved by the existing self-healing recovery in `scripts/azd-kv-recover.sh`); (3) `azd provision` (Phase B Bicep applies; KV secrets reseed automatically because `secrets` are passed as Bicep params from the `.env.<env>` file); (4) `azd deploy` (api + worker + migrations + mqtt); (5) verify `/health/ready` from the api's public ingress still works **and** that `az keyvault secret show --vault-name … --name jwt-secret` from your laptop fails with `Forbidden` (proves the firewall is on); (6) toggle `AZURE_DISABLE_PUBLIC_NETWORK_ACCESS=false` + `azd provision` for break-glass dev access (see B7).
- [shipped] **B7 — Bastion-or-jumpbox decision (small).** For laptop access to KV/PG when public access is disabled. Choices documented in ADR-017: (a) Azure Bastion in the `mgmt` subnet (~$140/mo, always-on), (b) ad-hoc dev container app with a public ingress + `az` CLI for break-glass, (c) deploy-time-only public access toggle via the Phase-B bool params. Recommendation: **(c) for dev, (a) for production**.

### Phase C — Tooling & CI updates

- [shipped] **C1 — `scripts/azd-network-check.sh`.** New preflight script that confirms the VNet + 3 private endpoints (KV / Postgres / ACR) + 3 private DNS zones exist and resolve to the expected `10.10.x.x` addresses from inside the ACA env. **Use `az containerapp exec --command "python -c 'import socket,sys; print(socket.gethostbyname(sys.argv[1]))' <fqdn>"`** — our slim Python image does not include `nslookup` / `dig`. Wired as a `postdeploy` hook in [azure.yaml](../azure.yaml). Skip cleanly when `AZURE_ENABLE_VNET=false`.
- [shipped] **C2 — `deploy-azure.yml` GHA networking.** Add a smoke step that runs the same Python `socket.gethostbyname` against KV's private FQDN from inside the ACA env and asserts the result is in the `10.10.x.x` range — fails the deploy if a misconfiguration leaves clients hitting the public IP. Gated on the same env flag as C1.
- [shipped] **C3 — Update [scripts/azd-preflight.sh](../scripts/azd-preflight.sh)** to verify `Microsoft.Network` resource provider is registered when `AZURE_ENABLE_VNET=true` (no-op otherwise to preserve Sprint 22 behaviour).

### Phase D — Documentation

- [shipped] **D1 — [ADR-017 — Network hardening: VNet integration + private endpoints](adr/017-network-hardening.md).** Records the Phase A vs Phase B split, the cost of the immutable ACA env recreate, the Bastion vs ad-hoc-toggle choice for dev access, and the explicit non-goal: "Phase B does not migrate the api ingress to private/internal — public ingress is intentional for v1; Front Door + WAF stays on the post-Sprint-23 hardening backlog." Shipped on the Sprint 22-C merge.
- [shipped] **D2 — Update [deploy/azure/README.md](../deploy/azure/README.md)** "Hardening backlog" section: KV private endpoint + Postgres private endpoint move from "deferred" to "shipped in Sprint 23"; add EMQX HA, Front Door + WAF, passwordless Postgres, geo-redundant backup as the new top of the deferred list.
- [shipped] **D3 — Update [docs/runbooks/azd-survival-guide.md](runbooks/azd-survival-guide.md)** "Common gotchas" with the `allowSharedKeyAccess` policy tell-tale and the `KeyVault: Forbidden from container app` symptom + DNS resolution check.
- [shipped] **D4 — `CHANGELOG.md` Sprint 23 section.**

### Acceptance criteria

- `azd up` from a clean Azure subscription with the corporate policy in `Deny` mode succeeds end-to-end (no manual policy exemptions required).
- `az keyvault secret show` from outside the VNet returns `Forbidden`; the api can still read the same secret at startup (proves private endpoint + DNS work).
- `python -c 'import socket; print(socket.gethostbyname("tpdev-kv-*.vault.azure.net"))'` from inside an ACA replica returns a `10.10.x.x` address (slim image has no `nslookup`).
- `scripts/azd-network-check.sh` exits 0 against a freshly provisioned env.
- No regression in the Sprint 22 first-deploy runbook for environments that opt out of Phase B (`AZURE_ENABLE_VNET=false` and `AZURE_DISABLE_PUBLIC_NETWORK_ACCESS=false` — both default `false`) — both modes coexist behind the flags.
- Mosquitto ACI boots without any Azure Files mount, with config baked into the image and password sourced from KV via `secureValue` env var.
- New unit suite [tests/unit/test_sprint23_phase_a.py](../tests/unit/test_sprint23_phase_a.py) green; total test count ≥ 445.
- `make check` clean (ruff + mypy + pytest).

### Deferred to Sprint 24+

- Front Door + WAF in front of the api ingress.
- Internal-only ACA ingress (api goes fully private; consumed via Front Door or VPN).
- EMQX Cloud production cutover (replaces the single-node Mosquitto ACI entirely; obviates the Phase A custom image).
- Passwordless Postgres via Entra ID.
- Production-grade Bastion + jumpbox standard for `staging` + `production` envs.
- **Close ACR public access** (`publicNetworkAccess=Disabled` + private-endpoint-only). Requires either (a) self-hosted GHA runners inside the VNet, or (b) `dataEndpointEnabled=true` + an ACR-tasks-based push pipeline. Both are out of scope for Sprint 23.

---

## Sprint 24 — Frontend Cloud Deployment (parity with Sprint 22 backend deploy)

> Design: [docs/design/frontend-deployment.md](design/frontend-deployment.md), [docs/adr/018-frontend-cloud-deployment.md](adr/018-frontend-cloud-deployment.md)
> Companion repo: [9owlsboston/TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI) — Phase B lands there
> Goal: ship the React 19 + Vite SPA to the Azure Static Web App that Sprint 22 C-1 already provisions but never deploys into. Mirror the Sprint 22 deployment ergonomics (per-env `.env.<env>`, `*-bootstrap.sh` / `*-env-load.sh` / `*-cicd-setup.sh` / `*-cicd-verify.sh` / `*-preflight.sh` scripts, OIDC where applicable, `deploy/<provider>/ui/` skeletons for AWS + GCP). Infra stays in this repo; the SPA bundle ships from the UI repo's GHA. No combined deploy — see [ADR-018](adr/018-frontend-cloud-deployment.md) for why.

### Ownership at a glance

Every task is tagged `[backend]` (this repo, `9owlsboston/TagPulse`) or `[ui]` (sibling repo, `9owlsboston/TagPulse-UI`). Phases group by ownership for clean parallel work; no task crosses repo boundaries.

| Phase | Repo | Task count | What lands |
|---|---|---|---|
| A — Backend prerequisites | `[backend]` TagPulse | 4 | `scripts/azd-ui-token.sh`, `/health/ready` CORS surface, Bicep audit, runbook update |
| B — UI shipping path | `[ui]` TagPulse-UI | 10 | `staticwebapp.config.json`, `.env.example`, 5 mirror scripts, 2 GHA workflows, repo-local quickstart |
| C — Operator documentation | `[backend]` TagPulse | 3 | `docs/runbooks/ui-first-deploy.md`, README cross-links, runbooks index |
| D — Multi-cloud skeletons | `[backend]` TagPulse | 3 | `deploy/aws/ui/`, `deploy/gcp/ui/`, `deploy/portable/ui/` |

**Order of operations:** A and B can ship in parallel (A1 unblocks B3, but the rest of A and B are independent). C depends on A complete + B at least at the manual-deploy stage (B1–B5). D is independent of A/B/C and can ship any time.

### Phase A — Backend prerequisites (this repo, `[backend]`)

- [shipped] **A1** `[backend]` **`scripts/azd-ui-token.sh`.** Read-only helper that runs `az staticwebapp secrets list` against the env's SWA and prints the deployment `apiKey` to stdout. Used by both the operator (when wiring the UI repo's GitHub Environment by hand) and `scripts/ui-bootstrap.sh` in the UI repo (when generating `.env.<env>` automatically). Refuses to print when stdout is a TTY without `--print` to keep it out of shell history. Idempotent.
- [shipped] **A2** `[backend]` **Surface CORS origins in `/health/ready`.** Extend the existing `config` snapshot (Sprint 22 A5) to include `cors.allow_origins` (already in `Settings`; just add to the response). Lets operators confirm the SWA hostname is in the allow-list without shelling into the container — closes the most likely "SPA loads but every API call 401s with CORS error" failure mode.
- [shipped] **A3** `[backend]` **Verify `static-web-app.bicep` does not output `apiKey`.** Already shipped in Sprint 22 C-1; re-audit and pin the test assertion in [tests/unit/test_sprint24_phase_a.py](../tests/unit/test_sprint24_phase_a.py) so a future contributor doesn't accidentally turn the SWA token into a Bicep output (which would land it in `azd env` and `git status` traces).
- [shipped] **A4** `[backend]` **Update [docs/runbooks/azure-first-deploy.md](runbooks/azure-first-deploy.md) Phase 3.** New step after the `azd up` smoke tests: copy the `staticWebAppHostname` Bicep output into `CORS_ALLOW_ORIGINS=https://${api},https://${swa}` in `deploy/azure/.env.<env>`, then `azd-env-load.sh <env> && azd provision` to push the new origin list into the api revision. Order matters — without this, the SPA loads but every fetch is blocked by the strict-mode CORS validator (Sprint 22 A2).

### Phase B — TagPulse-UI repo (the actual SPA shipping path, `[ui]`)

> All Phase B items are implemented in `9owlsboston/TagPulse-UI`. Tracked here for sprint-completion accounting; the `[ui]` tag is a reminder that no PR for these items lands in this repo.

- [shipped] **B1** `[ui]` **`staticwebapp.config.json`.** SPA fallback `/* → /index.html`, plus `globalHeaders` with HSTS (1y, includeSubDomains, preload), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`. Permissive starting CSP (script-src 'self'; connect-src 'self' https://*.azurecontainerapps.io) — lock-down deferred per [ADR-018 §5](adr/018-frontend-cloud-deployment.md).
- [shipped] **B2** `[ui]` **`.env.example` for build-time vars.** At minimum `VITE_API_BASE_URL`. **No secrets** — the SWA deployment token is a CI secret, never a build var.
- [shipped] **B3** `[ui]` **`scripts/ui-bootstrap.sh <env>`.** Reads the four needed values (`apiFqdn`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_STATIC_WEB_APPS_NAME`) out of the backend repo's `azd env get-values` (note: `SERVICE_API_URI` is NOT persisted by azd for the `containerapp` host — use the `apiFqdn` Bicep output and prepend `https://` to derive `VITE_API_BASE_URL`), plus the deployment token via `scripts/azd-ui-token.sh` (Phase A1 over there), and writes `.env.<env>` at mode 0600. Refuses to overwrite without `--force`. Mirrors `scripts/azd-bootstrap.sh` from this repo.
- [shipped] **B4** `[ui]` **`scripts/ui-env-load.sh <env>`.** `source`-able loader for the current shell. Mirrors `scripts/azd-env-load.sh`.
- [shipped] **B5** `[ui]` **`scripts/ui-preflight.sh`.** Checks `node ≥20`, `npm ≥10`, `gh` signed in to `9owlsboston`, `az` signed in to the tenant from `.env.<env>`. Prints fix commands on failure. Mirrors `scripts/azd-preflight.sh`.
- [shipped] **B6** `[ui]` **`scripts/ui-cicd-setup.sh <env>`.** Idempotent. Creates the GitHub Environment (`dev` / `staging` / `production`), sets four variables (`AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_STATIC_WEB_APPS_NAME`, `VITE_API_BASE_URL`), uploads the SWA deployment token as the `AZURE_STATIC_WEB_APPS_API_TOKEN` secret. `--rotate` flag regenerates the token via `az staticwebapp secrets reset-api-key` and re-uploads. Mirrors `scripts/azd-cicd-setup.sh`.
- [shipped] **B7** `[ui]` **`scripts/ui-cicd-verify.sh <env>`.** Confirms the Environment exists with the four expected variables and the secret present. Exit 0 = ready to deploy. Mirrors `scripts/azd-cicd-verify.sh`.
- [shipped] **B8** `[ui]` **`.github/workflows/deploy-azure.yml`.** Triggers: push to `main` (auto → `dev`), `v*` tag (auto → `staging`), `workflow_dispatch` (manual any env, gated by GitHub Environment reviewer rules for `production`). Steps: checkout → `npm ci` → `npm run build` → `Azure/static-web-apps-deploy@v1` with the deployment token + the built `dist/` → curl `https://${swa-hostname}/` and assert HTTP 200 + the asset hash from `dist/index.html` is present in the response. PR previews come for free from the action when `production_branch: main` is set.
- [shipped] **B9** `[ui]` **`.github/workflows/build-and-test.yml`.** PR build + lint + typecheck + vitest. Build-only on PR; no deploy.
- [shipped] **B10** `[ui]` **`docs/azure-deploy.md` in the UI repo.** Quick-start (5 commands). Links back to `tagpulse/docs/runbooks/ui-first-deploy.md` (Phase C-1) as the canonical operator runbook.

### Phase C — Documentation (this repo, `[backend]`)

- [shipped] **C1** `[backend]` **`docs/runbooks/ui-first-deploy.md`.** Six-phase checklist mirroring [docs/runbooks/azure-first-deploy.md](runbooks/azure-first-deploy.md): (Phase 0) prereqs (`node ≥20`, `npm ≥10`, `gh` signed in, `az` signed in to the right tenant, backend `.env.<env>` exists and `azd up` succeeded), (Phase 1) per-env bootstrap (`scripts/ui-bootstrap.sh <env>` from the UI repo), (Phase 2) first manual deploy via `npx @azure/static-web-apps-cli deploy ./dist --deployment-token …` to validate the wiring, (Phase 3) post-deploy smoke tests (`curl https://${swa-host}/` returns 200; SPA loads in browser; `POST /auth/login` round-trips through the deployed api; `/health/ready` shows the SWA hostname in `cors.allow_origins`), (Phase 4) CI/CD wiring (`scripts/ui-cicd-setup.sh <env>` + `scripts/ui-cicd-verify.sh <env>` + first `git push origin main` deploys end-to-end), (Phase 5) production cutover gates (reviewer rules on the `production` Environment, branch restriction to `v*` tags + `main`, alert on deploy failure). Top-N common-failures table mirrors the backend runbook's structure: CORS rejection (api hasn't been re-provisioned with the SWA hostname); SWA token expired (run `--rotate`); `VITE_API_BASE_URL` baked-in mismatch (rebuild after backend env recreate); `staticwebapp.config.json` syntax error (deploy fails silently — the action does not validate JSON); SWA region not in the `static-web-app.bicep` allow-list (Bicep deploy fails before SWA exists). Plus a decommission recipe (`az staticwebapp delete` is enough — no soft-delete reservation like KV).
- [shipped] **C2** `[backend]` **Update [README.md](../README.md) and [deploy/azure/README.md](../deploy/azure/README.md)** to link to the UI runbook from the deployment section. Both already link to `azure-first-deploy.md`; pair them.
- [shipped] **C3** `[backend]` **Update [docs/runbooks/README.md](runbooks/README.md)** index.

### Phase D — Multi-cloud skeletons (this repo, `[backend]`, structure-only per ADR-016 precedent)

- [shipped] **D1** `[backend]` **`deploy/aws/ui/` skeleton.** `README.md` listing the AWS mapping (S3 with `index.html`/`error.html` static-website hosting → CloudFront distribution → ACM cert → Route 53 alias) and the deploy mechanism (`aws s3 sync ./dist s3://${bucket} --delete && aws cloudfront create-invalidation --distribution-id ${dist} --paths '/*'`). `main.tf` is a TODO stub; no resources provision. Same shape as the Sprint 22 F1 backend skeleton.
- [shipped] **D2** `[backend]` **`deploy/gcp/ui/` skeleton.** `README.md` with the GCP mapping (Cloud Storage bucket as static website → Cloud CDN → managed cert → Cloud DNS → Cloud Load Balancing). `main.tf` is a TODO stub. Same shape as Sprint 22 F2.
- [shipped] **D3** `[backend]` **`deploy/portable/ui/` README.** Provider-agnostic notes: the `dist/` output is identical across providers; only the upload command and the CDN-front differ. Two-paragraph cross-cloud DR appendix to the Sprint 22 D-3 runbook (when D-3 lands) — one line per provider for the upload step.

### Acceptance criteria

- `azd up` from a clean Azure subscription against this repo lands an SWA whose `appsettings.VITE_API_BASE_URL` matches the deployed api FQDN (regression check on Sprint 22 C-1).
- `scripts/ui-bootstrap.sh dev` in the UI repo generates a complete `.env.dev` from a freshly-deployed backend with no manual editing — no value-by-value prompting, no copy-paste-from-`azd env get-values`.
- `scripts/ui-cicd-setup.sh dev` + `scripts/ui-cicd-verify.sh dev` exit 0; a subsequent `git push origin main` from the UI repo deploys an SPA bundle to `https://tpdev-ui.<random>.azurestaticapps.net` within 5 minutes.
- The deployed SPA loads, hits `https://${apiFqdn}/auth/login`, gets a JWT, and reaches the dashboard — proves CORS is configured + JWT round-trip works.
- `/health/ready` reports the SWA hostname in `config.cors.allow_origins`.
- `docs/runbooks/ui-first-deploy.md` walks a fresh operator end-to-end without any external context.
- `deploy/aws/ui/README.md` + `deploy/gcp/ui/README.md` exist with provider-mapping tables and `# TODO Sprint 25+` stubs (no implementation, parity with Sprint 22 F1/F2).
- No regression in the Sprint 22 backend deploy path — `azd up` without ever touching the UI repo continues to work; the SWA just serves its empty placeholder.
- `make check` clean (ruff + mypy + pytest); new unit suite [tests/unit/test_sprint24_phase_a.py](../tests/unit/test_sprint24_phase_a.py) green.

### Deferred to Sprint 25+

- Real AWS / GCP UI implementations (only skeletons in Sprint 24).
- Custom domain wiring (`app.tagpulse.io`) — gated on registering the domain.
- SWA Standard tier upgrade — gated on Free-tier caps biting (100GB/mo bandwidth, 500K invocations/day).
- Front Door + WAF in front of both SWA and api — paired with the Sprint 23+ deferred api-side FD work, not done piecemeal.
- CSP lock-down — gated on a stable asset manifest and a few weeks of permissive-CSP traffic to enumerate legitimate origins.
- E2E test in CI (Playwright against the deployed SWA) — gated on first design refresh worth protecting.
- Automated SWA token rotation cadence — track on the post-Sprint-24 hardening backlog alongside backend secret rotation.

---

## Sprint 25 — Frontend Resilience & Observability (shipped)

> Companion repo: [9owlsboston/TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI) — Phase B + Phase D land there
> Goal: close the silent-failure gaps that Sprint 24 left behind. Today the SPA always loads (it's just static files served by the SWA edge), so a broken backend, a wrong `VITE_API_BASE_URL`, or a missing CORS entry only surfaces when the user clicks Login and stares at a hung spinner. Sprint 25 adds (a) startup health gating on the client, (b) a CI gate that refuses to deploy a SPA against an unhealthy api, (c) a post-deploy SPA-vs-api verification, (d) CSP report-only telemetry so we can finally lock the policy down without breaking the app, and (e) browser telemetry to App Insights so we can actually see frontend errors and timings instead of relying on user reports. Plus the lowest-cost items from the Sprint 24+ deferred list: automated SWA token rotation cadence and a working portable-cloud upload recipe.
>
> Out of scope (deferred to Sprint 26+): real AWS / GCP UI implementations, custom domain wiring (`app.tagpulse.io`), Front Door + WAF, SWA Standard tier upgrade, E2E (Playwright) in CI.

### Ownership at a glance

Tasks are tagged `[backend]` (this repo, `9owlsboston/TagPulse`) or `[ui]` (sibling repo, `9owlsboston/TagPulse-UI`). Phases group by ownership for clean parallel work; no task crosses repo boundaries.

| Phase | Repo | Task count | What lands |
|---|---|---|---|
| A — Backend health & CSP support | `[backend]` TagPulse | 4 | `/health/live` lightweight probe, `cors.preflight_max_age`, `/security/csp-report` endpoint, runbook update |
| B — UI resilience & CI gating | `[ui]` TagPulse-UI | 5 | Startup health gate, error boundary upgrade, deploy-time api preflight, post-deploy SPA-vs-api smoke, CSP report-only header |
| C — Browser telemetry | `[ui]` TagPulse-UI | 2 | App Insights browser SDK wired, frontend error & route-change tracking |
| D — Operational | `[backend]` TagPulse | 2 | SWA token auto-rotation workflow, runbook |
| E — Multi-cloud follow-up | `[backend]` TagPulse | 1 | `deploy/portable/ui/` working `aws s3 sync` + Cloudflare Pages recipe (still skeleton-grade, but executable) |

**Order of operations:** A and B/C can ship in parallel after **A1** (`/health/live` already exists per Sprint 22 A6 — just confirm contract) and **A3** (CSP report endpoint) land. D and E are independent and can ship any time. Phase B depends on Phase A2 (preflight cache header) only for the deploy-time gate optimization; basic functionality works without it.

### Phase A — Backend health & CSP support (this repo, `[backend]`)

- [shipped] **A1** `[backend]` **Pin the `/health/live` contract for SPA polling.** `/health/live` was previously an undocumented copy of `/health` returning `{"status":"ok"}`. Sprint 25 promotes it to the SPA's startup-gate contract: `{"status":"alive","version":"<git-sha>","build_time":"<iso8601>"}`. `Cache-Control: no-store` is set on the response so the SWA edge / browser never memoize a stale "alive". `version` and `build_time` are baked into the image at build time via `BUILD_VERSION` / `BUILD_TIME` Docker build-args ([Dockerfile](../Dockerfile), [.github/workflows/build-and-push.yml](../.github/workflows/build-and-push.yml)); local `docker build` without `--build-arg` keeps the dev sentinels (`dev` / `unknown`). The legacy `/health` keeps returning `{"status":"ok"}` for k8s probes that pre-date `/live`. ([src/tagpulse/api/routes/health.py](../src/tagpulse/api/routes/health.py), [tests/unit/test_sprint25_phase_a.py](../tests/unit/test_sprint25_phase_a.py))
- [shipped] **A2** `[backend]` **CORS preflight max-age on `/health/*` and `/auth/login`.** New `Settings.cors_preflight_max_age_seconds` (default 0; the strict-mode validator forces 600 in non-dev when left at 0, and explicit non-zero env-var values survive untouched). Wired into the global `CORSMiddleware`'s `max_age=` parameter — applies to every endpoint, not just the high-frequency ones, because Starlette's middleware is global; functionally a superset of the spec. ([src/tagpulse/core/config.py](../src/tagpulse/core/config.py), [src/tagpulse/api/main.py](../src/tagpulse/api/main.py))
- [shipped] **A3** `[backend]` **`POST /security/csp-report` endpoint.** Receives both browser report shapes — `application/csp-report` (Chromium/Safari legacy `{"csp-report": {...}}`) and `application/reports+json` (Reporting API arrays of `{type, body}` envelopes). Emits a structured WARN log with `(blocked_uri, document_uri, violated_directive, source_file, line_number, column_number, user_agent)`. Increments the Prometheus counter `tagpulse_csp_violations_total{directive=...}`. Per-IP sliding-window rate limit at 10 reports / 60s (in-process; same trade-off as Sprint 22 A4). Open without auth (browsers don't send credentials on report POSTs). Bypassed in the Sprint 22 A4 tenant-keyed limiter ([src/tagpulse/core/rate_limit.py](../src/tagpulse/core/rate_limit.py)). Returns `204 No Content` on success, `429` when the per-IP cap is exceeded, and `204` (logged + dropped) on malformed JSON so a misbehaving extension can't 500 the api. ([src/tagpulse/api/routes/security.py](../src/tagpulse/api/routes/security.py))
- [shipped] **A4** `[backend]` **Updated [docs/runbooks/azure-first-deploy.md](runbooks/azure-first-deploy.md) Phase 3.** `/health/live` smoke now asserts the new contract shape + `Cache-Control: no-store` header. New **§3b — SPA-vs-api consistency smoke** catches the most insidious post-deploy failure (SPA built against a stale `VITE_API_BASE_URL`); pulls `index.html` from the SWA, asserts `/health/ready` against the embedded api URL, and verifies the SWA hostname is in the api's CORS allow-list. New **§3c — CSP violation triage** documents the report shape, the Log Analytics KQL for top violations, and the priority order (`script-src` / `connect-src` are policy actually breaking the SPA; `img-src` / `font-src` are typically extension noise).

### Phase B — UI resilience & CI gating (TagPulse-UI repo, `[ui]`)

> All Phase B items are implemented in `9owlsboston/TagPulse-UI`. Tracked here for sprint-completion accounting.

- [shipped] **B1** `[ui]` **Startup health gate in `<App />`.** On mount (and on every route change after >60s idle), `GET ${VITE_API_BASE_URL}/health/live`. On non-200 or network error, render a full-page `<ApiUnreachable />` banner ("TagPulse is temporarily unavailable. Retrying…") with an exponential-backoff retry (1s → 2s → 4s → cap at 30s) and a manual "Retry now" button. Replaces the current "login spinner forever" UX. Skip the gate when the previous probe was <60s ago and successful. Tested with a mock server that returns 503 + a network error.
- [shipped] **B2** `[ui]` **Error boundary upgrade.** The current root error boundary (Sprint 13) catches render errors but discards the stack trace. Upgrade to capture `(error.message, error.stack, componentStack)` and forward to the App Insights browser SDK (Phase C1). Render an "Something went wrong" card with a "Copy error details" button (clipboard) and a `[Reload]` action. Distinct from B1 (which handles api-down); B2 handles SPA-up-but-rendering-broken.
- [shipped] **B3** `[ui]` **Deploy-time api preflight in `deploy-azure.yml`.** ([TagPulse-UI#11](https://github.com/9owlsboston/TagPulse-UI/pull/11)) New step before `Azure/static-web-apps-deploy@v1`: `curl -fsS --max-time 10 ${VITE_API_BASE_URL}/health/ready` against the api the SPA was just built against. Fails the deploy if the api is unhealthy — operator sees the failure on the PR / push, doesn't ship a SPA the api can't serve. Skipped on `pull_request` events (preview deploys against `dev` api, which may be in-flux during a backend PR).
- [shipped] **B4** `[ui]` **Post-deploy SPA-vs-api smoke step.** ([TagPulse-UI#11](https://github.com/9owlsboston/TagPulse-UI/pull/11)) Extend the existing smoke test in [.github/workflows/deploy-azure.yml](https://github.com/9owlsboston/TagPulse-UI/blob/main/.github/workflows/deploy-azure.yml) to: (1) fetch the deployed SPA, (2) extract the asset hash + `VITE_API_BASE_URL` from the served `index.html`/main JS, (3) curl that origin's `/health/ready` and assert HTTP 200 + the SWA hostname appears in `config.cors.allow_origins`. Catches the most insidious post-deploy failure: SPA built against a *stale* `VITE_API_BASE_URL` (e.g. backend env was recreated and api FQDN changed but the GH Environment variable wasn't updated).
- [shipped] **B5** `[ui]` **CSP `Content-Security-Policy-Report-Only` header in `staticwebapp.config.json`.** ([TagPulse-UI#11](https://github.com/9owlsboston/TagPulse-UI/pull/11)) Add a parallel report-only policy that's stricter than today's permissive enforced one (no `unsafe-inline`, no `data:` for fonts, exact-origin allow-list). `report-uri` points at the new `${VITE_API_BASE_URL}/security/csp-report` endpoint (Phase A3). Both headers ship together: enforced policy keeps the app working; report-only policy silently captures violations. After ~4 weeks of stable traffic + zero unexpected violations, swap report-only → enforced (Sprint 26+).

### Phase C — Browser telemetry (TagPulse-UI repo, `[ui]`)

- [shipped] **C1** `[ui]` **App Insights browser SDK wired.** ([TagPulse-UI#11](https://github.com/9owlsboston/TagPulse-UI/pull/11)) Pull the connection string from `VITE_APP_INSIGHTS_CONNECTION_STRING` (new build-time variable, set by `scripts/ui-cicd-setup.sh` from the backend's `appInsightsConnectionString` Bicep output — already shipped to azd env). Use [`@microsoft/applicationinsights-web`](https://www.npmjs.com/package/@microsoft/applicationinsights-web) (NOT the snippet loader — bundled is more reliable for SPAs). Initialize once in `main.tsx` before `<App />` mounts. Disable in dev (empty connection string → no-op). Strip query strings from page-view URLs to avoid bleeding tag IDs / lot codes into telemetry. **Privacy note:** explicitly disable cookie usage + IP collection (`disableCookiesUsage: true`, `isStorageUseDisabled: true`) — this gives us anonymous diagnostic telemetry only, no PII.
- [shipped] **C2** `[ui]` **Frontend error & route-change tracking.** ([TagPulse-UI#11](https://github.com/9owlsboston/TagPulse-UI/pull/11)) Wire B2's error boundary to call `appInsights.trackException()`. Wire React Router v7 navigation events to `appInsights.trackPageView()` with the route pattern (e.g. `/devices/:id`, not the resolved id) so the App Insights `pages` tab groups correctly. Wire api errors from `client.ts` + `configureGenerated.ts` to `trackDependency()` with `success: false, resultCode: <http-status>`. Adds a 4-row "Frontend health" workbook to `deploy/azure/bicep/modules/monitoring.bicep` (top page-view counts, top exceptions, p95 SPA navigation timing, top failing api dependencies).

### Phase D — Operational (this repo, `[backend]`)

- [shipped] **D1** `[backend]` **`scripts/azd-ui-token-rotate.sh <env>`.** Idempotent. Runs `az staticwebapp secrets reset-api-key`, then `gh -R 9owlsboston/TagPulse-UI secret set AZURE_STATIC_WEB_APPS_API_TOKEN --env <env>` from the rotation script's stdin (the new token never lands on disk). Prints the new token's last-4-chars for audit but never the full token. Default cadence: 90 days; built-in 60-day idempotency gate refuses to rotate twice in quick succession unless `--force` is passed. Appends a structured JSON line to `deploy/azure/.audit/ui-token-rotation.jsonl` (gitignored — local audit trail; the GHA cron in D2 keeps the canonical record in run logs). ([scripts/azd-ui-token-rotate.sh](../scripts/azd-ui-token-rotate.sh))
- [shipped] **D2** `[backend]` **GHA workflow `.github/workflows/rotate-ui-token.yml`.** Scheduled (`cron: '0 9 1 1,4,7,10 *'` — 9:00 UTC on the 1st of Jan/Apr/Jul/Oct) plus `workflow_dispatch` with optional env-list and `force` inputs. Matrix runs D1 against `dev` / `staging` / `production`. OIDC-federated `azure/login@v2` with the per-Environment `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID` variables (re-uses the Sprint 22 C4 setup); cross-repo `gh secret set` uses a fine-scoped repo-scope PAT (`UI_REPO_SECRETS_PAT`) — rotation cadence and last-4-chars audited via the same script. Failure on any matrix leg opens an issue tagged `ops` + `security` with the run URL and a manual-recovery one-liner. ([.github/workflows/rotate-ui-token.yml](../.github/workflows/rotate-ui-token.yml))

### Phase E — Multi-cloud follow-up (this repo, `[backend]`, structure-only per ADR-016 precedent)

- [shipped] **E1** `[backend]` **`deploy/portable/ui/` working recipe.** Promoted the Sprint 24 D3 stub README to executable recipes with three concrete provider blocks: (a) AWS S3 + CloudFront with the two-pass cache-headers split (hashed assets `immutable`, `index.html` `no-store`) + targeted CDN invalidation; (b) Cloudflare Pages via `wrangler pages deploy ./dist --project-name=tagpulse-ui` (no infra at all — Cloudflare Pages provisions on first deploy and handles the cache split itself); (c) GCP Cloud Storage + Cloud LB with the same two-pass pattern. Adds a **Cross-cloud DR drill** section paired with the Sprint 22 D-3 backend DR runbook (steps to fetch the `dist.zip` artifact, rebuild against a backup-backend FQDN if needed, run the upload recipe, and CNAME DNS) plus per-provider drill notes (IAM scope, atomic-deploy, edge-invalidation propagation). Real Bicep/Terraform for the AWS path is still deferred Sprint 26+ (gated on a real customer asking for AWS hosting). ([deploy/portable/ui/README.md](../deploy/portable/ui/README.md))

### Acceptance criteria

- A SPA load against an api whose `/health/live` returns 503 shows the `<ApiUnreachable />` banner within 2s instead of hanging the login form.
- A SPA load against an api with the wrong CORS allow-list shows the same banner (any failure mode → same UX), and the App Insights `failures` blade shows the dependency call with `resultCode: 0` (browser-blocked).
- `deploy-azure.yml` against an unhealthy api fails at the preflight step *before* uploading the bundle. Log line cites the exact `/health/ready` payload.
- Post-deploy smoke catches a `VITE_API_BASE_URL` mismatch by failing the workflow when the SPA-derived api URL doesn't 200 on `/health/ready`.
- After 1 week of production traffic, the App Insights workbook shows non-zero page-view counts and at least one captured exception (proves wiring works end-to-end).
- The CSP `report-only` policy generates `<10` violations/day in production after 1 week of traffic excluding known-good `data:` fonts. (If ≥10/day, we know exactly what to allow before the Sprint 26+ enforced-CSP cutover.)
- `scripts/azd-ui-token-rotate.sh dev` rotates the token, the next `git push` to UI `main` deploys successfully (proves the new token was wired), and the audit log captures the event.
- `make check` clean (ruff + mypy + pytest); new unit suites `tests/unit/test_sprint25_phase_a.py` (health contract, CORS max-age, CSP-report endpoint) green.
- No regression in Sprint 24 deploy path — `scripts/ui-bootstrap.sh dev` + `git push origin main` from the UI repo continues to ship a SPA end-to-end.

### Deferred to Sprint 26+

- CSP enforced lock-down (gated on Phase B5's report-only telemetry showing zero unexpected violations for ≥4 weeks).
- Real AWS / GCP UI provisioning Bicep/Terraform (E1 ships executable upload recipes only; provisioning is still TODO).
- Custom domain wiring (`app.tagpulse.io`) — gated on registering the domain.
- SWA Standard tier upgrade — gated on Free-tier caps biting.
- Front Door + WAF in front of both SWA and api — paired with the Sprint 23+ deferred api-side FD work.
- E2E test in CI (Playwright against the deployed SWA) — gated on first design refresh worth protecting.
- Real User Monitoring (RUM) beyond the basic App Insights browser SDK — gated on first product-team request for funnel analytics.

---

## Sprint 26 — Operational Tooling Job (shipped)

> Goal: give operators a first-class, in-VNet way to run any `scripts/*.py` (and ad-hoc `python -m …`) against a deployed environment, without poking holes in the private Postgres firewall or shipping a separate "tools" image.
>
> Trigger: Sprint 25 left the dev environment without `Test Corp` seeded (smoke_setup.py never ran), so the deployed SPA's **Tenant ID** login flow has no working tenant out of the box. The Postgres Flexible Server is private (VNet-only, public access disabled per Sprint 22), so `python scripts/smoke_setup.py …` from a laptop is structurally impossible — and even after a tenant is seeded, every future operational task (rotate API keys, simulate devices, ad-hoc DB inspection, scheduled cleanups) hits the same wall.
>
> Non-goal: a separate tools image, a new ACR repo, or anything that requires a parallel build pipeline. The api image already contains everything `scripts/` needs (`httpx`, `asyncpg`, the `tagpulse` package, alembic). One `COPY scripts/` line + one Container Apps Job + one shell wrapper is the entire deliverable.
>
> Out of scope (deferred to Sprint 27+): scheduled jobs (cron triggers), a `src/tagpulse/cli/` Click-based entry-point unifying scripts, RBAC for who can start the job (today: anyone with `az containerapp job start` perms on the RG).

### Ownership at a glance

All tasks land in this repo (`9owlsboston/TagPulse`). No UI work.

| Phase | Task count | What lands |
|---|---|---|
| A — Image | 2 | `COPY scripts/` into the base stage; pin `BUILD_VERSION` to surface in `--version`-able scripts |
| B — Bicep | 2 | New `tools-job.bicep` module + workload wiring; same VNet, secrets, and identity as `tpdev-migrations` |
| C — Wrapper script | 2 | `scripts/azd-job.sh` (start a job with arbitrary command/args + tail logs); operator runbook |
| D — First-class tasks | 3 | Smoke-test the seed-tenant path end-to-end; CI smoke that the tools image still imports `scripts/smoke_setup.py`; Key Vault push so plaintext keys never hit Log Analytics |

**Order of operations:** A1 → B1 → B2 → C1 unblocks the first real run. D1/D2 prove the contract. A2/C2 are documentation polish that can land any time.

### Phase A — Image (this repo, `[backend]`)

- [shipped] **A1** `[backend]` **`COPY scripts/ scripts/` in `Dockerfile` `base` stage.** Single line, after `COPY migrations/ migrations/`. Adds ~50KB to the image. The api/worker/migrations targets all inherit it; nothing in `scripts/` is invoked at api startup so there's no cold-start cost. Verified with `python -c "import scripts.smoke_setup"` against the built image — `scripts/` ships with no `__init__.py` today, so the smoke is `python /app/scripts/smoke_setup.py --help` exiting 0. ([Dockerfile](../Dockerfile))
- [shipped] **A2** `[backend]` **Document the contract.** Add `## Operational scripts` section to [docs/runbooks/azure-first-deploy.md](runbooks/azure-first-deploy.md) covering: which scripts are designed to run against a live env (smoke_setup, simulate_*, benchmark_pg_metrics), which are local-only (load_test against localhost:8000 by default — caller must override `TAGPULSE_API_URL`), and the env-var contract every "live-safe" script must satisfy (`TAGPULSE_API_URL`, `TAGPULSE_SMOKE_DB_URL` or `DATABASE_URL`).

### Phase B — Bicep (this repo, `[backend]`)

- [shipped] **B1** `[backend]` **New module [`deploy/azure/bicep/modules/tools-job.bicep`](../deploy/azure/bicep/modules/tools-job.bicep).** Container Apps Job (manual trigger, replicaTimeout 1800s, retryLimit 0). Reuses the api image (`image` param wired from the same `SERVICE_API_IMAGE_NAME` azd-output that `migrations-job.bicep` consumes today). Identical secrets/env to `migrations-job.bicep` for `POSTGRES_*` + `DATABASE_URL`, plus `TAGPULSE_API_URL` set to the in-cluster api FQDN (`https://${apiApp.outputs.fqdn}`) so scripts that hit the api don't have to leave the env. **Default command intentionally a no-op** (`python -c "print('tools job ready'); import scripts.smoke_setup"`) — the wrapper script in C1 overrides command+args at start time via `az containerapp job update`.
- [shipped] **B2** `[backend]` **Wire into [`workload.bicep`](../deploy/azure/bicep/workload.bicep) + outputs.** New module call `toolsJob` (depends on `apiApp` for the FQDN, `postgres` for secrets). Output `toolsJobName` so the wrapper script in C1 can resolve it from `azd env get-values` instead of hard-coding. No change to `main.bicep` other than threading the new param-less module through.

### Phase C — Wrapper script + runbook (this repo, `[backend]`)

- [shipped] **C1** `[backend]` **`scripts/azd-job.sh <env> <script> [-- <script args…>]`.** Resolves `toolsJobName` + RG from `azd env get-values -e <env>`, then:
  1. `az containerapp job update -n <job> -g <rg> --command 'python' --args "scripts/<script>,<args…>"` (Azure's CLI takes args as comma-separated). The `--` boundary lets us forward any flags through unmangled.
  2. `EXEC=$(az containerapp job start -n <job> -g <rg> --query name -o tsv)` — captures the execution name.
  3. Polls `az containerapp job execution show` until `properties.status` is `Succeeded` / `Failed` (timeout 30 min, exit 1 on any other terminal state).
  4. `az containerapp job execution show … --query 'properties.template.containers[0].resources'` then streams logs via `az monitor log-analytics query` filtered by `ContainerAppName == 'tools-job-<env>' and TimeGenerated > <start>` so the operator sees stdout including the API key + `export TAGPULSE_API_KEY=…` line that `smoke_setup.py` prints.
  5. Exit code = job's exit code.

  Idempotency: `--update-only` skips the job-start and just tails the most recent execution (useful when the operator's terminal disconnected mid-run). Refuses to run when the working tree is dirty + `git push` hasn't happened yet, because the job runs the **deployed** image — local script edits won't take effect until the next deploy. Override with `--allow-stale`.

- [shipped] **C2** `[backend]` **New runbook [docs/runbooks/operational-tooling.md](runbooks/operational-tooling.md).** Three worked examples:
  - **Seed the demo tenant** (closes the Sprint 25 dev gap):
    `scripts/azd-job.sh dev smoke_setup.py -- --full --with-roles --with-subject-telemetry --regenerate-key`
  - **Rotate the demo admin's API key** (no schema change, idempotent):
    `scripts/azd-job.sh dev smoke_setup.py -- --regenerate-key`
  - **Push fake telemetry into the live env for a load smoke**:
    `scripts/azd-job.sh dev simulate_devices.py -- --with-gps --duration 60`

  Plus a "what NOT to run" section: `load_test.py` against `dev` from inside the env (would saturate the api's own egress); destructive scripts that don't exist yet but might (e.g. a future `reset_tenant.py`) — gated on a `--i-know-what-im-doing` flag in the wrapper.

### Phase D — First-class tasks + CI smoke (this repo, `[backend]`)

- [shipped] **D1** `[backend]` **End-to-end seed-tenant smoke after `azd up`.** New section in [docs/runbooks/azure-first-deploy.md](runbooks/azure-first-deploy.md) Phase 4 (Post-Deploy): after migrations succeed, run `scripts/azd-job.sh <env> smoke_setup.py -- --full --with-roles --with-subject-telemetry --regenerate-key`, then verify with two curls — `GET /tenant/config` against the seeded tenant returns 200 with `name: "Test Corp"`, and the SPA login form's **Tenant ID** tab now succeeds with `11111111-…`. Closes the Sprint 25 follow-up gap that PR #10 (UI) only patched the symptom of.
- [shipped] **D2** `[backend]` **CI smoke that the tools image still works.** New step in `.github/workflows/build-and-push.yml` after the api image build: `docker run --rm --entrypoint python <image> -c "import scripts.smoke_setup, scripts.simulate_devices, scripts.simulate_assets, scripts.simulate_inventory; print('OK')"`. Catches the embarrassing failure mode where someone moves a script under a subdirectory and the Bicep wrapper still tries to invoke it.
- [shipped] **D3** `[backend]` **`smoke_setup.py --key-vault-name <vault>` pushes plaintext keys to Key Vault instead of stdout.** When the flag is set (or `$TAGPULSE_SMOKE_KEY_VAULT_NAME` is exported), each freshly issued admin/role API key is written to KV as `tagpulse-<tenant-slug>-<role>-key` via `azure-keyvault-secrets` + `DefaultAzureCredential`; the script's stdout shows only the vault + secret name + version (no plaintext, no `export TAGPULSE_API_KEY=…` line). The end-of-run hint becomes `export TAGPULSE_API_KEY=$(az keyvault secret show --vault-name … --name … --query value -o tsv)` so an operator with `Key Vault Secrets User` can still pick the key up. Closes the Sprint 25 leak path where the tools-job's stdout (and therefore Log Analytics, retention 90 days) would carry the plaintext admin key. The tools-job's UAMI receives `Key Vault Secrets Officer` on the env's vault as a B1 ride-along; until then the flag works from a developer laptop with `az login`. Implementation in [scripts/smoke_setup.py](../scripts/smoke_setup.py); deps added to the `azure` optional-extra in [pyproject.toml](../pyproject.toml).

### Acceptance criteria

- `scripts/azd-job.sh dev smoke_setup.py -- --full --with-roles --with-subject-telemetry --regenerate-key` runs to completion against a fresh `tagpulse-dev` env in <5 minutes, prints the `export TAGPULSE_API_KEY=…` line, and the SPA's Tenant ID login flow then succeeds against the seeded tenant.
- `az containerapp job execution list -n tools-job-dev` shows the run with `status: Succeeded` and the executor name matches the wrapper's stdout.
- `make check` clean (ruff + mypy + pytest); no new tests added (the value is integration-test, covered by D1's runbook smoke).
- The `tpdev-migrations` job continues to run on `azd up` with no behavior change. (B1 reuses the same image and identity; only the job name and command differ.)
- No regression to the api/worker startup paths — the new `COPY scripts/` line adds ~50KB but doesn't pull any new package install. Verified with `docker history` size diff.
- `scripts/azd-job.sh dev smoke_setup.py --update-only` re-tails the most recent execution's logs without re-running the job (useful when the terminal disconnects mid-run).

### Risks & mitigations

- **Risk:** the tools job runs the *deployed* image, so local edits to `scripts/smoke_setup.py` don't take effect until the next `azd deploy`. **Mitigation:** wrapper refuses to run with a dirty tree + un-pushed commits unless `--allow-stale` is passed; runbook calls this out in bold.
- **Risk:** stdout in the job's container includes the freshly-rotated admin API key, which then lives in Log Analytics for the workspace's retention period (90 days). **Mitigation:** D3 — pass `--key-vault-name` (the runbook makes this the default for any non-laptop run) so the plaintext goes to KV and stdout only carries vault + secret coordinates. The runbook's tools-job examples all set the flag; the only path that still prints plaintext is intentional local dev.
- **Risk:** someone runs `scripts/load_test.py` via the wrapper and saturates the api's egress. **Mitigation:** runbook's "what NOT to run" section + Sprint 27+ optional allow-list of script names in the wrapper.

### Deferred to Sprint 27+

- Scheduled jobs (Container Apps Jobs `triggerType: Schedule`) — gated on first recurring task that's not just smoke (likely `cleanup_expired_tokens.py` or `compact_old_telemetry.py`).
- Unify `scripts/*.py` under a single `python -m tagpulse.cli …` Click-based entry-point — gated on >5 scripts living under `scripts/` AND first need to share flags / env-loading code across them.
- RBAC for who can start the job (today: anyone with `az containerapp job start` perms on the RG, which today is anyone with `Contributor` on `tagpulse-dev-rg`). Gated on a non-engineering operator persona existing.
- Wrapper-side script allow-list (refuse to run anything not in `scripts/_runbook_allowed.txt`) — gated on first near-miss with a destructive script.

---

## Sprint 27 — Inventory CRUD Completeness & Operational Polish

> Companion repo: [9owlsboston/TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI) — Phase A lands there
> Goal: close the CRUD gaps in the inventory UI that Sprint 15b left as read-only, add the manual stock adjustment flow that operators need for cycle-count corrections, and wire the backend endpoints that exist but are missing (lot/stock-item delete, manual stock-movement create, tag-data-mapping update/delete). Small sprint — no schema changes, no new tables.

### Ownership at a glance

| Phase | Repo | Task count | What lands |
|---|---|---|---|
| A — Inventory UI CRUD | `[ui]` TagPulse-UI | 6 | Lot edit form, product edit form, stock-item state editor, manual stock adjustment modal, CSV import page, tag-data-mapping editor (update + delete) |
| B — Backend gap-fill (inventory) | `[backend]` TagPulse | 4 | `DELETE /lots/{id}`, `DELETE /stock-items/{id}`, `POST /stock-movements` (manual adjustment), `PATCH`/`DELETE /tag-data-mappings/{id}` |
| C — Cross-entity UI polish | `[ui]` TagPulse-UI + `[backend]` TagPulse | 6 | Webhook test-fire, API key metadata, dead-letter UI page, alert bulk-ack, audit log date-range + export, API key `created_at` column |
| D — Operational | `[backend]` TagPulse | 2 | `TAGPULSE_API_KEY` as tools-job env var from KV (no more two-step key retrieval), CHANGELOG |

### Phase A — Inventory UI CRUD (TagPulse-UI repo, `[ui]`)

- [shipped] **A1** `[ui]` **Lot edit form on Lot detail page.** Admin/editor "Edit" button → inline form or modal with `lot_code`, `manufactured_at`, `expires_at`, `metadata` fields. Calls `PATCH /lots/{id}`. Lot detail page already renders these fields read-only; this adds the mutation path. Confirmation on `expires_at` change (may affect active `stock.expiring_within` rules).
- [shipped] **A2** `[ui]` **Product edit form on Product detail page.** Admin "Edit" button → modal with `name`, `sku`, `gtin`, `category`, `unit`, `attributes` fields. Calls `PATCH /products/{id}`. Currently the product detail is read-only with a stock-by-zone bar chart.
- [shipped] **A3** `[ui]` **Stock item state editor.** Editor+ "Change State" dropdown on stock-item detail or list-row action. Allowed transitions: `in_stock → consumed`, `in_stock → damaged`, `consumed → in_stock` (reversal). Calls `PATCH /stock-items/{id}` with `{state: "..."}`. State changes outside this list are rejected by the backend schema.
- [shipped] **A4** `[ui]` **Manual stock adjustment modal.** On the Stock Levels page, per-row "Adjust" action → modal with `movement_type` (enter/exit), `quantity`, `reason` text field. Calls `POST /stock-movements` (Phase B2) to create a corrective movement. The modal pre-fills product/lot/zone from the selected row. Gated on `role >= editor`.
- [shipped] **A5** `[ui]` **CSV import page.** Admin-only page (sidebar entry under Inventory) with three tabs (Products / Lots / Stock Items). Each tab: file picker + preview table + "Import" button. Calls the existing `POST /products/import`, `POST /lots/import`, `POST /stock-items/import` endpoints. Error rows displayed inline with row number + error message. Currently CSV import is API-only.
- [shipped] **A6** `[ui]` **Tag-data-mapping editor — update + delete.** The existing Tenant Settings "Tag data fields" sub-tab shows mappings but only supports create. Add inline edit (pencil icon → editable row) and delete (trash icon → confirmation). Calls `PATCH /tag-data-mappings/{id}` and `DELETE /tag-data-mappings/{id}` (Phase B4).

### Phase B — Backend gap-fill — inventory (this repo, `[backend]`)

- [shipped] **B1** `[backend]` **`DELETE /lots/{id}` (admin only).** Soft-delete or hard-delete (TBD — hard-delete if no stock_items reference the lot, 409 otherwise). Audit log entry `lot.deleted`.
- [shipped] **B2** `[backend]` **`POST /stock-movements` (editor+) — manual adjustment.** New `StockMovementCreate` schema: `product_id`, `lot_id` (optional), `zone_id`, `movement_type` ∈ {`enter`, `exit`, `adjustment`}, `quantity`, `reason` (required free text), `stock_item_id` (optional — for single-item corrections). Emits `Topic.STOCK_MOVEMENT_CREATED`. The `adjustment` type is new — distinct from ingestion-driven `enter`/`exit` so reports can filter operator corrections from automated flow.
- [shipped] **B3** `[backend]` **`DELETE /stock-items/{id}` (admin only).** Hard-delete if state is `consumed` or `damaged`; 409 if `in_stock` (force-delete with `?force=true`). Audit log entry `stock_item.deleted`.
- [shipped] **B4** `[backend]` **`PATCH /tag-data-mappings/{id}` + `DELETE /tag-data-mappings/{id}` (admin only).** `TagDataMappingUpdate` schema: `source_key`, `target_field`, `transform` (all optional). Delete is hard-delete (mappings are config, not transactional data). Audit log entries `tag_data_mapping.updated` / `tag_data_mapping.deleted`.

### Phase C — Cross-entity UI polish (both repos)

- [shipped] **C1** `[backend]` **`POST /integrations/{id}/test` (admin/editor).** Sends a synthetic test payload to the configured webhook URL with `X-TagPulse-Event: test` header. Returns the upstream HTTP status + response time. No event is published to the EventBus — this is a direct HTTP call. Currently the only way to verify a webhook URL works is to wait for a real event; operators have no "test fire" button. Timeout 10s; 4xx/5xx from the target is reported but not treated as a TagPulse error.
- [shipped] **C2** `[ui]` **Webhook test-fire button.** "Test" action on the integration detail page (editor+). Calls Phase C1 endpoint. Shows the upstream response status + latency inline. Renders "Connection refused" or "Timeout" as a clear error state with the target URL visible.
- [shipped] **C3** `[backend]` **API key metadata: `key_created_at` column.** Add `api_keys.created_at TIMESTAMPTZ DEFAULT now()` (Alembic migration). Populate on key generation; show in `UserResponse.api_key_created_at`. The User detail page currently shows the key prefix but no creation date — operators can't tell when the current key was issued.
- [shipped] **C4** `[ui]` **API key metadata on User detail.** Show `key_created_at` next to the prefix. Render "Key issued 3 days ago" relative timestamp. On Revoke → confirm with "This will invalidate the key issued on {date}". On Regenerate → confirm + copy-once flow (already exists) + update the displayed `key_created_at`.
- [shipped] **C5** `[ui]` **Dead-letter events page.** Admin-only page at `/admin/dead-letters` (sidebar entry under Admin). Table with `topic`, `error`, `created_at`, `payload preview` (truncated). Per-row actions: "Retry" (`POST /admin/dead-letter/{id}/retry`) and "Abandon" (`DELETE /admin/dead-letter/{id}`). Bulk select + batch retry/abandon. Currently dead-letter events are only accessible via API — there is no UI page.
- [shipped] **C6** `[ui]` **Alert bulk acknowledge.** Checkbox column on the Alert History table + "Acknowledge selected" button. Calls `POST /alerts/{id}/acknowledge` in parallel for each selected alert. (Backend bulk endpoint deferred — client-side fan-out is fine for up to ~50 alerts per page.)
- [shipped] **C7** `[ui]` **Wrap root tree with AntD `<App>` component.** Add `import { App as AntApp } from 'antd'` and wrap the JSX in `App.tsx` with `<AntApp>…</AntApp>`. Without this wrapper, AntD v5 static methods (`Modal.confirm()`, `message.success()`, `notification.info()`) silently no-op — the "Rotate token" confirm dialog, clipboard-copy success toast, and any other static-method-based feedback are completely invisible to the user. One-line fix that unblocks all existing `Modal.confirm` / `message.*` call sites across the app (DeviceDetail rotate token, API key copy, alert acknowledge, etc.).

### Phase D — Operational & IaC hardening (this repo, `[backend]`)

- [shipped] **D1** `[backend]` **Wire `TAGPULSE_API_KEY` into `tools-job.bicep` from Key Vault.** Today running a simulator against the deployed env requires a two-step dance: (1) `azd-job.sh dev get_kv_secret.py -- --name tagpulse-test-corp-admin-key`, (2) copy the key into `--api-key`. Instead, add `TAGPULSE_API_KEY` as a `secretRef` env var on the tools job sourced from the KV secret `tagpulse-test-corp-admin-key`. Scripts that read `$TAGPULSE_API_KEY` (all simulators do via `os.environ.get("TAGPULSE_API_KEY")`) will Just Work. The two-step dance becomes: `azd-job.sh dev simulate_inventory.py -- --tenant-id 11111111-… --duration 120` — no `--api-key` needed.
- [shipped] **D2** `[backend]` **Move MQTT username into Key Vault.** Today `mqttUsername` is a Bicep parameter (default `'tagpulse'`) passed as a plaintext env var to the Mosquitto ACI (`MOSQUITTO_USERNAME`) and the worker ACA (`MQTT_USERNAME`). The password is already in KV (`mqtt-broker-password`), but the username leaks into `az containerapp show` output and deployment logs. Fix: add `mqtt-broker-username` as a 4th KV secret in `keyvault.bicep`; swap the ACI and ACA env vars from `value:` to KV `secureValue`/`secretRef`; drop the `mqttUsername` Bicep param from `main.bicep` (seed from `.env.<env>` like the password). The Helm chart already does this correctly (both in a k8s Secret). Consistency fix, not a security emergency — the username alone is not exploitable.
- [shipped] **D3** `[backend]` **`CHANGELOG.md` Sprint 27 section.**

### Acceptance criteria

- Lot detail page shows an Edit button (editor+); saving a new `expires_at` updates the lot and the Lot Expiry Queue re-sorts correctly.
- Stock Levels page shows an "Adjust" action per row; posting an `adjustment` movement updates the `stock_levels` view within 1s (no page reload needed if SSE-backed; manual refresh otherwise).
- `POST /stock-movements` with `movement_type: "adjustment"` creates a movement row with `source: "manual"` metadata; the Stock Movements page shows it with a distinct badge.
- CSV import page uploads 100 products without error; error rows are displayed inline.
- Webhook integration detail page shows a "Test" button; clicking it against a valid URL shows `200 OK (142ms)`; clicking it against an invalid URL shows `Connection refused` with the target URL.
- User detail page shows "Key issued {date}" next to the key prefix; revoking a key clears the display; regenerating shows a new `key_created_at`.
- Dead-letter events page renders at least one retry-able event; "Retry" re-queues it and the row disappears from the list.
- Alert History supports selecting 5+ alerts and acknowledging them in one click.
- `azd-job.sh dev simulate_inventory.py -- --tenant-id 11111111-… --duration 60` succeeds without `--api-key` (the key comes from KV via the tools-job env var).
- `make check` clean; one new migration (C3: `api_keys.created_at`).

### Deferred

- Inventory cycle-count workflow (full reconciliation with expected-vs-actual counts, variance report) — needs a design doc; Sprint 27 ships only the manual-adjustment primitive it would build on.
- Stock-item bulk state transitions (select N items → mark all consumed) — gated on first warehouse operator requesting it.
- Lot merge / split operations — rare in RFID workflows; gated on customer request.

---

## Sprint 28 — Operational Excellence & On-Call Readiness

> Goal: harden the operate side of the platform now that build/ship/feature cadence is stable. Sprints 22–27 left a trail of one-off `azd-*` scripts, an in-VNet tools job, and a KV-rooted secret pattern; Sprint 28 consolidates that surface, adds the missing self-monitoring, and writes the runbooks an on-call engineer would need at 03:00. No new product features; no schema changes beyond an Azure Monitor alert ruleset and one optional `dead_letter_events.source` column.
>
> Trigger: recent commit churn (`#23` MQTT subscriber resilience, `839ad03` ACA name resolution, `0ed74f2` log-fetch rewire, `6f712f2` KV firewall probe) shows we keep rediscovering the same operational paper-cuts. Time to factor them out.
>
> Non-goal: new product features, schema-heavy work, UI-only polish, anything that depends on a customer commitment. Sprint 29 picks up product work.

### Ownership at a glance

All tasks land in this repo (`9owlsboston/TagPulse`). No UI work.

| Phase | Task count | What lands |
|---|---|---|
| A — Deployment & IaC reliability | 5 | CI preflight gate, Bicep what-if PR comment, image-SHA cross-check, post-deploy smoke gate, ACA name-resolution helper |
| B — Secrets & KV ergonomics | 4 | KV inventory/audit, consolidated rotation runbook, `--dry-run` on rotators, in-VNet KV expiry sweeper |
| C — MQTT broker / subscriber operations | 6 | MQTT OTel counters, broker canary script, dead-letter sink for malformed payloads, outage runbook, Mosquitto restart helper, server-TLS listener on 8883 |
| D — Observability on the platform itself | 5 | SLO doc, Azure Monitor alert rules in Bicep, saved KQL queries + workbook, MQTT freshness in `/healthz`, OTel `tenant_id` audit |
| E — Runbook & on-call readiness | 4 | Incident template, DB failover/restore drill, dead-letter triage, first quarterly DR drill executed |
| F — Developer/operator inner loop | 4 | `make` ops targets, `scripts/lib/azd-common.sh` shared lib, `azd-doctor.sh` aggregate health check, VS Code tasks for the common ops verbs |
| G — UI CRUD gap-fill | 8 | Page-by-page edit-capability audit + missing edit/create flows on Sites & Zones, Devices, Telemetry Models, Tenant Settings, Assets, plus the one missing backend PATCH that blocks them |
| H — Documentation | 7 | Doc audit + index, operator one-pager, API reference refresh, runbook index polish, link/markdown lint in CI, screenshot-light user-guide refresh, Azure architecture doc |

**Cross-repo note:** Phases A–F + H land in this repo (`9owlsboston/TagPulse`). Phase G lands in [9owlsboston/TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI) **except G1** (the missing `PATCH /telemetry-models/{id}` backend endpoint that unblocks G6).

**Order of operations:** F2 (shared lib) lands first so A2/A5/B1/C5 can use it. A1+D2 unblock the "we'll know if it breaks" story. E2 + E5 prove the DR plan. G2 (the audit) lands before G3–G8 so the gap list is authoritative, not anecdotal. H1 (doc audit) lands before H2–H6 for the same reason. Everything else is parallelizable.

### Phase A — Deployment & IaC reliability (this repo, `[backend]`)

- [shipped] **A1** `[backend]` **CI preflight gate.** Add a `preflight` job to `.github/workflows/deploy-azure.yml` that runs `scripts/azd-preflight.sh <env>` before the deploy job and fails fast on missing env vars, stale image tags, or KV secret gaps. Today preflight is operator-discipline-only; CI happily ships partial deploys (cf. `32893ab` which had to backfill `mqttUsername` after the fact). Output captured to the workflow summary.
- [shipped] **A2** `[backend]` **`scripts/lib/azd-aca-resolve.sh` — ACA / job / KV name resolver.** Three commits in the last two weeks (`839ad03`, `2c2957d`, `0ed74f2`) had to re-derive ACA resource names from the RG because the Bicep outputs weren't enough at runtime. Extract the `az containerapp list -g <rg> --query "[?starts_with(name,'tp${env}-')]"` pattern into one helper sourced by `azd-job.sh`, `azd-network-check.sh`, `azd-mqtt-restart.sh` (C5), and `azd-image-check.sh` (A3). DRY.
- [shipped] **A3** `[backend]` **`scripts/azd-image-check.sh` — image SHA cross-check.** Existing script verifies the api image tag exists in ACR; extend to assert that `api`, `worker`, `migrations-job`, and `tools-job` all reference the **same SHA**. Catches the partial-deploy failure mode where one Container App updates and another silently keeps an older image (subtle when the api+worker drift on shared model code).
- [shipped] **A4** `[backend]` **Bicep what-if as PR comment.** New step in the `deploy-azure` workflow on `pull_request`: `az deployment group what-if -g <rg> -f main.bicep` and post the diff as a sticky PR comment via `actions/github-script`. Surfaces destructive changes (resource recreation, secret rotation) before they merge. Uses an in-RG read-only SP — no plan-apply gap.
- [shipped] **A5** `[backend]` **Post-deploy smoke gate.** After `azd deploy` succeeds in CI, run `scripts/azd-smoke.sh <env>` (new) which curls `/healthz`, `/readyz`, `/tenant/config` (with the seeded Test Corp key from KV via A2's helper) and asserts 200 + expected JSON shapes. Fail the workflow on first miss; today nothing fails the workflow if the api boots but routes 500.

### Phase B — Secrets & KV ergonomics (this repo, `[backend]`)

- [shipped] **B1** `[backend]` **`scripts/azd-kv-audit.sh <env>` — operator-side inventory.** Lists every secret in the env's vault with `name`, `enabled`, `created`, `updated`, `expires`, `contentType`, and the principal IDs with `Get`/`List` access. Flags: secrets without an expiry; secrets older than 180 days; principals with `Officer` who probably only need `User`. Pure az-CLI; runs from operator laptop with the operator's Entra principal. Closes the "who has what" gap left after Sprint 27 D2 added `mqtt-broker-username`.
- [shipped] **B2** `[backend]` **`docs/runbooks/secret-rotation.md` — single source of truth.** One runbook with a per-secret table: `tagpulse-test-corp-admin-key`, `mqtt-broker-username`, `mqtt-broker-password`, `pg-admin-password`, `ui-deploy-token`, `azd-cicd-sp-secret`. For each: rotation cadence, who rotates, exact `azd-job.sh` or `azd-*-rotate.sh` command, how to verify, blast radius if compromised. Cross-links existing `device-token-rotation.md` and `azd-ui-token-rotate.sh`. Replaces tribal knowledge.
- [shipped] **B3** `[backend]` **`--dry-run` on `azd-grant-operator-kv.sh` and `azd-ui-token-rotate.sh`.** Both scripts mutate state (RBAC assignment, token rotation in the UI repo's GH env). `--dry-run` should print the exact `az role assignment create` / `gh secret set` invocation it *would* run without executing. Same pattern as Sprint 27 C1 webhook test-fire — confidence before commit.
- [shipped] **B4** `[backend]` **`scripts/sweep_kv_expiries.py` — in-VNet expiry sweeper.** Runs via `azd-job.sh`, lists all secrets with `expires` within 30 days across all envs the tools-job has read access to, and writes a JSON report to a Storage container. Pairs with D2 alert rule (`KV secret expiring`) so the alert has actionable detail attached. Builds on the `get_kv_secret.py` pattern (`azd-kv-get`).

### Phase C — MQTT broker / subscriber operations (this repo, `[backend]`)

- [shipped] **C1** `[backend]` **MQTT subscriber OTel counters.** Building on `#18`/`#19` resilience work, add: `mqtt_reconnect_attempts_total{tenant,reason}`, `mqtt_messages_rejected_total{topic_kind,reason}` (where reason ∈ `schema_invalid`, `unknown_topic`, `tenant_not_found`, `quota_exceeded`), `mqtt_subscriber_last_message_seconds` gauge per topic kind. Defined in `src/tagpulse/core/otel_metrics.py` per repo convention. Surfaced via Prometheus `/metrics` endpoint already wired in Sprint 11.
- [shipped] **C2** `[backend]` **`scripts/azd-mqtt-canary.py` — broker round-trip canary.** Runs via `azd-job.sh`. Connects with a test tenant credential, publishes a payload to `tenants/<canary-tenant>/devices/canary/heartbeat`, subscribes for the worker's downstream `event_bus` ack (or queries `tag_reads` for the canary timestamp), asserts <2s round-trip. Exits non-zero on failure. Schedulable later (Sprint 26 deferred Container Apps Job schedule trigger — this is the first concrete need). For now, runnable from CI post-deploy and on operator demand.
- [shipped] **C3** `[backend]` **Dead-letter sink for malformed MQTT payloads.** Today the subscriber logs+drops payloads it can't parse (per `aeed4a4`). Reroute these to the existing `dead_letter_events` table with `source='mqtt'` (new optional column — Alembic migration; defaults to `'event_bus'` for backward compat). Surfaces them in the existing dead-letter UI (Sprint 27 C5) so operators can inspect malformed devices without grepping Log Analytics.
- [shipped] **C4** `[backend]` **`docs/runbooks/mqtt-outage.md` — Mosquitto outage decision tree.** Symptoms → checks → fixes for: ACI restart loop, KV password rotation lock-out, ACR auth expiry, persistent volume full, VNet egress NSG drift (Sprint 22 left this surface), TLS cert near-expiry. Each branch links to the relevant `azd-*` script. Concrete: includes the `az container logs` + `az container show` invocations with the right names already filled in (using A2's resolver).
- [shipped] **C5** `[backend]` **`scripts/azd-mqtt-restart.sh <env>` — ACI restart helper.** Wraps `az container restart` + waits for the broker to accept connections + runs the C2 canary. Captures the manual sequence currently buried in `sprint-23-network-cutover.md`. Idempotent and safe to run during normal ops (Mosquitto persists state to its volume).
- [shipped] **C6** `[backend]` **Server-TLS listener on `tcp/8883` (no mTLS yet).** Today Mosquitto exposes `tcp/1883` plaintext with username+password (`docker/mosquitto.prod.conf` explicitly defers TLS to ADR-012). C6 ships the cheap half of [ADR-012](adr/012-mtls-for-mqtt.md): server-side TLS only, so device credentials and payloads stop traveling in clear text. Out of scope: per-device client certificates / `require_certificate true` — that's the full mTLS workstream and stays under ADR-012, gated on first paying customer with a contractual cert requirement.
  - **Cert source:** `mqtt-tls-cert` + `mqtt-tls-key` as 6th/7th KV secrets (PEM strings) under `keyvault.bicep`. v1 generates them via `az keyvault certificate create` with a self-signed CA per env, 12-month validity, auto-rotation flag set; the runbook (B2 secret-rotation) gets two rows. Future: switch to a publicly-trusted ACME-issued cert when we have a custom domain (currently devices connect to the `*.azurecontainer.io` FQDN, which can't be bound to LE — self-signed is the only viable v1 path; document this clearly).
  - **Mosquitto config:** add `listener 8883` block with `cafile`/`certfile`/`keyfile` pointing at `/mosquitto/config/{ca,cert,key}.pem`; entrypoint script materializes them from env vars `MOSQUITTO_TLS_CA`/`CERT`/`KEY` resolved via `secretRef`. Keep `listener 1883` enabled for Sprint 28 only (deprecation window) so existing devices have a migration runway; remove in Sprint 29.
  - **Bicep:** `mqtt.bicep` adds `{ protocol: 'TCP', port: 8883 }` alongside 1883 in both `containers[].ports` and the container-group `ipAddress.ports`; `mqttUrl` output gains a sibling `mqttTlsUrl = 'mqtts://${aci.properties.ipAddress.fqdn}:8883'`.
  - **Worker:** subscriber gets a `MQTT_USE_TLS` env var (default `true` once C6 ships); when set, paho's `tls_set(ca_certs=...)` is wired with the same self-signed CA cert (passed in via `MQTT_TLS_CA` secretRef so the worker trusts the broker without `tls_insecure`).
  - **Edge client (`clients/pi/`):** `MqttTransport` accepts `tls_ca_path` / `tls_use_system_cas`; sample env var is `TAGPULSE_MQTT_TLS_CA`; README updated. Devices that can't be updated continue working on 1883 until Sprint 29.
  - **Verification:** C2 canary (publishes via 8883), `openssl s_client -connect tp${env}-mqtt.…:8883 -showcerts` smoke in C5 restart helper.
  - **Cost:** zero infra cost (KV cert is free, ACI port is free). Effort estimate: ~1–2 days. Unblocks compliance audits that flag plaintext MQTT.

### Phase D — Observability on the platform itself (this repo, `[backend]`)

- [shipped] **D1** `[backend]` **`docs/observability/slos.md` — SLO definitions.** Four initial SLOs with target + window + measurement query: api availability (99.5% over 30d, `/healthz` external probe), api latency (p99 < 500ms over 7d, FastAPI middleware histogram), MQTT E2E ingestion (p95 publish→`tag_reads.timestamp` < 2s over 7d, requires C2 canary timestamps), alert delivery latency (p95 rule eval → webhook delivered < 10s, from existing `alerts_fired` + `webhook_deliveries` counters). Doc explicitly calls out which SLOs are aspirational vs measured-today.
- [shipped] **D2** `[backend]` **Azure Monitor alert rules in Bicep.** New module `deploy/azure/bicep/modules/alerts.bicep` with: api 5xx rate >1% over 5m (action group → operator email), MQTT subscriber `last_message_seconds > 300` (no traffic for 5min), `dead_letter_events` row count growth >100 in 1h, KV secret `Expires` within 30 days (sweeps via the resource provider, not the data plane). All alerts wired to one action group; severity 2 for everything in v1. No PagerDuty wiring (gated on a customer commit).
- [shipped] **D3** `[backend]` **`ops/azure-monitor/` — saved KQL + workbook.** Commit reusable queries: error spike by route, MQTT connect/disconnect timeline, top tenants by ingestion rate, dead-letter trend, slow query top-N (uses `pg_stat_statements` from the Sprint 13b benchmark). Plus one Azure Monitor Workbook JSON wiring all four into a single dashboard, importable via `az monitor workbooks create`. Cross-link from `docs/runbooks/README.md`.
- [shipped] **D4** `[backend]` **`/healthz` deep — MQTT freshness check.** Extend `/healthz` to include `mqtt_subscriber_last_message_age_seconds` from the C1 gauge; `/readyz` (deep) returns 503 if it's >300s on a non-empty tenant. Today the health endpoint reports broker connectivity but not message flow — silently-stuck subscribers (the failure mode `#18` fixed) wouldn't trip readiness.
- [shipped] **D5** `[backend]` **OTel `tenant_id` attribute audit.** Walk `src/tagpulse/core/otel_metrics.py` and assert every counter declared in the observability design has a `tenant_id` attribute available (or has a documented reason it doesn't, e.g., platform-wide gauges). Add a unit test that introspects the registered counters and fails if a new counter is added without `tenant_id` and isn't in the allowlist. Prevents the "we can't break this metric down per tenant" surprise.

### Phase E — Runbook & on-call readiness (this repo, `[backend]`)

- [shipped] **E1** `[backend]` **`docs/runbooks/incident-template.md` — incident management.** SEV1/2/3 definitions, comms templates (status update, customer notice, internal Slack), postmortem skeleton (timeline, root cause, contributing factors, action items with owners). Models the [Google SRE incident response chapter](https://sre.google/sre-book/managing-incidents/) lightly, scaled for a one-team product. Solo operator? Section on "what to do when you are also the customer."
- [shipped] **E2** `[backend]` **`docs/runbooks/db-failover-and-restore.md` — PG restore drill.** Step-by-step: trigger PITR via `az postgres flexible-server restore`, validate with row-count + last-`tag_reads.timestamp` queries, swap the api+worker secrets to the restored server, verify ingestion resumes. RPO target: 5min (PG Flex's PITR floor). RTO target: 30min (provisioning + secret swap + restart). Includes "Last drilled" date row updated by E5.
- [shipped] **E3** `[backend]` **`docs/runbooks/dead-letter-triage.md` — DLQ playbook.** When to retry vs abandon (idempotent vs non-idempotent topics — table per topic), common error signatures with their fixes (ValidationError → schema drift, IntegrityError on `tenant_id` FK → tenant deleted, etc.), escalation if dead-letter row count grows after retry. Pairs with C3's MQTT dead-letter sink.
- [deferred — first quarterly drill deferred to next sprint; runbook E2 ready] **E4** `[backend]` **First quarterly DR drill — execute E2.** Actually run the DB failover runbook against `tagpulse-dev`. Capture wall-clock timing for each step in E2's "Last drilled" row. File any runbook fixes uncovered as part of the same PR. Establishes the cadence (next drill: Sprint 32 or end of Q3, whichever first).

### Phase F — Developer/operator inner loop (this repo, `[backend]`)

- [shipped] **F1** `[backend]` **Ops-flavored `make` targets.** Add: `make smoke ENV=dev` (wraps A5), `make rotate-key ENV=dev TENANT=test-corp` (wraps `azd-job.sh smoke_setup.py -- --regenerate-key …`), `make logs SERVICE=api ENV=dev SINCE=15m` (wraps `az containerapp logs show` with the right name from A2), `make doctor ENV=dev` (wraps F3). Each `make help` entry one line. Discoverability for operators who don't memorize the `azd-*` script names.
- [shipped] **F2** `[backend]` **`scripts/lib/azd-common.sh` — shared shell lib.** Functions: `azd_env_resolve <env>` (sets `RG`, `KV_NAME`, `ACR_NAME`, `LOG_WORKSPACE_ID` from `azd env get-values`), `aca_name <env> <service>` (the recurring resolution pattern), `kv_secret_get <env> <name>` (single-shot wrapper around `az keyvault secret show`), `require_clean_tree` (the dirty-tree guard from Sprint 26 C1). All existing `azd-*.sh` scripts switched to source it. ~150 LOC removed across the suite, single bug-fix-once surface.
- [shipped] **F3** `[backend]` **`scripts/azd-doctor.sh <env>` — single aggregate health check.** Runs preflight (A1) + image-check (A3) + network-check (existing) + kv-audit (B1) + mqtt-canary (C2) + the smoke gate (A5). Prints a green/yellow/red dashboard per check with one-line remediation hints. Exit code = number of red checks. Wired into `make doctor` (F1) and runnable from CI on a schedule (Sprint 26 deferred — F3 makes it concrete enough to schedule).
- [shipped] **F4** `[backend]` **VS Code tasks.** Add tasks to `.vscode/tasks.json` for the common operator verbs: "Azure: Deploy dev", "Azure: Smoke dev", "Azure: Doctor dev", "Azure: Tools job — seed tenant", "Azure: Tools job — rotate key", "Azure: Tail api logs". Each task uses the corresponding `make` target. Discoverability inside the editor matches the CLI experience.

### Phase G — UI CRUD gap-fill (TagPulse-UI repo, `[ui]`; G1 is `[backend]`)

> Trigger: an operator wanted to change the **Boston DC** site's timezone and discovered Sites & Zones is read-only. A backend grep confirms `PATCH /sites/{id}`, `PATCH /zones/{id}`, `PATCH /devices/{id}`, `DELETE /sites/{id}`, `DELETE /zones/{id}` all exist — the surface is fully wirable, the UI just never grew the buttons. Sprint 27 closed the inventory CRUD gaps; Sprint 28 G closes the platform-config CRUD gaps.

- [shipped] **G1** `[backend]` **`PATCH /telemetry-models/{model_id}` (admin only).** Backend has POST + DELETE for telemetry models but **no PATCH**. Operators today have to delete + recreate to change a metric's `unit`, `min`, `max`, or `expected_range` — which orphans every historical reading's quarantine status against the deleted model id. Add `TelemetryModelUpdate` schema (all fields optional except the implicit `subject_kind`/`key` immutables), wire the route, audit log entry `telemetry_model.updated`. Unblocks G6.
- [deferred — UI repo workstream — separate PR in TagPulse-UI] **G2** `[ui]` **Page-by-page CRUD audit** → [docs/refs/ui-crud-audit-sprint28.md](refs/ui-crud-audit-sprint28.md). One-page table per UI route: `entity`, `list/detail/create/edit/delete` columns, `?` (gap) vs `✓` (shipped) vs `n/a` (legitimately read-only — e.g., audit logs, dead-letter events should never be edited). Lands first so G3–G8 work from a confirmed list, not anecdotes. Initial known gaps from the gap scan: Sites & Zones (no edit, no create-zone-from-map), Device detail (no edit form — name/site/zone/notes are PATCH-able but UI-immutable), Telemetry Models (no edit — gated on G1), Tenant Settings → Map config (PATCH endpoint exists but no inline editor), Assets bindings (DELETE exists but no remove-binding row action), Integration delivery log retry (UI-only gap — `POST /admin/dead-letter/{id}/retry` exists for event_bus but not integration deliveries — defer if backend missing).
- [deferred — UI repo workstream — separate PR in TagPulse-UI] **G3** `[ui]` **Sites & Zones — Edit Site + Edit Zone + Delete.** "Edit" pencil per row on the Sites table → modal with `name`, `timezone` (IANA TZ select), `address`, `metadata`. Same on the Zones nested list (`name`, `kind`, `polygon` if zone is map-backed — for v1 just `name` + `kind`, polygon edit deferred to map-page interaction). Trash icon → confirm modal → `DELETE`. 409 when zone has active assets is rendered as "Move 12 assets out before deleting" with a link. Closes the **Boston DC timezone** trigger.
- [deferred — UI repo workstream — separate PR in TagPulse-UI] **G4** `[ui]` **Device detail — Edit form.** "Edit" button (editor+) → modal with `name`, `description`, `site_id` (select), `zone_id` (select, filtered by selected site), `metadata` (JSON editor). Calls `PATCH /devices/{id}`. Today the only mutation paths visible on Device detail are Rotate Token and Decommission — operators can't even fix a typo'd device name without a `curl`.
- [deferred — UI repo workstream — separate PR in TagPulse-UI] **G5** `[ui]` **Device list — Bulk site/zone re-assignment.** Checkbox column + "Move to site/zone…" action button (admin+). Calls `PATCH /devices/{id}` per selected row in parallel (≤50/page; consistent with Sprint 27 C6 client-side fan-out pattern). Closes the practical gap of moving 20 readers when a site is reorganized.
- [deferred — UI repo workstream — separate PR in TagPulse-UI] **G6** `[ui]` **Telemetry Models — Edit metric.** Pencil per row → modal with `unit`, `min`, `max`, `expected_range`, `description`. `subject_kind` and `key` are immutable (rendered disabled with a tooltip linking to the runbook on "renaming a metric"). Calls `PATCH /telemetry-models/{id}` (G1). Closes the orphan-on-recreate gap.
- [deferred — UI repo workstream — separate PR in TagPulse-UI] **G7** `[ui]` **Tenant Settings — Map config inline editor.** The `/tenant/map-config` `PATCH` endpoint exists (used by Sprint 17a map onboarding) but the Tenant Settings → Map sub-tab today only displays the resolved config. Add an editor: `tile_provider` (dropdown: `osm` / `mapbox` / `maptiler` / `custom`), `api_key` (masked), `style_url` (text), `attribution` (text). Save calls `PATCH /tenant/map-config`. Live preview tile in the modal so operators see the change before saving. Admin only.
- [deferred — UI repo workstream — separate PR in TagPulse-UI] **G8** `[ui]` **Assets — Remove binding action + edit asset attributes.** Trash icon on each row of the Asset detail "Bindings" table calls `DELETE /assets/{id}/bindings/{binding_value}` (endpoint exists). Asset detail "Edit" button → modal with `name`, `kind`, `attributes` (JSON), calling `PATCH /assets/{id}`. Closes the gap that an asset whose binding tag was destroyed by a forklift can only be unbound via API.

### Phase H — Documentation (this repo, `[backend]`)

> Trigger: docs grew organically through 27 sprints. New ADRs land but nothing backlinks; runbooks/README is a stub; the `docs/design/` and `docs/runbooks/` indexes don't agree on what exists. A new operator's first hour shouldn't be `find docs -name '*.md'`.

- [shipped] **H1** `[backend]` **Documentation audit** → [docs/refs/docs-audit-sprint28.md](refs/docs-audit-sprint28.md). Inventory every `.md` under `docs/`, plus top-level (`README`, `CONTRIBUTING`, `CHANGELOG`). Per file: `last_modified`, `last_modified_sprint` (greppable from headings), `still_accurate` (Y/N — spot-check), `linked_from` (count). Flag stale (≥3 sprints stale + still referenced), orphaned (no inbound links), and contradictory (e.g., a runbook step that conflicts with a script's current behavior). Output drives H2–H6. Same pattern as G2.
- [shipped] **H2** `[backend]` **`docs/runbooks/README.md` — proper index.** Today it's 14 lines. Replace with a categorized table: **First-time setup** (azure-first-deploy, ui-first-deploy), **Day-to-day ops** (operational-tooling, secret-rotation [B2], azd-survival-guide), **Incident response** (incident-template [E1], db-failover-and-restore [E2], dead-letter-triage [E3], mqtt-outage [C4]), **Migrations & cutovers** (sprint-23-network-cutover, geofence-postgis-trigger, subject-scoped-telemetry, device-token-rotation). Each row: one-line summary + last-validated date.
- [shipped] **H3** `[backend]` **`docs/operator-quickstart.md` — one-page "how to operate TagPulse".** New file. Sections: "What is running where" (one diagram pointing at api ACA, worker ACA, mosquitto ACI, PG Flex, KV, ACR, Log Analytics — naming pattern `tp${env}-*`), "How to log in" (kv → tenant key → SPA), "How to do the 5 most common things" (deploy a change, rotate the test-corp key, simulate ingestion, retry a dead-letter, restart Mosquitto — each one line pointing at the right `make` target from F1). Audience: a new engineer on day 1, or oneself at 03:00 on day 200. ≤2 printed pages.
- [shipped] **H4** `[backend]` **Refresh `README.md` + `docs/architecture.md`.** README: drop stale Sprint 1–10 "what's coming" language; add a "Status" section pointing at the latest shipped sprint; ensure deploy/dev quickstart commands actually work in May 2026 (verify against current `Makefile`/`scripts/`). Architecture: add the network-hardening + tools-job + alert-rules surfaces shipped Sprints 22–28; mark anything aspirational as such. Verify every diagram against the `ascii-diagram-alignment.md` recipe (in user memory) constraints.
- [shipped] **H5** `[backend]` **Markdown lint + link check in CI.** Add `markdownlint-cli2` and `lychee` (link checker) as a workflow step on `pull_request` for any path under `docs/`, top-level `*.md`, and `CHANGELOG.md`. Allowlist: `localhost` URLs, `example.com`, intentional anchor-only fragments. Fails CI on broken internal links, broken absolute external links (with retry on 5xx so flaky upstreams don't break us), and the lint rules already implicit in our existing markdown style (no trailing whitespace, fenced code language tags required, single H1). Catches the runbook drift from H2 automatically going forward.
- [done] **H6** `[backend]` **API reference refresh.** Regenerated [openapi.json](../openapi.json) with `make export-openapi`. Dropped the Sprint 21 `GET /telemetry-models/{device_type}` 410 Gone tombstone from [src/tagpulse/api/routes/telemetry_models.py](../src/tagpulse/api/routes/telemetry_models.py) — the Sprint 19 301 redirect and the Sprint 21 410 each ran past their full retention windows, so the un-routed path now simply 404s. Added a CI gate to [.github/workflows/ci.yml](../.github/workflows/ci.yml): `make export-openapi && git diff --exit-code openapi.json` so future route-shape changes can't ship without a matching spec commit.
- [shipped] **H7** `[backend]` **Azure architecture doc** → [docs/azure-architecture.md](azure-architecture.md). Physical/deployed view: every Bicep-provisioned resource type with the per-env name, the VNet/subnet/NSG layout, identity & secrets flow, data-plane flows, what is intentionally NOT deployed (Front Door, Bastion, Storage, etc.) and why, per-env SKU differences, and the operator entry-point cheat sheet (`azd-job.sh`, `make doctor`, etc.). Complements `docs/architecture.md` (logical) which now backlinks to it. Triggered by the gap that nothing in `docs/` told a new operator what an `aca-infra` subnet was or why Mosquitto was on ACI not ACA. Landed mid-sprint per user request.

### Acceptance criteria

- A pull request that bumps a Bicep parameter shows the resource diff as a sticky PR comment within 3 minutes of push (A4).
- Merging to `main` triggers the deploy workflow; if the seeded tenant returns 500 on `/tenant/config`, the workflow fails with the failing curl in the summary (A5).
- `scripts/azd-doctor.sh dev` runs in <60s and prints a per-check status line with a remediation hint for any red check (F3).
- An operator can answer "when does any KV secret in dev expire next?" in one command: `make doctor ENV=dev` (or `scripts/azd-kv-audit.sh dev`) (B1, F1).
- Stopping the Mosquitto ACI for >5 minutes triggers an Azure Monitor alert email (D2 + D4) and `make logs SERVICE=mqtt ENV=dev` shows the broker logs (F1).
- The dead-letter UI page (Sprint 27 C5) shows MQTT-rejected payloads with `source='mqtt'` and a meaningful `error` (C3).
- `mqtts://tp${env}-mqtt.${region}.azurecontainer.io:8883` accepts a TLS connection presenting the env's self-signed cert; `clients/pi/` connects with `TAGPULSE_MQTT_TLS_CA=…` set; `tcp/1883` continues to accept connections during the Sprint 28 → Sprint 29 deprecation window (C6).
- A new engineer can run the DB restore drill end-to-end against `dev` by following `docs/runbooks/db-failover-and-restore.md` without asking questions; "Last drilled" timestamp on the runbook is current (E2 + E4).
- The Sites & Zones page shows an Edit pencil for the **Boston DC** row; clicking it opens a modal where `timezone` can be changed from `America/New_York` to any IANA TZ; saving calls `PATCH /sites/{id}` and the row's timezone column updates without a page reload (G3 — the trigger).
- Device detail page (editor+) shows an Edit button that opens a modal whose Save call to `PATCH /devices/{id}` updates `name`/`site_id`/`zone_id`/`metadata`; the read-only banner that operators currently work around with `curl` is gone (G4).
- Telemetry Models page (admin) shows an Edit pencil per row; saving updates `unit`/`min`/`max` without recreating the model — historical quarantine references survive (G1 + G6).
- [docs/refs/ui-crud-audit-sprint28.md](refs/ui-crud-audit-sprint28.md) and [docs/refs/docs-audit-sprint28.md](refs/docs-audit-sprint28.md) exist, are linked from `docs/roadmap.md` (this entry), and every gap in either audit either ships in Sprint 28 or has an explicit "deferred — gated on…" line.
- [docs/operator-quickstart.md](operator-quickstart.md) exists, is ≤2 printed pages, and a new engineer can complete "deploy a change to dev" + "rotate the test-corp key" using only that doc + this repo's `Makefile` (H3 + F1).
- `markdownlint-cli2` + `lychee` run on every PR touching `docs/`, `*.md`, or `CHANGELOG.md`; a PR that introduces a broken internal link fails CI before merge (H5).
- `make check` clean. One Alembic migration (C3 `dead_letter_events.source` column). One Bicep module added (`alerts.bicep`). One backend route added (G1 `PATCH /telemetry-models/{id}`).

### Risks & mitigations

- **Risk:** Azure Monitor alert noise in v1 (D2). **Mitigation:** ship at SEV 2 with email-only routing; revisit thresholds after one week of data; promote to PagerDuty only when a customer requires it.
- **Risk:** the DR drill (E4) accidentally breaks `dev` for other engineers. **Mitigation:** schedule the drill, announce it in CHANGELOG-Unreleased before running, and execute against a freshly `azd up`'d throwaway env (`tagpulse-drdrill`) instead of the shared `dev`. Update E2 runbook to require this.
- **Risk:** consolidating shell scripts (F2) regresses behavior the existing scripts encoded by accident. **Mitigation:** F2 lands as a pure refactor PR with no behavior change; each migrated script gets a smoke run against `dev` before the PR merges; rollback is a single revert.
- **Risk:** broker canary (C2) creates a low-volume but persistent "fake tenant" in production. **Mitigation:** canary tenant is created by `smoke_setup.py --canary`, lives under `00000000-0000-0000-0000-0000000000ca`, is excluded from billing aggregations via a `tenants.is_canary` flag (small migration; consider folding into the C3 migration to keep migration count at one).
- **Risk:** the C6 self-signed cert means device side has to trust a per-env CA — a misconfigured `TAGPULSE_MQTT_TLS_CA` causes silent connection failures that look identical to a network outage. **Mitigation:** keep `tcp/1883` enabled through Sprint 28 as the rollback path; the C2 canary publishes via 8883 so we detect TLS-side breakage before devices do; the mqtt-outage runbook (C4) gets an explicit "if TLS handshake failing, fallback to 1883" branch. Full migration to TLS-only happens in Sprint 29 once devices are confirmed migrated.

### Deferred to Sprint 29+

- **Scheduled Container Apps Jobs** (broker canary on a 5-min cron, KV expiry sweep on a daily cron). Sprint 26 deferred this; Sprint 28 builds the jobs but leaves them manual-trigger so we have a few weeks of manual operating data before automating.
- **PagerDuty / Opsgenie integration** for D2 alert action groups. Gated on first paying customer with an SLA.
- **Multi-region failover.** Today everything is single-region; DR plan in E2 is restore-in-place. Cross-region active-passive is its own ADR.
- **`src/tagpulse/cli/` Click-based unification of `scripts/*.py`.** Sprint 26 deferred; Sprint 28 doesn't add enough new scripts to trip the >5-script gate yet.
- **Synthetic external probe** (api availability SLO measured from outside Azure — currently we measure from inside the same region). Gated on first SLA commitment.
- **Cost & resource hygiene sweep** (idle ACA min-replicas review, log retention tuning, ACR untagged image cleanup) — folded into a future "Sprint 28b cost pass" if D2's KV/ACR cost alerts fire, otherwise gated on first invoice surprise.

---

## Sprint 29 — Edge simulator MQTT movement publisher (shipped)

> Goal: extend the Pi edge client's smoke publisher to replay GPS waypoint tracks over a single MQTT session so we can demo asset movement (and exercise the Sprint 17a `subject.zone_changed` evaluator) without real hardware. Single PR, no schema changes.

- [shipped] **Edge MQTT movement simulation — paho publisher tracks** ([#21](https://github.com/9owlsboston/TagPulse/pull/21), [`6ecf9b8`](https://github.com/9owlsboston/TagPulse/commit/6ecf9b8)). Added GPS waypoint replay to the Pi edge client's smoke MQTT publisher.
- [shipped] **Companion docs: domain concepts 101 + `drive_track` tag-id support** ([#22](https://github.com/9owlsboston/TagPulse/pull/22), [`b7057ee`](https://github.com/9owlsboston/TagPulse/commit/b7057ee)). Added [docs/guides/domain-concepts-101.md](guides/domain-concepts-101.md) primer and extended the `drive_track` helper so simulated waypoints can be published as `tag-reads` (consumed by the Asset Path tab) rather than only as device-`/location` updates.
- [shipped] **MQTT subscriber resilience + payload shape docs** ([#23](https://github.com/9owlsboston/TagPulse/pull/23), [`567e7f2`](https://github.com/9owlsboston/TagPulse/commit/567e7f2)). Bundled fix for [#18](https://github.com/9owlsboston/TagPulse/issues/18) / [#19](https://github.com/9owlsboston/TagPulse/issues/19).
- [shipped] **CI deploy-azure: resolve short SHAs + default to latest `main`** ([#24](https://github.com/9owlsboston/TagPulse/pull/24), [`1b0d667`](https://github.com/9owlsboston/TagPulse/commit/1b0d667)). Workflow hardening so manual deploys don't fail on short-SHA inputs.

## Sprints 30, 31, 32 — Not used

> The `sprint-30` / `sprint-31` / `sprint-32` labels were never adopted. Sprint 29 → Sprint 33 happened directly; no intermediate workstream existed. Listed here so future audits don't refile the gap.

## Sprint 33 — Reference-design remediation kickoff + UI quick-wins (shipped)

> Goal: scope-lock the gap audit against an external IoT cloud-platform reference design into a single planning document, land the ADR stubs for the blocking-tier items, and ship the near-zero-cost UI quick-wins that close the perceptual gap. Per-tenant branding backend slice rides along.

- [shipped] **Reference-design remediation plan + ADRs 019–024 (Proposed)** ([#30](https://github.com/9owlsboston/TagPulse/pull/30), [`854ad30`](https://github.com/9owlsboston/TagPulse/commit/854ad30)). Lands [docs/design/reference-design-remediation.md](design/reference-design-remediation.md) (Commit/Defer/Drop decision per gap, sprint slots, §7 "Updating this document" rule) and the six ADR stubs at [docs/adr/019-categories.md](adr/019-categories.md), [adr/020-labels-first-class.md](adr/020-labels-first-class.md), [adr/021-configurable-sensing-events.md](adr/021-configurable-sensing-events.md), [adr/022-soft-assets.md](adr/022-soft-assets.md), [adr/023-outbound-connections-mqtt-kafka.md](adr/023-outbound-connections-mqtt-kafka.md), [adr/024-position-estimation.md](adr/024-position-estimation.md). All six land at status **Proposed**.
- [shipped] **UI quick-win remediation rows flipped to ✅ Done** ([#35](https://github.com/9owlsboston/TagPulse/pull/35), [`e92bd10`](https://github.com/9owlsboston/TagPulse/commit/e92bd10)). Doc reconciliation after the corresponding TagPulse-UI quick-wins shipped (Sider section groups, Account dropdown, AntD `ConfigProvider`+theme, reusable `<LastUpdate/>`, light/dark toggle, per-tenant branding UI).

## Sprint 34 — Categories + structured sites (shipped)

> Goal: ratify [ADR 019](adr/019-categories.md) and ship the Categories entity end-to-end. Structured site addresses + Site/Transporter discriminator ride along.

- [shipped] **Categories (ADR 019 ratified)** ([#31](https://github.com/9owlsboston/TagPulse/pull/31), [`6487347`](https://github.com/9owlsboston/TagPulse/commit/6487347)). Categories entity ships across backend + UI; `assets.category_id` FK with `ON DELETE RESTRICT`.
- [shipped] **Structured site address + Site/Transporter discriminator (gap 2.7)** ([#33](https://github.com/9owlsboston/TagPulse/pull/33), [`ec32ecd`](https://github.com/9owlsboston/TagPulse/commit/ec32ecd)). Sites gain structured address fields and a `kind` discriminator separating Sites from Transporters.
- [shipped] **Remediation row flips — backend + UI rows marked Done** ([#34](https://github.com/9owlsboston/TagPulse/pull/34), [`c428c72`](https://github.com/9owlsboston/TagPulse/commit/c428c72)). Doc reconciliation per the remediation doc's §7 update rule.

## Sprint 35 — Labels first-class (shipped, ADR 020)

> Goal: ratify [ADR 020](adr/020-labels-first-class.md) and ship the labels catalog + per-entity association surface + deep-object filter. Multi-phase backend ship train.

- [shipped] **Kickoff — ratify ADR 020 + roadmap** ([#36](https://github.com/9owlsboston/TagPulse/pull/36), [`6970854`](https://github.com/9owlsboston/TagPulse/commit/6970854)).
- [shipped] **Phase A — labels catalog schema** ([#37](https://github.com/9owlsboston/TagPulse/pull/37), [`dc91f3a`](https://github.com/9owlsboston/TagPulse/commit/dc91f3a)).
- [shipped] **Phase B — labels API** ([#38](https://github.com/9owlsboston/TagPulse/pull/38), [`a6bdde5`](https://github.com/9owlsboston/TagPulse/commit/a6bdde5)).
- [shipped] **Phase C — labels deep-object filter** ([#39](https://github.com/9owlsboston/TagPulse/pull/39), [`f8bc438`](https://github.com/9owlsboston/TagPulse/commit/f8bc438)).
- [shipped] **Phase E — labels surface: user guide, operator quickstart, remediation flip** ([#40](https://github.com/9owlsboston/TagPulse/pull/40), [`4b70e31`](https://github.com/9owlsboston/TagPulse/commit/4b70e31)).
- [shipped → Sprint 39] **Phase B orphan entity_labels cleanup** intentionally deferred; shipped in Sprint 39 (see below).

## Sprint 36 — Auto-deploy backend to dev (shipped) *(SLIP — see note)*

> **Slip note:** the originally planned scope was ADR 021 v2 Configurable Sensing Events (remediation rows 2.3 + 2.9 + UI 1.1 / 3.5) plus the outbound event envelope upgrade. None of that scope shipped under this label — only a one-PR CI change did. The planned scope is now Sprint 41 (see plan below).

- [shipped] **Auto-deploy backend to dev on `push:main`** ([#41](https://github.com/9owlsboston/TagPulse/pull/41), [`67d0281`](https://github.com/9owlsboston/TagPulse/commit/67d0281)). Closes the manual-`azd deploy` gap that let dev drift from `main` after merge.
- [shipped — direct to main] Three small ops fixes landed outside the PR flow during this window: dev-wake cron range fix ([`ae780fd`](https://github.com/9owlsboston/TagPulse/commit/ae780fd)), pre-push guard blocking direct pushes to main ([`ea11f08`](https://github.com/9owlsboston/TagPulse/commit/ea11f08)), strip `azd` 1.25 update nag + doctor recovery cheat sheet ([`5a6345a`](https://github.com/9owlsboston/TagPulse/commit/5a6345a)).

## Sprint 37 — Category server-filter + UI label parity follow-ups (shipped) *(SLIP — see note)*

> **Slip note:** the originally planned scope was ADR 023 MQTT outbound dispatcher (remediation row 2.5 + UI 3.6 Connections page redesign + row 2.15 per-conn rate-limit + monitor). None of that scope shipped under this label — the work that *did* ship was follow-on label/category UI catch-up and one small backend slice. ADR 023 stays at Proposed; row 2.5 stays Commit.

- [shipped] **Doc flip — remediation row 3.9a → ✅ Done after TagPulse-UI ships label chips** ([#42](https://github.com/9owlsboston/TagPulse/pull/42), [`265746a`](https://github.com/9owlsboston/TagPulse/commit/265746a)). Reconciliation per §7.
- [shipped] **Backend `GET /assets?category_id=` server-side filter (row 2.8a)** ([#43](https://github.com/9owlsboston/TagPulse/pull/43), [`6f45e21`](https://github.com/9owlsboston/TagPulse/commit/6f45e21)). Promotes Categories filter on the Assets list from client-side to server-side; combines with existing `asset_type`/`status`/`q`/`labels[…]` via AND.
- [shipped] **Doc flip — remediation row 2.8a → ✅ Done after #43** ([#44](https://github.com/9owlsboston/TagPulse/pull/44), [`b9773be`](https://github.com/9owlsboston/TagPulse/commit/b9773be)).

## Sprint 38 — Remediation row flips after UI catch-up (shipped) *(SLIP — see note)*

> **Slip note:** the originally planned scope was the Bridge/Gateway device-role split (remediation row 1.1) + Connectivity Monitor backend + UI (rows 2.13 + 3.8) + Tags page (rows 1.1 + 3.4) + Bridge OTA toggle (row 2.11). None of that scope shipped — only doc status-flips reconciling rows whose implementing TagPulse-UI PRs had merged. The deferred scope stays Commit on the remediation doc.

- [shipped] **Doc flip — rows 3.9b / 3.9c / 3.9d → ✅ Done after UI ships** ([#45](https://github.com/9owlsboston/TagPulse/pull/45), [`165b8fd`](https://github.com/9owlsboston/TagPulse/commit/165b8fd)).
- [shipped] **Doc flip — row 3.3a → ✅ Done after TagPulse-UI #44** ([#46](https://github.com/9owlsboston/TagPulse/pull/46), [`1f2570e`](https://github.com/9owlsboston/TagPulse/commit/1f2570e)).

## Sprint 39 — ADR 020 Phase B orphan cleanup (shipped) *(SLIP — see note)*

> **Slip note:** the originally planned scope was ADR 022 Soft Assets (remediation row 2.4) + the Soft Assets column on the Locations table (row 3.2 part 2). That scope was deferred; the work that *did* ship was the lone unshipped clause of ADR 020 (Phase B orphan `entity_labels` cleanup, row 2.2a) carried over from the Sprint 35 ship train. ADR 022 stays at Proposed; row 2.4 stays Commit.

- [shipped] **ADR 020 Phase B — orphan `entity_labels` cleanup on hard-delete entity handlers (row 2.2a)** ([#47](https://github.com/9owlsboston/TagPulse/pull/47), [`26a6a58`](https://github.com/9owlsboston/TagPulse/commit/26a6a58)).
- [shipped] **Doc flip — remediation row 2.2a → ✅ Done after #47** ([#48](https://github.com/9owlsboston/TagPulse/pull/48), [`369dd49`](https://github.com/9owlsboston/TagPulse/commit/369dd49)).

## Sprint 40 — Vendor-neutrality doc sweep (shipped) *(SLIP — see note)*

> **Slip note:** the originally planned scope was [ADR 024](adr/024-position-estimation.md) Indoor position estimation (remediation row 2.18) — trilateration processor on the ADR 021 v2 sensing-events stack, `asset_positions` hypertable, BYO-positions ingest path. That scope was deferred and now requires ADR 021 v2 to land first (Sprint 41). The work that *did* ship was a forward-only documentation rewording pass. ADR 024 stays at Proposed; row 2.18 stays Commit.

- [shipped] **Repo vendor-neutrality sweep** ([#49](https://github.com/9owlsboston/TagPulse/pull/49), [`1f9595b`](https://github.com/9owlsboston/TagPulse/commit/1f9595b)). Forward-only documentation/docstring rewording pass replacing vendor-specific terminology with TagPulse-native equivalents.

## Methodology drift in Sprints 36–40

Sprints 36–40 above each carry a *(SLIP)* marker because the planned scope for those numbers did not ship under those labels — the labels were applied to follow-on work and doc reconciliation while the original ADR-driven scope (ADR 021 Sensing Events, ADR 023 MQTT, Bridge/Gateway split, ADR 022 Soft Assets, ADR 024 Position Estimation) stayed in **Proposed** status on the remediation doc. The five blocking-tier ADRs are not yet ratified; Sprint 41 onward picks them up in their original sequence:

| Originally planned for | Scope | Now targeted for |
|---|---|---|
| Sprint 36 | ADR 021 v2 Configurable Sensing Events + outbound envelope upgrade | Sprint 41 |
| Sprint 37 | ADR 023 MQTT outbound dispatcher + Connections page redesign + rate-limit + monitor | Sprint 42 |
| Sprint 38 | Bridge/Gateway split + Connectivity Monitor + Tags page + OTA toggle | Sprint 43 |
| Sprint 39 | ADR 022 Soft Assets + auto-create policy + Locations Soft Assets column | Sprint 44 |
| Sprint 40 | ADR 024 Indoor position estimation (depends on ADR 021 v2 landing first) | Sprint 45 |

Going forward, sprint numbers will be allocated by `scripts/start-sprint.sh <NN> <topic-slug>` and tied to one planned workstream each; out-of-band doc flips will land as `docs/...` branches rather than as new sprint labels.

## Sprint 41 — Configurable Signaling Events (ADR 021 v2)

> Design: [ADR-021 v2](adr/021-configurable-sensing-events.md), [ADR-019](adr/019-categories.md) (closure), [reference-design-remediation plan](design/reference-design-remediation.md) (rows 2.3 backend, 2.9 envelope, 1.1 sidebar group, 3.5 modal)
> Goal: ratify ADR 021 v2 Proposed → Accepted and ship the full Configurable Signaling Events surface in one sprint per the ADR's "single-PR delivery, single migration" decision: extend `rules` with the additive scoping/processor/confidence columns, add the new `signaling.<event_type>.<trigger>` condition kinds across all four event types (Location / Geolocation / Temperature / Geofencing), the OverlappingZones processor module, the periodic-cadence dispatcher, the upgraded outbound webhook envelope (which fires for legacy rules too with safe defaults — closes gap 2.9), and the consolidated "Events & Alerts" UI surface in TagPulse-UI. The same sprint **closes [ADR-019](adr/019-categories.md)** by dropping the deprecated `assets.asset_type` shadow column (Phase H) — the new Signaling Events scope categories rather than free-form type strings, so finishing the deprecation here keeps the API surface internally consistent. UI work also includes a **broader sidebar reorganization** and a **collapsible icon-mode sidebar** for wider data tables (Phase F additions). Sprint workstream PR is opened by `scripts/start-sprint.sh 41 sensing-events` per the canonical convention (the branch slug stays `sensing-events` as a historical artifact — the on-disk branch + PR #54 ship under that name; the in-repo terminology is "signaling" going forward). This section is the planning lock so the implementation PR has scope to point at.
>
> **Terminology note (mid-sprint):** Following the Backlog "Rule taxonomy unification" item, this sprint adopts **"signaling"** in place of "sensing" throughout new code, docs, and UI labels, aligning with Azure Monitor's "Signal → Condition → Action" vocabulary that the deferred post-Sprint-41 cleanup ADR will ratify. The ADR-021 file path stays (`adr/021-configurable-sensing-events.md`) for link stability; its title + body are updated to "Configurable Signaling Events". Historical CHANGELOG / remediation entries that say "Sensing Events" are intentionally **not** rewritten — they describe shipped or planned work as-written at the time.

### Phase A — Schema migration

- [done, `e54332b` (PR [#54](https://github.com/9owlsboston/TagPulse/pull/54))] **A1 — Alembic migration** extending `rules` per [ADR 021 §"Schema"](adr/021-configurable-sensing-events.md): additive nullable columns (`event_type`, `trigger`, `processor` VARCHAR(32); `confidence_threshold` NUMERIC(3,2) DEFAULT 0.0; `category_ids` UUID[] DEFAULT '{}'; `asset_label_filters` / `zone_label_filters` / `site_label_filters` JSONB; `integration_ids` UUID[]). Migration `040_rules_signaling_events.py` shipped. Legacy rows untouched — NULL `event_type` is the implicit `kind=legacy` discriminator. Idempotent; reruns are a no-op.
- [done, `e54332b`] **A2 — Partial index** `idx_rules_signaling_active ON rules (tenant_id, event_type, trigger) WHERE enabled = true AND event_type IS NOT NULL` so the new evaluator paths probe a small slice rather than full-scanning `rules`. Shipped in the same migration.
- [done, `e54332b`] **A3 — Extend `_RULE_CONDITION_PATTERN`** regex with the 12 new `signaling.*.*` values from [ADR 021 §"New condition_type values"](adr/021-configurable-sensing-events.md). VARCHAR + pattern stays (per ADR open question #1 — easier to extend than Postgres ENUM); the 10 legacy `condition_type` values are untouched.
- [done, `e54332b` (A discriminator helper) + `9f02f38` (per-trigger config models)] **A4 — Pydantic schemas.** Phase A landed the regex + `SIGNALING_VALID_PAIRS` lookup + `split_signaling_condition_type` helper; Phase B landed the per-trigger `BaseModel` config classes (`SignalingPeriodicConfig`, `SignalingOnChangeConfig`, `SignalingOnInactivityConfig`, `SignalingOnInferenceConfig`, `SignalingOnEntryConfig`, `SignalingOnExitConfig`) + `validate_signaling_condition_config()` consumed by `RulesService`. Invalid pairs reject at schema validation, not at the evaluator. Implementation pragmatically chose a two-level helper over a Pydantic `Field(discriminator=...)` union — same error-message granularity, less framework surface, drop-in replaceable later.
- [done, `e54332b`] **A5 — Conformance test** — covered by the existing `tests/integration/test_migration_round_trip.py` harness (gated on TimescaleDB container via `make migration-check`); 51 new unit tests in `tests/unit/test_rule_schemas.py` parametrize all 12 valid pairs through `RuleCreate` + a fail-list of 16 invalid pairs.

### Phase B — Evaluator + service

- [done, `9f02f38`] **B1 — `RuleService` `kind=` filter** (`signaling` = `event_type IS NOT NULL`, `legacy` = `IS NULL`). `RulesService.list_rules(tenant_id, *, enabled_only=False, kind=None)` uses the partial index from A2 for the signaling path. API responses expose computed `kind` via a Pydantic `model_validator` on `RuleResponse`; the discriminator stays implicit at the column level (per ADR Consequences §"trade-offs").
- [done, `9f02f38`] **B2 — Default-cap enforcement** at the `POST` / `PATCH` handlers: 5 active rows per `(tenant_id, event_type, category_id)` scope (broadcast scope — empty `category_ids` — is its own bucket). Hard reject with HTTP 409 carrying `{event_type, category_id, current_count, cap, override_hint}` (per ADR open question #4); admin-only `?override=true` flag bypasses with one `audit_logs` row per override (`action="signaling.cap_override"`). Enforced in the API layer not the DB so errors are friendly and per-tenant relaxation is cheap.
- [done, `9f02f38`] **B3 — `PeriodicSignalingDispatcher`** worker shipped as a Phase B shell in `src/tagpulse/signaling/periodic_dispatcher.py` (loop + cadence accounting + meter tick); the real `_evaluate_periodic_rule` body landed in Phase D `19f979e` once `OverlappingZonesProcessor` was available. Cadence stored in `condition_config.cadence_minutes` (1 ≤ N ≤ 1440). Wakes on a cadence tick, evaluates `signaling.*.periodic` rules, reuses the existing rules-engine output path.
- [done, `9f02f38`] **B4 — `signaling.attribution_settled` event-bus topic** (`Topic.SIGNALING_ATTRIBUTION_SETTLED`, in-process per ADR 010) shipped in `src/tagpulse/events/protocol.py`. Emitted by the OverlappingZones processor (Phase D) when its aggregation window resolves; `signaling.*.on_inference` rules subscribe via `RuleEvaluator.on_attribution_settled` (Phase D).
- [done, `9f02f38` (B-baseline) + `19f979e` (Phase D additions)] **B5 — Unit tests** — ~50 new tests on Phase B (test_rule_schemas / test_signaling_event_bus / test_signaling_periodic_dispatcher / test_signaling_rule_service / test_signaling_rules_api), +50 more on Phase D (isolated/overlapping zones + on_inference) for a Sprint 41 unit-test delta of ~100 new tests.

### Phase C — Outbound envelope upgrade (closes gap 2.9)

- [done, `feae604`] **C1 — `tagpulse/integrations/signaling_envelope.py`** module shipped — pure (no I/O) `build_envelope(*, rule_id, event_type, confidence_threshold, category_id=None, labels=None) -> SignalingEnvelopeFields` + `derive_key_set(event_type)` + per-event-type `_KEY_SETS` table.
- [done, `feae604`] **C2 — Wired into the dispatcher** so the envelope fires for **all rules**, not just signaling. Legacy rules get safe defaults (`confidence=1.0`, `keySet=[]`, `eventConfigurationId=str(rule_id)`, `categoryId=null`, `labels=[]`). Additive — existing webhook consumers see the same payload they get today, plus the five new fields. Gated on `event.topic == Topic.ALERT_TRIGGERED` so raw broadcasts (`tag_read.created`, `telemetry.recorded`) keep the pre-Phase-C envelope unchanged. Categories / labels stay at safe defaults pending a future Phase that wires matched-entity propagation.
- [done, `feae604`] **C3 — Conformance test** — 34 new tests across `tests/unit/test_signaling_envelope.py` (24, pure-function paths) and `tests/unit/test_webhook_envelope.py` (10, dispatcher path via `httpx.MockTransport`) cover legacy-rule preserve-all-history + five-new-defaults; signaling-rule populate-from-columns per event type; non-`ALERT_TRIGGERED` events regression-guarded against accidental envelope leak.

### Phase D — Processor implementations

> **Coordinate-system scope.** Both processors operate on **zone membership** — either `tag_reads.reader_id ∈ zones.fixed_reader_ids` (reader-bound, no coordinates needed) or `(tag_reads.latitude, tag_reads.longitude) WITHIN zones.polygon_geojson` (WGS84 geofence). They emit `(asset, zone, confidence)`, **not** local `(x, y)` positions. True indoor `(x, y)` trilateration — for football-field-size sites with a fixed-reader XY grid — is a separate processor (`trilateration`) shipped by [ADR 024](adr/024-position-estimation.md) in Sprint 45, which also adds `sites.coord_system JSONB`, `devices.position_x/y/z`, and the `asset_positions` hypertable. Warehouse customers who want zone-level attribution today should model aisles / sub-areas as **reader-bound zones** and use OverlappingZones; customers who want sub-meter `(x, y)` wait for Sprint 45.

- [done, `19f979e`] **D1 — IsolatedZones (made explicit).** No algorithm change — codified the existing implicit single-zone attribution into pure functions in `src/tagpulse/signaling/isolated_zones.py` (`ZoneCandidate`, `attribute_reader_bound`, `attribute_geofence`, combined `attribute(...)`). Default `processor` when NULL and `event_type IN (location, geofencing)`. 17 new tests in `tests/unit/test_signaling_isolated_zones.py`.
- [done, `19f979e`] **D2 — OverlappingZones (genuinely new).** New `src/tagpulse/signaling/overlapping_zones.py` module per [ADR 021 §"Processor implementation"](adr/021-configurable-sensing-events.md). Pure layer (`AggregationConfig`, `_attributable_zones_for_read`, `_age_weight`, `aggregate(...)`) + runtime layer (`OverlappingZonesProcessor.run_once_for_rule`). Runs aggregation window over `tag_reads` matching `(asset, [overlapping zones])`, applies RSSI floor + time-error filter + aging weight, emits one **coordinate-system-agnostic** `signaling.attribution_settled` event per zone the asset confidently occupies (payload explicitly excludes `latitude` / `longitude` / `x` / `y` per `42294f5` clarification — trilateration is [ADR 024](adr/024-position-estimation.md) / Sprint 45). Config in `condition_config.processor_config` JSONB (`aggregation_window_s` enum `30 | 60 | 300 | 1800` per ADR open question #5, `min_rssi_dbm` with tri-state semantics, `zone_bleed_filter`, `aging_weight`, `time_error_filter`).
- [done, `19f979e`] **D3 — Tests** — 27 tests in `test_signaling_overlapping_zones.py` cover synthetic multi-reader streams (single-zone, 50/50 genuine overlap, 80/20 share, RSSI floor / window enforcement / aging-shift / bleed-filter / deterministic order); 7 dispatcher-routing tests in `test_signaling_overlapping_zones_processor.py` (with an explicit **forbidden-coordinate-fields** assertion against the emitted payload); 10 on-inference consumer tests in `test_signaling_on_inference.py` (happy-path, threshold, cooldown per `(tenant, rule, asset)`, fan-out across all three on_inference event_types, defensive paths).

### Phase E — API surface

- [done, Phase B2 `9f02f38` (already shipped on the existing URL by the time Phase E started — see Phase E CHANGELOG entry)] **E1 — Reuse `/v1/tenants/{slug}/rules?kind=signaling`** instead of standing up a parallel `/sensing-events` URL namespace. **Deviates from [ADR 021 §"API surface"](adr/021-configurable-sensing-events.md)** which proposed a UX-alias namespace — the deviation avoids baking a permanent legacy URL into the API at the moment the deferred Sprint-4X taxonomy-unification ADR (Backlog "Rule taxonomy unification") plans to rename `rules` → `alert_rules` and collapse the dual-shape kinds anyway. Concretely: `GET /v1/tenants/{slug}/rules?kind=signaling` lists; `POST /v1/tenants/{slug}/rules` with `"kind": "signaling"` in the body creates; per-rule mutations (`/activate` / `/deactivate` / `/duplicate`) ride on the existing `/rules/{id}/*` endpoints. Reuses RBAC, RLS, audit, and validation paths unchanged. The "Signaling Events" framing lives in the UI label layer (Phase F1 — sidebar) and the SignalingRuleModal (Phase F2 — see [reference-design-remediation.md row 3.5](design/reference-design-remediation.md)), not the URL.
- [done, `975ef6f`] **E2 — Per-rule integration routing.** Empty/null `integration_ids` = broadcast (legacy behaviour preserved); non-empty = **replace** the global broadcast (per ADR open question #3 — replace, not augment). Implemented in `WebhookDispatcher._resolve_alert_rule_context` (renamed from `_build_alert_envelope` — one rule lookup per tick now feeds both the Phase-C envelope build and the Phase-E integration filter; no second DB lookup). Allow-list intersects with the subscription set: non-subscribed integration in the list is skipped; disjoint list yields zero deliveries (surfaces operator mis-config rather than papering over). 12 new tests in `tests/unit/test_webhook_per_rule_routing.py`.
- [done, `975ef6f`] **E3 — OpenAPI regeneration** — `python scripts/export_openapi.py > openapi.json` picks up the Phase A schema additions (`event_type`, `trigger`, `processor`, `confidence_threshold`, `category_ids`, `*_label_filters`, `integration_ids` on `RuleCreate` / `RuleUpdate` / `RuleResponse`), the extended `condition_type` regex covering all 22 values, the `kind` discriminator on `RuleResponse`, and the `?kind=` query param on `GET /rules`. Diff stat: `+413/-2` lines. TagPulse-UI ran `npm run generate-api` against this spec in Phase F.

### Phase F — UI (TagPulse-UI repo, separate PR)

- [done, TagPulse-UI [PR #46](https://github.com/9owlsboston/TagPulse-UI/pull/46) `ebb2f82`] **F1 — "Signaling Events" sidebar group** shipped in `src/components/Layout.tsx`. Consolidates Rules + Alerts under one Ant Menu `type: 'group'` header per UI gap 1.1. Final label is **"Signaling Events"** (matches the v2.1 sensing→signaling rename) rather than the neutral-during-planning "Events & Alerts" — the Azure-Monitor-aligned vocabulary is already adopted across the schema (`event_type`, `signaling.*`), the event bus (`Topic.SIGNALING_ATTRIBUTION_SETTLED`), and the React components (`SignalingRuleModal`, `SIGNALING_VALID_PAIRS`) so the sidebar label aligns with the rest of the surface.
- [done, UI PR #46 `ebb2f82`] **F2 — `SignalingRuleModal`** shipped at `src/pages/rules/SignalingRuleModal.tsx`. Steps: Event Type → Trigger (validated against the 13-pair `SIGNALING_VALID_PAIRS` matrix in `src/types.ts` — impossible pairs hidden from the dropdown, not greyed) → per-trigger config form → optional Category / Label scoping. Form POSTs to `POST /v1/tenants/{slug}/rules` with `"kind": "signaling"` body (per Phase E1 — no `/sensing-events` URL).
- [done, UI PR #46 `ebb2f82`] **F3 — RuleEditor Tabs wrapper.** `src/pages/rules/RuleEditor.tsx` now wraps the form in an AntD Tabs component — "Signaling Event" tab is the default for create (with an info Alert pointing operators at the new pattern); the **"Legacy rule"** sub-tab keeps the existing 10 legacy `condition_type` form editable for round-tripping existing rules per [ADR 021 §"UI"](adr/021-configurable-sensing-events.md).
- [done, UI PR #46 `ebb2f82`] **F4 — vitest coverage** — 16 cases in `src/pages/rules/SignalingRuleModal.test.tsx` parametrize all 13 valid `(event_type, trigger)` pairs through the form + 4 invalid-pair rejections (one per event_type). Uses `PointerEventsCheckLevel.Never` + 15s per-test timeout + explicit `afterEach(cleanup)` to handle AntD motion + Select interactions.
- [done, UI PR #46 `ebb2f82`] **F5 — Sidebar reorganization.** Top-level groups consolidated: DATA MANAGEMENT (Assets, Categories, Sites, Labels) / EDGE MANAGEMENT (Devices, Integrations) / SIGNALING EVENTS (Rules, Alerts) / INVENTORY. Admin items continue to live in the top-right Account dropdown (from the Sprint 33 quick-win row).
- [done, UI PR #46 `ebb2f82`] **F6 — Collapsible icon-mode sidebar.** Ant `Sider` with `collapsible` + `collapsedWidth={64}` pattern; chevron toggle expands / collapses; tooltips appear on hover when collapsed. Persisted in `localStorage` keyed `(tenantId, userId)` so each operator's preference travels with their account. **Surfaced + fixed a real bug** during F8 test writing — AntD `Sider.onCollapse(_, 'responsive')` fires on jsdom mount (default `matchMedia` returns `matches: false`) and was clobbering the persisted preference; `handleSiderCollapse(next, type)` now only writes to `localStorage` when `type === 'clickTrigger'`, while responsive events still update visible state via `setCollapsedState`.
- [done, UI PR #46 `ebb2f82`] **F7 — Asset form: drop visible `Type` field.** `src/pages/assets/AssetList.tsx` and `src/pages/assets/AssetDetail.tsx` drop the visible `asset_type` input. A hidden `Form.Item` pinned `asset_type` to `'asset'` for create and preserved the existing value for edit, so the backend (still carrying the column at that point) kept round-tripping cleanly. Filter chips on the Assets list page drop the legacy Type chip in favour of the existing Category chip. The backing migration that drops the column itself shipped immediately afterwards as Phase H in this PR.
- [done, UI PR #46 `ebb2f82`] **F8 — vitest coverage** — 4 cases in `src/components/Layout.test.tsx` cover the collapsed-sidebar interaction (toggle, `localStorage` persistence with the `(tenantId, userId)` key shape, the responsive-event regression guard from F6). `npm run check` (lint + typecheck + 208/208 tests) clean on the final commit.

### Phase G — Documentation + roadmap reconciliation

- [done, this PR (Phase G commit on PR [#54](https://github.com/9owlsboston/TagPulse/pull/54))] **G1 — Ratified [ADR 021](adr/021-configurable-sensing-events.md)** Proposed → **Accepted**. v2.2 revision-history entry records the Sprint 41 closure (Phases A–E on PR #54; Phase F on TagPulse-UI PR [#46](https://github.com/9owlsboston/TagPulse-UI/pull/46) `ebb2f82`) and the v2.1 terminology rename (sensing → signaling). All v2.1 open questions resolved as leaned.
- [done, this PR] **G2 — Flipped remediation rows** [2.3](design/reference-design-remediation.md) (Configurable Signaling Events backend, PR #54 `e54332b` → `975ef6f`) + [2.9](design/reference-design-remediation.md) (Outbound event envelope, PR #54 `feae604`) + the [§3.2 1.1](design/reference-design-remediation.md) "Unify Telemetry/Models/Rules/Alerts" row (UI PR #46 `ebb2f82`) + [3.5](design/reference-design-remediation.md) ("Signaling Events modal", UI PR #46 `ebb2f82`) to **✅ Done** with the implementing merge SHAs, per the doc's own §7 "Updating this document" rule.
- [done, this PR] **G3 — User-guide section "Configurable Signaling Events"** shipped in [docs/user-guide.md](user-guide.md) (between Rules and Alerts). Walks through the 13-pair valid matrix, the four creation steps the modal exposes, four curl recipes (Location `on_change`, Temperature `periodic`, Geofencing `on_entry`, Geolocation `on_inactivity`) all POSTing to `/v1/tenants/{slug}/rules` with `"kind": "signaling"` body, the `?kind=` list filter, the per-scope cap + admin override (HTTP 409 / `?override=true` + audit log), and the per-rule integration routing semantics.
- [done, this PR] **G4 — CHANGELOG `## Unreleased`** entry covering Phase F UI work + Phase G doc reconciliation. (Phases A–E already had per-phase CHANGELOG entries on their implementing commits.)
- [done, this PR (Phase H commit on PR [#54](https://github.com/9owlsboston/TagPulse/pull/54))] **G5 — ADR 019 close-out.** Ships in lock-step with Phase H below. [ADR 019](adr/019-categories.md) Status → **Completed**; [docs/data-models.md](data-models.md) drops the `assets.asset_type` row + the deprecated-shadow note; [docs/design/assets-and-zones.md](design/assets-and-zones.md), [docs/design/tracking-modes.md](design/tracking-modes.md), [docs/design/geofencing-and-map.md](design/geofencing-and-map.md), and [docs/guides/domain-concepts-101.md](guides/domain-concepts-101.md) drop `asset_type` from schema snippets and example payloads.

### Phase H — Close ADR 019: drop `assets.asset_type`

ADR 019 (Sprint 34) introduced `category_id` as the structured replacement for the free-form `assets.asset_type` `VARCHAR(50) NOT NULL` shadow column, with the explicit plan: "Drops in a future migration once UI + clients have switched." Sprint 41 brought the UI side (Phase F7); this Phase H completes the backend half in the same PR. The new Signaling Events flow scopes by category, not type, so completing the deprecation keeps the API surface internally consistent. **Breaking API change** — bumps the Unreleased section with a migration note for external webhook consumers and any direct API callers.

- [done, this PR] **H1 — Backfill + NOT NULL migration.** [`migrations/versions/041_drop_assets_asset_type.py`](../migrations/versions/041_drop_assets_asset_type.py): (a) JOIN-backfills `assets.category_id` from `categories.name` keyed off the legacy `asset_type`; (b) safety-net seeds a per-tenant `_uncategorized` Category (`category_type='object'`, `required_tags=1`) for any residual NULL rows; (c) `ALTER TABLE assets ALTER COLUMN category_id SET NOT NULL`; (d) `DROP INDEX IF EXISTS ix_assets_tenant_type`; (e) `ALTER TABLE assets DROP COLUMN asset_type`. Idempotent against partial reruns via `information_schema` guards. Symmetric `downgrade()` re-adds the column nullable, backfills from `categories.name`, promotes to NOT NULL, and recreates the index.
- [done, this PR] **H2 — Pydantic schemas.** Removed `asset_type` from `AssetCreate` / `AssetUpdate` / `AssetResponse` / `ManifestEntry` / `ManifestResponse` / `AssetInZoneSummary` in [src/tagpulse/models/schemas.py](../src/tagpulse/models/schemas.py). `category_id` is now **required** on `AssetCreate` (was nullable during the ADR 019 compatibility window) and required on `AssetResponse`. `AssetUpdate.category_id` stays optional with the semantics "omit = no change" (null is rejected at the API layer).
- [done, this PR] **H3 — `AssetService` + repos.** Dropped the `asset_type` parameter from `AssetService.list_assets()` and the audit-log `changes` dict in [src/tagpulse/api/services/asset_service.py](../src/tagpulse/api/services/asset_service.py); removed the `AssetModel.asset_type` filter branch + `_asset_to_response` mapping in [src/tagpulse/repositories/timescaledb/assets.py](../src/tagpulse/repositories/timescaledb/assets.py) (including both arms of the `get_descendants` recursive CTE); removed the `a.asset_type` raw-SQL columns in [src/tagpulse/repositories/timescaledb/asset_location.py](../src/tagpulse/repositories/timescaledb/asset_location.py).
- [done, this PR] **H4 — API surface.** Removed `?asset_type=` query param from `GET /assets`. The route now returns **HTTP 400** with a migration hint when any client still sends `?asset_type=…` — drop the guard in Sprint 42. [openapi.json](../openapi.json) regenerated.
- [done, this PR] **H5 — Tests.** Updated 8 unit/integration test files (`test_asset_service.py`, `test_carrier_external.py`, `test_categories.py`, `test_asset_location.py`, `test_sprint21_caches.py`, `test_sprint19_subject_telemetry.py`, `test_label_filter.py`, `test_assets_repository_filters.py`) to drop `asset_type` from fixtures and supply `category_id` where the schema now requires it. Added [tests/unit/test_assets_route_asset_type_removed.py](../tests/unit/test_assets_route_asset_type_removed.py) covering the 400 regression — 9 test files touched in commit `140ea40` per `git diff --stat`. Migration round-trip is exercised by the existing [tests/integration/test_migration_round_trip.py](../tests/integration/test_migration_round_trip.py) harness (gated on `TAGPULSE_INTEGRATION_DB_URL`).
- [done, this PR] **H6 — EPC parser stays.** The `asset_type` *bits* parsed from EPC tag binary in [src/tagpulse/rfid/epc.py](../src/tagpulse/rfid/epc.py) are RFID-protocol semantics, not the DB column; that code is untouched per the original plan.

### Out of scope (deferred)

- **ADR 023 MQTT outbound dispatcher** — Sprint 42 per the Methodology-drift table above.
- **ADR 024 Indoor position estimation** — depends on this sprint's `processor` enum being live (Sprint 45 will add a third value `trilateration` per [ADR 024](adr/024-position-estimation.md)).
- **Backfilling `category_ids` / `*_label_filters` onto legacy rules** — the columns accept it but no UI flow exposes it. Out-of-scope until a customer asks.
- **Postgres ENUM migration for `condition_type`** — open question #1 in the ADR; lean keep VARCHAR + pattern.
- **Renaming the `asset_type` EPC parser variable** — the RFID-binary `asset_type` bits are a separate concept from the DB column; cleanup is a forward-only cosmetic and not gating.

---

## Sprint 46 — Edge wire format v2: backend ingest + presence model (shipped)

**Goal.** Land the server-side half of the v2 wire-format contract — Pydantic models, Alembic migration for `tag_presence`, MQTTSubscriber v2 branch, synchronous presence reconciler, two new event-bus topics, observability counters. Producer side (Pi-gateway reference impl, WM reader-direct) ships in Sprint 47.

**Specs / ADRs (all proposed, all on `docs/edge-wire-format-v2-draft` branch).**
- [docs/design/edge-wire-format-v2.md](design/edge-wire-format-v2.md) — wire-format and server-side model spec (KISS pass shipped commit `155f1e5`).
- [docs/adr/025-edge-wire-format-v2.md](adr/025-edge-wire-format-v2.md) — ratifies §3 wire contract.
- [docs/adr/026-presence-model.md](adr/026-presence-model.md) — ratifies §4 `tag_presence` + synchronous reconciler.

**Phases** (mirrors spec §10):
- **Phase A — Spec finalization** *(this PR — `docs/edge-wire-format-v2-draft` branch).* Resolve §8 open questions (DONE — all marked resolved 2026-05-23 under Shape C producer architecture). Land ADR 025 + ADR 026 (DONE — commit `58bbcc8`). Promote spec out of DRAFT. Merge branch to `main`.
- **Phase B — Schema.** [done, this PR] New Alembic migration `042_tag_presence.py` per ADR 026 §3.1 (regular table, no hypertable, no `last_seq`/`suspect` columns; PK `(tenant_id, device_id, epc)`; `status` CHECK in `('present','gone')`; two partial indexes `WHERE status='present'` covering `(tenant_id, device_id)` and `(tenant_id, epc)`; RLS via `app.current_tenant_id` GUC per the migration 007 / 027 pattern). Pydantic v2 discriminated union for v2 messages in new `src/tagpulse/ingestion/wm_wire_format.py` (`WmSnapMessage` / `WmAppearedMessage` / `WmDisappearedMessage` keyed on `t`, with `WmSnapEntry` for `epcs[]` rows; `extra="forbid"` rejects reserved field names and shape-violations like `epcs` on a `t=1`; field-level validators normalize EPCs to uppercase hex and reject explicit `null` on optional sensor fields per spec §6). 24 new unit tests in `tests/unit/test_wm_wire_format.py` cover every spec §6 rejection row plus the §3 happy-path examples.
- **Phase C — Subscriber.** [done, this PR] v2 detection branch in `_handle_tag_read` (recognize by integer `t` field per spec §1.4 / §9.1 #4) — dispatch hook routes to a new `_handle_wm_v2_message` method that validates against the Phase B `WmMessage` discriminated union via a module-level `TypeAdapter`, classifies `ValidationError` to a spec §6 reason label, and drops invalid payloads through the existing `_record_rejection` + `_persist_mqtt_drop` pair. On valid messages it opens a session, sets `app.current_tenant_id` inline so the `tag_presence` RLS policy admits the rows, dispatches to one of three new reconciler coroutines, and maps each WM message to one-or-more `TagReadCreate` instances via two new module-level helpers (`_wm_snap_to_tag_reads` / `_wm_appeared_to_tag_read`) that flow through the unchanged `IngestionService.ingest()` per spec §4.4 (`t=2` writes no `tag_reads` row per spec §4.3). `LocationSource` Literal extends to include `"reader_gnss"` (spec §4.4); `TagPresenceModel` ORM mirrors migration 042. New module `src/tagpulse/ingestion/presence_reconciler.py` (~290 LOC, three public coroutines `reconcile_snap` / `apply_appeared` / `apply_disappeared`) implements spec §4.2 verbatim — synchronous reconciliation on snap receipt with no window state, multi-antenna collapse picking the strongest RSSI per spec §9, idempotent transitions (present→present and gone→gone are no-ops; never-seen `t=2` is silently ignored per spec §6). Two new event-bus topics added to `src/tagpulse/events/protocol.py`: `Topic.SIGNALING_TAG_APPEARED`, `Topic.SIGNALING_TAG_DISAPPEARED` — both carry `{tenant_id, device_id, epc, observed_at, source: "delta"|"snap"}`. v1 path stays untouched (coexistence per spec §9.1 #4; the v2 detection predicate is narrow — string `t` and absent `t` still fall through to v1). 25 new unit tests: 14 in `tests/unit/test_presence_reconciler.py` covering every spec §5 scenario (first snap from empty / idempotent snap / missing-from-snap / empty snap / reader rejoin / multi-antenna collapse / event payload shape / all three `apply_appeared` state transitions / all three `apply_disappeared` cases / §5.7 lost-`t=2` healed by next snap), 11 in `tests/unit/test_mqtt_subscriber_v2_dispatch.py` covering the dispatch hook (integer-`t` routes to v2; string `t` and list payloads fall through), happy paths for all three variants (snap with full §4.4 mapping fidelity asserted on the resulting `TagReadCreate`), and rejection paths (`unknown_type` for `t=99`, `invalid_epc` for short non-hex EPC, plus direct unit coverage of `_classify_wm_validation_error`). `make check` clean: **1155 passed, 1 skipped** (+25 from Phase B). Full §4.5 `sn → device_id` resolution via `devices.metadata->>'serial'` + JWT cross-check deferred — Phase C trusts the topic-derived `device_id` matching the v1 pattern (TODO comment in handler points at spec §4.5).
- **Phase D — Tests.** [done, this PR] New `tests/unit/test_wm_v2_conformance.py` (12 tests, 9 classes) drives the *real* `MqttSubscriber._handle_tag_read` → `_handle_wm_v2_message` → `presence_reconciler` → event-bus path from MQTT payload bytes against a `_FakeSession` that scripts SELECT results in FIFO order and a `_FakeBus` that captures emitted topic/event tuples — only `IngestionService.ingest()` and `_persist_mqtt_drop` are stubbed (AsyncMocks for call-count assertions). One test class per spec §5 subsection: **§5.1** steady-state (zero messages → no writes, no events); **§5.2** mixed deltas (5×`t=1` + 3×`t=2` → 5 appeared + 3 disappeared + 5 ingest calls, since `t=2` writes no `tag_reads` row per spec §4.3); **§5.3** periodic snapshot reconciles against scripted current-present-set; **§5.4** empty snap marks all present gone; **§5.5** producer reboot snap-on-reconnect against empty present-set surfaces every entry as appeared (validates the no-wire-level-reboot-signal property per ADR-025 §3.6); **§5.6** subscriber replay split into two tests — replayed `t=1` for already-`present` EPC is a no-op upsert with no duplicate event but `tag_reads` still flows, replayed `t=2` for never-seen EPC silently dropped per spec §6 `tagpulse_mqtt_wm_sub_no_presence_total` semantics; **§5.7** lost-`t=2` healed by next snap (the composite scenario — missed `t=2` leaves EPC `present`, next snap omits it → reconciler marks gone and emits the missed event, validating the "self-heal at snap cadence" property that justifies the spec §3.6 "no reboot signal needed" decision). Plus two large-snap tests (1000-entry processes without error; 5001-entry exercises the spec §6 soft-cap path with a caplog assertion that "above soft cap" is logged but `_persist_mqtt_drop` is NOT awaited — soft cap is warning-only, Phase E will wire the `tagpulse_mqtt_wm_snap_large_total{sn}` counter on the same call site) and two v1/v2 coexistence tests (interleaved v1-then-v2 on same subscriber routes to the right handler with no cross-talk; v1 payload with a string-valued `t` field is NOT misclassified as v2 — the dispatch hook's narrow `isinstance(raw.get("t"), int)` guard correctly rejects strings, which matters because historical v1 publishers use `t` as a free-form metadata key). File lives in `tests/unit/` (not `tests/conformance/`) because the latter is a Sprint 16 stub-only directory waiting for a device-under-test harness against `edge-device-contract.md` §3 and the `Makefile`'s `test` target only walks `tests/unit`; "conformance" labels the file's spec-section organization, not a separate runner. `make check` clean: **1167 passed, 1 skipped** (+12 from Phase C's 1155).
- **Phase E — Observability.** [done, this PR] Seven new OTel metric objects wired at the v2 ingest + reconciliation call sites per spec §6. Counter definitions consolidated in a new `# -- Sprint 46 Phase E ... --` section at the top of [src/tagpulse/core/otel_metrics.py](../src/tagpulse/core/otel_metrics.py) (~96 LOC inserted before the Sprint 35 ADR-020 labels block). Subscriber-side: `tagpulse_mqtt_wm_rejections_total{reason}` bumps inside `_handle_wm_v2_message`'s `except ValidationError` alongside the existing `_record_rejection("wm_v2", reason)` + `_persist_mqtt_drop(...)` pair — labels cover the §6 reasons the Phase C handler can actually emit (`missing_type`, `unknown_type`, `invalid_epc`, `missing_required_field`, `epcs_wrong_type`, `invalid_snap_entry`, `explicit_null`, `invalid_json`, `invalid_schema`); the §6 reasons `device_not_found`/`sn_jwt_mismatch`/`clock_skew` are not emitted yet because they require the §4.5 `sn → device_id` lookup + JWT cross-check still deferred to a later sprint. `tagpulse_mqtt_wm_snap_large_total{sn}` bumps on the soft-cap path with `sn` as a string label per spec (cardinality bounded by device count). `tagpulse_presence_reconcile_duration_seconds` histogram labelled `t ∈ {snap, appeared, disappeared}` wraps each of the three reconciler dispatch branches via `time.perf_counter()` — measures reconciler work only, not the surrounding GUC set or `IngestionService.ingest()` fan-out. Reconciler-side: `tagpulse_mqtt_wm_sub_no_presence_total` bumps in `apply_disappeared` when `prior_status is None` (distinguishes "subscriber lost state across rolling deploy" from "producer never sent a snap"); `tagpulse_presence_entries_total{status}` with `status ∈ {present, gone}` bumps `+= len(snap_state)` after `reconcile_snap`'s upsert, `+= len(gone_epcs)` after the gone-marking UPDATE, `+= 1` after `apply_appeared`'s single upsert, and `+= 1` in `apply_disappeared`'s transition branch (gives operators a denominator for the duration histogram); `tagpulse_signaling_tag_appeared_total{source}` and `tagpulse_signaling_tag_disappeared_total{source}` with `source ∈ {snap, delta}` bump inside `presence_reconciler._emit()` via a topic-based branch on `Topic.SIGNALING_TAG_APPEARED` vs `Topic.SIGNALING_TAG_DISAPPEARED` — single chokepoint guarantees no caller can emit without a counter bump, and the `source` label lets dashboards prove the reader is sending real-time deltas rather than relying on snap-only reconciliation. Every `.add()` / `.record()` call site is wrapped in `try: ... except Exception: logger.exception(...)` mirroring the existing `_record_rejection` defensive pattern — instrumentation failures cannot stall the MQTT loop (Sprint 28 C1 critical-path guarantee). The histogram is the first one in `otel_metrics.py` but the OTel SDK API is identical to counters. New `tests/unit/test_wm_v2_metrics.py` (9 tests, 6 classes) installs an `InMemoryMetricReader`, rebinds every Phase E metric on the shared meter (and in `mqtt_subscriber` + `presence_reconciler` which captured originals at import), then drives `MqttSubscriber._handle_tag_read` per scenario and asserts the counter snapshot includes the expected `(name, labels) → value` tuple — invalid-EPC rejection, 5001-entry snap large + 1-entry snap no-bump, `t=2` for unknown EPC no-presence bump, mixed snap present/gone accumulation, signaling counters per source, histogram point recording per `t` label. `make check` clean: **1176 passed, 1 skipped** (+9 from Phase D's 1167).
- **Phase F — Docs.** [done, this PR] Two doc deliverables targeting the producer and operator audiences. [docs/guides/device-developer-guide.md](guides/device-developer-guide.md) gains a new **§3.4 "v2 wire format — presence-oriented (Sprint 46+)"** between the existing first-message walkthrough and the HTTP reference — positioned so a producer-side developer hits v1 first (older, still-supported) and then v2 with explicit "both supported indefinitely per spec §9.1 #4" language up front. Covers the `t ∈ {0, 1, 2}` mapping table with snap-cadence semantics (default 300 s OR 100 cycles AND on reconnect — without snaps the subscriber has no recovery primitive for a missed `t=2`), explicit callout that v2 ships on the SAME `tag-reads` topic as v1 with integer `t` as the discriminator, one minimal JSON example per message type, the MQTT settings table (QoS 1, retain false, `clean_session=false`, auth unchanged), a seven-item producer-responsibility list (EPC uppercase hex with `invalid_epc` rejection; `sn` semantics; `ts` epoch ms; the explicit-null rejection rule — omit unsupported sensor fields, never send `null` per §6 `explicit_null`; snap cadence rationale; multi-antenna entries allowed in same `epcs[]`; soft-cap behavior), pointer at `clients/pi/tagpulse_edge/` reference implementation, and a `tag_presence` SELECT template cross-linked to the operator runbook. New runbook [docs/runbooks/wm-wire-format-v2.md](runbooks/wm-wire-format-v2.md) (~165 lines) is the operator-side companion: §1 "What's at reader X right now?" — three SQL templates against `tag_presence` (currently-present with the required RLS GUC `set_config('app.current_tenant_id', ..., true)`; recently-gone; per-device present-EPC counts; EPC-history-across-devices) plus the explicit gotcha that `tag_presence` is **state** not history ("where was this EPC at 14:32" still needs `tag_reads`, and `t=2` writes no `tag_reads` row per §4.3); §2 Phase E counter cheat-sheet — row-per-counter table giving operators "what rising means" + "operator action" for all seven counters, plus the explicit rule that single `wm_sub_no_presence_total` bumps post-rolling-deploy are normal but sustained non-zero rate is the canonical signal that snap-on-reconnect isn't firing; §3 the spec §9.2 #1 "two subscriber replicas during ACA rolling deploy" gotcha — what operators may see (brief duplicate `entries_total` rows, two `_emit()` publishes that the event bus dedups for a short TTL, RSSI/antenna flap), explicit self-heal time bound ("never act unless inconsistency persists > 10 minutes" — next snap puts every row back to authoritative state at the 300 s default cadence) and an `az containerapp revision list` + `revision deactivate` diagnosis recipe; §4 the "tag stuck present" customer-support triage walkthrough (find the row, check producer health via most-recent `tag_reads`, check `mqtt_drops` for rejection counter spikes on the same device, and an emergency-only manual `UPDATE tag_presence SET status='gone'` template with explicit "file a follow-up" guard so manual healing doesn't become habit); §5 cross-link table to producer guide, spec, reconciler + dispatch code, and the `otel_metrics.py` Phase E section. CHANGELOG entry combined with Phase E above.

**Out-of-scope for Sprint 46:**
- Producer-side implementation (Pi-gateway translator, WM reader-direct firmware) — Sprint 47.
- `docs/design/reader-to-edge-contract.md` for the §8.4 Q-LAN-* questions — companion spec, WM-facing, separate review cycle.
- High-churn-reader event-bus mitigation (spec §9.2 #5) — accepted as risk for pilot scale; ADR-level mitigation decision required *before* a production deploy with sustained > 10 churn events/sec/reader.
- `tag_presence` growth policy (spec §9.2 #2) — accepted as backlog; ADR-026-deferred.

**Risks.**
- ACA rolling-revision deploys briefly run two subscriber replicas, breaking the single-replica invariant for ~30–60 s per deploy (spec §9.2 #1). Self-heal recovers within one snap cadence; flagged in operator runbook addendum.
- v2 producer is unspecified — the spec defines the wire, not the producer. Sprint 47 picks one or both of (Pi-gateway translator, WM reader-direct) without spec impact.

---

## Sprint 47 — Edge wire format v2: producer side (shipped — Pi-gateway producer + reader-to-edge contract)

**Goal.** Ship the Pi-gateway reference producer for v2, plus the LAN-side contract (`docs/design/reader-to-edge-contract.md` + ADR-027) that any third-party reader vendor — WM included — can target. The conformance harness validates the producer end-to-end against the Sprint 46 subscriber for all 7 spec §5 scenarios.

- **Phase A — Reader-to-edge LAN contract.** [done, this PR] New companion spec [docs/design/reader-to-edge-contract.md](design/reader-to-edge-contract.md) resolves spec v2 §8.4 Q-LAN-1..Q-LAN-7 (transport options, CSV schema, sensor-failure encoding, empty-cycle signalling, per-SKU capability descriptor, reset signalling, header `issi`→`rssi` correction). Ratified by [ADR-027](adr/027-reader-to-edge-contract.md) — Proposed status, promotes to Accepted after at least one vendor reviews. Pi owns the wall clock; reader emits monotonic boot counter only (resolves spec v2 §8 Q7).
- **Phase B — Pi-gateway producer + unit tests.** [done, this PR] New pure-logic producer at [clients/pi/tagpulse_edge/wm_v2_producer.py](../clients/pi/tagpulse_edge/wm_v2_producer.py) — `WmV2Producer` + `CycleEpcObservation`. Cycle-diff state keyed by `(antenna, EPC)`; per-EPC `t=2` (one departure message per EPC across all antennas) per spec §2.2. Supports Profile A (delta, default 300 s / 100 cycle snap cadence) and Profile B (snap every cycle, `snap_cycle_count=0`). Sensor field omission (`tmp`/`hum` keys absent when `None`) mirrors the subscriber's `_reject_explicit_null_*` enforcement. EPC validation and field range constants duplicated locally (NOT imported from the backend) so the Pi-gateway package stays self-contained for Pi-side packaging — any change MUST be matched in both places. 34 unit tests in [clients/pi/tests/test_wm_v2_producer.py](../clients/pi/tests/test_wm_v2_producer.py) cover snap triggers, cycle diff, sensor omission, EPC validation, antenna-move semantics, soft-cap warning, and reset/begin_session.
- **Phase C — End-to-end conformance harness.** [done, this PR] New [clients/pi/tests/test_wm_v2_producer_e2e.py](../clients/pi/tests/test_wm_v2_producer_e2e.py) drives the producer through scripted scenarios for all 7 §5 cases (steady-state, mixed deltas, periodic snap, empty snap, reboot, subscriber outage, lost sub) and round-trips every emitted dict through the backend's `WmMessage` discriminated-union parser via `TypeAdapter.validate_json` — i.e., asserts the producer cannot emit a message the subscriber would DLQ. Backend-side §5 coverage in `tests/unit/test_wm_v2_conformance.py` (Sprint 46 Phase D) remains the subscriber-side harness; together the two span the full conformance matrix.

**Out-of-scope for Sprint 47 (deferred):**
- WM reader-direct firmware speaking v2 directly to the cellular broker (skips the Pi entirely) — gated on WM dev capacity + vendor sign-off on the new `reader-to-edge-contract.md`. Until then, WM readers ride the Pi-gateway path #1 from this sprint.
- LAN-side parser (the CSV-over-TCP / file / serial layer that produces `CycleEpcObservation` records on the Pi) — wired in Sprint 48 when the first concrete reader integration lands. The producer module is reader-agnostic on purpose; only the LAN parser is vendor-shaped.
- Server → reader config push (spec §9.3 #1) — v2.1 of the spec.
- Heartbeat / reader-error message types `t=3` / `t=4` (spec §9.3 #2) — v2.1.
- Binary wire format v3 (spec §9.3 #3) — gated on measured bandwidth justifying the cost.

**Spec follow-ups (now closed):**
- v2 spec §8.4 (Q-LAN-1..Q-LAN-7) — resolved in this sprint's Phase A.
- v2 spec §8 Q7 (clock discipline) — resolved by ADR-027 §3: Pi owns wall clock.
- v2 spec §11 review checklist line "`docs/design/reader-to-edge-contract.md` drafted — Sprint 47 companion" — checked off in this sprint.

---

## Backlog (not scheduled)
- **Rule taxonomy unification (post-Sprint 41 cleanup).** Collapse the dual-shape `rules` table (10 pre-existing `condition_type` values + 12 new `signaling.<event_type>.<trigger>` values added in Sprint 41) into a single Azure-Monitor-aligned `(signal_type, condition, action_group)` taxonomy. Concrete deliverables: rename `rules` → `alert_rules`; rename / subsume `condition_type` into a flat `signal_type` enum that covers both the signaling event types (`location_change`, `geolocation_change`, `temperature`, `geofence_transition`, `inactivity`) and the non-event signals the pre-existing rules cover (`generic_threshold`, `rate_change`, `tag_read_rate`, `stock_threshold`, `stock_movement`); retire the "legacy rule" framing in docs / Pydantic / UI sub-tabs; unify the "Signaling Events" and "Legacy rules" UI surfaces into one "Alert Rules" page; update [docs/data-models.md](data-models.md), the rule-schema docstrings, ADR-021 revision history, and the CHANGELOG. Pre-req: Sprint 41 fully shipped so real-world usage informs the new taxonomy and there is no in-flight feature churn around `rules`. New ADR will supersede the relevant portions of ADR-021 v2 (additive signaling-event columns) and ADR-006 (the original rules engine).
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
- **PG16 + TimescaleDB re-evaluation.** PG15 was pinned (`deploy/azure/bicep/modules/postgres.bicep`) because Azure Database for PostgreSQL Flexible Server removed the open-source TimescaleDB extension on PG16 — `CREATE EXTENSION timescaledb` terminates the asyncpg connection mid-statement. PG15 community support runs through **Nov 2027**. Action: re-test PG16 + TimescaleDB on a throwaway Flex server **annually** (next: May 2027) and again any time Microsoft / Timescale announce extension changes. When unblocked: write a migration runbook (PITR-fork → `pg_upgrade` or logical replication → cutover) and bump `postgresVersion` default. Until then, do not let PG15 run past Q2 2027 without a confirmed escape plan.
