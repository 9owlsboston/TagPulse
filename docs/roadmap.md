# TagPulse Roadmap

---

## Sprint 1 — Core Ingestion Pipeline

- [planned] MQTT subscriber — connect to broker, subscribe to device topics
- [planned] HTTP ingestion endpoint — REST push path for devices that can't speak MQTT
- [planned] Tag read message schema — Pydantic model (tag ID, reader ID, timestamp, signal strength, optional sensor payload)
- [planned] TimescaleDB schema — hypertable for tag reads, table for device registry
- [planned] Message validation + persistence — parse incoming messages, write to TimescaleDB
- [planned] Database migrations — Alembic setup with initial schema

## Sprint 2 — Device Registry & Configuration

- [planned] CRUD API for readers — register, list, get, update, decommission readers
- [planned] Device configuration profiles — per-device settings, connection credentials, metadata
- [planned] Device status tracking — last seen, connection state, firmware version
- [planned] Telemetry model definitions — per-device-type schema (what metrics a device type reports, units, expected ranges)
- [planned] MQTT topic convention — `devices/{device_id}/tag-reads`, `devices/{device_id}/status`

## Sprint 3 — Query & Telemetry Monitoring API

- [planned] Tag read query endpoint — filter by reader, tag, time range, with pagination
- [planned] Basic aggregations — reads per reader per hour, unique tags per time window
- [planned] Live telemetry API — recent reads per device, current device status summary
- [planned] Device health API — connectivity status, last-seen, error rates

## Sprint 4 — Dev & Ops Baseline

- [planned] Docker Compose — app + TimescaleDB + Mosquitto for local dev
- [planned] CI pipeline — GitHub Actions with lint + typecheck + test
- [planned] CONTRIBUTING.md + CHANGELOG.md
- [planned] Structured logging — JSON formatter, request ID correlation

## Sprint 5 — Rules & Alerts Engine

- [planned] Rule configuration API — CRUD for user-defined rules (conditions, actions, schedules)
- [planned] Rule evaluation engine — evaluate conditions against incoming telemetry stream
- [planned] Built-in conditions — threshold breach, absence detection ("tag not seen for N min"), rate change
- [planned] Alert routing — deliver alerts via webhook, email, or internal notification queue
- [planned] Alert history — log of triggered alerts with context (which rule, which device, what data)

## Sprint 6 — Analytics Module Framework

- [planned] Plugin interface — base class for analytics modules, registration, lifecycle
- [planned] First module: read frequency analytics — reads/min per reader, anomaly flagging
- [planned] Background task runner — async worker for analytics that don't block ingestion

## Sprint 7 — Integration & Export Layer

- [planned] Outbound webhooks — push events to external systems on configurable triggers
- [planned] Event streaming endpoint — SSE or WebSocket feed for real-time consumers
- [planned] Scheduled data exports — periodic CSV/JSON export to object storage or email
- [planned] External API — documented REST API for third-party system integration
- [planned] Integration configuration API — CRUD for webhook/export targets

## Sprint 8 — Admin UI

- [planned] Technology decision — framework selection (see ADR-007)
- [planned] Device management views — register, configure, monitor device fleet
- [planned] Telemetry dashboard — live and historical telemetry visualization per device/group
- [planned] Rule & alert management — create/edit rules, view alert history
- [planned] Integration management — configure webhooks, exports, view delivery status

## Sprint 9 — Production Hardening

- [planned] Health checks — deep health (DB connectivity, MQTT broker status)
- [planned] Graceful shutdown — drain MQTT connections, flush pending writes
- [planned] Retry + dead letter — failed message handling with configurable retry
- [planned] Dockerfile + deployment config
- [planned] docs/architecture.md + docs/runbooks/

## Sprint 10 — Observability

- [planned] Platform metrics — ingestion rate, message latency, DB write throughput
- [planned] Device telemetry metrics — per-device data freshness, error rates
- [planned] Rule engine metrics — evaluations/sec, alert trigger rate
- [planned] Alerting rules — ingestion stall, reader offline, DB lag

---

## Backlog (not scheduled)

- Cloud-to-device commands (reader configuration push via MQTT)
- Device self-registration / provisioning flow
- Multi-tenant support
- Edge gateway with store-and-forward
- Second device type support (beyond RFID readers)
- Mobile app for field technicians
- Audit logging for configuration changes
