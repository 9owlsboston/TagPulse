# Design Document: Telemetry & Location Foundations (Sprint 14)

**Date:** 2026-05-02
**Status:** proposed
**Related:** [asset-tracking-gap-analysis.md](asset-tracking-gap-analysis.md) (A1, A2, A7), [storage-strategy.md](storage-strategy.md), [data-models.md](../data-models.md), [rfid-tag-data-model.md](rfid-tag-data-model.md), [mobile-carriers-and-manifests.md](mobile-carriers-and-manifests.md) (uses the `…/location` topic + `EdgeConfig` throttling defined here)

---

## 1. Problem Statement

Today every numeric reading from a device must arrive as a `tag_read` event. That breaks down for two real cases the reference edge client must support:

1. **Mobile scanners with GPS.** Lat/lon today lands in `tag_reads.sensor_data` JSONB. Downstream consumers (rules, analytics, UI, exports) cannot rely on a schema; queries cannot use indexes.
2. **Sensor-only readings.** An edge device reporting ambient warehouse temperature every 60 s has no `tag_id`. Stuffing a synthetic tag breaks aggregations, quotas, and dedup.

We also currently expose only two MQTT topic suffixes (`tag-reads`, `status`). Devices have no clean channel for sensor metrics, location updates, or device-side events (buffer drained, GPS fix lost).
**Tag-borne sensor data** (e.g., RFMicron / Axzon Magnus-S temperature returned with the inventory response) is a third case. Per [rfid-tag-data-model.md](rfid-tag-data-model.md) decision **D4**, those readings land in `device_telemetry` with `metadata.source='tag'` and `metadata.tag_read_id=<originating row>`, and are mirrored inline on `tag_reads.tag_data` for one-row-query convenience. Sprint 14 implements both paths in the same ingestion pass.
---

## 2. Scope

In scope:

- Structured location columns on `tag_reads`.
- New `device_telemetry` hypertable + ingestion paths (HTTP + MQTT).
- Validation of telemetry against existing `telemetry_models` definitions.
- Three new MQTT topic suffixes: `telemetry`, `location`, `events`.
- Edge client (`clients/pi/`) end-to-end wiring of `submit_telemetry` / `submit_location`.
- Simulator updates so a fresh `docker-compose up` exercises the new paths.
- UI parity (see §8).

Out of scope (deferred to later sprints):

- Asset / zone modeling (Sprint 15).
- PostGIS / polygon containment (Sprint 17).
- Map visualization beyond a single device's last known position (Sprint 17).

---

## 3. Data Model

### 3.1 `tag_reads` extension (migration 016)

```sql
ALTER TABLE tag_reads
  ADD COLUMN latitude            DOUBLE PRECISION NULL,
  ADD COLUMN longitude           DOUBLE PRECISION NULL,
  ADD COLUMN location_accuracy_m DOUBLE PRECISION NULL,
  ADD COLUMN location_source     VARCHAR(20)      NULL;

-- Partial index supports "reads with location" queries without bloating writes.
CREATE INDEX ix_tag_reads_location
  ON tag_reads (tenant_id, timestamp DESC)
  WHERE latitude IS NOT NULL;
```

`location_source ∈ {'gps','fixed','inferred'}`. Validated by Pydantic enum, not DB CHECK (cheap to evolve).

No PostGIS. Lat/lon are plain doubles. Bounding-box queries are `WHERE latitude BETWEEN … AND longitude BETWEEN …` and use the partial index for the time slice.

### 3.2 `device_telemetry` hypertable (migration 016)

```sql
CREATE TABLE device_telemetry (
    id            UUID NOT NULL DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL REFERENCES tenants(id),
    device_id     UUID NOT NULL REFERENCES devices(id),
    timestamp     TIMESTAMPTZ NOT NULL,
    metric_name   VARCHAR(100) NOT NULL,
    metric_value  DOUBLE PRECISION NOT NULL,
    unit          VARCHAR(20) NULL,
    metadata      JSONB NULL,
    PRIMARY KEY (id, timestamp)
);

SELECT create_hypertable('device_telemetry', 'timestamp');

CREATE INDEX ix_device_telemetry_lookup
  ON device_telemetry (tenant_id, device_id, metric_name, timestamp DESC);

ALTER TABLE device_telemetry ENABLE ROW LEVEL SECURITY;
CREATE POLICY device_telemetry_tenant_isolation ON device_telemetry
  USING (tenant_id = current_setting('app.current_tenant')::uuid);
```

Composite PK `(id, timestamp)` matches the established TimescaleDB 2.26+ pattern (see migration 001 fix in CHANGELOG).

### 3.3 Quarantine table (lightweight)

Out-of-range or unknown metrics land in:

```sql
CREATE TABLE telemetry_quarantine (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL,
    device_id     UUID NOT NULL,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    metric_name   VARCHAR(100) NOT NULL,
    metric_value  DOUBLE PRECISION NULL,
    raw_payload   JSONB NOT NULL,
    reason        VARCHAR(40) NOT NULL  -- 'unknown_metric' | 'out_of_range' | 'unit_mismatch'
);
```

Capped retention (7 days) via existing Timescale retention policy pattern. Surfaced in UI under Telemetry Models.

---

## 4. Ingestion

### 4.1 HTTP `POST /telemetry`

Batched payload — same shape as `POST /tag-reads`:

```json
{
  "device_id": "…",
  "readings": [
    {"timestamp": "2026-05-02T12:00:00Z", "metric_name": "temperature", "metric_value": 4.2, "unit": "C"},
    {"timestamp": "2026-05-02T12:00:00Z", "metric_name": "battery_pct", "metric_value": 87.0}
  ]
}
```

Rate-limited and metered as `telemetry_ingestion` (one count per reading).

### 4.2 MQTT topic taxonomy

```
tenants/{tenant_id}/devices/{device_id}/tag-reads     # existing
tenants/{tenant_id}/devices/{device_id}/status        # existing
tenants/{tenant_id}/devices/{device_id}/telemetry     # NEW
tenants/{tenant_id}/devices/{device_id}/location      # NEW
tenants/{tenant_id}/devices/{device_id}/events        # NEW
```

`_parse_topic` gains four new branches (`telemetry`, `location`, `events`, plus a fallback that drops to dead-letter with reason `unknown_topic_suffix`). The existing wildcard subscription `tenants/+/devices/+/+` already covers them.

`location` payload:

```json
{"timestamp": "…", "latitude": 47.6, "longitude": -122.3,
 "accuracy_m": 5.0, "source": "gps"}
```

`events` payload (free-form, persisted to `audit_logs` with `event_source = 'device'`):

```json
{"timestamp": "…", "event_type": "buffer_drained", "details": {"queued": 142}}
```

### 4.3 Validation pipeline

Per reading:

1. `metric_name` must exist in the device's `telemetry_model` for its `device_type`.
   - Miss → `telemetry_quarantine` row with `reason='unknown_metric'`; metered as `telemetry_quarantined`.
2. `metric_value` within `min/max` bounds in the model.
   - Miss → quarantine with `reason='out_of_range'` AND emit `telemetry.out_of_range` event on the EventBus (rules engine consumes; allows alerting on excursions).
3. `unit` must match the model's declared unit (warning only — enrich, don't reject).
4. Timestamp passes the `ClockGuard` rules from §6 (Sprint 16 promotes server-side enforcement).

Validation is async at the service layer, never inside the route handler.

---

## 5. Edge Client (`clients/pi/` — hardware-agnostic reference) Wiring

`tagpulse_edge.agent.EdgeAgent` already exposes `submit_tag_read`, `submit_telemetry`, `submit_location` (per asset-tracking gap analysis §A5). This sprint:

- Routes each method to its MQTT topic via `MqttTransport.publish(topic_suffix, payload)`.
- Adds `_telemetry_outbox` to the SQLite ring buffer so sensor readings are restart-safe (separate from tag_reads outbox; same `Outbox` class, different DB table).
- Updates the example in `clients/pi/examples/run_reader.py` to publish a synthetic temperature reading every 30 s and a GPS fix every 10 s.
- New unit tests: `test_agent_submit_telemetry`, `test_agent_submit_location`.

Backward compatibility: `EdgeConfig` gains `telemetry_enabled: bool = True`, `location_enabled: bool = False` (defaults preserve existing behavior).

---

## 6. Time Validation (preview of Sprint 16)

Sprint 14 introduces *the columns* and ingestion paths. Sprint 16 promotes the strict `ClockGuard` rules to a backend middleware. For Sprint 14 we only:

- Reject events with `timestamp` older than 24 h or more than 5 min in the future.
- Log + dead-letter, do not 500.

Same rule as `clients/pi/tagpulse_edge/clock.py` enforces — symmetric on both ends.

---

## 7. Metering

| Dimension | When |
|---|---|
| `telemetry_ingestion` | Per accepted reading (HTTP or MQTT) |
| `telemetry_quarantined` | Per quarantined reading |
| `location_updates` | Per accepted location event |
| `device_events` | Per accepted device event |

Quotas reuse `tenant_quotas`; default unset (alert_only) for the new dimensions until usage patterns settle.

---

## 8. UI Parity (TagPulse-UI, in lockstep)

| Page | Change |
|---|---|
| Device detail | New **Location** tab — last known lat/lon, accuracy, source, timestamp, Leaflet mini-map |
| Device detail | New **Telemetry** tab — metric line chart per `metric_name`, time-range picker, unit-aware Y axis |
| Telemetry Models | New "Quarantined readings" section per model — counts by reason, click-through to recent samples |
| Data Explorer | Add `latitude` / `longitude` columns; "has location" checkbox filter; CSV export includes lat/lon |
| Overview dashboard | New KPI tile: **Devices reporting location (24 h)** |
| Sidebar | No structural change |

Dependencies: `leaflet` + `react-leaflet` (no API key, OSM tiles). Bundle impact ~40 KB gzip — acceptable per ADR-007.

UI parity is a **release gate** for the sprint: backend feature without UI surface is not "done."

---

## 9. Testing Strategy

- Unit: location Pydantic schema, telemetry validator (model lookup + range check), `_parse_topic` for new suffixes.
- Unit: quarantine writer + `telemetry.out_of_range` event emission.
- Integration: HTTP `POST /telemetry` round-trip → DB row → query API.
- Integration: MQTT publish to each new topic suffix → DB row.
- Edge client: fake MqttTransport asserts payloads on each topic.
- Migration: Alembic upgrade + downgrade green on a fresh DB.

Coverage target: matches existing `device_telemetry` peer modules (≥85 % line).

---

## 10. Rollout

1. Migration 016 (additive only — no data backfill required).
2. Backend deploy with feature flag `TELEMETRY_INGESTION_ENABLED=false` initially; flip to `true` after smoke test.
3. UI deploy depends on backend.
4. Simulator + edge client update last so dogfood traffic appears in the UI on first refresh.

Rollback: feature flag off + revert migration. No destructive changes.

---

## 11. Decisions (resolved)

| # | Question | Decision |
|---|---|---|
| 1 | Telemetry retention default? | **90 days** via Timescale retention policy on `device_telemetry`; per-tenant override exposed later when a customer needs it. |
| 2 | Timescale compression on `device_telemetry`? | **Yes**, on chunks older than **7 days** — mirrors existing `tag_reads` policy. |
| 3 | Quarantine UI access? | **Admin-only** for v1. Lower-tier roles can be added if operations teams need self-service triage. |
| 4 | Per-device metric overrides on top of `telemetry_models`? | **Defer.** Today: per-device-type, per-tenant. Add per-device overrides only on explicit request — the override matrix gets ugly fast. |
