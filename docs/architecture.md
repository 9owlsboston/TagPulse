# TagPulse Architecture

## System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│  TagPulse Platform                                               │
│                                                                  │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐   │
│  │ Ingestion       │  │ Service         │  │ Rules & Alerts  │   │
│  │ Layer           │─▶│ Layer           │─▶│ Engine          │   │
│  │                 │  │                 │  │                 │   │
│  │ • MQTT Sub      │  │ • Device Reg    │  │ • Conditions    │   │
│  │ • HTTP Push     │  │ • Query         │  │ • Evaluation    │   │
│  │                 │  │ • Telemetry     │  │ • Alert Route   │   │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘   │
│           │                    │                    │            │
│           ▼                    │                    ▼            │
│  ┌─────────────────┐                       ┌─────────────────┐   │
│  │ TimescaleDB     │◀──────────────────────│ Integration     │   │
│  │                 │                       │ Layer           │   │
│  │ • tag_reads     │                       │                 │   │
│  │   (hyper)       │                       │ • Webhooks out  │   │
│  │ • devices       │                       │ • SSE stream    │   │
│  │ • rules         │                       │ • Exports       │   │
│  │ • alerts        │                       │                 │   │
│  │ • integr.       │                       │                 │   │
│  └─────────────────┘                       └────────┬────────┘   │
│                                                     │            │
│                                                     ▼            │
│                                             External Systems     │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │ Analytics Modules (plugin architecture)                      ││
│  │ • Read frequency  • Anomaly detection  • ...                 ││
│  └──────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────┘
```

## Components

### Ingestion Layer
Accepts device telemetry via two protocols:
- **MQTT subscriber** — connects to external broker (EMQX/Mosquitto), subscribes to `devices/{device_id}/tag-reads` and `devices/{device_id}/status` topics
- **HTTP push endpoint** — `POST /tag-reads` for devices that can't speak MQTT

Both paths validate messages against Pydantic schemas and write to TimescaleDB. See [ADR-002](adr/002-mqtt-device-connectivity.md).

### Service Layer
FastAPI REST API providing:
- **Device registry** — CRUD for reader registration, configuration profiles, status tracking
- **Query API** — tag read queries with filters (reader, tag, time range), pagination, aggregations
- **Telemetry monitoring** — live device status, recent reads, device health

All business logic lives in service functions, not route handlers (per `copilot-instructions.md`).

### Rules & Alerts Engine
Evaluates user-defined rules against incoming telemetry:
- **Conditions** — threshold breach, absence detection, rate change
- **Actions** — webhook call, email, internal notification queue
- **Scope** — per-device, per-group, or global

Runs in an async background worker to avoid contending with ingestion. See [ADR-005](adr/005-embedded-rules-engine.md).

### Integration Layer
Pushes data and events to external systems:
- **Outbound webhooks** — triggered by configurable events (alerts, device status changes)
- **SSE streaming** — real-time feed for external consumers
- **Scheduled exports** — periodic CSV/JSON to object storage or email

All targets configured via CRUD API. See [ADR-006](adr/006-webhook-integration-layer.md).

### Analytics Modules
Pluggable Python packages following a plugin pattern:
- Base class with registration and lifecycle hooks
- First module: read frequency analytics (reads/min, anomaly flagging)
- Runs in background workers, shares DB connection pool

See [ADR-004](adr/004-monolith-plugin-analytics.md).

### Storage (TimescaleDB)
Single database engine for both time-series and relational data:

| Table | Type | Purpose |
|-------|------|---------|
| `tag_reads` | Hypertable | Time-series tag read events (auto-partitioned, compressed) |
| `devices` | Regular | Device registry and configuration |
| `rules` | Regular | User-defined rule definitions |
| `alerts` | Hypertable | Alert history (time-series) |
| `integrations` | Regular | Webhook/export target configuration |

See [ADR-003](adr/003-timescaledb-storage.md).

### Admin UI (planned)
Web interface for device management, telemetry dashboards, rule/alert configuration, and integration management. Technology decision deferred. See [ADR-007](adr/007-admin-ui-technology.md).

## Data Flow

```
1. Device sends tag read
   ├── MQTT: publish to devices/{id}/tag-reads
   └── HTTP: POST /tag-reads

2. Ingestion validates message (Pydantic schema)

3. Valid message written to tag_reads hypertable

4. Rules engine evaluates against active rules
   └── Match? → Create alert → Route to action (webhook/email)

5. Analytics modules process in background
   └── Compute aggregates, detect anomalies

6. Integration layer pushes to external systems
   ├── Webhooks fire on configured triggers
   ├── SSE streams live events to connected consumers
   └── Scheduled exports run on cron
```

## Key Decisions

| Decision | Reference |
|----------|-----------|
| Python + FastAPI backend | [ADR-001](adr/001-python-fastapi-backend.md) |
| MQTT for device connectivity | [ADR-002](adr/002-mqtt-device-connectivity.md) |
| TimescaleDB for storage | [ADR-003](adr/003-timescaledb-storage.md) |
| Monolith-first with plugin analytics | [ADR-004](adr/004-monolith-plugin-analytics.md) |
| Embedded rules engine | [ADR-005](adr/005-embedded-rules-engine.md) |
| Webhook-first integration | [ADR-006](adr/006-webhook-integration-layer.md) |
| Admin UI technology | [ADR-007](adr/007-admin-ui-technology.md) (proposed) |

## External Dependencies

| Dependency | Role | Required? |
|-----------|------|-----------|
| MQTT Broker (EMQX / Mosquitto) | Device message transport | Yes |
| TimescaleDB | Data storage | Yes |
| SMTP server | Email alert delivery | Optional |

## Project Structure

```
src/tagpulse/
  api/            # FastAPI routes (thin handlers → service layer)
  ingestion/      # MQTT subscriber + HTTP push endpoint
  models/         # SQLAlchemy models + Pydantic schemas
  rules/          # Rule engine, conditions, alert routing
  analytics/      # Plugin analytics modules
  integrations/   # Webhooks, SSE, scheduled exports
  core/           # Config, dependencies, shared utilities
```
