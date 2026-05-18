# ADR-021: Configurable Signaling Events

- Status: Proposed (Sprint 33, May 2026) — **revised after first review**
- Implements: gap 2.3 (and the outbound-envelope half of 2.9) in the external schema/API audit notes (held locally)
- Related: [reference-design-remediation plan](../design/reference-design-remediation.md), ADR [005 embedded rules engine](005-embedded-rules-engine.md) (the existing automation surface that this ADR extends), ADR [015 telemetry rules & deprecation](015-telemetry-rules-and-deprecation.md), ADR [019 Categories](019-categories.md) (scoping prerequisite), ADR [020 Labels](020-labels-first-class.md) (scoping prerequisite)
- Revision history: v1 proposed a parallel `sensing_event_configs` table; v2 extends `rules` after stakeholder review; **v2.1 (Sprint 41 Phase A) renames "sensing" → "signaling"** throughout (aligns with Azure Monitor's "Signal → Condition → Action" vocabulary; the file path stays `021-configurable-sensing-events.md` for link stability). See §"Decision history" below.

## Context

The reference design exposes "Configurable Signaling Events" as the primary
operator-facing surface for telemetry-driven detection. A signaling event is a
persisted configuration scoped to one or more Categories + optional Label
filters, with:

- **Event type:** `Location` / `Geolocation` / `Temperature` / `Geofencing`.
- **Trigger:** `OnChange` / `Periodic` / `OnInactivity` / `OnInference` /
  `OnEntry` / `OnExit`.
- **Processor:** `IsolatedZones` / `OverlappingZones` (algorithm choice
  for resolving zone attribution when multiple zones could match).
- **Confidence threshold:** All / 0.5 / 0.75 — the precision/recall dial.
- **Outbound payload:** carries `confidence`, `keySet[]`,
  `eventConfigurationId`, `categoryId`, `labels[]` propagated from the
  matched entity.
- **Default cap:** 5 active configurations per `(event_type, category)`.

### How this maps to what TagPulse has today

`rules` already covers the bulk of this conceptually:

| Reference concept | TagPulse `rules` today | Verdict |
|---|---|---|
| Config row (`id` / `name` / `description` / `status active|inactive`) | `id` / `name` / `description` / `enabled` | **Same** |
| Trigger config JSONB | `condition_config JSONB` | **Same shape** |
| Activation | `enabled bool` (no Duplicate action) | **Same**, add endpoint |
| Output history | `alerts` rows on fire | **Same** |
| Action / fan-out | `action_type` + global integrations broadcast | **Different** — they pick `connections[]` per config |
| Scoping | `scope_device_id` (single device) | **Different** — they scope by `categories[] × labels[]` |
| Trigger semantics | `condition_type` mixes domain + trigger + processor | **Same capability, different axis** |
| Processor choice | implicit (one zone-attribution algorithm) | **Genuinely new** |
| Confidence threshold | not modelled | **Genuinely new** |
| Output envelope | TagPulse-shaped JSON | **Genuinely new** (gap 2.9) |
| 5-per-`(event_type, category)` cap | none | **Genuinely new** (API layer) |

About 60 % overlap (terminology + JSONB-shape equivalence), 40 %
structurally new. The new bits — per-category × per-label scoping
(prerequisite ADRs 019 + 020), explicit processor choice, first-class
confidence, per-rule integration routing — do not require a new table.
`condition_config JSONB` already absorbs arbitrary trigger/processor
shapes; the rest are additive columns.

### Why the original v1 ADR called for a new table (and why it was wrong)

v1 followed the external schema/API audit's §3.4 recommendation of a
parallel `sensing_event_configs` table, on the argument that *"On
Inactivity / Periodic / processor configs don't map onto threshold/absence
cleanly."* On re-reading the actual code:

- `condition_config` is already JSONB, so any shape fits cleanly.
- Our `absence` condition already implements "OnInactivity" for tags.
- A new table doubles the surface area (two evaluators, two CRUD APIs,
  two RBAC checks, two RLS policies) for what is fundamentally the same
  primitive: "persisted config → matches event → emits alert + dispatches
  to integrations".
- UI gap 1.1 (UI-LOOK-AND-FEEL-GAPS) asks for **one** consolidated
  "Events & Alerts" page. One backend table makes that free; two
  backend tables forces the UI to merge two schemas.

## Decision (v2 — to be ratified in Sprint 36)

**Extend `rules`** with the missing scoping + processor + confidence
columns. Add new `signaling.<event_type>.<trigger>` `condition_type` values.
Keep all existing condition types unchanged. Implement all four event
types in Sprint 36 (single-PR delivery, single migration).

### Schema (additive, all nullable for existing rows)

```sql
ALTER TABLE rules
    -- Signaling-event taxonomy (nullable for legacy rules):
    ADD COLUMN event_type VARCHAR(32),
        -- location | geolocation | temperature | geofencing | NULL (= legacy)
    ADD COLUMN trigger VARCHAR(32),
        -- on_change | periodic | on_inactivity | on_inference | on_entry | on_exit | NULL
    ADD COLUMN processor VARCHAR(32),
        -- isolated_zones | overlapping_zones | NULL

    -- Confidence + scoping (also useful retroactively for legacy rules):
    ADD COLUMN confidence_threshold NUMERIC(3,2) NOT NULL DEFAULT 0.0,
    ADD COLUMN category_ids UUID[] NOT NULL DEFAULT '{}',   -- empty = all categories
    ADD COLUMN asset_label_filters JSONB,                   -- [{key, value_in:[...]}]
    ADD COLUMN zone_label_filters JSONB,
    ADD COLUMN site_label_filters JSONB,

    -- Per-rule integration routing (replaces global broadcast):
    ADD COLUMN integration_ids UUID[];                      -- empty/null = broadcast (legacy)

-- Indexes for fast lookup by the new evaluator:
CREATE INDEX idx_rules_signaling_active
    ON rules (tenant_id, event_type, trigger)
    WHERE enabled = true AND event_type IS NOT NULL;

-- Default-cap enforced in API (not DB): 5 active rows per
-- (tenant_id, event_type, unnest(category_ids)).
```

### New `condition_type` values

Added to `_RULE_CONDITION_PATTERN` alongside the existing 10:

| condition_type | Maps to (event_type, trigger) |
|---|---|
| `signaling.location.on_change` | (location, on_change) |
| `signaling.location.periodic` | (location, periodic) |
| `signaling.location.on_inactivity` | (location, on_inactivity) |
| `signaling.location.on_inference` | (location, on_inference) |
| `signaling.geolocation.on_change` | (geolocation, on_change) |
| `signaling.geolocation.periodic` | (geolocation, periodic) |
| `signaling.geolocation.on_inactivity` | (geolocation, on_inactivity) |
| `signaling.temperature.on_change` | (temperature, on_change) |
| `signaling.temperature.periodic` | (temperature, periodic) |
| `signaling.temperature.on_inactivity` | (temperature, on_inactivity) |
| `signaling.geofencing.on_entry` | (geofencing, on_entry) |
| `signaling.geofencing.on_exit` | (geofencing, on_exit) |

The Pydantic layer enforces the valid `(event_type, trigger)` pairs as a
discriminated union (e.g. `temperature × on_entry` is invalid — entry/exit
are spatial primitives only). Legacy `condition_type` values
(`threshold`/`absence`/`zone.*`/`stock.*`/`telemetry.*`) untouched; they
leave `event_type` NULL.

### Evaluator pipeline

The existing rules-engine machinery is reused. Three additions:

1. **`PeriodicSignalingDispatcher`** — new worker that wakes on a cadence
   tick and evaluates `signaling.*.periodic` rules. Cadence stored in
   `condition_config.cadence_minutes` (1 ≤ N ≤ 1440). Single new evaluator
   path; no other periodic infrastructure to build.
2. **`signaling.attribution_settled`** — new in-process event bus topic
   (ADR 010) emitted by the OverlappingZones processor when its
   aggregation window resolves. `signaling.*.on_inference` rules subscribe.
3. **Outbound envelope** — `tagpulse/integrations/signaling_envelope.py`
   builds the reference-shaped payload with `confidence`, `keySet[]`,
   `eventConfigurationId` (= `rules.id`), `categoryId`, propagated
   `labels[]`. **This fires for all rules**, not just signaling — legacy
   rules get `confidence=1.0`, `keySet=[]`, `categoryId=null`,
   `labels=[]` (fields are additive; existing webhook consumers see the
   same shape they get today with the new fields appended).

### Processor implementation

- **IsolatedZones** is the existing implicit behaviour, made explicit. No
  algorithm change. Default when `processor IS NULL` and `event_type
  IN (location, geofencing)`.
- **OverlappingZones** is genuinely new. New module
  `tagpulse/signaling/overlapping_zones.py` runs aggregation window over
  `tag_reads` matching `(asset, [overlapping zones])`, applies RSSI
  floor + time-error filter, weights by aging weight, emits one
  `signaling.attribution_settled` event per zone the asset confidently
  occupies. Config in `condition_config.processor_config` JSONB:

  ```jsonc
  {
    "aggregation_window_s": 30 | 60 | 300 | 1800,
    "min_rssi_dbm": -80,
    "zone_bleed_filter": true,
    "aging_weight": 0.5,            // 0..1
    "time_error_filter": true
  }
  ```

#### `signaling.attribution_settled` payload — coordinate-system-agnostic

The processor emits one event per `(asset, zone)` pair it confidently
resolved within the aggregation window. Shape:

```jsonc
{
  "tenant_id": "<uuid>",
  "asset_id": "<uuid>",
  "zone_id": "<uuid>",
  "site_id": "<uuid>",                // resolved from the zone
  "confidence": 0.87,                  // 0.00..1.00
  "window_start": "<iso-8601>",        // aggregation_window_s boundary
  "window_end": "<iso-8601>",
  "contributing_reads": 42,            // tag_reads count in the window
  "contributing_readers": ["<uuid>", "<uuid>"],
  "rule_id": "<uuid>"                  // the rule that ran this evaluation
}
```

The payload carries **zone identity + confidence**, not raw
coordinates. There are no `latitude` / `longitude` / `x` / `y` /
`position_*` fields — the OverlappingZones processor operates on zone
**membership** (reader-bound OR lat/lon geofence containment), and a
zone-shaped result is what its downstream `on_inference` consumers
need. Per-asset `(x, y, confidence)` fixes are emitted by a separate
processor (`trilateration`) into the `asset_positions` hypertable — see
[ADR 024](024-position-estimation.md) (Sprint 45). The two outputs
coexist: zone-attribution evidence is for routing and alerting;
position fixes are for map overlays and distance-based queries.

### API surface

Sprint 41 ships these as **`/rules?kind=signaling`** rather than the
UX-alias namespace originally proposed below, per the Sprint 41 Phase E1
deviation (see [docs/roadmap.md](../roadmap.md) Sprint 41 plan). The
rationale: standing up a parallel `/sensing-events` URL would bake a
permanent legacy path into the API at the same moment the deferred
post-Sprint-41 "Rule taxonomy unification" ADR (Backlog) plans to
rename `rules` → `alert_rules`. Reusing `/rules` with a `kind` filter
avoids that churn.

*Original v2 namespace proposal (superseded by Sprint 41 Phase E1):*

```
GET    /v1/tenants/{slug}/sensing-events                   # alias of /rules?kind=sensing
POST   /v1/tenants/{slug}/sensing-events
GET    /v1/tenants/{slug}/sensing-events/{id}
PATCH  /v1/tenants/{slug}/sensing-events/{id}
DELETE /v1/tenants/{slug}/sensing-events/{id}
POST   /v1/tenants/{slug}/sensing-events/{id}/duplicate
POST   /v1/tenants/{slug}/sensing-events/{id}/activate
POST   /v1/tenants/{slug}/sensing-events/{id}/deactivate
```

These resolve to the existing `RuleService` with a `kind=signaling`
filter (rules where `event_type IS NOT NULL`). Reusing `RuleService`
keeps RBAC, RLS, audit, and validation paths consistent. The
"Signaling Events" framing lives in the UI label layer, not the URL.

The 5-per-`(event_type, category)` cap is enforced in the
`POST` / `PATCH` / `activate` API handlers, not the DB (cleaner error
messages, easier to relax per-tenant).

### UI (per UI gap 3.5)

- Existing "Rules" sidebar item plus Telemetry / Telemetry Models /
  Alerts are consolidated under a new "Events & Alerts" sidebar
  group (UI gap 1.1).
- "Add Alert Rule" modal matches the reference layout (Event Name →
  Event Type → Category × Labels scoping → Trigger → Confidence →
  Retention → Connections → Advanced).
- Legacy `condition_type` rules (the 10 existing values) remain editable
  via a "Legacy rule" sub-tab — same form they have today. New rule
  creation defaults to the Alert Rule flow.
## Alternatives considered

1. **(v1) New `sensing_event_configs` parallel table** — rejected after
   stakeholder review. Doubled the surface area for ~60 % overlap with
   `rules`. Forced the UI to merge two schemas. See "Decision history"
   below.
2. **Polymorphic `rules.rule_kind = legacy | sensing_event`** —
   rejected; the discriminator is implicit from `event_type IS NULL`
   already. Adding another column would be redundant.
3. **Stage event types across Sprints 36 / 38 / 39** (Location +
   Temperature first; Geolocation + Geofencing later) — rejected in
   favour of single-PR delivery in Sprint 36. The schema is one
   migration; staging the API/evaluator gates by `event_type` is more
   release coordination than it's worth. The four event types share the
   evaluator, envelope, API, and UI — splitting them would mean shipping
   half a feature.
4. **Forgo the OverlappingZones processor** — rejected; the use case
   (warehouses with intentional zone overlap, layered geofences) is real
   and currently unserveable. The implementation is one new module
   bounded by the aggregation window.

## Consequences

### Positive

- One table, one evaluator, one CRUD service to maintain.
- Migration is purely additive (every new column nullable or default).
  Legacy rules unchanged in behaviour.
- UI consolidation (gap 1.1) is free — one backend list.
- Outbound envelope upgrade benefits *all* rules, not just signaling ones.
- `category_ids` + `*_label_filters` columns can be back-applied to
  legacy rules over time without further schema churn.
- `integration_ids` per-rule routing is also a long-standing nice-to-have
  for legacy rules.

### Negative / trade-offs

- `rules` table gains 8 columns; row size grows ~80 bytes when populated.
  Negligible.
- `condition_type` enum grows from 10 → 22 values. The
  `_RULE_CONDITION_PATTERN` regex stays readable.
- Pydantic validation gets a discriminated union for `(event_type,
  trigger)`. More schemas, but a clean pattern.
- Two evaluator wake-up patterns now coexist in the rules engine:
  event-driven (existing + signaling on_change/on_entry/on_exit) and
  cadence-driven (signaling periodic). Bounded by the cadence dispatcher.
- The "rules with `event_type IS NULL`" discriminator is slightly
  implicit — surface it as `kind` property in the API response for
  clarity.

### Migration & rollout

- Migration `040_rules_signaling_events.py` adds the columns
  and indexes. Idempotent. No data backfill required for legacy rules.
- Outbound envelope additions land same-sprint. Conformance test asserts
  that webhook payloads for legacy rules still include the historical
  fields unchanged, plus the new fields with safe defaults.
- No grandfather period needed — legacy rules continue to work
  identically; signaling rules are net-new functionality.

## Decision history

- **v1 (2026-05-17, this PR commit `cf10f17`)**: parallel
  `sensing_event_configs` table, two evaluators, two CRUD APIs.
- **v2 (2026-05-17, this PR commit on top of `f3bec20`)**: extend
  `rules`. Reasoning recorded in commit message + this section. The v1
  approach was structurally over-engineered for the 60 % overlap;
  follow-up review on the actual code showed `condition_config JSONB`
  already absorbs every shape the reference design requires.
- **v2.1 (Sprint 41 Phase A, May 2026)**: terminology rename
  "sensing" → "signaling" throughout title + body + new code +
  Sprint 41 plan, aligning with Azure Monitor's "Signal → Condition →
  Action" vocabulary that the deferred post-Sprint-41 "Rule taxonomy
  unification" ADR (roadmap Backlog) will ratify. ADR file path stays
  (`021-configurable-sensing-events.md`) for link stability; the
  historical v1 "`sensing_event_configs`" table name and the
  superseded `/sensing-events` URL block are preserved verbatim as
  historical record. Also locks in **Phase E1 deviation**: drop the
  parallel `/sensing-events` URL namespace in favour of
  `/rules?kind=signaling` so the deferred taxonomy-unification ADR
  (which plans `rules` → `alert_rules`) is not left to delete a
  permanent legacy URL.

## Open questions for Sprint 36

- Should `condition_type` stay free-string-with-pattern or migrate to a
  Postgres `ENUM` type? Lean keep as VARCHAR + pattern — easier to extend
  in future sprints without a migration per added value.
- Pydantic discriminator: `Field(discriminator="condition_type")` with 22
  variants, or a two-level discriminator on `event_type` then `trigger`?
  Lean the two-level — keeps each branch's schema small and the error
  messages targeted.
- Should the per-rule `integration_ids` (when set) **replace** the
  global broadcast, or **augment** it? Lean replace (more useful;
  operators who want broadcast leave the column null/empty).
- Default cap of 5 per `(event_type, category)`: hard reject vs. soft
  warning with admin override? Lean hard reject with admin-only override
  flag on the `POST` endpoint.
- `OverlappingZones` aggregation window: hard-coded enum (30/60/300/1800
  s) per reference, or free integer with min/max? Lean enum — matches
  reference and keeps the processor's perf envelope predictable.
