# Design Document: Alerts & Anomaly Detection

**Date:** 2026-04-26
**Status:** implemented
**Related:**
- [ADR-005 (Embedded Rules Engine)](../adr/005-embedded-rules-engine.md)
- [ADR-010 (Internal Event Bus)](../adr/010-internal-event-bus.md)
- [Analytics Module Framework](analytics-module-framework.md)

---

## 1. Overview

TagPulse provides user-defined rules that evaluate incoming telemetry in real time, fire alerts when conditions match, and deliver notifications via configurable actions. A separate analytics subsystem detects statistical anomalies (read-frequency deviations) and stores results for querying.

### Event Flow

```
Tag Read (HTTP/MQTT)
  |
  v
IngestionService.ingest()
  +-- Persist to DB
  +-- Record device last_seen
  +-- Publish TAG_READ_CREATED event
        |
        v
  AsyncEventBus (asyncio.Queue per topic)
        |
        +-- RuleEvaluator
        |     +-- Fetch active rules for device
        |     +-- Evaluate condition against payload
        |     +-- If match: create Alert + publish ALERT_TRIGGERED
        |     +-- Record metrics (rule_evaluations, alerts_fired)
        |
        +-- ReadFrequencyModule
        |     +-- Increment in-memory counter per device
        |     +-- Every 60s: flush to analytics_results table
        |     +-- Compute anomaly_flag (2-sigma deviation)
        |
        +-- AlertDeliveryService (on ALERT_TRIGGERED)
        |     +-- Dispatch to webhook / email / notification
        |
        +-- WebhookDispatcher (on all topics)
              +-- Match integrations for event type
              +-- Apply filters, POST with HMAC signing
              +-- Log delivery attempts + retry logic
```

---

## 2. Rules System

### 2.1 Data Model

**Table: `rules`** (regular table, not hypertable)

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `tenant_id` | UUID FK -> tenants | Indexed |
| `name` | VARCHAR(255) | |
| `description` | TEXT | |
| `condition_type` | VARCHAR(50) | `threshold`, `absence`, `rate_change` |
| `condition_config` | JSONB | Condition-specific parameters |
| `action_type` | VARCHAR(50) | `webhook`, `email`, `notification` |
| `action_config` | JSONB | Action target parameters |
| `scope_device_id` | UUID nullable | If set, rule applies only to this device |
| `enabled` | BOOLEAN | Default: true |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |

Migration: `006_rules_alerts.py`

### 2.2 Condition Types

#### Threshold — Numeric field comparison

```json
{
  "field": "signal_strength",
  "operator": "lt",
  "value": -70
}
```

Operators: `gt`, `lt`, `gte`, `lte`, `eq`. Returns false if field missing, non-numeric, or operator invalid.

**Example:** Alert when signal strength drops below -70 dBm.

#### Absence — Tag not seen

```json
{
  "tag_id": "TAG001",
  "minutes": 10
}
```

**Phase 1 (current):** Simplified — triggers when a *different* tag is read, acting as a proxy signal that the monitored tag may be absent. True timer-based absence detection (background scheduler) is deferred to v2.

#### Rate Change — Signal strength deviation from baseline

```json
{
  "change_percent": 20,
  "baseline": -50.0,
  "window_minutes": 5
}
```

Evaluation:

```
deviation = abs((signal - baseline) / baseline) * 100
triggers  = deviation > change_percent
```

Default baseline: -50.0 dBm. Returns false if signal missing or baseline is 0.

**Example:** Signal = -70, baseline = -50 -> deviation = 40% -> triggers if change_percent < 40.

### 2.3 Rule Evaluation Flow

`RuleEvaluator` subscribes to `TAG_READ_CREATED`:

1. Extract `device_id` and `tenant_id` from event payload
2. Query active rules scoped to the device (or global rules with `scope_device_id = NULL`)
3. For each rule, call `_evaluate_condition()` which dispatches to `_eval_threshold`, `_eval_absence`, or `_eval_rate_change`
4. On match: create `AlertModel` record, publish `ALERT_TRIGGERED` event with alert details and action config
5. Increment Prometheus counters (`rule_evaluations`, `alerts_fired`)

### 2.4 Key Files

| File | Class | Purpose |
|------|-------|---------|
| `src/tagpulse/models/rule_schemas.py` | `RuleCreate`, `RuleUpdate`, `RuleResponse`, condition schemas | Pydantic validation |
| `src/tagpulse/rules/__init__.py` | `RulesService` | CRUD operations + fetch active rules |
| `src/tagpulse/rules/evaluator.py` | `RuleEvaluator` | Event handler, condition evaluation |
| `src/tagpulse/api/routes/rules.py` | FastAPI routes | REST API for rules CRUD + alerts |

---

## 3. Alerts System

### 3.1 Data Model

**Table: `alerts`** (TimescaleDB hypertable, partitioned on `triggered_at`)

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Composite PK: (id, triggered_at) |
| `tenant_id` | UUID FK -> tenants | Indexed |
| `rule_id` | UUID FK -> rules | Indexed |
| `device_id` | UUID nullable | Indexed |
| `severity` | VARCHAR(20) | `warning` (default), `critical`, `info` |
| `message` | TEXT | Human-readable alert message |
| `context` | JSONB | Full event payload + condition details |
| `status` | VARCHAR(20) | `open` (default), `acknowledged`, `resolved` |
| `triggered_at` | TIMESTAMPTZ | Time-series key, indexed |

Migration: `006_rules_alerts.py`

### 3.2 Alert Lifecycle

1. **Triggered** — RuleEvaluator creates alert with status `open`, message like `"Rule 'High Signal' triggered: threshold condition met"`, and full event context
2. **Delivered** — AlertDeliveryService dispatches to configured action target
3. **Acknowledged** — User marks via `POST /alerts/{id}/acknowledge` (status -> `acknowledged`)
4. **Resolved** — Not yet implemented; planned for time-based or operator-driven closure

### 3.3 Alert Delivery

`AlertDeliveryService` subscribes to `ALERT_TRIGGERED` and routes by action type:

| Action | Config | Behavior |
|--------|--------|----------|
| `webhook` | `{"url": "...", "headers": {...}}` | POST JSON payload to URL |
| `email` | `{"to": "ops@example.com"}` | Logged (email service not yet implemented) |
| `notification` | `{}` | Logged to internal notification queue |

Webhook payload:

```json
{
  "alert_id": "uuid",
  "tenant_id": "uuid",
  "rule_id": "uuid",
  "device_id": "uuid",
  "severity": "warning",
  "message": "Rule 'High Signal' triggered: threshold condition met"
}
```

Key file: `src/tagpulse/rules/delivery.py`

---

## 4. Anomaly Detection

### 4.1 Architecture

Anomaly detection uses the **analytics module plugin framework** (see [analytics-module-framework.md](analytics-module-framework.md)). Modules inherit from `AnalyticsModule`, subscribe to EventBus topics, and store results in the generic `analytics_results` table.

### 4.2 ReadFrequencyModule

**Purpose:** Detect abnormal read rates per device per minute.

**Algorithm:**

1. Subscribe to `TAG_READ_CREATED`
2. On each event: increment in-memory counter keyed by `(tenant_id, device_id)`
3. Every 60 seconds, flush:
   a. Snapshot counters as `reads_per_minute`
   b. Store metric to `analytics_results`
   c. Query 1-hour rolling average and standard deviation
   d. Flag anomaly if `|current_rate - mean| > 2 * stddev`
   e. Store `anomaly_flag` (0 or 1) to `analytics_results`
   f. Reset counters

**Constant:** `ANOMALY_STDDEV_FACTOR = 2.0` (2-sigma threshold, hardcoded)

**Example:** Device normally reads ~100 tags/min (mean=100, stddev=10). A sudden spike to 130 -> `|130 - 100| = 30 > 2 * 10 = 20` -> anomaly flagged.

### 4.3 Analytics Results Data Model

**Table: `analytics_results`** (regular table)

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `tenant_id` | UUID FK | Indexed |
| `module_name` | VARCHAR(100) | e.g., `read_frequency`. Indexed |
| `device_id` | UUID | Indexed |
| `metric_name` | VARCHAR(100) | e.g., `reads_per_minute`, `anomaly_flag` |
| `metric_value` | FLOAT | Numeric value |
| `computed_at` | TIMESTAMPTZ | Indexed |

Migration: `008_analytics_results.py`

### 4.4 Key Files

| File | Class | Purpose |
|------|-------|---------|
| `src/tagpulse/analytics/__init__.py` | `AnalyticsModule` | Abstract base class |
| `src/tagpulse/analytics/read_frequency.py` | `ReadFrequencyModule` | Read frequency + anomaly detection |
| `src/tagpulse/api/routes/analytics.py` | FastAPI routes | Query analytics results |

---

## 5. Event Bus Integration

### 5.1 Topics & Subscribers

| Topic | Published by | Subscribers |
|-------|-------------|-------------|
| `TAG_READ_CREATED` | IngestionService | RuleEvaluator, ReadFrequencyModule, WebhookDispatcher |
| `ALERT_TRIGGERED` | RuleEvaluator | AlertDeliveryService, WebhookDispatcher |
| `DEVICE_STATUS_CHANGED` | Device state updates | WebhookDispatcher |
| `DEVICE_REGISTERED` | Device registration | WebhookDispatcher |
| `DEVICE_DECOMMISSIONED` | Device removal | WebhookDispatcher |

### 5.2 EventBus Configuration

- **Implementation:** `AsyncEventBus` (in-process, asyncio.Queue per topic)
- **Capacity:** 10,000 events per topic (configurable)
- **Overflow:** `drop_oldest` policy
- **Dead-letter:** Failed events logged to `dead_letter_events` table with error trace and retry count
- **Consumer model:** Per-topic background task; handlers called sequentially

Key file: `src/tagpulse/events/async_bus.py`

### 5.3 Startup Registration

In `src/tagpulse/api/main.py` lifespan:

```python
event_bus = AsyncEventBus(capacity=10000)
await event_bus.subscribe(Topic.TAG_READ_CREATED, evaluator.on_tag_read)
await event_bus.subscribe(Topic.TAG_READ_CREATED, read_freq_module.on_event)
await event_bus.subscribe(Topic.ALERT_TRIGGERED, delivery_service.on_alert_triggered)
await event_bus.start()
```

---

## 6. API Endpoints

### 6.1 Rules CRUD

| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| POST | `/rules` | admin, editor | Create rule |
| GET | `/rules` | admin, editor, viewer | List rules (optional `enabled_only`) |
| GET | `/rules/{rule_id}` | admin, editor, viewer | Get single rule |
| PATCH | `/rules/{rule_id}` | admin, editor | Partial update |
| DELETE | `/rules/{rule_id}` | admin | Delete rule |

### 6.2 Alerts

| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| GET | `/alerts` | admin, editor, viewer | Query alerts (filters: rule_id, device_id, status; pagination) |
| POST | `/alerts/{alert_id}/acknowledge` | admin, editor | Mark as acknowledged |

Query params: `rule_id`, `device_id`, `status`, `limit` (default 100, max 1000), `offset`.

### 6.3 Analytics

| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| GET | `/analytics/read-frequency` | admin, editor, viewer | Query read frequency + anomaly flags |

Query params: `device_id`, `start`, `end`, `metric` (default `reads_per_minute`), `limit`.

---

## 7. Webhook Dispatcher (Integration Layer)

Separate from rule-action webhooks, `WebhookDispatcher` handles integration-level event routing:

- **Retry strategy:** 5 attempts with delays `[0, 30, 120, 600, 3600]` seconds
- **HTTP handling:** 2xx = success, 4xx = fail immediately, 5xx = retry then dead-letter
- **Filtering:** Optional per-integration payload filters (threshold-style logic)
- **HMAC signing:** Optional `X-TagPulse-Signature` header (HMAC-SHA256)
- **Enrichment:** Per-integration custom fields added to payload

Key file: `src/tagpulse/integrations/webhook.py`

---

## 8. Current Limitations

| Area | Limitation | Planned Fix |
|------|-----------|-------------|
| Absence detection | No background timers; proxy logic only | v2: stateful absence timers per device/tag |
| Rate change | Static baseline, not historical windowed | v2: windowed analysis from stored telemetry |
| Anomaly threshold | Hardcoded 2-sigma | Configurable per device/tenant |
| Email/notification delivery | Logged only, not sent | Integration with email service |
| Alert resolution | Manual acknowledge only | Auto-resolution after configurable duration |
| AlertDeliveryService retries | No retry on webhook failure | Align with WebhookDispatcher retry strategy |

---

## 9. Extending the System

### Adding a New Condition Type

1. Add Pydantic schema in `src/tagpulse/models/rule_schemas.py`
2. Add `_eval_{name}()` function in `src/tagpulse/rules/evaluator.py`
3. Add case to `_evaluate_condition()` dispatch
4. Add unit tests in `tests/unit/test_rule_evaluator.py`

### Adding a New Analytics Module

1. Create module inheriting `AnalyticsModule` in `src/tagpulse/analytics/`
2. Implement `name`, `subscribed_topics`, `on_event()`
3. Register in `src/tagpulse/api/main.py` lifespan
4. Add API route in `src/tagpulse/api/routes/analytics.py` if needed
5. Add unit tests

### Adding a New Action Type

1. Add handling branch in `AlertDeliveryService.on_alert_triggered()`
2. Update `action_type` validation in rule schemas
3. Add unit tests in `tests/unit/test_alert_delivery.py`
