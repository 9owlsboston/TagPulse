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
- [planned] docs/runbooks/ — operational runbook documents

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

- [planned] `db_session_var: ContextVar[AsyncSession]` in `tagpulse.core.context`; `tenant_context()` async helper for non-request code (background jobs, scripts); refactor existing `get_session()` dependency to populate the contextvar. Per [storage-strategy.md §6 Q2](design/storage-strategy.md).
- [planned] `PoolRegistry` built once at startup from `config/database.yaml`; v1 ships with single `shared_default` entry.
- [planned] `tenants.db_pool_key VARCHAR(64) NOT NULL DEFAULT 'shared_default'` column + Alembic migration; middleware reads it per request, fetches a session from the matching pool, sets `app.current_tenant_id` for shared-pool tenants (RLS).
- [planned] `AdminRepository` in `src/tagpulse/repositories/admin.py` for cross-tenant operations gated by admin role at the route layer (will be the home for the `GET /admin/tag-collisions` endpoint added in Sprint 15).
- [planned] `MetricsRepository` abstraction (deterministic; both backends first-class) in `src/tagpulse/repositories/metrics.py`. Selected once at startup from `DATABASE_BACKEND` config (`timescale` \| `postgres`). **Timescale impl** uses continuous aggregates (`tag_reads_hourly_by_reader`, `alerts_daily_by_tenant`); **PG impl** uses materialized views refreshed by `pg_cron` (or app-side scheduler). Scope intentionally tight — only time-bucketed aggregation queries. Per [storage-strategy.md §6 Q1](design/storage-strategy.md).
- [planned] CI integration tests for both `MetricsRepository` backends; review rule: any new method requires both implementations in the same PR.
- [planned] Document PG-mode scaling ceiling in [storage-strategy.md](design/storage-strategy.md) §6 once benchmarked (expected ~1–2k devices/tenant).

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

- [planned] `assets` table + CRUD API `/assets` (tenant-scoped, RLS)
- [planned] `assets.parent_asset_id` for carrier containment per [mobile-carriers-and-manifests.md](design/mobile-carriers-and-manifests.md)
- [partial] `devices.mobility` flag (`fixed` \| `mobile`); ingestion skips fixed-zone lookup for mobile readers (migration 020a) — column + check constraint shipped in migration 017; ingestion branching wires up alongside asset bindings in Phase B
- [planned] `asset_tag_bindings` table — historical tag-to-asset mappings; column **named `binding_value` from day one** (no deprecation dance — the table is new in this sprint); `binding_kind` ∈ {`epc`,`tid`,`device`} per [rfid-tag-data-model.md](design/rfid-tag-data-model.md) and [mobile-carriers-and-manifests.md](design/mobile-carriers-and-manifests.md)
- [planned] **Tag-collision admin tooling** — non-unique global index on `asset_tag_bindings(binding_value) WHERE unbound_at IS NULL`; admin-only `GET /admin/tag-collisions?binding_value=…` (returns count of other tenants with an active binding, never tenant identities); bulk-import preflight check ("X of N bindings collide with another tenant"); OTel counter `tag_collisions_global_total`. Lives on `AdminRepository` (Sprint 13b). Per [assets-and-zones.md §11 Q3](design/assets-and-zones.md).
- [planned] **`external_locations` hypertable** (migration 021) — `(tenant_id, asset_id, latitude, longitude, recorded_at, source, accuracy_meters?, speed_kph?, heading_deg?, metadata)`; RLS by `tenant_id`; compression/retention parity with `device_telemetry`. Per [mobile-carriers-and-manifests.md §10 Q5](design/mobile-carriers-and-manifests.md).
- [planned] **`POST /assets/{asset_id}/external-position`** endpoint (editor+, tenant rate-limited, default 60/min) — generic ingestion for non-RFID carriers; TMS-specific adapters land in backlog.
- [planned] `POST /assets/{id}/load`, `POST /assets/{id}/unload`, `GET /assets/{id}/manifest` for carrier semantics
- [planned] `asset_current_location` SQL view — latest tag read per active binding, **UNION** with the latest `external_locations` row; new `latest_position_source` column lets the UI render "via Samsara" vs "via Reader-12"
- [done] `sites` table — physical locations (name, address, default_timezone) — **shared substrate, used by both modes**
- [done] `zones` table — reader-bound (polygon nullable, deferred to Sprint 17) — **shared substrate**
- [done] `/sites` and `/zones` CRUD APIs
- [planned] Ingestion emits `subject.zone_changed` event (with `subject_kind='asset'`) when reader transition crosses zone boundary
- [planned] Repository: `get_assets_in_zone()`, `get_asset_path()`
- [planned] Simulator: bind tag IDs to named assets; cross zones over time
- [planned] **UI:** Assets page — list, search by external_ref/tag, detail with current location/zone/binding history
- [planned] **UI:** Sites & Zones page (admin/editor) — site list + zone editor with reader picker
- [planned] **UI:** Asset detail — recent path timeline (reader hops), with **merged-source timeline badged by source** (RFID-derived vs external/TMS-derived) per [mobile-carriers-and-manifests.md §10 Q5](design/mobile-carriers-and-manifests.md)
- [planned] **UI:** Device detail — "Covers zones: …" panel
- [planned] **UI:** Sidebar — Assets + Sites entries with role guards (visible when `tenants.tracking_modes` includes `asset`)

## Sprint 15b — Inventory Tracking (sibling to Sprint 15)

> Design: [docs/design/tracking-modes.md](design/tracking-modes.md)
> Goal: serve the **inventory-tracking** domain (count of stock per SKU/lot/zone, expiration, in/out movements). Sits on the same substrate as Sprint 15; runs in parallel and can ship in either order.

- [planned] `tenants.tracking_modes` JSONB column — `['asset']` default; `'inventory'` opt-in
- [planned] `products` table + CRUD API `/products` (SKU, GTIN, name, category, unit, attributes)
- [planned] `lots` table + nested API `/products/{id}/lots` (lot_code, manufactured_at, expires_at)
- [planned] `stock_items` table — per-tag inventory unit; auto-created by ingestion when SGTIN read matches a registered product. Column **named `binding_value` from day one** (no deprecation dance — table is new in this sprint).
- [planned] `stock_items.parent_stock_item_id` — case/pallet containment promoted from backlog per [mobile-carriers-and-manifests.md](design/mobile-carriers-and-manifests.md) §4.3
- [planned] **`tag_data_mappings` table** (migration 020b) — per-(tenant, device_type, product) mapping from `tag_data` keys to semantic fields (lot, expiry, batch, mfg date, serial); most-specific scope wins. Replaces the speculative "overload `telemetry_models`" path. Ingestion's lot/expiry inference reads from this table. Per [tracking-modes.md §11 Q2](design/tracking-modes.md).
- [planned] `stock_movements` hypertable — append-only ledger (enter/exit/transfer/consume)
- [planned] `stock_levels` SQL view — live count per (product, lot, zone)
- [planned] Ingestion inventory branch — SKU lookup by GTIN (LRU cached), lot inference from `tag_data`, emit `subject.zone_changed` with `subject_kind='stock_item'` and `stock.movement_recorded`
- [planned] APIs: `/stock-items`, `/stock-levels`, `/stock-movements` (filter by product/lot/zone/state/time)
- [planned] Rules engine: `stock.below_threshold`, `stock.expiring_within`, `stock.unexpected_in_zone`
- [planned] Periodic workers — below-threshold scan (60 s), expiring-soon scan (daily)
- [planned] CSV import endpoints for products / lots / stock_items (bulk onboarding)
- [planned] Simulator: inventory profile — register sample products, emit SGTIN tag streams across zones, simulate consume / expire
- [planned] Metering: new dimensions `inventory_movements`, `stock_items_active`
- [planned] **UI:** Products page — catalog list, SKU detail with stock-by-zone bar chart
- [planned] **UI:** Lots sub-page — expiry queue, lot detail
- [planned] **UI:** Stock Levels page — pivot grid (product × zone), CSV export
- [planned] **UI:** Stock Movements page — chronological ledger filter (product / zone / time)
- [planned] **UI:** Rule wizard — inventory condition step
- [planned] **UI:** Sidebar — Products / Stock Levels / Stock Movements entries (visible when `tenants.tracking_modes` includes `inventory`)
- [planned] **UI:** Tenant settings page — admin toggle for `tracking_modes`; **"Sensor metrics"** sub-tab (declared telemetry keys mirrored to `device_telemetry`); **"Tag data fields"** sub-tab (editor for `tag_data_mappings`). Per [admin-ui.md §10](design/admin-ui.md).

## Sprint 16 — Edge Contract & Identity Hardening

> Design: [docs/design/edge-device-contract.md](design/edge-device-contract.md), ADR-011
> Goal: codify the wire contract `clients/pi/` enforces; tighten device identity before fleets get bigger.

- [planned] `docs/design/edge-device-contract.md` — dedup, ENTER/EXIT, batching, clock rules, heartbeat
- [planned] ADR-011 — device identity roadmap (token rotation → mTLS → TPM)
- [planned] Backend ingestion middleware: reject events older than 24h or >5min in future; metering dimension `events_rejected_clock`
- [planned] `POST /device-registry/{id}/rotate-token` (admin only) — revoke previous token, audit log entry
- [planned] Provisioning metering: `device_token_rotations` dimension
- [planned] Edge client doc — README in `clients/pi/` linked to contract spec
- [planned] **UI:** Device detail "Security" panel — token last-rotated, rotate button (admin), copy-once token reveal modal
- [planned] **UI:** Device detail "Heartbeat" panel — uptime, queue depth, firmware, connection state
- [planned] **UI:** Audit log — "device security events" filter preset

## Sprint 17a — Geofencing & Map UI

> Design: [docs/design/geofencing-and-map.md](design/geofencing-and-map.md), [docs/design/tracking-modes.md](design/tracking-modes.md), [docs/design/mobile-carriers-and-manifests.md](design/mobile-carriers-and-manifests.md)
> Goal: polygon zones + map-based situational awareness in the admin UI — supporting both asset markers, inventory stock-density layers, and moving carriers (trucks/forklifts) with click-through manifests.

- [planned] Store polygon as GeoJSON on `zones.polygon_geojson`
- [planned] Spatial query: bounding-box prefilter + Python point-in-polygon (no PostGIS)
- [planned] **OTel instrumentation for PostGIS-trigger threshold** — histogram `geofence.evaluation.duration` (unit `s`, attribute `tenant_id`); histogram `geofence.candidates_per_evaluation` (unit count, measures bbox prefilter selectivity); `tracer.start_as_current_span("geofence.evaluate")` wrap for trace-level debugging. Surfaces via existing Prometheus exporter per [observability.md §2](design/observability.md). Prometheus alert rule (1h sustained: p99 > 10ms OR p95 candidates > 50) opens ADR-013 (PostGIS adoption). Runbook entry in `docs/runbooks/`. Per [geofencing-and-map.md §11 Q5](design/geofencing-and-map.md).
- [planned] **`MapConfigResolver` abstraction** — `tenants.tile_provider JSONB NULL` column (NULL = system default = OSM public for POC); `GET /tenants/me/map-config` endpoint returning `{tile_url_template, attribution, max_zoom, subdomains?}`; service in `src/tagpulse/services/map_config.py` with one builder per `kind` (`osm`, `mapbox`, `maptiler`, `self_hosted`). Switching providers later is a settings change, not a code change. Per [geofencing-and-map.md §11 Q4](design/geofencing-and-map.md).
- [planned] Rules engine: `zone.entered`, `zone.exited`, `zone.dwell_exceeded` condition types with `subject_kind` filter (`asset` \| `stock_item` \| `device`)
- [planned] Ingestion: emit `subject.zone_changed` for geofence transitions in addition to reader-bound — includes `subject_kind='device'` for mobile-reader transitions per [mobile-carriers-and-manifests.md](design/mobile-carriers-and-manifests.md) §6
- [planned] **UI:** Map page — carriers render as moving icons with click-through manifest pop-out
- [planned] Simulator: emit synthetic GPS tracks across geofence polygons
- [planned] **UI:** Map page (Leaflet + react-leaflet, **provider-agnostic tiles** via `MapConfigResolver`) — live asset markers + stock-density heat tiles, layer toggle, zone polygon overlay, time-slider path replay. UI footer always renders the resolver's `attribution` string; OSM-default footer note: "Default tiles intended for development; configure a production provider before public deployment."
- [planned] **UI:** Zone editor — polygon-draw mode (leaflet-draw)
- [planned] **UI:** Rule wizard — geofence step

## Sprint 17b — mTLS for MQTT (A6 Phase 2)

> Design: ADR-012 (TBD when sprint starts)
> Goal: production-grade per-device cryptographic identity.

- [planned] ADR-012 — mTLS for MQTT, broker selection (Mosquitto vs EMQX)
- [planned] `devices.cert_thumbprint` column + provisioning issues per-device cert
- [planned] Broker config update; backward-compat path for API-key devices
- [planned] **UI:** Device detail — cert upload/rotate flow

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
