# TagPulse Roadmap

Current cycle: **Q2 2026 (Apr–Jun)** | Next cycle: **Q3 2026 (Jul–Sep)**

---

## Q2 2026 — Foundation

### Milestone 1: Core Ingestion Pipeline
- [planned] MQTT subscriber — connect to broker, subscribe to device topics
- [planned] Tag read message schema — Pydantic model (tag ID, reader ID, timestamp, signal strength, optional sensor payload)
- [planned] TimescaleDB schema — hypertable for tag reads, table for device registry
- [planned] Message validation + persistence — parse MQTT messages, write to TimescaleDB
- [planned] Database migrations — Alembic setup with initial schema

### Milestone 2: Device Registry
- [planned] CRUD API for readers — register, list, get, update, decommission readers
- [planned] Device status tracking — last seen, connection state, firmware version
- [planned] MQTT topic convention — `devices/{device_id}/tag-reads`, `devices/{device_id}/status`

### Milestone 3: Query API
- [planned] Tag read query endpoint — filter by reader, tag, time range, with pagination
- [planned] Basic aggregations — reads per reader per hour, unique tags per time window

### Milestone 4: Dev & Ops Baseline
- [planned] Docker Compose — app + TimescaleDB + Mosquitto for local dev
- [planned] CI pipeline — GitHub Actions with lint + typecheck + test
- [planned] CONTRIBUTING.md + CHANGELOG.md (Phase 1 trigger)
- [planned] Structured logging — JSON formatter, request ID correlation

---

## Q3 2026 — Analytics & Production Readiness

### Milestone 5: Analytics Module Framework
- [planned] Plugin interface — base class for analytics modules, registration, lifecycle
- [planned] First module: read frequency analytics — reads/min per reader, anomaly flagging
- [planned] Background task runner — async worker for analytics that don't block ingestion

### Milestone 6: Production Hardening
- [planned] Health checks — deep health (DB connectivity, MQTT broker status)
- [planned] Graceful shutdown — drain MQTT connections, flush pending writes
- [planned] Retry + dead letter — failed message handling with configurable retry
- [planned] Dockerfile + deployment config
- [planned] docs/architecture.md + docs/runbooks/

### Milestone 7: Observability
- [planned] Metrics — ingestion rate, message latency, DB write throughput
- [planned] Alerting rules — ingestion stall, reader offline, DB lag

---

## Backlog (not scheduled)

- Web dashboard for real-time tag read visualization
- Cloud-to-device commands (reader configuration push)
- Multi-tenant support
- Batch import/export (CSV, JSON)
- Second device type support (beyond RFID readers)
- Edge gateway with store-and-forward
