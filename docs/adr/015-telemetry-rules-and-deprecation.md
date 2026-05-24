# ADR-015: Subject-Scoped Telemetry Rules & Sprint 18 Deprecation Sunset

- Status: Accepted (Sprint 20, May 2026); §5 + §6 carry-overs shipped Sprint 21 (May 2026)
- Supersedes: none
- Related: [ADR-013](013-telemetry-subject-scoping.md) (schema), [ADR-014](014-telemetry-multi-subject-ingest.md) (ingest+API), [ADR-005](005-embedded-rules-engine.md) (rules engine), [docs/design/subject-scoped-telemetry.md](../design/subject-scoped-telemetry.md), [docs/design/alerts-anomaly-detection.md](../design/alerts-anomaly-detection.md)

> **Update (Sprint 21 — May 2026):** §5 carry-overs shipped — `_TELEMETRY_SUBJECT_KINDS` replaced by `SUBJECT_KINDS_CACHE` (30 s TTL, `tagpulse.core.telemetry_caches`); `PATCH /tenant/config` calls `invalidate_subject_kinds(tenant_id)` so the writing worker converges immediately and siblings within one TTL. Redis pub/sub deferred (revisit if operators report > 30 s settle as a problem). New `LATEST_TELEMETRY_CACHE` (30 s TTL) coalesces `latest_per_metric` on `GET /assets/{id}` and `GET /lots/{id}`. §6 sunset shipped — see [migration 032](../../migrations/versions/032_drop_legacy_device_telemetry.py) and the [runbook](../runbooks/subject-scoped-telemetry.md). All UI items deferred to the [TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI) repo.

## Context

ADR-013 added the subject-scoped `telemetry_readings` hypertable.
ADR-014 turned on the multi-subject write path and the
`/telemetry/readings/...` HTTP + MQTT subject ingest. Sprint 20 is the
operator-facing follow-up: surface the new dimension to the rules
engine and to the admin UI, and start the runway to drop the Sprint 18
back-compat path.

Three forces shaped this decision:

1. **Operator ROI of subject-scoped telemetry depends on alerting.**
   Lot- / asset-scoped temperature dashboards are useful but passive.
   The headline use case (cold-chain breach for a lot) only pays for
   itself when the platform pages someone, not when an operator hand-
   refreshes a chart.
2. **The existing `threshold` rule type is bound to the tag-read
   pipeline.** It evaluates on `Topic.TAG_READ_CREATED` and dereferences
   reader-side metadata; reusing it would either silently double-fire
   or require a `subject_kind` discriminator inside an event whose
   schema is byte-for-byte stable since Sprint 14.
3. **The Sprint 18 deprecation window cannot close mid-cutover.**
   Tenants opt into subject-scoped telemetry asynchronously; dropping
   the back-compat view + hypertable before the slowest tenant's
   retention window has cycled would silently truncate dashboards.

## Decision

### 1. New `telemetry.threshold` rule condition (do not extend `threshold`)

The Sprint 20 spec sketched `threshold` gaining a `subject_kind`
branch. We instead introduced a **new** condition type:

- `condition_type = "telemetry.threshold"`
- `condition_config = { subject_kind, metric_name, operator, value, subject_id?, cooldown_s? }`

Rationale:

- **Different event source.** `threshold` fires on `TAG_READ_CREATED`;
  `telemetry.threshold` fires on a new `Topic.TELEMETRY_RECORDED`. The
  two events have non-overlapping payloads and the rule body would
  branch on `condition_type` anyway.
- **Sprint 14 contract preservation.** `Topic.TAG_READ_CREATED`'s
  payload schema is consumed by analytics modules, the rules engine,
  the dwell tracker, and at least one external integration. Adding a
  `subject_kind` discriminator to either the event or the existing
  rule would force every consumer to learn the new shape with no
  behavioural change for them.
- **Validation surface stays narrow.** A dedicated
  `TelemetryThresholdCondition` Pydantic model can enforce
  `subject_kind ∈ {device, asset, lot, stock_item, zone}` and reject
  rules whose subject kind the tenant hasn't opted into (UI hint —
  not a backend gate; see §3).

### 2. New `Topic.TELEMETRY_RECORDED` published by all four producers

Every code path that persists a `telemetry_readings` row now publishes
the same `Event`:

| Producer                                                  | Source value | Subject kind   |
|-----------------------------------------------------------|--------------|----------------|
| `IngestionService._mirror_tag_borne_sensors` (fan-out from v1 HTTP **and** v2 MQTT `tag-reads`; see [edge-wire-format-v2 §4.6](../design/edge-wire-format-v2.md)) | `"tag"` | asset/lot/stock_item |
| `POST /telemetry/readings/ingest`                         | `"external"` | any            |
| `MqttSubscriber._handle_subject_telemetry`                | `"external"` | any            |
| `TelemetryService._process_reading_with_response` + `ingest_location` | `"device"` | device         |

The fourth producer (added during the cross-sprint audit) covers the
device-scoped path that backs the legacy MQTT `devices/{id}/telemetry`
topic, the HTTP batch ingest, the `_mirror_tag_borne_sensors` step-1
device mirror, and standalone location updates. Without it, the
schema would accept a `telemetry.threshold` rule with
`subject_kind='device'` but no event would ever match — a silent
contract gap.

Publish happens **after** `session.commit()` (or, for
`TelemetryService`, after the repo's `flush()` returns the persisted
row) so handlers that re-read the row through a fresh session always
see it. The MQTT path specifically collects
`(row, metric_name, metric_value, unit)` tuples during the transaction
and emits the events in a second pass.

### 3. Built-in rule template registry (`tagpulse.rules.templates`)

Two seed templates ship in code (not in the database):

- `lot.cold_chain_breach` — `subject_kind=lot, metric_name=temperature_c, operator=gt, value=8.0, cooldown_s=900`
- `asset.high_temperature` — `subject_kind=asset, metric_name=temperature_c, operator=gt, value=60.0, cooldown_s=600`

Exposed via `GET /rule-templates` (list) and
`GET /rule-templates/{key}` (single). The response body is a fully
POST-able rule body — the UI POSTs it back to `/rules` after operator
edits. The `requires_subject_kind` field is a UI hint (so the UI can
gate or warn when the tenant hasn't opted into that kind); the backend
does **not** reject rules that target non-opted-in kinds. They simply
never fire because no events with that subject kind get published.

The registry is a flat constant in code rather than a DB table because
(a) we expect a handful of templates over the next year, (b) adding
one is a code change anyway (validation, simulator support, docs), and
(c) DB-backed templates would need their own multi-tenant ownership
model that we don't need yet.

### 4. Per-(tenant, rule, subject) cooldown reuses `_RULE_COOLDOWN_UNTIL`

The shared in-process cooldown table introduced in Sprint 17a is keyed
by `(tenant_id, rule_id, subject_id_str)`. Two firings against the same
lot within `cooldown_s` produce one alert; firings against different
lots under the same rule are independent. Default `cooldown_s=300`.
This is the same trade-off the zone rules made — we accept that a
multi-process deployment will see at most N alerts per cooldown window
where N = worker count.

### 5. UI items deferred to TagPulse-UI

The Sprint 20 spec lists five UI deliverables (subject picker on the
telemetry timeline, rule builder for `telemetry.threshold`, template
gallery, alert detail context, opt-in toggle on the tenant settings
page). They live in the separate `TagPulse-UI` repo and are tracked on
the Sprint 21 roadmap. The backend ships everything those UI surfaces
need: API endpoints, rule templates, opt-in column, alert context.

### 6. Sprint 18 deprecation sunset deferred to Sprint 21

The Sprint 20 spec also called for dropping the
`telemetry_readings_legacy_device` hypertable and the
`device_telemetry` view, removing `TimescaleTelemetryRepository` and
`DeviceTelemetryModel` from `src/`, and removing the
`GET /telemetry-models/{device_type}` 301 redirect.

We deferred all six items to Sprint 21. Trigger condition: the slowest
tenant's `telemetry_retention_days` window has cycled past the date
that tenant first opted into a non-`device` subject kind. The
[subject-scoped-telemetry runbook](../runbooks/subject-scoped-telemetry.md)
documents the precondition checks (`pg_stat_user_tables` shows zero
reads on the legacy hypertable for a full retention window, no Grafana
dashboards reference the view, `grep` of `src/` is clean).

## Consequences

### Positive

- Operators can author cold-chain rules from a copy-paste template;
  no manual JSON wrangling for the common case.
- Sprint 14 tag-read schema and Sprint 17a zone rules continue to work
  unchanged.
- Rule families share a uniform `_RULE_COOLDOWN_UNTIL` table, so future
  observability (metrics on cooldown hit-rate) covers all rule types
  at once.
- Deprecation sunset has a documented exit criterion — no ad-hoc
  "let's drop it next sprint" decisions.

### Negative

- Two condition types now mean "fire when a number crosses a
  threshold" (`threshold` for tag-read RSSI, `telemetry.threshold` for
  subject-scoped metrics). Documentation must keep them distinct.
- The cooldown table is in-process. A two-worker deployment authoring
  a cold-chain rule for a fast-cycling lot can produce up to two
  alerts per cooldown window. Acceptable for Sprint 20; cross-process
  invalidation is a Sprint 21 backlog item.
- Built-in templates are a code-change-to-add. Tenants cannot author
  org-private templates. We will revisit if template count grows past
  ~10 or if a tenant requests org-scoped templates.
- The Sprint 18 back-compat path lingers for one more sprint, costing
  a small amount of write amplification on `telemetry_readings_legacy_device`
  for tenants that haven't opted in. Storage impact monitored via the
  existing telemetry billing dimension.

### Neutral

- New rule type validates via Pydantic, same as every other condition.
  No new validation framework.
- `Topic.TELEMETRY_RECORDED` adds one event topic; the bus contract
  is unchanged.

## Alternatives considered

1. **Extend `threshold` with a `subject_kind` branch.** Rejected — see
   §1 rationale. Would have forced every Sprint 14 consumer to learn
   the discriminator with no behavioural payoff.
2. **Make rule templates DB-backed (per-tenant).** Rejected for
   Sprint 20 — no demand signal yet, and would have required a tenant
   ownership model + admin UI we don't need. Re-open if tenant count
   or template count grows.
3. **Drop the back-compat path in Sprint 20 with a "no new tenants"
   carve-out.** Rejected — silent truncation risk for in-flight
   dashboards is too high. The retention-cycle gate is cheap.
4. **Synchronous rule evaluation inline in the ingest path.** Rejected
   — couples rule latency to ingest latency, and a misbehaving rule
   could back-pressure the entire telemetry write pipeline. The async
   bus pattern matches every other rule family.
