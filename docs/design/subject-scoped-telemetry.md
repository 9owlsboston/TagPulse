# Design Document: Subject-Scoped Telemetry

**Date:** 2026-05-05
**Status:** proposed
**Author:** TagPulse platform team
**Related:** [telemetry-and-location.md](telemetry-and-location.md), [rfid-tag-data-model.md](rfid-tag-data-model.md), [data-models.md](../data-models.md), [assets-and-zones.md](assets-and-zones.md), [storage-strategy.md](storage-strategy.md), ADR 003 (TimescaleDB), ADR 005 (rules engine)

---

## 1. Problem Statement

Telemetry today is **device-scoped only**. The `device_telemetry` hypertable carries `(tenant_id, device_id, timestamp, metric_name, metric_value, …)` and `telemetry_models` defines metric schemas keyed on `device_type` (Sprint 14 — see [telemetry-and-location.md](telemetry-and-location.md) §3.2).

That works for fixed readers and edge gateways but breaks for the increasingly common cases where the **subject of the measurement is not a device**:

| Scenario | Subject of the temperature/humidity reading |
|---|---|
| Cold-chain milk pallet under one warehouse reader | The **lot** (`L-2026-W18`) — every carton in the lot must stay ≤ 4 °C; one breach = recall paperwork |
| RFMicron Magnus-S temperature on a returnable container EPC | The **asset** (the container) — its history follows the container across sites |
| Forklift battery voltage reported by a handheld scanner that read the forklift's tag | The **asset** (forklift #4) — the reader's own battery is irrelevant |
| Ambient temperature at a fixed reader | The **device** (today's only supported case) |
| Humidity in a zone, averaged across all readers in it | The **zone** |

Concrete pain today (asked in the Assets-page review on 2026-05-05): a milk-carton tag with on-tag temperature has its reading saved to `tag_reads.sensor_data` JSONB and *also* mirrored to `device_telemetry` keyed on the **reader's** `device_id` ([rfid-tag-data-model.md](rfid-tag-data-model.md) decision D4). Neither is queryable per asset/lot, neither surfaces on the Assets page, and rules on lot-level cold-chain breaches cannot be expressed.

---

## 2. Goals & Non-Goals

### Goals

1. One telemetry pipeline that accepts readings keyed on **any** subject kind: `device`, `asset`, `lot`, `stock_item`, `zone`.
2. Backwards-compatible ingestion: existing device-scoped MQTT and HTTP paths continue to work without device or simulator changes.
3. First-class rules support: `condition_config` can target a `subject_kind` and refer to its metrics.
4. UI surfaces telemetry on the entity's own detail page (Asset → Telemetry tab, Lot → Cold-chain card, Zone → Environmental panel) instead of only the Devices → Telemetry page.
5. Migration path that preserves all existing `device_telemetry` rows and keeps the existing `(tenant, device, metric, ts)` index hot.

### Non-Goals

- Replacing `tag_reads` (event log) or `asset_current_location` (location view). Telemetry is a **parallel** stream.
- Cross-subject aggregations beyond what TimescaleDB continuous aggregates already provide.
- Edge-side schema negotiation. Edge clients still publish to one of the existing topics; the resolution to a subject is a **server-side** mapping.
- Per-subject quotas (Sprint 14's `tenant_quotas` already covers raw read counts).

---

## 3. Data Model

### 3.1 New table: `telemetry_readings`

Replaces `device_telemetry` as the new authoritative store. The old table is **renamed** rather than dropped to preserve history; a SQL view keeps the old name working for the deprecation window.

```sql
CREATE TABLE telemetry_readings (
    id              UUID NOT NULL DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    subject_kind    VARCHAR(32) NOT NULL,   -- enum: device|asset|lot|stock_item|zone
    subject_id      UUID NOT NULL,
    device_id       UUID NULL,              -- the reporting device, when known
    timestamp       TIMESTAMPTZ NOT NULL,
    metric_name     VARCHAR(100) NOT NULL,
    metric_value    DOUBLE PRECISION NOT NULL,
    unit            VARCHAR(20) NULL,
    source          VARCHAR(20) NOT NULL,   -- enum: device|tag|external|derived
    metadata        JSONB NULL,
    PRIMARY KEY (id, timestamp),
    CONSTRAINT ck_telemetry_subject_kind
        CHECK (subject_kind IN ('device', 'asset', 'lot', 'stock_item', 'zone'))
);

SELECT create_hypertable('telemetry_readings', 'timestamp', if_not_exists => TRUE);

-- Hot path: per-subject metric history
CREATE INDEX ix_telemetry_readings_subject
  ON telemetry_readings (tenant_id, subject_kind, subject_id, metric_name, timestamp DESC);

-- Reporting-device lookup (replaces ix_device_telemetry_lookup for source='device')
CREATE INDEX ix_telemetry_readings_device
  ON telemetry_readings (tenant_id, device_id, metric_name, timestamp DESC)
  WHERE device_id IS NOT NULL;

ALTER TABLE telemetry_readings ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_telemetry_readings ON telemetry_readings
  USING (tenant_id = current_setting('app.current_tenant_id')::uuid);
```

Notes:

- `device_id` is **always populated when a device reported the reading** (even when `subject_kind != 'device'`). This preserves the "which reader saw this" audit trail and lets the existing per-device dashboards keep working.
- `source` follows the [rfid-tag-data-model.md](rfid-tag-data-model.md) §D4 vocabulary (`device` = self-report, `tag` = on-tag sensor, `external` = TMS push, `derived` = computed by an analytics module).
- `metadata` carries `tag_read_id`, `epc`, `signal_strength`, etc. — the same way today's `device_telemetry` does.

### 3.2 `telemetry_models` extension

`telemetry_models` already keys on `device_type`. We add a `subject_kind` column so a tenant can also define schemas like *"this is what cold-chain telemetry looks like for a lot"*. `device_type` becomes nullable when `subject_kind != 'device'`.

```sql
ALTER TABLE telemetry_models
  ADD COLUMN subject_kind VARCHAR(32) NOT NULL DEFAULT 'device',
  ADD CONSTRAINT ck_telemetry_models_subject_kind
    CHECK (subject_kind IN ('device','asset','lot','stock_item','zone')),
  ALTER COLUMN device_type DROP NOT NULL,
  ADD CONSTRAINT ck_telemetry_models_device_type
    CHECK (
      (subject_kind = 'device' AND device_type IS NOT NULL)
      OR (subject_kind <> 'device' AND device_type IS NULL)
    );

-- Replace old uniqueness (was UNIQUE(tenant_id, device_type)):
DROP INDEX IF EXISTS ix_telemetry_models_tenant_device_type;
CREATE UNIQUE INDEX ix_telemetry_models_tenant_subject ON telemetry_models (
  tenant_id,
  subject_kind,
  COALESCE(device_type, '')
);
```

### 3.3 Backwards-compat view

To keep existing repositories, analytics modules, and Grafana dashboards working through the deprecation window:

```sql
ALTER TABLE device_telemetry RENAME TO telemetry_readings_legacy_device;

CREATE VIEW device_telemetry AS
SELECT id, tenant_id, device_id, timestamp, metric_name, metric_value, unit, metadata
FROM   telemetry_readings
WHERE  subject_kind = 'device';

-- The new ingest path writes to telemetry_readings only.
-- A one-shot back-fill copies every row from telemetry_readings_legacy_device
-- into telemetry_readings with subject_kind='device', subject_id=device_id, source='device'.
```

After two minor releases (one for code-cutover, one for tenant-config migration tooling) we drop the legacy table and the view.

### 3.4 Rules engine

`rules.condition_config` already takes a free-form JSONB blob. Existing telemetry threshold rules look like:

```json
{ "device_type": "rfid_reader", "metric": "temperature", "operator": "gt", "value": 30 }
```

We add (additive, not breaking):

```json
{
  "subject_kind": "lot",
  "metric": "temperature_c",
  "operator": "gt",
  "value": 4.0,
  "for_seconds": 600
}
```

The evaluator resolves matching rows from `telemetry_readings` filtered by `subject_kind`. When a rule's `subject_kind` is `device` and only `device_type` is set, the existing semantics are preserved (compatibility mode for already-deployed rules).

---

## 4. Ingest Pipeline

### 4.1 Decision: where does subject resolution happen?

**Option A — edge declares it.** Reader publishes `{"subject_kind":"asset","subject_id":"<uuid>",…}`.
**Option B — server resolves.** Reader publishes the same payload it does today; server maps `tag_id → asset` via `asset_tag_bindings`, `epc → lot/stock_item` via `tag_data_mappings`.

**We pick B.** Edges should not need to know the asset table; the binding can change between the read leaving the truck and arriving at the broker. Centralised resolution also keeps tenant-scoping enforced at one boundary.

### 4.2 Resolution algorithm

For each incoming payload (tag-read or telemetry-only event), the ingest service produces **0 or more** `telemetry_readings` rows:

```
inputs:
  device_id   (always present — the reporting device)
  tag_id      (optional)
  epc         (optional, parsed from tag_id when SGTIN)
  metrics     (the {name: value} pairs to emit)

emit_subjects:
  always: (device, device_id, source='device')

  if tag_id:
    binding = active_binding_for(tenant_id, tag_id)
    if binding:
      emit (asset, binding.asset_id, source='tag')

  if epc:
    decoded = tag_data_mapping_decode(tenant_id, epc)
    if decoded.lot_id:
      emit (lot, decoded.lot_id, source='tag')
    if decoded.stock_item_id:
      emit (stock_item, decoded.stock_item_id, source='tag')

  zone resolution (Sprint 17+, async):
    emit (zone, current_zone_id, source='derived') via background analytics task
```

A single tag-read with on-tag temperature on a bound milk carton's EPC therefore produces **three** rows: one for the reader (device), one for the lot, one for the stock_item. They share `device_id`, `timestamp`, `metric_name`, `metric_value`, `metadata.tag_read_id` so they're fully traceable.

This expands the row count by ~2-3× for tenants with active EPC bindings + tag_data_mappings. See §7.

### 4.3 MQTT topic surface

No new topics. The existing `telemetry`, `tag-reads`, `location`, `events` suffixes already carry everything needed; subject resolution is server-side.

### 4.4 Validation

Validation mirrors Sprint 14 behaviour:

1. Look up the matching `telemetry_models` row by `(tenant_id, subject_kind, device_type-or-NULL)`.
2. If no model exists → **quarantine** with `reason='no_model'`.
3. If `metric_name` is unknown to the model → quarantine `reason='unknown_metric'`.
4. If value outside model's `min`/`max` → quarantine `reason='out_of_range'`.

Quarantine table gains `subject_kind` + `subject_id` columns the same way `telemetry_readings` does.

---

## 5. API Surface

### 5.1 New endpoints (additive)

```
GET /telemetry/readings
    ?subject_kind=lot&subject_id=…&metric=temperature_c&since=…&until=…&limit=500

GET /telemetry/aggregates
    ?subject_kind=lot&subject_id=…&metric=temperature_c
    &bucket=1m&fn=avg&since=…&until=…
```

### 5.2 Embedded on entity endpoints

| Endpoint | Adds |
|---|---|
| `GET /assets/{id}` | `latest_telemetry: {metric_name: {value, unit, recorded_at, source}}` (most recent value per metric within last hour) |
| `GET /lots/{id}` | Same plus `cold_chain_summary: {min, max, avg, breach_count, last_breach_at}` over configured horizon |
| `GET /stock-items/{id}` | `latest_telemetry` |
| `GET /zones/{id}` | `latest_telemetry` (only when zone has a model defined) |
| `GET /assets/current-locations` | Optional `?include=temperature_c,humidity_pct` adds latest sensor values to each row, so the Assets list grid can show them as columns |

### 5.3 Existing endpoints — unchanged

`/telemetry` (the device-scoped query introduced in Sprint 14) keeps working via the `device_telemetry` view. Marked deprecated; new clients use `/telemetry/readings?subject_kind=device&subject_id=…`.

---

## 6. UI Changes

| Page | Change |
|---|---|
| **Assets list** ([AssetList.tsx](../../../TagPulse-UI/src/pages/assets/AssetList.tsx)) | Optional column "Temperature" (gated by tenant config + presence of an `asset` telemetry model). Sortable, filterable by range. |
| **Asset detail** | New **Telemetry** tab — line charts per metric, sourced from `/telemetry/aggregates?subject_kind=asset`. |
| **Lot detail** | **Cold-chain card** — current value, 24-h sparkline, breach count, downloadable CSV for recall paperwork. |
| **Inventory → Lot Expiry / Stock Levels** | Existing pages unchanged; add red badge when a row's lot has open cold-chain breaches. |
| **Telemetry Models admin** | Selector adds `subject_kind` field; conditionally hides `device_type`. |
| **Rules editor** | When `condition_type = telemetry.threshold`, new selector for `subject_kind` (device|asset|lot|stock_item|zone). |
| **Telemetry page** (current Devices → Telemetry) | Add `subject_kind` filter. Default `device` to preserve current view. |

---

## 7. Storage & Performance

### 7.1 Row-count multiplier

Worst case: every tag-read on a bound EPC with a `tag_data_mapping` produces 3 telemetry rows (device + asset + stock_item). For a tenant doing 10 reads/sec with 100% bound + decoded:

- Today: 10 telemetry rows/sec
- After: ~30 telemetry rows/sec

Mitigations:

1. **On-tag sensor data is the only multi-row producer.** Reader self-reports remain 1 row each.
2. TimescaleDB compression (Sprint 14 plan, deferred) becomes more important — target 5× compression on rows older than 7 days.
3. Per-subject continuous aggregates (`cagg_telemetry_1m`, `cagg_telemetry_1h`) so dashboards never scan raw data.
4. Cold-chain rules use `for_seconds` to avoid one-row-spike alerts; the evaluator queries the 1-minute aggregate, not the raw table.

### 7.2 Query patterns

Two hot paths, both fully indexed:

- *"Show this lot's last 24 h of temperature"* → `(tenant, kind=lot, id, metric, ts DESC LIMIT N)` → `ix_telemetry_readings_subject`.
- *"Show this reader's last hour of voltages"* → `(tenant, device_id, metric, ts DESC)` → `ix_telemetry_readings_device`.

Cross-subject queries (e.g., *"all assets currently > 30 °C"*) are an aggregate pivot; expected workload is small and served by a continuous aggregate.

### 7.3 Retention

Same as `device_telemetry` today — Timescale retention policy `INTERVAL '90 days'` on raw + `INTERVAL '2 years'` on hourly cagg. Per-subject retention overrides (e.g., keep cold-chain at full resolution for 7 years for FDA) deferred to a follow-up ADR.

---

## 8. Rollout Plan

> **Status (May 2026):** Sprints 18 and 19 shipped as designed. Sprint 20 shipped the **rules-engine surface** (`telemetry.threshold` condition, `lot.cold_chain_breach` + `asset.high_temperature` templates, `Topic.TELEMETRY_RECORDED` published by all four producers including the device-scoped path — see [ADR-015](../adr/015-telemetry-rules-and-deprecation.md)). **Sprint 21 (May 2026, backend) shipped the back-compat sunset** — [migration 032](../../migrations/versions/032_drop_legacy_device_telemetry.py) drops the `device_telemetry` view + `telemetry_readings_legacy_device` table + RLS policy + Sprint 14 lookup index; `TimescaleTelemetryRepository` and `DeviceTelemetryModel` are removed; the `/telemetry-models/{device_type}` 301 redirect becomes `410 Gone` with a migration hint. Sprint 21 also closed the two ADR-015 §5 carry-overs: `LATEST_TELEMETRY_CACHE` (30 s TTL) coalesces `latest_per_metric` on `GET /assets/{id}` / `GET /lots/{id}`, and `SUBJECT_KINDS_CACHE` (30 s TTL, invalidated by `PATCH /tenant/config`) replaces the unbounded `_TELEMETRY_SUBJECT_KINDS` dict. **UI items** remain in the separate [TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI) repo. See [docs/roadmap.md](../roadmap.md) Sprint 21 entry and [docs/runbooks/subject-scoped-telemetry.md](../runbooks/subject-scoped-telemetry.md) for the operator-facing checklist. The rest of this section preserves the original three-sprint plan for historical context.

Three sprints, each shippable independently. Originally scheduled in [roadmap.md](../roadmap.md) as **Sprint 18** (committed) and **Sprints 19–20** (in backlog, gated on Sprint 18 outcome).

### Sprint 18 — schema + back-compat (read-only)

1. New `telemetry_readings` table + indexes + RLS.
2. Rename `device_telemetry` → `telemetry_readings_legacy_device`; create the back-compat view.
3. Back-fill from legacy table. **No ingest changes**, no UI changes. Behaviour is byte-identical to today.

### Sprint 19 — multi-subject ingest

4. Update ingest service: emit asset / lot / stock_item rows when bindings/mappings resolve.
5. Add `subject_kind` to `telemetry_models` and quarantine table.
6. New `/telemetry/readings` and `/telemetry/aggregates` endpoints.
7. **Embed** `latest_telemetry` on `/assets/{id}` and `/lots/{id}`.
8. Update the Assets list opt-in temperature column behind a feature flag (default off).

### Sprint 20 — rules + UI (split: rules shipped Sprint 20, UI + sunset deferred to Sprint 21)

9. Telemetry threshold rule evaluator gains `subject_kind` branch. **Shipped as a new `telemetry.threshold` condition type rather than extending `threshold`** — see [ADR-015 §1](../adr/015-telemetry-rules-and-deprecation.md) for the rationale (preserves the Sprint 14 `TAG_READ_CREATED` payload byte-for-byte).
10. Asset detail Telemetry tab; Lot detail Cold-chain card. **Deferred to Sprint 21** ([TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI)).
11. Telemetry-models admin + rules-editor UI updates. **Deferred to Sprint 21** ([TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI)).
12. Drop the deprecation view + legacy table once telemetry retention has cycled past. **Deferred to Sprint 21**, gated on the slowest tenant's retention window cycling past the Sprint 18 cutover — see [ADR-015 §6](../adr/015-telemetry-rules-and-deprecation.md) and the runbook.

### Migration safety

- Each migration's `downgrade()` is exercised in CI (existing convention).
- `telemetry_readings_legacy_device` is **never written to** after cutover — it's a snapshot. If anything goes wrong, the view can be dropped and the legacy table renamed back without data loss.
- Continuous-aggregate migrations gated on `TIMESCALEDB_CAGG_ENABLED` flag (existing convention).

---

## 9. Alternatives Considered

### 9.1 Asset-only extension (option A in the original gap analysis)

Add `latest_sensor_data` JSONB to `asset_current_location`. **Rejected** as a long-term answer: doesn't help lots / zones, no rule support, no time-series queries, no aggregation. Suitable only as a stop-gap.

### 9.2 Per-subject parallel tables

`asset_telemetry_readings`, `lot_telemetry_readings`, `zone_telemetry_readings`. **Rejected**: 3-4× the migration / RLS / index / quarantine surface; rules engine has to switch on table; new subject kinds require schema changes.

### 9.3 Subject-on-the-edge (option A in §4.1)

**Rejected** as default — but kept as an opt-in: an MQTT payload may include `"subject_kind"` + `"subject_id"` to bypass server-side resolution. Used when the edge already knows the answer (e.g. a TMS push of truck-engine telemetry where the asset id is known and there's no tag-read).

### 9.4 Promote `tag_reads.sensor_data` to first-class columns

**Rejected**: doesn't generalise to non-tag telemetry, and locks the schema to a fixed set of metrics. We want tenant-defined schemas (the existing `telemetry_models` mechanism).

---

## 10. Open Questions

1. **Asset hierarchy.** When a forklift carries a pallet that has a temperature, should the forklift's temperature query bubble up the carrier's children? Probably no by default — manifest queries handle that — but there's a UX argument for "show me everything *on* this carrier."
2. **Zone telemetry ownership.** Multiple readers in one zone reporting ambient temp — is the zone's value the latest, the average, or the max? Suggest *configurable on the telemetry model* with `aggregator: latest|avg|max|min`.
3. **Cold-chain export format.** GS1 EPCIS event vs. plain CSV vs. PDF. Defer to a follow-up doc once a regulated-tenant signs.
4. **Quotas.** Should `telemetry.write` quota count once per ingested payload, or once per emitted subject row? Strong preference for once per payload (the row multiplication is a server cost, not a tenant cost).

---

## 11. Acceptance Criteria

- [ ] Cold-chain milk simulator (extension of `simulate_inventory.py`) emits on-tag temperatures; **Lot detail page** shows breaches within 5 s of a value > 4 °C.
- [ ] A `lot.cold_chain_breach` rule fires within `for_seconds` of sustained breach and an entry appears in **Alerts** with the lot ID populated and a deep-link to the lot.
- [ ] Existing Sprint 14 device-scoped Telemetry page renders unchanged.
- [ ] Migration `downgrade()` round-trips on a populated DB without data loss (asserted in CI).
- [ ] No regression on ingest p99 latency at 200 reads/sec on the dev box (current Sprint 14 baseline).
- [ ] Continuous-aggregate refresh keeps up with 30 rows/sec sustained ingest.
