# ADR-014: Multi-Subject Telemetry Ingest & API Surface

- Status: Accepted (Sprint 19, May 2026); follow-ups landed/deferred per [ADR-015](015-telemetry-rules-and-deprecation.md)
- Supersedes: none
- Related: [ADR-013](013-telemetry-subject-scoping.md) (subject-scoped telemetry schema), [ADR-015](015-telemetry-rules-and-deprecation.md) (rules + sunset), docs/design/subject-scoped-telemetry.md, docs/design/rfid-tag-data-model.md §6/D4

> **Update (May 2026):** of the three Sprint 20 follow-ups in §Follow-ups, only the `lot.cold_chain_breach` template **shipped in Sprint 20**. The back-compat sunset and cross-process `_TELEMETRY_SUBJECT_KINDS` invalidation **deferred to Sprint 21** (see [ADR-015 §6](015-telemetry-rules-and-deprecation.md) for the retention-cycle gate). The cagg compression mentioned in §Negative did **not** land in Sprint 20 — evaluate again in Sprint 21+ once storage telemetry shows real pressure.

## Context

Sprint 18 (ADR-013) introduced the subject-scoped `telemetry_readings`
hypertable + back-compat path, but kept the write side bound to
`subject_kind='device'` so the schema landed without behavioural risk.
Sprint 19 turns the multi-subject write path on and exposes the new
read surface to clients.

Three pressures shaped this decision:

1. **Cold-chain UX needs lot-scoped queries.** The Lot Expiry Queue
   (sprint 15b) lists lots, but cannot show the most recent
   temperature reading per lot — only per device. Operators currently
   reverse-resolve via the binding chain in the UI.
2. **Per-asset GPS from external systems.** The integration team has
   pending TMS / mobile-app onboardings that publish pre-resolved
   per-asset GPS — we need an HTTP and MQTT surface that does not
   require them to fake a synthetic device row.
3. **Storage cost.** The full N-subject fan-out for every tag-borne
   metric multiplies row count by ~3-4×; we cannot enable it
   universally without explicit operator opt-in.

## Decision

### 1. Tenant opt-in via `tenants.telemetry_subject_kinds`

A new JSONB column on `tenants` (default `["device"]`) controls which
subject kinds the ingest pipeline is allowed to fan-out to. The Sprint
19 plan suggested folding this into the existing `tracking_modes`
column shape; instead we added a dedicated column. Reasoning:
`tracking_modes` is a flat `list[str]` consumed by several services
(asset enrichment, inventory enrichment, sidebar UI gating) that would
all have to learn a new shape. A separate column keeps each migration
narrow and the reader ergonomic.

### 2. Server-side subject resolution in the ingest pipeline

`IngestionService._mirror_tag_borne_sensors` resolves each tag-read
into a list of `(subject_kind, subject_id)` tuples and writes one
`telemetry_readings` row per (resolved subject × tag-borne metric).
Resolution sources:

| Subject kind | Resolution path |
|--------------|-----------------|
| `device`     | `read.device_id` (always present) |
| `asset`      | `asset_tag_bindings.get_active_by_value(tenant_id, tag_id)` |
| `stock_item` | `stock_items.get_active_by_binding(tenant_id, kind, tag_id)` |
| `lot`        | derived from the resolved `stock_item.lot_id` |
| `zone`       | deferred (depends on continuous zone-presence stream); not in Sprint 19/20 scope, no firm sprint commitment yet |

Unresolved subjects log `telemetry.subject_unresolved` at INFO and are
silently skipped — never an error. The legacy device-scoped row keeps
flowing through `TelemetryService.ingest_reading` unchanged so the
Sprint 14 contract is byte-for-byte preserved.

### 3. New HTTP surface

* `GET /telemetry/readings?subject_kind=…&subject_id=…` — subject-scoped
  query (the multi-subject successor to `GET /telemetry`, which stays
  for the device-only contract).
* `GET /telemetry/aggregates?…&bucket_seconds=…` — time-bucketed
  avg/min/max/count, served from `cagg_telemetry_1m` / `cagg_telemetry_1h`
  for the two common widths and from a live `time_bucket()` over the
  raw hypertable for arbitrary widths.
* `POST /telemetry/readings/ingest` — admin-only direct write for
  pre-resolved external observations (TMS GPS, BMS temperatures, etc).
* `GET /telemetry-models/{subject_kind}/{key}` — subject-scoped model
  lookup. The legacy `GET /telemetry-models/{device_type}` returns a
  301 redirect to `device/{device_type}` for the deprecation window;
  removal **deferred to Sprint 21** (gated, see [ADR-015 §6](015-telemetry-rules-and-deprecation.md)).

### 4. New MQTT topic

`tenants/{tenant_id}/subjects/{subject_kind}/{subject_id}/telemetry`
— a dedicated topic family for pre-resolved external integrations.
Subject is taken from the topic, not the body, so a misrouted publish
cannot smuggle a different subject in.

### 5. Continuous aggregates

Two TimescaleDB continuous aggregates are added in migration 031:

| Cagg                | Bucket | Refresh policy |
|---------------------|--------|---------------|
| `cagg_telemetry_1m` | 1 min  | last 2h, every 1m |
| `cagg_telemetry_1h` | 1h     | last 31d, every 15m |

Both are keyed on `(tenant_id, subject_kind, subject_id, metric_name, bucket)`
and source from `telemetry_readings` directly (the back-compat
`device_telemetry` view is read-only and cannot back a cagg).

### 6. Embedded `latest_telemetry` on entity GETs

`GET /assets/{id}` and `GET /lots/{id}` embed up to 5 latest readings
(one per metric) when the tenant has opted into the matching
`subject_kind`. Implementation uses a single `DISTINCT ON (metric_name)
… ORDER BY metric_name, timestamp DESC` query so it costs one
hypertable scan capped to N metrics, not N round trips.

### 7. Migration round-trip CI harness

The Sprint 18 audit found that two migrations had silent downgrade gaps
(missing constraint drop, orphaned index). Sprint 19 ships an
integration test (`tests/integration/test_migration_round_trip.py`)
that runs `alembic upgrade head → downgrade -1 → upgrade head` against
a real TimescaleDB instance via `make migration-check`, gated on the
`TAGPULSE_INTEGRATION_DB_URL` env var so unit-test runs stay hermetic.

## Consequences

### Positive

- Lot/asset/stock_item-scoped telemetry queries are a single endpoint
  call instead of a join chain.
- External integrations have a first-class write surface (HTTP + MQTT)
  that does not require synthetic device rows.
- Continuous aggregates make minute- and hour-grained dashboards
  storage-bounded, not scan-bounded.
- The CI round-trip catches downgrade regressions before a production
  rollback hits them.

### Negative / accepted trade-offs

- Storage growth: an opted-in `["device", "asset", "lot"]` tenant pays
  ~3× write IOPS per tag-borne metric. Mitigated by the opt-in default
  (`["device"]`). Cagg compression originally slated for Sprint 20 did
  not land — revisit in Sprint 21+ once storage telemetry shows real
  pressure.
- Cold cache after a tenant flips `telemetry_subject_kinds` — the
  process-local cache (`_TELEMETRY_SUBJECT_KINDS`) is not invalidated
  cross-process. Acceptable because a tenant only flips the flag a
  handful of times in its lifetime.
- The dedicated column instead of a `tracking_modes`-shape extension
  is a small spec deviation; called out in this ADR so future readers
  do not hunt for a non-existent nested-shape migration.

## Alternatives considered

1. **Client-side fan-out at the SDK.** Rejected: pushes business
   resolution into untrusted edge code and prevents server-side
   tenant gating.
2. **Single-row design with a `subjects: jsonb` column.** Rejected:
   defeats the purpose of TimescaleDB indexes on `subject_id` and
   makes per-subject queries un-cacheable.
3. **Per-subject hypertables (`asset_telemetry`, `lot_telemetry`, …).**
   Rejected: 3× the chunk count and 3× the cagg surface for no
   query-performance win because callers always know `subject_kind`.

## Follow-ups

- **Sprint 20 (shipped):** add the `lot.cold_chain_breach` rule
  template (depends on subject-scoped telemetry being live). See
  [ADR-015 §3](015-telemetry-rules-and-deprecation.md).
- **Sprint 21 (deferred, gated):** drop the back-compat
  `device_telemetry` view + the `telemetry_readings_legacy_device`
  table; remove `TimescaleTelemetryRepository` and
  `DeviceTelemetryModel`; remove the `/telemetry-models/{device_type}`
  301 redirect. Trigger condition: slowest tenant's
  `telemetry_retention_days` cycled past the Sprint 18 cutover. See
  [ADR-015 §6](015-telemetry-rules-and-deprecation.md) and the
  [subject-scoped-telemetry runbook](../runbooks/subject-scoped-telemetry.md).
- **Sprint 21 (deferred):** cross-process cache invalidation for
  `_TELEMETRY_SUBJECT_KINDS` (likely Redis pub/sub or short TTL).
  Today a worker restart is required after `PATCH /tenant/config`
  flips subject opt-in.
