# Changelog

All notable changes to TagPulse will be documented in this file.

## Unreleased

### Added
- **Sprint 15b — Phase E (rules / workers / imports / simulator / metering)**:
  - **Rules engine — inventory conditions**: `RuleCreate` / `RuleUpdate` now accept `stock.below_threshold`, `stock.expiring_within`, `stock.unexpected_in_zone` in `condition_type`. Backed by new Pydantic models `StockBelowThresholdCondition`, `StockExpiringWithinCondition`, `StockUnexpectedInZoneCondition`.
  - **Event-driven branch**: `RuleEvaluator.on_subject_zone_changed` now subscribes to `Topic.SUBJECT_ZONE_CHANGED` (in addition to `TAG_READ_CREATED`) and fires alerts for `stock.unexpected_in_zone` rules whenever a `stock_item` enters a zone outside its configured `allowed_zone_ids`. Rules can be scoped per-product or apply to all products in the tenant.
  - **InventoryRuleWorker** (`src/tagpulse/workers/inventory_rule_worker.py`): periodic background scanner.
    - Every 60 s: evaluates `stock.below_threshold` rules across all tenants by querying the `stock_levels` view (filtered by product/lot/zone) and firing an alert when the aggregate drops under the rule threshold.
    - Once per UTC day: evaluates `stock.expiring_within` rules by scanning `lots` for rows whose `expires_at` is within `days` of now (optionally filtered by `product_id`); records a single alert per rule listing the matching lot IDs.
    - Once per UTC day: writes the per-tenant `stock_items_active` metering snapshot.
    - New `RulesService.get_active_rules_by_condition_type` and `get_active_rules_by_condition_types_all_tenants` helpers feed both the worker and the evaluator's zone-changed branch.
  - **Bulk CSV import endpoints** (admin-only, mounted via new `inventory_imports` router): `POST /products/import`, `POST /lots/import`, `POST /stock-items/import`. UTF-8 / UTF-8-BOM tolerant, 5 MiB / 10 000 row caps, idempotent on duplicate keys (skipped not failed). The stock-items endpoint accepts `?preflight=true` to return cross-tenant `binding_value` collision counts (via `TimescaleAssetTagBindingRepository.count_other_tenant_collisions`) without writing any rows — wires the tag-collision admin tooling shipped in Phase B into the bulk-onboarding workflow per `docs/design/assets-and-zones.md` §11 Q3.
  - **Inventory simulator** `scripts/simulate_inventory.py`: end-to-end exercise of the inventory branch — seeds a 3-product catalog with valid GTIN-14s, creates a near-expiry lot per product, registers a tenant-scope `tag_data_mapping` (`tag_data.lot → lot_code`), then streams SGTIN-96 EPCs that decode back to the catalog (so ingestion auto-creates `stock_items`, emits zone transitions, and feeds the worker scans).
  - **Metering — new dimensions**:
    - `inventory_movements` (counter, unit `events`): incremented by `IngestionService` whenever a `stock_movements` row is appended on a zone transition. Wired through a new optional `usage_meter` parameter on `IngestionService` + a new `get_usage_meter_optional` FastAPI dependency reading `app.state.usage_meter`. The MQTT subscriber now also receives the meter and forwards it into the per-message ingestion service.
    - `stock_items_active` (gauge, unit `items`): recorded once per UTC day by the worker via the new `UsageMeter.record_snapshot(tenant_id, dimension, unit, value)` method, which uses `INSERT ... ON CONFLICT DO UPDATE SET quantity = :qty` (replace, not sum) so gauges don't accumulate across writes.
  - `python-multipart` added to runtime dependencies (required by FastAPI's `UploadFile`).
  - 9 new unit tests in `tests/unit/test_phase_e_inventory_rules.py` covering schema acceptance, evaluator zone-changed fire/skip/asset-ignore branches, worker below-threshold fire/skip, and CSV BOM/timestamp parsing helpers. **269 passing total.**

### Changed
- **Sprint 15 — Phase A-C audit mitigations (asset-tracking hardening)**:
  - Migration `022_phase_abc_hardening.py`:
    - `external_locations.asset_id` now has an explicit `ON DELETE CASCADE` FK to `assets.id` (was completely missing — orphan / cross-tenant asset_ids could be persisted).
    - GIN partial index `ix_zones_fixed_readers_gin ON zones (fixed_reader_ids) WHERE kind = 'reader_bound'` so `TimescaleZoneRepository.get_zone_for_reader`'s JSONB `@>` lookup stops sequential-scanning the zones table on every fixed-reader read.
    - `ck_zones_kind_payload` tightened to require `jsonb_array_length(fixed_reader_ids) > 0` for `reader_bound` zones — empty lists silently bypassed `IS NOT NULL` and broke `get_zone_for_reader`.
  - `SiteModel`, `ZoneModel`, `AssetModel`: `updated_at` columns now carry `onupdate=func.now()` (was missing on all three — `updated_at` always equalled `created_at`, breaking any UI "last modified" indicator).
  - `AssetService.load_onto_carrier`: multi-step containment-cycle guard. The previous self-loop check only caught `asset_id == parent_asset_id`; an `A→B→A` request would silently form a loop that hung the recursive CTE in `get_descendants` (and hence `GET /assets/{id}/manifest`). New `_assert_no_parent_cycle` walks the proposed parent's ancestry (capped at 64 hops as a safety net for already-corrupt data).
  - `IngestionService` asset-tracking branch:
    - `(tenant, binding_value) → (asset_id, binding_id)` cache (bounded LRU) eliminates the per-read `get_active_by_value` round-trip; misses are cached too so floods of unbound EPCs no longer hammer the DB.
    - `(tenant, device_id) → mobility` cache (bounded LRU) eliminates the per-read `device_repo.get` round-trip.
  - `ZoneCreate` / `ZoneUpdate` Pydantic schemas now reject inconsistent payloads at construction:
    - `ZoneCreate`: `kind='reader_bound'` requires non-empty `fixed_reader_ids`; `kind='geofence'` requires `polygon_geojson`. The route handler's manual checks were removed in favour of this single source of truth.
    - `ZoneUpdate`: `fixed_reader_ids=[]` is rejected up-front instead of failing later at the DB layer.
  - `GET /assets/{asset_id}/external-positions` now returns `404` when the asset doesn't exist in the tenant (was silently returning `[]`, inconsistent with `POST .../external-position`).
  - 8 new unit tests covering carrier-cycle prevention (direct + multi-step + happy path), Zone schema validators (create + update), binding cache hit count (hits + misses), and device-mobility cache hit count. **260 passing total.**
  - **Deferred to Sprint 17 UI**: `tenants.tracking_modes` admin API/UI — the ingestion guard exists but tenants cannot flip the value yet (default `['asset']`).


- **Sprint 15b — Phase D audit mitigations (inventory hardening)**:
  - Migration `021_inventory_hardening.py`:
    - `stock_items.lot_id` FK now `ON DELETE SET NULL` (was implicit `NO ACTION` blocking lot deletes).
    - `stock_movements.stock_item_id` now has an explicit `ON DELETE RESTRICT` FK to `stock_items.id` (was orphan-prone).
    - Adds partial indexes `ix_stock_movements_from_zone` / `ix_stock_movements_to_zone` (`WHERE X_zone_id IS NOT NULL`, ordered by `occurred_at DESC`) so per-zone history queries no longer scan the whole hypertable.
    - `stock_levels` view recreated `WITH (security_invoker = true)` so RLS policies are enforced as the calling tenant, not the view owner.
    - `ck_tag_data_mappings_scope_kind` tightened to `IN ('tenant','product')` — `'device_type'` is dead code (no resolver wired anywhere) and is removed from the schema, the Pydantic `Literal`, and the migration check.
  - `IngestionService`:
    - Inventory branch is now gated on the tenant's `tracking_modes` containing `'inventory'` (in-process LRU-cached, bounded). Tenants on `'asset'`-only mode no longer pay the SGTIN→product lookup cost.
    - GTIN→product_id lookups are cached per `(tenant, gtin)` (bounded LRU); both hits and misses are cached so unmapped SGTIN floods don't hammer the DB.
    - `_LAST_ZONE_BY_ASSET` and `_LAST_ZONE_BY_STOCK_ITEM` writes now go through a bounded FIFO setter — caches can no longer grow without limit on long-lived workers.
    - New `tenant_repo: TimescaleTenantRepository | None` constructor parameter, wired through `get_ingestion_service` and `MQTTSubscriber._build_ingestion_service`. When omitted (older test rigs) the inventory branch behaves as before.
  - `TagDataMappingCreate` now refuses inconsistent payloads at the schema layer: `scope_kind='tenant'` requires `scope_id IS NULL`, all other scopes require a non-null `scope_id` (matches the DB check).
  - `InventoryService.delete_product` now blocks deletion when **any** stock_items reference the product (not only non-terminal ones), mirroring the FK reality and avoiding 500s on orphaned-history products.
  - 6 new unit tests in `tests/unit/test_ingestion_inventory.py` cover tracking-mode gating, GTIN cache hit count, `enter` vs `transfer` movement_type, lot-mapping product→tenant fallthrough, and the auto-create race recovery path. **252 passing total.**

### Added
- **Sprint 15b — Inventory Tracking (Phase D.5): ingestion inventory branch**:
  - `IngestionService` now resolves SGTIN reads to a registered product (GTIN-14 derived from the EPC's company_prefix + item_ref via the new `tagpulse.rfid.epc.gtin14_from_decoded` helper, mod-10 check digit per GS1 §7.9), auto-creates a `stock_item` on the first sighting (lot inferred from `tag_data` via `tag_data_mappings` — most-specific scope wins, product > tenant), bumps `current_zone_id` + `last_seen_at` on every observation, and on a zone transition appends a `stock_movements` row (`enter` for first-known zone, `transfer` otherwise) plus emits `Topic.SUBJECT_ZONE_CHANGED` with `subject_kind='stock_item'`.
  - Process-local `_LAST_ZONE_BY_STOCK_ITEM` cache mirrors the asset cache; multi-worker durability deferred to Sprint 17 alongside the rules engine (per `docs/design/assets-and-zones.md` §5).
  - Mobile readers and missing inventory repos short-circuit cleanly — `stock_item` is still auto-created on first SGTIN sighting, but no zone resolution / movement / event is emitted.
  - New `TimescaleStockItemRepository.record_observation(tenant_id, stock_item_id, *, zone_id, observed_at) -> (prev_zone, new_zone)` for the ingestion hot-path.
  - DI factory (`get_ingestion_service`) and the MQTT subscriber (`MQTTSubscriber._build_ingestion_service`) now wire all five inventory repos into the service.
  - New OTel counters: `tagpulse_stock_items_auto_created_total`, `tagpulse_stock_movements_recorded_total`, `tagpulse_inventory_unmapped_sgtin_total`. The existing `tagpulse_subject_zone_changed_total` now carries `subject_kind='stock_item'` in addition to `'asset'`.
  - 7 new unit tests in `tests/unit/test_ingestion_inventory.py` (246 passing total).
- **Sprint 15b — Inventory Tracking (Phase D): products, lots, stock items, movements, mappings**:
  - Migration `020_inventory.py`: creates `products` (unique `(tenant_id, sku)`, partial index on GTIN, `unit ∈ {each,case,pallet}`), `lots` (unique `(tenant_id, product_id, lot_code)`, partial index for upcoming expirations), `stock_items` (`binding_kind ∈ {epc,tid}`, `state ∈ {in_stock,in_transit,consumed,expired,lost}`, partial unique index on active bindings, aggregation index by `(tenant, product, lot, zone)`), `stock_movements` hypertable on `occurred_at` (`movement_type ∈ {enter,exit,transfer,consume}`), `tag_data_mappings` (with check constraints binding `scope_id` consistency to `scope_kind ∈ {tenant,device_type,product}`). Adds `stock_levels` view (`SELECT product_id, lot_id, current_zone_id, COUNT(*) WHERE state='in_stock'`). All tables enable RLS with `tenant_isolation` policies.
  - ORM: `ProductModel`, `LotModel`, `StockItemModel`, `StockMovementModel`, `TagDataMappingModel`. Pydantic: `Product*`, `Lot*`, `StockItem*`, `StockMovementResponse`, `StockLevelRow`, `TagDataMapping*`.
  - Repositories: `TimescaleProductRepository` (delete blocks when active stock items reference the product → 409), `TimescaleLotRepository` (with `expiring_within_days` filter), `TimescaleStockItemRepository` (`get_active_by_binding` for ingestion hot-path; `stock_levels` query against the view), `TimescaleStockMovementRepository`, `TimescaleTagDataMappingRepository`.
  - `InventoryService` with full audit coverage (`product.created/updated/deleted`, `lot.created/updated`, `stock_item.created/updated`, `tag_data_mapping.created/deleted`).
  - Routes (mounted at `/products`, `/products/{id}/lots`, `/lots/{id}`, `/stock-items`, `/stock-levels`, `/stock-movements`, `/tag-data-mappings`): viewer+ reads; editor+ stock-item & lot writes; admin-only product CRUD and tag-data-mapping management.
  - 9 new unit tests in `tests/unit/test_inventory_service.py` (239 passing total).
  - **Deferred (Phase D.2)**: ingestion inventory branch — SKU lookup by GTIN, lot inference from `tag_data` via `tag_data_mappings`, auto-create `stock_item` on first SGTIN read, append `stock_movements` row + emit `subject.zone_changed` (`subject_kind='stock_item'`). Mirrors the B/B.2 split.
- **Sprint 15 — Asset Tracking (Phase C): external positions + carrier semantics**:
  - `external_locations` hypertable (migration 019) for non-RFID position fixes — `(tenant_id, asset_id, recorded_at, latitude, longitude, source, accuracy_meters?, speed_kph?, heading_deg?, metadata)`, RLS by `tenant_id`, hypertable on `recorded_at`. Lat/lon range checks at the DB layer.
  - `POST /assets/{asset_id}/external-position` (editor+) and `GET /assets/{asset_id}/external-positions` (viewer+) — generic non-RFID position ingestion. Emits `Topic.EXTERNAL_LOCATION_RECORDED`.
  - Carrier semantics endpoints: `POST /assets/{id}/load` (attach to parent carrier), `POST /assets/{id}/unload` (detach), `GET /assets/{id}/manifest` (recursive containment tree). Both load/unload are idempotent and emit `Topic.ASSET_LOADED` / `Topic.ASSET_UNLOADED` per [mobile-carriers-and-manifests.md §6](docs/design/mobile-carriers-and-manifests.md).
  - Manifest built via recursive CTE on `assets.parent_asset_id`; tenant_id enforced at every level.
  - New OTel counters: `tagpulse_external_locations_recorded_total` (with `source` attribute), `tagpulse_asset_load_operations_total` (with `op` attribute).
  - 11 new unit tests in `tests/unit/test_carrier_external.py` (230 passing total).
- **Sprint 15 — Asset Tracking (Phase B.2): ingestion enrichment + zone transitions**:
  - `IngestionService` now resolves the active asset binding for incoming tag reads (tries `identity.epc`, then `identity.tid`, then `tag_id`) and looks up the reader-bound zone for fixed devices. Mobile readers (`device.mobility = 'mobile'`) skip the zone lookup per [mobile-carriers-and-manifests.md §4.1](docs/design/mobile-carriers-and-manifests.md).
  - On a zone transition, publishes `Topic.SUBJECT_ZONE_CHANGED` (`subject_kind='asset'`, with `from_zone_id`/`to_zone_id`/`tag_read_id`) onto the event bus. Process-local last-zone cache (per design §5; multi-worker durability deferred to Sprint 17 alongside the rules engine).
  - DI factory and MQTT subscriber now inject `TimescaleAssetTagBindingRepository` + `TimescaleZoneRepository` into the ingestion service.
  - `DeviceResponse.mobility` exposed on the API + repo response mapper so consumers can render fixed-vs-mobile state.
  - New OTel counters: `tagpulse_tag_reads_without_asset_total`, `tagpulse_subject_zone_changed_total`.
- **Sprint 15 — Asset Tracking (Phase B): assets, tag bindings, collision tooling**:
  - Migration `018_assets_bindings.py`: `assets` (with `parent_asset_id` self-FK for carrier containment, `external_ref` unique per tenant, `status ∈ {active,retired,lost}` check constraint) and `asset_tag_bindings` (`binding_value` + `binding_kind ∈ {epc,tid,device}` from day one). Partial unique index `ix_asset_tag_bindings_active` enforces one active binding per `(tenant_id, binding_value)`. Non-unique global index `ix_asset_tag_bindings_global_value` powers admin tag-collision tooling. RLS policies on both tables.
  - ORM: `AssetModel`, `AssetTagBindingModel`. Pydantic schemas: `AssetCreate/Update/Response`, `AssetTagBindingCreate/Response`, `TagCollisionResponse`.
  - `TimescaleAssetRepository` (CRUD with soft-delete via `status='retired'`, ilike search on `name`/`external_ref`) and `TimescaleAssetTagBindingRepository` (`bind`, `unbind`, `get_active_by_value`, `count_other_tenant_collisions`).
  - `AssetService` with audit hooks (`asset.created/updated/retired/bound/unbound`); routes mounted at `/assets` (editor+ writes, viewer+ reads), `/assets/{id}/bindings` POST/GET, `/assets/{id}/bindings/{binding_value}` DELETE.
  - **Admin `GET /admin/tag-collisions?binding_value=…`** — cross-tenant collision count for bulk-import preflight; never reveals tenant identities. Increments OTel counter `tagpulse_tag_collisions_global_total`.
- **Sprint 15 — Asset Tracking Substrate (Phase A)**:
  - Migration `017_sites_zones_tracking_modes.py`: adds `tenants.tracking_modes` (JSONB, default `["asset"]`), `devices.mobility` (`fixed`|`mobile`, default `fixed`, check-constrained); creates `sites` (tenant-scoped, unique `(tenant_id, name)`, default timezone) and `zones` (per-site, kind `reader_bound` requiring `fixed_reader_ids` JSONB or `geofence` requiring `polygon_geojson`, enforced via check constraint). Adds `tenant_isolation_*` RLS policies using `current_setting('app.current_tenant_id')::uuid`.
  - New ORM models `SiteModel` and `ZoneModel`; new Pydantic schemas `SiteCreate/Update/Response`, `ZoneCreate/Update/Response`, and `SubjectZoneChanged` event payload (Topic.SUBJECT_ZONE_CHANGED reserved for Phase B subject emission).
  - `TimescaleSiteRepository` and `TimescaleZoneRepository`; `get_zone_for_reader(tenant_id, device_id)` does JSONB containment lookup over `zones.fixed_reader_ids`, returning the deterministically-oldest zone per the design's collision rule.
  - `SiteZoneService` with audit hooks (`site.created/updated/deleted`, `zone.created/updated/deleted`); CRUD routes mounted at `/sites` and `/zones`. Reads gated to viewer+; writes gated to admin per asset-tracking design §4. Route-level guards reject `reader_bound` zones missing `fixed_reader_ids`, `geofence` zones missing `polygon_geojson`, and unknown `kind` values.
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
