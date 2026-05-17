# ADR-021: Configurable Sensing Events

- Status: Proposed (Sprint 33, May 2026)
- Implements: gap 2.3 (and the outbound-envelope half of 2.9) in `~/ws/TagPulse-Design/IMPLEMENTATION-GAPS.md`
- Related: [reference-design-remediation plan](../design/reference-design-remediation.md), ADR [005 embedded rules engine](005-embedded-rules-engine.md) (the existing automation surface that this ADR coexists with), ADR [015 telemetry rules & deprecation](015-telemetry-rules-and-deprecation.md), ADR [019 Categories](019-categories.md) (scoping prerequisite), ADR [020 Labels](020-labels-first-class.md) (scoping prerequisite)

## Context

The reference design exposes "Configurable Sensing Events" as the primary
operator-facing surface for telemetry-driven detection. A sensing event is a
persisted configuration scoped to one or more Categories + optional Label
filters, with:

- **Event type:** `Location` / `Geolocation` / `Temperature` / `Geofencing`.
- **Trigger:** `On Change` / `Periodic` / `On Inactivity` / `On Inference` /
  `On Entry` / `On Exit`.
- **Processor:** `IsolatedZones` / `OverlappingZones` (algorithm choice).
- **Confidence threshold:** All / 50 % / 75 %.
- **Outbound payload:** carries `confidence`, `keySet[]`,
  `eventConfigurationId`, `categoryId`, `labels[]` propagated from the
  matched entity.
- **Default cap:** 5 configurations per `(event_type, category)` pair.

TagPulse today exposes `rules` with three condition types
(`threshold` / `absence` / `rate_change`) plus the Sprint 17a geofence
additions (`zone.entered` / `zone.exited` / `zone.dwell_exceeded`) and Sprint
21 subject-scoped telemetry (`telemetry.threshold`). The condition-type
dropdown has accumulated 10 disparate options and has no category scoping,
no label filters, no confidence concept, and no processor choice. It also
does not emit `confidence` / `keySet[]` / `eventConfigurationId` in
outbound payloads (gap 2.9).

`rules` is well-loved for *non-category* automations (e.g. "alert when device
X is silent for 30 min"). We do not want to delete or migrate it.

## Decision (proposed — to be ratified in Sprint 36)

Introduce a new `sensing_event_configs` table for category-scoped sensing
events. Keep `rules` for entity-agnostic automations. Both produce `alerts`
rows on the existing table; the outbound payload gains the new envelope
fields documented below regardless of source.

```sql
CREATE TABLE sensing_event_configs (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    event_type VARCHAR(32) NOT NULL,        -- location | geolocation | temperature | geofencing
    trigger VARCHAR(32) NOT NULL,           -- on_change | periodic | on_inactivity | on_inference | on_entry | on_exit
    processor VARCHAR(32),                  -- isolated_zones | overlapping_zones (nullable for non-spatial types)
    confidence_threshold NUMERIC(3,2) NOT NULL DEFAULT 0.0,  -- 0.0 = all events
    location_retention_minutes INT,
    category_ids UUID[] NOT NULL DEFAULT '{}',   -- empty = all categories
    asset_label_filters JSONB,              -- [{key, value_in: [...]}]
    zone_label_filters JSONB,
    c2c_connection_ids UUID[],              -- which integrations forward this event
    advanced_config JSONB,                  -- processor params (aggregation_window, min_rssi, …)
    status VARCHAR(16) NOT NULL DEFAULT 'active',  -- active | inactive
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by UUID REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by UUID REFERENCES users(id)
);
-- RLS on tenant_id.
-- Soft default-cap enforced in API: 5 active rows per (event_type, category_id).
```

Outbound event envelope (applies to **all** alert sources — `rules` and
`sensing_event_configs`):

```jsonc
{
  "event_id": "uuid",
  "event_configuration_id": "uuid|null",   // null for rules-sourced
  "category_id": "uuid|null",
  "asset_id": "uuid|null",
  "event_name": "string",
  "event_type": "string",
  "value": "any",
  "confidence": 0.0,
  "key_set": ["string", ...],
  "labels": [{ "key": "...", "value": "..." }, ...],
  "start": "iso8601",
  "end": "iso8601|null",
  "created_on": "iso8601"
}
```

API surface:

```
GET    /v1/tenants/{slug}/sensing-events
POST   /v1/tenants/{slug}/sensing-events
GET    /v1/tenants/{slug}/sensing-events/{id}
PATCH  /v1/tenants/{slug}/sensing-events/{id}
DELETE /v1/tenants/{slug}/sensing-events/{id}
POST   /v1/tenants/{slug}/sensing-events/{id}/duplicate   (reference design's "Duplicate" affordance)
POST   /v1/tenants/{slug}/sensing-events/{id}/activate
POST   /v1/tenants/{slug}/sensing-events/{id}/deactivate
```

Worker pipeline:

- The existing rules engine stays in place for non-category automations.
- A new `sensing_event_evaluator` runs alongside the rules engine, fed by the
  same internal event bus (ADR 010), evaluating active configs against
  inbound `tag_reads` + `telemetry_readings` + `subject.zone_changed` events.
- Outbound `WebhookDispatcher` (and the MQTT dispatcher landing in ADR 023)
  receives the new envelope shape.

UI:

- New "Sensing Events & Data" sidebar item replaces Telemetry / Telemetry
  Models / Rules / Alerts at top level (those four become subpages or admin-
  scoped tools).
- "Add Sensing Event" modal matches the reference layout (Event Name → Event
  Type → Category × Labels scoping → Trigger → Confidence → Retention →
  Connections → Advanced).

## Alternatives considered

1. **Migrate `rules` rows into `sensing_event_configs`** — rejected; the two
   models have different scoping semantics. `rules` is per-device, sensing
   events are per-category × label. A forced migration would lose precision.
2. **Polymorphic `rules.rule_kind = legacy | sensing_event`** — rejected;
   adds discriminator complexity without buying clarity.
3. **Keep `rules` as the only surface, add columns** — rejected; the column
   set diverges too far (label filters, category filters, processor,
   confidence — all unused by today's rule types) and would bloat the row.

## Consequences

- **Positive:** matches reference UI mental model; unblocks the visually
  richest UI gap (3.5); enables the new outbound envelope shape (gap 2.9).
- **Two evaluators to maintain:** rules engine + sensing-event evaluator.
  Bounded cost — both share the same event-bus subscription pattern.
- **Outbound envelope is a versioned contract change.** New fields are
  additive; existing webhook consumers won't break, but the payload doc and
  conformance tests need updating.
- **Migration:** none for data. Operators continue to use `rules` for their
  existing automations and adopt `sensing_event_configs` for new
  category-scoped events at their own pace.

## Open questions for Sprint 36

- Default cap of 5 per `(event_type, category)`: enforce strictly, or as a
  soft warning? Lean strict with admin-only override.
- Should `advanced_config` JSONB be schema-validated per `event_type` /
  `processor` combination? Lean yes, via JSON Schema files committed under
  `src/tagpulse/sensing_events/schemas/`.
- Backwards-compat for the outbound envelope: ship as `v2` topic / a new
  header, or unconditionally additive? Lean additive (existing fields
  unchanged, new fields appended).
