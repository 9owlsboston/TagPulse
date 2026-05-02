# Design Document: Integration & Export Layer (Sprint 8)

**Date:** 2026-04-25
**Status:** accepted (updated with Azure IoT patterns)
**Related:** [ADR-006 (Webhook-first integration layer)](../adr/006-webhook-integration-layer.md), [ADR-010 (Internal event bus)](../adr/010-internal-event-bus.md)
**References:** Azure IoT Hub message routing, Azure IoT Central data export

---

## 1. Problem Statement

TagPulse needs to push data and events to external systems. Three channels (per ADR-006):

1. **Outbound webhooks** — HTTP POST on configurable event triggers
2. **SSE streaming** — real-time event feed for connected consumers
3. **Scheduled exports** — periodic CSV/JSON to object storage or email

All must be tenant-scoped, metered, and have delivery status tracking.

### Design Influences from Azure IoT

| Azure IoT Pattern | TagPulse Adoption |
|-------------------|-------------------|
| **Message routing with filter queries** (IoT Hub) | Filter expressions on integration targets — reuse rule condition engine |
| **Message enrichment** (IoT Central) | Enrichments config — add device name, tenant metadata to payloads |
| **Per-type payload schemas** (IoT Central) | `messageSource` + `messageType` fields in all webhook payloads |
| **Endpoint health monitoring** (IoT Hub) | `health_status` field on integrations — auto-updated from delivery success rate |
| **15-min retry window** (IoT Central) | Configurable retry policy per integration in config JSONB |

---

## 2. Integration Target Data Model

### `integrations` Table

```
integrations
------------
id              UUID PK
tenant_id       UUID FK → tenants.id (NOT NULL, indexed)
name            VARCHAR(255) NOT NULL
type            VARCHAR(20) NOT NULL    -- 'webhook' | 'sse' | 'export'
events          JSONB NOT NULL          -- list of event types to subscribe to
config          JSONB NOT NULL          -- type-specific configuration
enabled         BOOLEAN NOT NULL DEFAULT true
status          VARCHAR(20) NOT NULL DEFAULT 'active'
health_status   VARCHAR(20) NOT NULL DEFAULT 'unknown' -- unknown | healthy | unhealthy | dead
filters         JSONB NULL              -- optional event filter conditions
enrichments     JSONB NULL              -- key-value metadata added to payloads
last_triggered  TIMESTAMPTZ NULL
created_at      TIMESTAMPTZ NOT NULL
updated_at      TIMESTAMPTZ NOT NULL
```

### `integration_deliveries` Table (delivery log)

```
integration_deliveries
----------------------
id              UUID PK
integration_id  UUID FK → integrations.id
tenant_id       UUID FK → tenants.id (indexed)
event_type      VARCHAR(50) NOT NULL
payload         JSONB NOT NULL
status          VARCHAR(20) NOT NULL    -- 'pending' | 'delivered' | 'failed' | 'dead_letter'
attempts        INT NOT NULL DEFAULT 0
last_attempt_at TIMESTAMPTZ NULL
response_code   INT NULL
error_message   TEXT NULL
created_at      TIMESTAMPTZ NOT NULL (indexed)
```

### Config Shapes by Type

**Webhook:**
```json
{
  "url": "https://example.com/hook",
  "headers": {"Authorization": "Bearer xxx"},
  "secret": "hmac-signing-key",
  "timeout_seconds": 10
}
```

**SSE:**
```json
{
  "events": ["tag_read.created", "alert.triggered"]
}
```

**Export:**
```json
{
  "format": "csv",
  "schedule": "0 6 * * *",
  "destination": "https://storage.example.com/exports/",
  "include_fields": ["device_id", "tag_id", "timestamp", "signal_strength"]
}
```

### Subscribable Event Types

| Event | Source | When |
|-------|--------|------|
| `tag_read.created` | Ingestion | Every tag read |
| `alert.triggered` | Rules engine | Rule condition matched |
| `device.status_changed` | MQTT status | Device comes online/offline |
| `device.registered` | Device API | New device created |
| `device.decommissioned` | Device API | Device decommissioned |

### Webhook Payload Schema (Azure IoT Central pattern)

All webhook payloads include standard envelope fields for consumer clarity:

```json
{
  "messageSource": "tag_read.created",
  "messageType": "telemetry",
  "tenantId": "uuid",
  "enqueuedTime": "2026-04-25T12:00:00Z",
  "enrichments": {},
  "data": { ... }
}
```

### Event Filters (Azure IoT Hub routing query pattern)

Integration targets can define filter conditions that events must match before delivery. Reuses the same condition format as the rules engine:

```json
{
  "filters": [
    {"field": "signal_strength", "operator": "lt", "value": -80}
  ]
}
```

Only events whose payload passes all filter conditions are dispatched. Events without the specified field are skipped (not delivered).

### Message Enrichments (Azure IoT Central pattern)

Enrichments add extra key-value metadata to payloads before delivery:

```json
{
  "enrichments": {
    "site": "warehouse-a",
    "region": "us-east"
  }
}
```

Enrichments are merged into the `enrichments` field of the payload envelope. Future enhancement: support `$device.name` variable references resolved at dispatch time.

### Endpoint Health (Azure IoT Hub pattern)

Each integration tracks endpoint health based on recent delivery success:

| Status | Meaning | Transition |
|--------|---------|------------|
| `unknown` | No deliveries yet | Initial state |
| `healthy` | Last 10 deliveries all succeeded | On delivery success |
| `unhealthy` | 3+ consecutive failures | On 3 consecutive failures |
| `dead` | 10+ consecutive failures | On 10 consecutive failures, disable integration |

Health is exposed in `GET /integrations/{id}` response and auto-updated by the webhook dispatcher.

---

## 3. Outbound Webhooks

### Event Flow

```
EventBus (TAG_READ_CREATED / ALERT_TRIGGERED / ...)
    ↓ subscriber
WebhookDispatcher
    ↓ for each matching integration
    ↓ create integration_deliveries row (status=pending)
    ↓ POST to webhook URL
    ↓ update status (delivered | failed)
    ↓ meter.record(tenant_id, "webhook_deliveries", "requests")
```

### Retry Policy

| Attempt | Delay | Max |
|---------|-------|-----|
| 1 | Immediate | — |
| 2 | 30 seconds | — |
| 3 | 2 minutes | — |
| 4 | 10 minutes | — |
| 5 | 1 hour | Dead letter |

After 5 failed attempts → set `status = 'dead_letter'`, log, stop retrying.

Retries are scheduled via `asyncio.create_task` with `asyncio.sleep`. Not persistent across restarts — acceptable for v1. Persistent retry (e.g., DB-backed job queue) is a Sprint 10 enhancement.

### Payload Signing

If `config.secret` is set, add `X-TagPulse-Signature` header:
```
HMAC-SHA256(secret, json.dumps(payload))
```

Consumers can verify authenticity.

### Timeout and Error Handling

- HTTP timeout: `config.timeout_seconds` (default 10)
- On 2xx: `status = 'delivered'`
- On 4xx: `status = 'failed'`, no retry (client error)
- On 5xx: `status = 'failed'`, schedule retry
- On network error: `status = 'failed'`, schedule retry

---

## 4. SSE Streaming

### Endpoint

```
GET /integrations/stream
Headers: X-Tenant-ID: {tenant_id}
Query: ?events=tag_read.created,alert.triggered
```

### How It Works

1. Client connects with `Accept: text/event-stream`.
2. Server holds the connection open via FastAPI `StreamingResponse`.
3. An in-memory `asyncio.Queue` per connection buffers events.
4. The SSE handler subscribes to EventBus topics matching the client's `?events` filter.
5. Each event is serialized as SSE (`data: {...}\n\n`) and pushed to the client.
6. On disconnect, the subscription is cleaned up.

### Connection Management

- Max concurrent SSE connections per tenant: configurable (default 10).
- Heartbeat: send `:keepalive\n\n` every 30 seconds to detect dead connections.
- Metering: `meter.record(tenant_id, "sse_connections", "connections")` on connect.

### Tenant Scoping

SSE events are filtered by `tenant_id` — each connection only receives events for its tenant. The EventBus handler checks `event.payload.get("tenant_id")` before forwarding.

---

## 5. Scheduled Exports

### How It Works

1. Integration with `type = 'export'` has a cron schedule in `config.schedule`.
2. A background `asyncio.Task` runs a scheduler loop (check every 60 seconds).
3. When a schedule fires:
   - Query `tag_reads` for the tenant since last export.
   - Format as CSV or JSON.
   - POST to `config.destination` URL (pre-signed S3 URL, webhook, etc.).
   - Record delivery in `integration_deliveries`.
   - `meter.record(tenant_id, "export_volume", "bytes", payload_size)`.

### Cron Parsing

Use a lightweight cron parser (e.g., `croniter`). Add to `pyproject.toml` dependencies.

### Export Limits

- Max 100,000 rows per export (paginate larger exports).
- Max export frequency: 1/hour per integration.

---

## 6. CRUD API

```
POST   /integrations               — create integration target
GET    /integrations               — list integration targets
GET    /integrations/{id}          — get integration target
PATCH  /integrations/{id}          — update integration target
DELETE /integrations/{id}          — delete integration target
GET    /integrations/{id}/deliveries — list delivery history
```

All routes tenant-scoped via `get_current_tenant`.

---

## 7. Metering

| Dimension | When |
|-----------|------|
| `webhook_deliveries` | Each webhook POST (success or failure) |
| `sse_connections` | Each SSE connection opened |
| `export_volume` | Bytes exported per scheduled export |

---

## 8. Project Structure

```
src/tagpulse/integrations/
    __init__.py
    models.py              # IntegrationModel, IntegrationDeliveryModel (if separate from database.py)
    service.py             # IntegrationService (CRUD + delivery log)
    webhook.py             # WebhookDispatcher (subscribe to EventBus, POST, retry)
    sse.py                 # SSE streaming endpoint handler
    exports.py             # Scheduled export runner
src/tagpulse/api/routes/
    integrations.py        # CRUD + delivery history + SSE stream endpoint
migrations/versions/
    009_integrations.py    # integrations + integration_deliveries tables
```

---

## 9. Testing Strategy

- Unit tests: `WebhookDispatcher` with mock httpx client
- Unit tests: integration CRUD service with fake repo
- Unit tests: SSE event filtering logic
- Unit tests: export formatting (CSV/JSON serialization)
- Unit tests: retry logic (attempt counting, backoff delays)
- No live HTTP tests for v1

---

## 10. Dependencies

- `httpx` — already in pyproject.toml (used by alert delivery)
- `croniter` — for cron schedule parsing (new dependency)
- `sse-starlette` — for SSE response support (or use raw `StreamingResponse`)

---

## 11. Decisions (resolved)

| # | Question | Decision |
|---|---|---|
| 1 | Encrypt webhook secrets? | **Plaintext JSONB for v1; encrypt in Sprint 10.** Acceptable risk pre-prod; encrypt-at-rest column or KMS-wrapped value once hardening sprint lands. |
| 2 | SSE library? | **Raw `StreamingResponse`** — simpler, no extra dependency, sufficient for our use case. |
| 3 | S3 export support? | **HTTP POST only for v1.** S3 is supported indirectly: customer generates a pre-signed URL and registers it as an HTTP POST destination. Revisit native S3 if multiple customers ask. |
| 4 | Dead-letter deliveries queryable? | **Yes** — filter `status=dead_letter` in delivery-history endpoint. |
