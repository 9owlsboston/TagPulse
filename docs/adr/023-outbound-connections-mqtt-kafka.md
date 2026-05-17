# ADR-023: Outbound Connections — Add MQTT Dispatcher (Kafka/Pub-Sub Deferred)

- Status: Proposed (Sprint 33, May 2026)
- Implements: gap 2.5 (partial — MQTT only) + 2.15 (rate-limit + monitor) in `~/ws/TagPulse-Design/IMPLEMENTATION-GAPS.md`
- Related: [reference-design-remediation plan](../design/reference-design-remediation.md), ADR [006 webhook integration layer](006-webhook-integration-layer.md) (existing HTTP dispatcher), ADR [002 MQTT for device connectivity](002-mqtt-device-connectivity.md) (inbound MQTT — we reuse the broker patterns), ADR [010 internal event bus](010-internal-event-bus.md), ADR [021 Configurable Sensing Events](021-configurable-sensing-events.md) (the payload shape this dispatcher delivers)

## Context

Today TagPulse outbound integrations support three types (`webhook`, `sse`,
`export`) via `integrations` rows and the `WebhookDispatcher`. Reference
design exposes four outbound types: **HTTP** (we have, as `webhook`),
**MQTT**, **Kafka**, **Pub/Sub** — all with body templating
(`{{field}}` / `{{{value}}}`).

For our customer base, MQTT is the only non-HTTP outbound integration with
real demand (operators already running an MQTT broker on-prem for plant
floor visibility want events forwarded to a topic they own). Kafka and
Pub/Sub are reasonable in the abstract but cover <5 % of asks and each is a
distinct dispatcher with its own auth + delivery-guarantee semantics. Adding
them speculatively would 3× the maintenance surface of `integrations/` for
limited near-term value.

Gap 2.15 additionally calls out per-connection rate-limit and a "Monitor"
view (data rate / error rate over 1 h). Both are dispatcher-agnostic, so
they ride along with this ADR.

## Decision (proposed — to be ratified in Sprint 37)

Add an MQTT dispatcher, refactor the dispatcher selection into a registry,
add per-connection rate-limit + delivery-stats fields, and add a Jinja2
body-templating layer used by all dispatcher types.

### Schema

```sql
-- 'mqtt' joins existing enum
-- (today: webhook | sse | export → tomorrow: webhook | sse | export | mqtt)
ALTER TABLE integrations
    ADD COLUMN rate_limit_per_minute INT,                           -- null = unlimited
    ADD COLUMN body_template TEXT,                                  -- Jinja2; null = default envelope
    ADD COLUMN last_success_at TIMESTAMPTZ,
    ADD COLUMN last_failure_at TIMESTAMPTZ,
    ADD COLUMN last_failure_message TEXT;
```

`config` JSONB shape for MQTT integrations:

```jsonc
{
  "broker_url": "mqtts://broker.example.com:8883",
  "topic": "tagpulse/{tenant}/events",   // {tenant} and {event_type} templated
  "qos": 1,                              // 0 | 1 | 2
  "client_id": "tagpulse-{tenant}-{integration_id}",
  "credentials_key_vault_ref": "kv://...",   // username/password OR client cert
  "tls": { "ca_bundle_kv_ref": "kv://...", "verify_hostname": true }
}
```

### Dispatcher registry

```python
# src/tagpulse/integrations/dispatchers/__init__.py
DISPATCHERS: dict[str, type[Dispatcher]] = {
    "webhook": WebhookDispatcher,
    "sse":     SseDispatcher,
    "export":  ExportDispatcher,
    "mqtt":    MqttDispatcher,   # new
}
```

`MqttDispatcher` reuses the asyncio-mqtt client patterns from the inbound
subscriber (ADR 002) but with reversed direction. Connection pool is one
client per `(broker_url, credentials)` tuple, shared across integrations on
the same broker.

### Body templating

A Jinja2-based renderer with:

- Sandboxed environment (no filesystem / no shell / no eval).
- Auto-escape off (payloads are JSON, not HTML).
- Triple-mustache equivalent via `{{ value | safe }}`.
- Available context: the new outbound event envelope from ADR 021 plus
  `tenant.slug`, `integration.name`, `now`.

Default template (when `body_template IS NULL`):

```jinja
{{ event | tojson }}
```

### Rate-limit + monitor

- Per-integration token-bucket in Redis (existing infra), keyed by
  `integration_id`. Drops with `WARNING` log + counter when exceeded.
- New endpoint `GET /v1/tenants/{slug}/integrations/{id}/monitor?window=1h`
  returns `{ delivered, failed, rate_per_minute, p50_latency_ms,
  p95_latency_ms, last_success_at, last_failure_at }` from existing
  `integration_deliveries` rows + new Prom histograms.

### Scope cuts (explicit non-goals here)

- **Kafka and Pub-Sub dispatchers** — deferred to backlog. Re-evaluate when
  first customer asks. Dispatcher registry leaves room to slot them in
  without further schema churn.
- **Connection Import/Export JSON** (other half of gap 2.15) — dropped.
  Low-value vs. cost; operators can export-via-API + commit-to-git already.
- **Body-template visual preview** UI — Sprint 37+1 ticket; not part of the
  v1 dispatcher.

## Alternatives considered

1. **All four dispatchers in one sprint** — rejected; 3× the surface area of
   a single-dispatcher PR, two of which have no customer demand. Premature.
2. **MQTT-bridge via a sidecar reusing inbound subscriber** — rejected;
   reverses the dependency. Outbound integration deserves its own dispatcher
   so it inherits the rate-limit + monitor + retry semantics of the
   integrations layer, not the ingest layer.
3. **Forgo templating and hardcode the envelope** — rejected; operators
   integrating with downstream systems (e.g. Splunk, Datadog) require field
   re-shaping. Templating is the lowest-friction way to give that.

## Consequences

- **One new dispatcher to maintain.** Same delivery-guarantee model
  (at-least-once + dedupe via `event_id`).
- **New runtime dep:** asyncio-mqtt (already in tree from inbound).
- **New secret-handling pattern** for MQTT credentials in Key Vault. Reuse
  the existing `kv://` reference resolver from inbound MQTT.
- **Templating sandbox is a security boundary.** Add `tests/integration/test_template_sandbox.py` that asserts attempts to escape the sandbox fail.
- **Backwards compat:** existing webhook integrations keep their current
  payload because `body_template IS NULL` renders the default envelope —
  which is the same shape they receive today (after the envelope additions
  in ADR 021 land).

## Open questions for Sprint 37

- Per-tenant default rate-limit? Lean no — null (unlimited) by default,
  operator opts in per-connection.
- Should MQTT delivery report failures via the existing
  `integration_deliveries` table or a new MQTT-specific table?
  Lean reuse the existing table; add a nullable `mqtt_reason_code` column.
- Topic templating syntax: Python `.format()` or Jinja2 (matching body)?
  Lean Jinja2 for consistency.
- Connection pooling granularity: per-broker-URL or per-integration?
  Lean per-broker-URL with per-integration logical topic.
