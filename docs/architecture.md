# TagPulse Architecture

## System Overview

![TagPulse Architecture](assets/architecture.png)

<details>
<summary>ASCII source (for editing)</summary>

```
+---------------------------------------------------------------------------+
|   TagPulse Platform                                                       |
|                                                                           |
|   +-----------------+      +-----------------+      +-----------------+   |
|   | Ingestion       |      | Service         |      | Rules & Alerts  |   |
|   | Layer           | ---> | Layer           | ---> | Engine          |   |
|   |                 |      |                 |      |                 |   |
|   | * MQTT Sub      |      | * Device Reg    |      | * Conditions    |   |
|   | * HTTP Push     |      | * Query         |      | * Evaluation    |   |
|   |                 |      | * Telemetry     |      | * Alert Route   |   |
|   +-----------------+      +-----------------+      +-----------------+   |
|          |                                                 |              |
|          v                                                 v              |
|   +-------------------------------------------------------------------+   |
|   | EventBus (internal pub/sub)                                       |   |
|   | * Capacity-limited queues   * Back-pressure policies              |   |
|   | * Phase 1: asyncio.Queue    * Phase 2: Redis Streams              |   |
|   +-------------------------------------------------------------------+   |
|          |                    |                            |              |
|          v                    v                            v              |
|   +-----------------+  +-----------------+         +-----------------+   |
|   | TimescaleDB     |  | Analytics       |         | Integration     |   |
|   |                 |  | Modules         |         | Layer           |   |
|   | * tag_reads     |  |                 |         |                 |   |
|   |   (hyper)       |  | * Read freq     |         | * Webhooks out  |   |
|   | * devices       |  | * Anomaly det   |         | * SSE stream    |   |
|   | * rules         |  | * ...           |         | * Exports       |   |
|   | * alerts        |  |                 |         |                 |   |
|   | * integr.       |  +-----------------+         +-----------------+   |
|   +-----------------+                                      v              |
|                                                    External Systems       |
+---------------------------------------------------------------------------+
```

</details>

To regenerate the PNG after editing the ASCII source:
```bash
java -Djava.awt.headless=true -jar /usr/share/ditaa/ditaa.jar /tmp/tagpulse-arch.txt docs/assets/architecture.png --no-shadows --scale 2
```

## Components

### Ingestion Layer
Accepts device telemetry via two protocols:
- **MQTT subscriber** — connects to external broker (EMQX/Mosquitto), subscribes to `devices/{device_id}/tag-reads` and `devices/{device_id}/status` topics
- **HTTP push endpoint** — `POST /tag-reads` for devices that can't speak MQTT

Both paths validate messages against Pydantic schemas, write to TimescaleDB, and publish events to the internal EventBus. See [ADR-002](adr/002-mqtt-device-connectivity.md).

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

### Internal EventBus
Capacity-limited async pub/sub bus connecting producers (ingestion, rules engine) to consumers (rules engine, analytics, integration layer):
- **Protocol-based** — `EventBus` protocol with `publish()`, `subscribe()`, `start()`, `stop()` methods
- **Capacity limits** — configurable max queue size per topic (default 10,000), high-watermark warnings at 80%
- **Overflow policies** — `drop_oldest` (default), `drop_newest`, `block`, or `raise` when queues are full
- **Topics** — `tag_read.created`, `device.status_changed`, `alert.triggered`, `device.registered`, `device.decommissioned`
- **Phased implementation** — Phase 1: in-process `asyncio.Queue` → Phase 2: Redis Streams → Phase 3: Kafka/Redpanda

See [ADR-010](adr/010-internal-event-bus.md).

### Analytics Modules
Pluggable Python packages following a plugin pattern:
- Base class with registration and lifecycle hooks
- First module: read frequency analytics (reads/min, anomaly flagging)
- Subscribes to `tag_read.created` events via the EventBus
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

Full schema reference: [data-models.md](data-models.md). See also [ADR-003](adr/003-timescaledb-storage.md).

### Admin UI
React 19 + TypeScript + Vite SPA in a separate repo ([TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI)). Served via nginx container on port 3000, proxies API calls to the backend via Docker network alias `api`. Includes dashboard, device management, telemetry charts, data explorer, rule wizard, integration config, and usage/billing views. See [ADR-007](adr/007-admin-ui-technology.md) and [design/admin-ui.md](design/admin-ui.md).

## Data Flow

```
1. Device sends tag read
   ├── MQTT: publish to devices/{id}/tag-reads
   └── HTTP: POST /tag-reads

2. Ingestion validates message (Pydantic schema)

3. Valid message written to tag_reads hypertable

4. Ingestion publishes TagReadCreated event → EventBus
   (capacity-limited; overflow policy applies if consumers lag)

5. EventBus fans out to subscribers:
   ├── Rules engine evaluates against active rules
   │   └── Match? → Create alert → publish AlertTriggered → EventBus
   └── Analytics modules compute aggregates, detect anomalies

6. Integration layer subscribes to AlertTriggered events
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
| Multi-tenancy strategy | [ADR-008](adr/008-multi-tenancy-strategy.md) (proposed) |
| Containerization & local dev | [ADR-009](adr/009-containerization-local-dev.md) (proposed) |
| Internal event bus | [ADR-010](adr/010-internal-event-bus.md) (proposed) |

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
  models/         # SQLAlchemy models + Pydantic schemas (see docs/data-models.md)
  repositories/   # Storage protocol + implementations (see design/storage-strategy.md)
  events/         # EventBus protocol + implementations (see ADR-010)
  rules/          # Rule engine, conditions, alert routing
  analytics/      # Plugin analytics modules
  integrations/   # Webhooks, SSE, scheduled exports
  core/           # Config, dependencies, shared utilities
```
