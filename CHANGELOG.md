# Changelog

All notable changes to TagPulse will be documented in this file.

## Unreleased

### Added
- **Sprint 14 — Telemetry & Location Foundations** (backend slice):
  - Migration `016_telemetry_location_rfid.py`: extends `tag_reads` with location (`latitude`, `longitude`, `location_accuracy_m`, `location_source`), structured RFID identity (`epc`, `epc_hex`, `epc_scheme`, `epc_decoded`, `tid`, `user_memory_hex`), `tag_data`, and `reader_antenna`. Adds partial indexes for location/EPC/TID. Creates `device_telemetry` hypertable + RLS policy + lookup index. Creates `telemetry_quarantine` table + RLS policy.
  - New `device_telemetry` and `telemetry_quarantine` ORM models; `TelemetryRepository` protocol + Timescale implementation.
  - New Pydantic schemas: `Location`, `Identity`, `TelemetryReading`, `TelemetryBatch`, `TelemetrySingle`, `TelemetryResponse`, `LocationPayload`, `DeviceEventPayload`. `TagReadCreate.tag_id` is now optional and defaults to `identity.epc` / `identity.tid` / `identity.epc_hex` at ingestion.
  - Pure-Python EPC decoder (`tagpulse.rfid.epc.decode_epc_hex`) for SGTIN-96/198, SSCC-96, GIAI-96/202, GRAI-96/170; returns `("raw", {})` for unknown/malformed inputs.
  - `IngestionService` now normalizes incoming reads (decodes EPC, defaults `tag_id`, applies the 4 KB `tag_data` cap with silent truncate + `_truncated=true` marker) and mirrors numeric `tag_data.*` keys as `device_telemetry` rows tagged with `{source: "tag", tag_read_id, epc, tid}`.
  - `TelemetryService` validates against per-tenant `telemetry_models`: unknown metric → quarantine `unknown_metric`; out-of-range → quarantine `out_of_range` + `telemetry.out_of_range` event for the rules engine; stale (>24 h old) or future (>5 min skew) timestamps → quarantine `stale_timestamp`; unit mismatches enriched but accepted. Standalone location updates split into `location.latitude` + `location.longitude` rows with `unit="deg"`.
  - New `POST /telemetry` route (batch ingestion) plus MQTT topic taxonomy expansion: subscriber now uses wildcard `tenants/+/devices/+/+` and dispatches `tag-reads`, `status`, `telemetry`, `location`, `events` suffixes.
  - Edge client (`clients/pi`): `RawTagRead` accepts `epc`, `epc_hex`, `tid`, `user_memory_hex`, `tag_data`, `reader_antenna`; agent forwards them under an `identity` sub-object on `tag-reads` payloads.
  - Simulator (`scripts/simulate_devices.py`): occasionally attaches GPS location, sensor-tag profile (temperature in `tag_data` + synthetic `epc_hex`), and emits standalone telemetry batches.
  - OTel counters: `telemetry_ingestion`, `telemetry_quarantined`, `location_updates`, `device_events`, `tag_data_truncations`. Usage metering middleware records `telemetry_ingestion`/`readings` for `POST /telemetry`.
  - Tests: `test_epc_decoder.py`, `test_tag_data_cap.py`, `test_telemetry_service.py`; updated `test_schemas.py` for the new optional `tag_id` contract. (194 unit tests passing.)
  - UI work for Sprint 14 lives in the `TagPulse-UI` repo and is tracked separately.
- `make export-openapi` target writes the FastAPI OpenAPI spec to `openapi.json`. Committed alongside backend changes so the `TagPulse-UI` client generator can regenerate against a known-good spec without booting a backend container.
- **Sprint 14 read endpoints** (unblocks UI work in `TagPulse-UI`):
  - `GET /telemetry` — query persisted readings filtered by `device_id`, `metric_name`, `start`, `end`, `limit`.
  - `GET /telemetry/quarantine` — list quarantined rows filtered by `device_id`, `reason`, `limit`, `offset`.
  - `GET /tag-reads` accepts new `has_location` (bool) and `epc_scheme` (str) query parameters.
  - New schema `TelemetryQuarantineResponse`. `TelemetryRepository` protocol gains `list_quarantine`. `openapi.json` regenerated.

### Changed
- Open-questions sweep across 17 design docs. Stale items (overtaken by later design work) closed; recommended items ratified as **Decisions**; ~10 genuinely open items kept under **Still open** subsections for follow-up. LLM strategy questions explicitly deferred to Phase 1 kickoff.
- Bucket-3 open-question decisions recorded:
  - **Tag reuse across tenants** (`assets-and-zones.md` §11): per-tenant uniqueness preserved; non-unique global index added to support an admin-only `GET /admin/tag-collisions` endpoint, bulk-import preflight check, and `tag_collisions_global_total` Prometheus counter. Tenant-facing surface unchanged.
  - **Lot inference from `tag_data`** (`tracking-modes.md` §11): new `tag_data_mappings` table (planned migration **020b**) with `(tenant, device_type, product)` scope precedence, instead of overloading `telemetry_models`. Admin UI gets a **Tag data fields** sub-tab on Tenant Settings (Sprint 15b).
  - **User-memory size cap** (`rfid-tag-data-model.md` §9): 4 KB inline cap with silent truncate + `tag_data._truncated=true` flag + `tag_data_truncations_total{tenant}` Prometheus counter. Quarantine reserved for malformed data, not oversized data; overflow-table opt-in is backlog.
  - **TID-binding framing** (`rfid-tag-data-model.md` §9 + new §4.4): tiered framing \u2014 *"stronger than EPC, not cryptographic."* New §4.4 **Security model** table makes per-`binding_kind` threat-model boundaries explicit. **Gen2v2 Authenticate** added as a gated roadmap backlog item with concrete trigger criteria.
  - **`tag_id` → `binding_value` rename** (`mobile-carriers-and-manifests.md` §10 Q1): both `asset_tag_bindings` (Sprint 15) and `stock_items` (Sprint 15b) ship with the column named `binding_value` from day one. The originally-discussed deprecation-window pattern (dual-serialize for one release, OpenAPI deprecation marker) is unnecessary because the tables are new in those sprints — no existing consumer to deprecate. `tag_reads.tag_id` is unrelated and stays. Roadmap sprints 15 and 15b updated; doc forward-compatibility notes simplified to naming notes.
  - **Non-RFID carriers** (`mobile-carriers-and-manifests.md` §10 Q5): adopt a generic `POST /assets/{id}/external-position` endpoint backed by a new `external_locations` hypertable (RLS, compression/retention parity with `device_telemetry`). The `asset_current_location` view UNIONs reader-derived and external positions with a `latest_position_source` column. Geofence engine remains source-agnostic so Sprint 17 rules fire on TMS positions for free. TMS vendor adapters (Samsara, Geotab, Motive) become a paid integration tier (`src/tagpulse/integrations/tms/`) gated on a paying customer; each is a thin polling service that calls the generic endpoint. New migration `021` registered in [data-models.md](docs/data-models.md).
  - **Continuous aggregates / hot-path performance** (`storage-strategy.md` §6 Q1): adopt a **deterministic `MetricsRepository` abstraction** (Option B-deterministic) rather than dashboards-only or speculative portability. Both backends first-class — Timescale impl uses continuous aggregates, PG impl uses materialized views refreshed by `pg_cron`. Selected once at startup via `DATABASE_BACKEND` config. Scope intentionally tight: only time-bucketed aggregation queries (est. 4–8 methods total). Both implementations ship in v1 with CI integration tests; review rule requires both impls in the same PR for any new `MetricsRepository` method. PG-mode scaling ceiling becomes an explicit product statement (TBD on benchmarking; expected ~1–2k devices/tenant)..
  - **Tenant DB routing** (`storage-strategy.md` §6 Q2 + ADR-008): adopt **hybrid middleware-default routing with mixed-tier capability built in from v1** (Option C). Single seam: `db_session_var: ContextVar[AsyncSession]`. Tenants carry a `db_pool_key` (default `'shared_default'`) that the middleware resolves per request via a startup-built `PoolRegistry`. Most tenants share the default pool with RLS isolation; sovereign tenants get a dedicated key pointing at a region-specific cluster. Background/admin code uses `async with tenant_context(tenant_id):`; cross-tenant operations go through a dedicated `AdminRepository` (visible in code review). Promotion shared→sovereign is a `pg_dump`-filtered data move + one row update; **no code change**. v1 scope: add `tenants.db_pool_key`, introduce `db_session_var` and `tenant_context()`, wire existing `get_session()` to populate the contextvar; pool registry ships with one entry. ADR-008 Tier-2 section expanded with the routing mechanism and a mixed-tier worked example.
  - **Tile provider strategy** (`geofencing-and-map.md` §11 Q4): adopt **C-deferred** — ship a `MapConfigResolver` abstraction now (POC default OSM public); defer production tile-provider infrastructure until first paying customer or public demo. Frontend stays provider-agnostic via `GET /tenants/me/map-config` returning `{tile_url_template, attribution, max_zoom, subdomains?}`. New `tenants.tile_provider JSONB NULL` field for per-tenant overrides. Switching providers later is a settings change, not a code change; adding a new provider is one builder function. UI footer always renders attribution; OSM-default footer note flags "dev/POC tiles—configure a production provider before public deployment." Triggers to revisit: first paying customer, public demo, or sovereign/firewalled customer.
  - **PostGIS migration trigger threshold** (`geofencing-and-map.md` §11 Q5): adopt **Option C** — latency-primary + zone-density secondary, instrumented via **OpenTelemetry** (with Prometheus exporter, per [observability.md](docs/design/observability.md) §2). ADR-013 opens when either `geofence.evaluation.duration` p99 > 10 ms **or** `geofence.candidates_per_evaluation` p95 > 50, sustained 1h for any production tenant. Active-subject count rejected as a trigger (wrong axis — affects event volume, not per-evaluation latency). Per-evaluation OTel span wraps the work for trace-level debugging. Both instruments ship with Sprint 17 alongside the geofence engine.

**Bucket-3 sweep complete.** All 10 genuinely-open questions identified across 17 design docs are now Resolved. Eight design docs (`assets-and-zones.md`, `tracking-modes.md`, `rfid-tag-data-model.md`, `mobile-carriers-and-manifests.md`, `storage-strategy.md`, `geofencing-and-map.md`, plus the previously-resolved `iot-central-gap-analysis.md` and `asset-tracking-gap-analysis.md`) now have **no open questions**. Remaining Still-open items elsewhere are intentionally deferred: LLM strategy questions (Phase 1 ADR), Tier-2 DB routing implementation (first sovereign customer), and any analytics-module specifics (per-module ADRs).

**Roadmap audit & gap fixes** ([docs/roadmap.md](docs/roadmap.md)). All bucket-3 decisions now have concrete tasks landed in their target sprints:
  - **New Sprint 13b — Multi-tier Foundations** added: `db_session_var` + `tenant_context()` + `PoolRegistry` + `tenants.db_pool_key` + `AdminRepository` + `MetricsRepository` (both Timescale + PG impls). Foundational seam for sovereign-tenant onboarding and for `MetricsRepository` consumers later.
  - **Sprint 14:** added `tag_data` 4 KB cap with silent-truncate + OTel counter (Q3).
  - **Sprint 15:** added tag-collision admin tooling (Q1), `external_locations` hypertable (migration 021) + `POST /assets/{id}/external-position` endpoint (Q5), `asset_current_location` view UNION update with `latest_position_source`, merged-source asset detail timeline. Renamed binding column to `binding_value` from day one (no deprecation dance needed since the table is new).
  - **Sprint 15b:** added `tag_data_mappings` table (migration 020b) and Tenant Settings sub-tabs ("Sensor metrics", "Tag data fields"). `stock_items.binding_value` from day one.
  - **Sprint 17a:** added `MapConfigResolver` abstraction + `tenants.tile_provider` + `GET /tenants/me/map-config` (Q9). Added OTel instrumentation for PostGIS-trigger threshold (Q10): histograms `geofence.evaluation.duration` and `geofence.candidates_per_evaluation` + trace span + Prometheus alert rule.
  - **Backlog cleanup:** "Non-RFID carrier integration" removed (resolved in Sprint 15) and replaced with paid TMS-vendor-adapter tier. "Database-per-tenant" reworded to reflect that the routing seam ships in v1. Three new backlog entries: ADR-013 (PostGIS adoption), ADR-014 (production tile provider), TileServer GL container + tooling.

### Added
- Design document: [docs/design/llm-integration-strategy.md](docs/design/llm-integration-strategy.md) — strategy + phasing for LLM/SLM integration. Server-side is the default (NL Data Explorer, summarization, rule authoring assist) via a constrained tool-calling layer in `src/tagpulse/ai/` that wraps existing repositories and inherits RLS. Edge-resident SLM is explicitly parking-lot, gated on disconnected-ops, voice-handheld, or multimodal-fusion scenarios that have no current customer demand. Backlog updated with AI Phases 1–4.
- Design document: [docs/design/mobile-carriers-and-manifests.md](docs/design/mobile-carriers-and-manifests.md) — mobile reader support (vehicle-mounted, forklift, handheld), carrier containment (`assets.parent_asset_id`, `stock_items.parent_stock_item_id`), three canonical communication patterns (manifest-only / periodic re-scan / cold-chain), edge-agent location throttling, `binding_kind='device'`. Promotes the pallet-of-cases hierarchy backlog item into Sprint 15b. Cross-links from [assets-and-zones.md](docs/design/assets-and-zones.md), [tracking-modes.md](docs/design/tracking-modes.md), [telemetry-and-location.md](docs/design/telemetry-and-location.md), and [edge-device-contract.md](docs/design/edge-device-contract.md).
- Reference document: [docs/refs/edge-hardware-and-rfid-primer.md](docs/refs/edge-hardware-and-rfid-primer.md) — RFID 101 (UHF Gen2 bands, reader anatomy, tag memory banks, GS1 EPC schemes, reader protocols), reference hardware tiers (Linux SBCs, industrial gateways, MCUs, reader-integrated platforms) mapped to ADR-011 identity phases, and a non-RFID peripheral integration guide (environmental, motion, location, vision, industrial I/O, barcode) showing how `device_telemetry`, `tag_reads.tag_data`, and the `…/events` topic absorb new sensors without schema churn.
- **Edge device reference client (`clients/pi/`):** Python package shipped to edge-device developers that enforces the on-the-wire device contract — dedup window, ENTER/EXIT state machine, batched publish, SQLite WAL ring buffer (restart-safe, size + age bounded), full-jitter exponential reconnect backoff, UTC timestamp validation, MQTT LWT, periodic heartbeat. The current reference target is a Raspberry Pi-class single-board computer, but the contract and code are hardware-agnostic and intended to run on any tag scanner / sensor gateway with Python 3.10+. Includes runnable example and 16 unit/integration tests. (Path retained for backward compatibility; rename to `clients/edge/` is tracked separately.)
- Design document: [docs/design/asset-tracking-gap-analysis.md](docs/design/asset-tracking-gap-analysis.md) — end-to-end gap audit against the home-grown edge-device asset-tracking goal (location, sensor telemetry, asset/zone model, device identity, MQTT topic taxonomy).
- Reference document: [docs/azure-iot-asset-tracking.md](docs/azure-iot-asset-tracking.md) — Azure-equivalent architecture for asset tracking.
- **UI Authentication (Sprint 13):** Two-mode login page — API Key (email + key → full role-based access) and Tenant ID (backward-compat viewer access)
- `POST /auth/login` endpoint — exchanges email + API key for a 1-hour JWT access token
- JWT authentication in `get_current_user` middleware (JWT → API key → X-Tenant-ID priority)
- `JWT_SECRET` and `JWT_EXPIRY_SECONDS` configuration settings
- Login rate limiting (5 attempts/minute per IP) on `POST /auth/login`
- `RoleGuard` component and `useCanPerform()` hook for role-based UI rendering
- Role guards on all mutation actions: device decommission (admin), create/edit rules (editor+), delete rules (admin), create integrations (editor+), delete integrations (admin), create telemetry models (editor+), delete telemetry models (admin), acknowledge alerts (editor+)
- Usage menu item hidden for non-admin users in sidebar
- User profile display in header (name, role badge, tenant name)
- JWT token expiry handling — auto-logout on expired/revoked tokens
- API client sends `Authorization: Bearer <JWT>` when logged in via API key
- 15 unit tests for JWT creation/decode, login schemas, rate limiting, and API key verification
- `PyJWT>=2.8` dependency added to pyproject.toml
- `/auth` and `/users` nginx proxy routes for Docker deployment
- Design document: [docs/design/ui-authentication.md](docs/design/ui-authentication.md)

### Changed
- Auth context expanded: `user`, `role`, `accessToken`, `isAuthenticated`, `loginWithApiKey()`, `loginWithTenantId()`
- Login page redesigned with Ant Design Tabs (API Key tab default, Tenant ID tab secondary)
- Sidebar menu items filtered by role (Usage visible to admin only)
- API client upgraded: JWT Bearer token takes priority over X-Tenant-ID header

### Fixed
- Navigation highlight: "Telemetry Models" no longer incorrectly highlights "Telemetry" (longest-prefix-match fix)

---

## Previous Unreleased

### Added
- Audit logging now records `user_id` to attribute who made each change (migration 015)
- Role-based permission matrix: admin (full), editor (create/update), viewer (read-only) on device, rule, integration, telemetry model, and admin routes
- `count_alerts_since` repository method for computing device error rates
- Unit tests for user routes, provisioning schemas, and admin ops (test_user_routes.py, test_provisioning.py, test_admin_ops.py)
- Remote IoT testing guide: ngrok tunneling for HTTP API and MQTT broker (quickstart)
- Corporate proxy (WSL) troubleshooting section in quickstart guide

### Changed
- Device health `error_rate` now computed from alert-to-read ratio instead of hardcoded 0.0
- Routes migrated from `get_current_tenant` to `require_role()` for proper access control

### Fixed
- Docker Compose: added `api` network alias to `app` service so the UI nginx proxy can resolve `http://api:8000`
- TagPulse-UI Dockerfile: created nginx cache directories with correct ownership for non-root operation
- TagPulse-UI nginx.conf: set `pid /tmp/nginx.pid` to allow non-root nginx process
- Migration 001: composite primary key `(id, timestamp)` on `tag_reads` for TimescaleDB 2.26+ hypertable compatibility
- Migration 006: composite primary key `(id, triggered_at)` on `alerts` for TimescaleDB 2.26+ hypertable compatibility

### Added (prior)
- Core ingestion pipeline: MQTT subscriber + HTTP push endpoint (Sprint 1)
- Tag read Pydantic schemas with validation (Sprint 1)
- TimescaleDB schema with hypertable for tag reads (Sprint 1)
- Alembic migrations with async support (Sprint 1)
- EventBus: capacity-limited async pub/sub with overflow policies (Sprint 1)
- Device registry: CRUD API for readers at `/device-registry` (Sprint 2)
- Device configuration profiles: metadata + configuration JSONB (Sprint 2)
- Device status tracking: connection state, firmware version, last seen (Sprint 2)
- MQTT status topic handling: `devices/{device_id}/status` (Sprint 2)
- Telemetry model definitions: per-device-type metric schemas (Sprint 2)
- Tag read query API with filters and pagination (Sprint 3)
- Aggregations: reads per hour, unique tags per time window (Sprint 3)
- Live telemetry API: recent reads per device (Sprint 3)
- Device health API: connectivity, last-seen, reads/hour (Sprint 3)
- Docker Compose: app + TimescaleDB + Mosquitto for local dev (Sprint 4)
- Dockerfile: multi-stage build with non-root user (Sprint 4)
- GitHub Actions CI: lint + typecheck + test (Sprint 4)
- Structured JSON logging with request ID correlation (Sprint 4)
- CONTRIBUTING.md with branch naming and PR expectations (Sprint 4)
