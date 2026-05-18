# Design Document: End-to-End Asset Tracking Gap Analysis

**Date:** 2026-05-01
**Status:** draft
**Related:** [docs/azure-iot-asset-tracking.md](../azure-iot-asset-tracking.md), [docs/design/iot-central-gap-analysis.md](iot-central-gap-analysis.md), [docs/design/storage-strategy.md](storage-strategy.md), [docs/refs/edge-hardware-and-rfid-primer.md](../refs/edge-hardware-and-rfid-primer.md)

---

## 1. Goal

Deliver an end-to-end asset tracking solution where **home-grown edge
devices** (tag scanner + optional sensors) report on physical assets to the
TagPulse backend. The current reference target is a Raspberry Pi-class
single-board computer running the [`clients/pi/`](../../clients/pi/)
reference client, but TagPulse treats this as **one experiment among many** —
the contract, schema, and wire format are hardware-agnostic and any
MQTT-/HTTP-capable tag scanner or sensor gateway is a valid producer.

Per-event payloads from an edge device may include:

- RFID tag reads (asset identity, signal strength)
- **GPS / location coordinates** (for mobile or vehicle-mounted readers)
- **Temperature** and other environmental sensor data
- Device-local state (battery, uptime, firmware, connectivity)

The goal is for an operator to answer questions like:

- *Where is asset X right now? Where has it been?*
- *Which assets are inside zone Y? Which left in the last hour?*
- *Show temperature history for cold-chain asset Z; alert on excursions.*
- *Which scanners are online, and what's their last known position?*

Device firmware is **out of scope for this repo** — but the **wire contracts,
device identity, and backend data model that every edge device depends on
are in scope.**

---

## 2. What We Have Today

| Capability | Status | Where |
|---|---|---|
| HTTP + MQTT ingestion of `tag_read` events | done | `ingestion/`, `api/routes/ingestion.py` |
| Device registry (CRUD, status, last-seen) | done | `api/routes/devices.py` |
| Device self-provisioning + admin approval | done | `api/routes/provisioning.py` |
| Tenant isolation (RLS, per-tenant API keys, per-tenant MQTT topics) | done | Sprint 5 |
| Telemetry **model definitions** (per device-type metric schema) | done | `models/schemas.py::TelemetryModelCreate` |
| Per-tag-read free-form `sensor_data` JSONB column | done | `migrations/001_initial_schema.py` |
| Rules + alerts (threshold, absence, rate change) | done | Sprint 6 |
| Read-frequency analytics + anomaly flagging | done | Sprint 7 |
| Webhooks / SSE / external API | done | Sprint 8 |
| Audit logging, dead letters, observability | done | Sprints 10–11 |

The simulator (`scripts/simulate_devices.py`) already shoves `temperature`,
`humidity`, and `battery_pct` into `sensor_data` — so the JSONB pipe works
end-to-end, but **the platform has no first-class understanding of those fields**.

---

## 3. Gap Summary

| # | Capability | Status | Priority | Where it lands |
|---|---|---|---|---|
| A1 | First-class **location** on tag reads (lat/lon, accuracy, source) | missing | **P1** | Schema + ingestion |
| A2 | First-class **sensor telemetry** stream (separate from tag reads) | missing | **P1** | New hypertable + topic |
| A3 | **Asset** entity (the thing being tracked, distinct from the reader) | missing | **P1** | New table + API |
| A4 | **Zone / site / geofence** model | missing | **P1** | New tables, geo index |
| A5 | Device-side contract for **edge filtering / dedup / ENTER-EXIT** | undocumented | **P1** | Edge device spec |
| A6 | **Per-device identity** stronger than shared API key (X.509 / per-device key) | partial | **P1** | Provisioning extension |
| A7 | MQTT topic taxonomy beyond `tag-reads` / `status` (sensors, location, events) | missing | **P1** | Topic design |
| A8 | **Spatial queries** (assets in zone, path history, nearest reader) | missing | **P2** | PostGIS or Timescale geo |
| A9 | **Map visualization** in admin UI | missing | **P2** | TagPulse-UI |
| A10 | **Geofence rules** (alert when asset enters/exits zone, dwell time) | missing | **P2** | Rules engine extension |
| A11 | **Offline buffering / store-and-forward** on the edge device | not in repo | **P2** | Edge device spec |
| A12 | **Cloud-to-device commands** (reconfigure edge devices remotely) | backlog (G8) | **P3** | Already tracked |
| A13 | Cold-chain / excursion-specific analytics module | missing | **P3** | New analytics plugin |

P1 = required for a credible end-to-end demo. P2 = required for production
asset-tracking value. P3 = nice-to-have / domain-specific.

---

## 4. P1 Gaps — Detail and Proposed Remediation

### A1. First-class location on tag reads

**Problem.** `tag_reads` has `signal_strength` and a free-form `sensor_data`
JSONB. A scanner mounted on a forklift sending GPS has nowhere structured to
put it; downstream consumers must reach into JSON with no schema guarantees.

**Proposal.** Add nullable, indexed columns to `tag_reads`:

```
ALTER TABLE tag_reads
  ADD COLUMN latitude        DOUBLE PRECISION NULL,
  ADD COLUMN longitude       DOUBLE PRECISION NULL,
  ADD COLUMN location_accuracy_m DOUBLE PRECISION NULL,
  ADD COLUMN location_source VARCHAR(20) NULL;  -- 'gps' | 'fixed' | 'inferred'
```

Update `TagReadCreate` with an optional `Location` sub-model. Keep `sensor_data`
JSONB for truly free-form payloads. **No PostGIS dependency yet** — store
plain lat/lon; add PostGIS in A8 only if spatial queries demand it.

### A2. First-class sensor telemetry stream

**Problem.** An edge device may report temperature **without** an RFID tag
read (e.g., ambient warehouse temperature every 60 s). Today there is no
place for it — `tag_reads` requires a `tag_id`. Stuffing sensor-only data
into `sensor_data` with a fake tag is a hack and breaks aggregations.

**Proposal.** New hypertable `device_telemetry`:

```
device_telemetry (hypertable on `timestamp`)
--------------------------------------------
device_id     UUID NOT NULL
tenant_id     UUID NOT NULL          -- RLS
timestamp     TIMESTAMPTZ NOT NULL
metric_name   VARCHAR(100) NOT NULL  -- 'temperature', 'battery_pct', ...
metric_value  DOUBLE PRECISION NOT NULL
unit          VARCHAR(20) NULL
metadata      JSONB NULL
```

Validated against the existing `telemetry_models` definitions per device type
(unknown metric names rejected or quarantined; min/max range checks emit
`telemetry.out_of_range` events that the rules engine can consume).

New ingestion paths:

- `POST /telemetry` (HTTP)
- MQTT topic `tenants/{tenant_id}/devices/{device_id}/telemetry`

### A3. Asset entity

**Problem.** Today, assets exist only as opaque `tag_id` strings on tag-read
events. There is no record of *what* asset that tag is bound to, who owns it,
its current location, or its history as a first-class object.

**Proposal.** New `assets` table + lightweight bindings:

```
assets
------
id              UUID PK
tenant_id       UUID FK
external_ref    VARCHAR(255) NULL   -- ERP/WMS asset code
name            VARCHAR(255) NOT NULL
asset_type      VARCHAR(50) NOT NULL    -- 'pallet' | 'tool' | 'container' | ...
status          VARCHAR(20) NOT NULL    -- 'active' | 'retired' | 'lost'
metadata        JSONB
created_at      TIMESTAMPTZ
updated_at      TIMESTAMPTZ

asset_tag_bindings
------------------
asset_id        UUID FK
tag_id          VARCHAR(256) NOT NULL
bound_at        TIMESTAMPTZ NOT NULL
unbound_at      TIMESTAMPTZ NULL    -- NULL = current binding
PRIMARY KEY (asset_id, tag_id, bound_at)
```

A view `asset_current_location` joins the most recent `tag_read` for each
bound tag. Tag bindings change over time (tags fail, get re-applied) —
historical bindings preserve provenance.

> **Schema-evolution note (2026-05, post-Sprint 41).** The `asset_type`
> `VARCHAR(50)` column was implemented in Sprint 13 (migration 018) but is
> **no longer current.** [ADR 019](../adr/019-categories.md) (Sprint 34)
> replaced it with a first-class `categories` table + required
> `assets.category_id` FK; Sprint 41 Phase H (migration
> [041_drop_assets_asset_type](../../migrations/versions/041_drop_assets_asset_type.py))
> dropped the column outright. See [docs/data-models.md](../data-models.md)
> for the current `assets` schema. The proposal above is preserved verbatim
> as a historical record of the original Sprint-13 design.

### A4. Zone / site / geofence model

**Problem.** "DockDoor-3" and "zone-A" exist only as freeform strings in
`devices.metadata`. Rules engine can't say "alert when asset leaves zone X."

**Proposal.** Two-level hierarchy:

```
sites   (id, tenant_id, name, address, default_timezone)
zones   (id, site_id, name, kind, polygon_geojson NULL, fixed_reader_ids JSONB NULL)
```

Two zone modes:

1. **Reader-bound zones** — a zone is defined by which reader(s) cover it.
   ENTER/EXIT inferred from reader transitions (works without GPS).
2. **Geofence zones** — a polygon. Asset is "in zone" when its last known
   `(latitude, longitude)` is inside the polygon. Requires A1 and (for
   nontrivial cases) A8.

Reader-bound zones are sufficient for the first cut and unblock A10.

### A5. Device-side contract for edge processing

**Problem.** RFID readers naturally produce duplicate reads at high frequency.
If the edge device forwards every raw read, we burn ingestion quota and
downstream queries become useless. Today there is **no documented contract**
for what an edge device must do before publishing.

**Proposal.** A reference Python implementation now lives in
[`clients/pi/`](../../clients/pi/) (path retained from the initial Raspberry
Pi experiment; the code itself is hardware-agnostic). It is shipped to
edge-device developers and enforces the contract on the wire so every device
— Pi, industrial gateway, or third-party scanner — behaves identically. The
contract:

- **De-dup window:** suppress identical `(tag_id, reader_antenna)` reads within
  N seconds (default 5 s; configurable via device `configuration` JSON).
- **ENTER/EXIT semantics:** publish one event when a tag first appears, one
  when it has been absent for `exit_timeout_s` (default 10 s).
- **Batching:** up to 100 events or 1 s, whichever first; one MQTT publish.
- **Offline buffer:** local SQLite ring buffer, drained on reconnect, max
  age 24 h (covered by A11).
- **Time:** all timestamps must be UTC and NTP-synced; backend rejects events
  more than 24 h old or more than 5 min in the future.
- **Heartbeat:** publish `status` every 60 s with `connection_state`,
  `firmware_version`, `uptime_s`, `queue_depth`.

These constants live in the device's `configuration` so they can be tuned per
deployment without firmware changes (and are deliverable via A12 later).

The reference implementation provides:

| Module | Purpose |
|---|---|
| `tagpulse_edge.config.EdgeConfig` | All knobs in one dataclass; loadable from JSON |
| `tagpulse_edge.dedup.PresenceTracker` | Pure-logic dedup + ENTER/EXIT state machine |
| `tagpulse_edge.buffer.Outbox` | SQLite WAL ring buffer (size + age bounded, restart-safe) |
| `tagpulse_edge.clock.ClockGuard` | UTC normalization + max-age / max-skew validation |
| `tagpulse_edge.transport.MqttTransport` | paho-mqtt wrapper with full-jitter exponential backoff and LWT |
| `tagpulse_edge.agent.EdgeAgent` | Orchestrator; hardware loop calls `submit_tag_read` / `submit_telemetry` / `submit_location` and never blocks on the network |

Pure-logic modules are unit-tested; the agent has a fake-publisher
integration test. The example in `clients/pi/examples/run_reader.py`
exercises the full pipeline against any local MQTT broker.

### A6. Stronger per-device identity

**Problem.** Provisioning today uses a tenant-scoped pre-shared key, then
issues a long-lived token. For fleet-scale edge-device deployments, a stolen
device (or its token) can impersonate any registered device until manually
revoked. There is no per-device cryptographic identity.

**Proposal (incremental).**

- Phase 1 (now): rotate the per-device token on every approval; expose
  `POST /device-registry/{id}/rotate-token` (admin-only).
- Phase 2: support **X.509 client certs** for MQTT (mTLS). Backend stores
  the device's cert thumbprint; broker enforces mTLS; `device_id` derived
  from cert subject. ADR required.
- Phase 3 (optional): hardware-backed keys (TPM / DICE / Secure Element)
  where the platform supports it — Pi 4/5+ via fTPM, industrial gateways
  via discrete TPM 2.0, embedded SoCs via SE.

Phase 1 unblocks the demo; Phase 2 is the production target.

### A7. MQTT topic taxonomy

**Problem.** Today only `…/tag-reads` and `…/status` exist. There is no
topic for sensor-only telemetry, location updates, or device-side events
(e.g., "buffer drained", "GPS fix lost").

**Proposal.** Extend the topic tree:

```
tenants/{tenant_id}/devices/{device_id}/tag-reads     # existing
tenants/{tenant_id}/devices/{device_id}/status        # existing
tenants/{tenant_id}/devices/{device_id}/telemetry     # NEW: sensor metrics (A2)
tenants/{tenant_id}/devices/{device_id}/location      # NEW: location-only (A1)
tenants/{tenant_id}/devices/{device_id}/events        # NEW: device-side events
```

Subscriber filter `tenants/+/devices/+/+` already covers all of these; the
existing `_parse_topic` helper just needs the new topic-type branches.

---

## 5. P2 Gaps — Brief

- **A8 spatial queries** — start with naive bounding-box filters in SQL; add
  PostGIS only if/when polygon containment or distance queries become hot.
  ADR required if PostGIS is adopted.
- **A9 map visualization** — TagPulse-UI gets a `Map` page (Leaflet +
  OpenStreetMap tiles, no API key required) showing live asset positions
  and a path-replay control. Tracked separately in TagPulse-UI repo.
- **A10 geofence rules** — extend the rules engine with two condition types:
  `zone.entered`, `zone.exited`, `zone.dwell_exceeded`. Producers: ingestion
  emits `asset.zone_changed` events when a tag read crosses a zone boundary
  (reader-bound or geofence).
- **A11 offline buffering on the edge device** — already tracked as backlog
  item G10. Backend support is already in place (idempotent ingest via event
  id, late timestamps accepted within 24 h per A5).

---

## 6. Out of Scope (Explicit)

- Edge-device firmware itself (separate repo / per-vendor work).
- BLE / UWB / computer-vision tracking (covered as alternatives in
  [azure-iot-asset-tracking.md](../azure-iot-asset-tracking.md) §12).
- Hosting topology on Azure (IoT Hub vs broker, ADX vs Timescale) — TagPulse
  is the self-hosted alternative; Azure-equivalent mapping lives in the
  reference doc.

---

## 7. Phasing Proposal

| Phase | Scope | Sprints |
|---|---|---|
| **14 — Telemetry & Location** | A1, A2, A7 (topics for both), update simulator | one sprint |
| **15 — Assets & Zones** | A3, A4 (reader-bound only), `asset_current_location` view | one sprint |
| **16 — Edge Contract & Identity** | A5 (doc + ADR), A6 phase 1, provisioning rotate-token | one sprint |
| **17 — Geofencing & Map** | A8 (basic), A10, A9 (TagPulse-UI), A6 phase 2 (mTLS) | two sprints |

Each phase ends with simulator updates so the system can be exercised
end-to-end before any specific edge-device firmware is ready.

---

## 8. Decisions (resolved)

The original Sprint 14 open questions have all been answered by subsequent design work:

| # | Question | Resolution |
|---|---|---|
| 1 | One asset, multiple tags? | **Yes**, via lifecycle bindings — see [assets-and-zones.md §3.2](assets-and-zones.md). |
| 2 | Sensor-only `device_type`? | Hardware terminology is now generalized; `device_type` already supports this. See [docs/refs/edge-hardware-and-rfid-primer.md](../refs/edge-hardware-and-rfid-primer.md). |
| 3 | GPS cadence | **Both allowed** — inline on `tag_reads` and on the lower-cadence `…/location` topic. See [telemetry-and-location.md](telemetry-and-location.md). |
| 4 | mTLS broker selection | Tracked under [identity-device-provisioning.md](identity-device-provisioning.md) and ADR-011 (mTLS is Phase 2). |
| 5 | Retention | Resolved in [storage-strategy.md](storage-strategy.md) and [telemetry-and-location.md §11](telemetry-and-location.md): 90-day default + Timescale compression after 7 days. |
