# TagPulse Roadmap

---

## Sprint 1 — Core Ingestion Pipeline

- [done] MQTT subscriber — connect to broker, subscribe to device topics
- [done] HTTP ingestion endpoint — REST push path for devices that can't speak MQTT
- [done] Tag read message schema — Pydantic model (tag ID, reader ID, timestamp, signal strength, optional sensor payload)
- [done] TimescaleDB schema — hypertable for tag reads, table for device registry
- [done] Message validation + persistence — parse incoming messages, write to TimescaleDB
- [done] Database migrations — Alembic setup with initial schema

## Sprint 2 — Device Registry & Configuration

- [done] CRUD API for readers — register, list, get, update, decommission readers
- [done] Device configuration profiles — per-device settings, connection credentials, metadata
- [done] Device status tracking — last seen, connection state, firmware version
- [done] Telemetry model definitions — per-device-type schema (what metrics a device type reports, units, expected ranges)
- [done] MQTT topic convention — `devices/{device_id}/tag-reads`, `devices/{device_id}/status`

## Sprint 3 — Query & Telemetry Monitoring API

- [done] Tag read query endpoint — filter by reader, tag, time range, with pagination
- [done] Basic aggregations — reads per reader per hour, unique tags per time window
- [done] Live telemetry API — recent reads per device, current device status summary
- [done] Device health API — connectivity status, last-seen, error rates

## Sprint 4 — Dev & Ops Baseline

- [done] Docker Compose — app + TimescaleDB + Mosquitto for local dev
- [done] CI pipeline — GitHub Actions with lint + typecheck + test
- [done] CONTRIBUTING.md + CHANGELOG.md
- [done] Structured logging — JSON formatter, request ID correlation

## Sprint 5 — Multi-Tenancy & Usage Metering

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

- [done] Rule configuration API — CRUD for user-defined rules (conditions, actions, schedules)
- [done] Rule evaluation engine — evaluate conditions against incoming telemetry stream
- [done] Built-in conditions — threshold breach, absence detection ("tag not seen for N min"), rate change
- [done] Alert routing — deliver alerts via webhook, email, or internal notification queue
- [done] Alert history — log of triggered alerts with context (which rule, which device, what data)
- [done] Rules metering — record `rule_evaluations` and `alerts_fired` dimensions per tenant

## Sprint 7 — Analytics Module Framework

- [done] Plugin interface — base class for analytics modules, registration, lifecycle
- [done] First module: read frequency analytics — reads/min per reader, anomaly flagging
- [done] Background task runner — async worker for analytics that don't block ingestion

## Sprint 8 — Integration & Export Layer

- [done] Outbound webhooks — push events to external systems on configurable triggers
- [done] Event streaming endpoint — SSE or WebSocket feed for real-time consumers
- [planned] Scheduled data exports — periodic CSV/JSON export to object storage or email
- [done] External API — documented REST API for third-party system integration
- [done] Integration configuration API — CRUD for webhook/export targets
- [done] Integration metering — record `webhook_deliveries`, `sse_connections`, `export_volume` per tenant

## Sprint 9 — Admin UI

- [done] Technology decision — React 19 + TypeScript + Vite SPA in separate repo (see ADR-007, design/admin-ui.md)
- [done] Overview dashboard — KPI tiles (devices, reads, alerts, anomalies) with auto-refresh
- [done] Device management views — register, configure, monitor device fleet
- [done] Telemetry dashboard — live and historical telemetry visualization per device/group
- [done] Data Explorer — form-based ad-hoc query builder for tag reads
- [done] Telemetry model management — list, create, delete per-device-type metric schemas
- [done] Rule & alert management — create/edit rules, view alert history
- [done] Integration management — configure webhooks, exports, view delivery status
- [done] Usage & billing dashboard — per-tenant usage charts, quota status, billing export

## Sprint 10 — Production Hardening

- [done] Health checks — deep health (DB connectivity, MQTT broker status)
- [done] Graceful shutdown — drain EventBus queues, flush pending writes
- [done] Retry + dead letter — dead_letter_events table, admin API for retry/abandon
- [done] Audit logging — audit_logs table with tenant-scoped query API
- [done] Dockerfile + deployment config — HEALTHCHECK, multi-worker, labels
- [planned] docs/runbooks/ — operational runbook documents

## Sprint 11 — Observability

- [planned] Platform metrics — ingestion rate, message latency, DB write throughput
- [planned] Device telemetry metrics — per-device data freshness, error rates
- [planned] Rule engine metrics — evaluations/sec, alert trigger rate
- [planned] Alerting rules — ingestion stall, reader offline, DB lag

## Sprint 12 — Identity & Device Provisioning

- [planned] User & role management — users table, admin/editor/viewer roles, API key auth (G1, G2)
- [planned] API key generation — hashed keys, per-user, revocable
- [planned] Device self-registration — provisioning endpoint with pre-shared key auth (G4)
- [planned] Device approval flow — admin approves pending devices

---

## Backlog (not scheduled)

- Cloud-to-device commands (reader configuration push via MQTT) (G8)
- Bulk device operations / jobs (G9)
- Database-per-tenant for data residency (ADR-008 Tier 2)
- Edge gateway with store-and-forward (G10)
- Scheduled data exports with croniter (G12)
- Data export transformations (G11)
- Customizable drag-and-drop dashboards (react-grid-layout, Sprint 9+) (G13)
- Device type-specific UI views (G14)
- Second device type support (beyond RFID readers)
- Mobile app for field technicians
- MQTT connection metering via broker plugin/proxy
