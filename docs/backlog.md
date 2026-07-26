# Backlog

Lightweight scratch list for **in-flight ideas** you don't want to lose
but won't pull into the active sprint. See
`.github/copilot-instructions.md` § Cross-Repo Workflow for the model.

## How to use this file

- Add a line whenever you notice something mid-work that's out of scope.
- Don't edit existing sprints/PRs to absorb the idea.
- Drain this file during sprint planning: each item either
  - gets promoted to `docs/roadmap.md` (becomes a future sprint), or
  - gets a `chore/<topic>` branch (small standalone PR), or
  - gets deleted (was a fleeting thought).

Format per entry: `- [YYYY-MM-DD] <one-line description> [tag]`
Tags: `[backend]`, `[ui]`, `[docs]`, `[ops]`, `[idea]`.

## Open items

- [2026-07-25] **Backfill the last fake-only SQL paths onto the C-6RTX harness.** Done (C-XSD1): tag-reads `asset_q` (correlated `EXISTS` over active bindings → assets) + `GET /tag-reads/facets` now have live-DB integration tests. **Done (C-4PAD):** the Sprint 77 **Transfers / Reconciliation** wildcard filters — tag-transfers `epc_q` + `statuses` and each of the three reconciliation-view `q` filters — now have live-DB integration tests too (`tests/integration/test_transfer_reconciliation_db.py`, + `make_user`/`make_tag`/`make_product`/`make_stock_item` factories). No fake-only SQL paths remain from the audit. [backend]
- [2026-06-21] **Sprint 72 follow-up — Journey map highlight on leg select.** The leg cross-filter highlights the env chart but not the map trail; pan/highlight the map to the selected leg's window. [ui]
- [2026-06-21] **Sprint 72 follow-up — Journey map highlight on leg select.** The leg cross-filter highlights the env chart but not the map trail; pan/highlight the map to the selected leg's window. [ui]

### Post-Sprint-58 demo-data chore cluster (discovered 2026-06-13)

Surfaced while enriching the `demo-wm-dc` tenant with non-perishable SuperMart
SKUs. Scripts + ADR landed on `chore/demo-data-fixes`; the prod bug + sim gaps
below remain open and feed Sprint 59 §59.3.

- [2026-06-13] **(done in `chore/sim-tooling-dwell`, C-W7XM)** ~~SIM GAP — serial alignment~~: the serial scheme is now the shared `simulate_inventory.stock_unit_serial(product_idx, unit_idx)` helper `= (product_idx+1)*100_000 + unit_idx` — any seeder that materializes stock items via direct `POST /stock-items` MUST import and use it (a mismatch produces different EPCs, so streamed reads never bind and stock shows zone `unassigned`). [backend]
- [2026-06-13] **(done in `chore/sim-tooling-dwell`, C-W7XM)** ~~SIM GAP — dwell vs heartbeat window~~: `_build_units` now **caps** the per-stage dwell at `_MAX_STAGE_DWELL_S=240s` and emits **resident heartbeat re-reads** every `_HEARTBEAT_S=240s` once a unit settles at its final stage, so a long `--duration` keeps every reader inside the dashboard's 5-min online window (worst read-gap ≤240s, was ~540s). Fixes the "0 active devices" cold-open regression (Sprint 59 §59.7). [backend]
- [2026-06-13] **(done in `chore/demo-data-fixes`)** Promoted the working `/tmp` seeders into `scripts/` with docstrings + a `scripts/README.md`: `seed_nonperishable_skus.py`, `verify_catalog.py`, `check_devices_online.py`, `cleanup_demo_stock_items.py` (soft-retire via `state=consumed`), plus the two gate-bug workarounds `seed_stock_items.py` / `register_inventory_tags.py` (marked obsolete-once-fixed). Good basis for the Sprint 59 catalog-depth work. [backend]
- [2026-06-13] **SuperMart as validation vehicle:** use SuperMart business use cases/scenarios to drive demo-data design and *exercise app capability to surface gaps* (the gate bug + sim gaps above are the first finds). Feed into Sprint 59 scenario design. See [docs/design/supermart-inventory-scenario.md](design/supermart-inventory-scenario.md). [idea]

### UI

- [2026-06-13] **Standardize list-page column filters** per [ADR-030](adr/030-list-page-column-filters.md): a shared `makeEnumFilterColumn` factory (checkbox + `filterSearch`) for low-cardinality columns, with the client-vs-server rule (server-paginated lists must drive a query param, not client `onFilter`). Migrate the ~10 list pages incrementally; `AssetList` is the reference. First concrete ask: a `category` filter on the Products list (needs `GET /products?category=` server-side). [ui]

### General

- [2026-05-25] Normalize `reads-per-hour` sparkline `v` to reads/hr (currently bucket-volume, ~6× headline number with default `bucket_hours=6`); or rename the tile-id semantics. PR #79 follow-up. [backend]
- [2026-05-25] Eliminate double `get_summary()` per Dashboard load — `/sparklines` re-runs the 13-query summary that the UI already fetched via `/summary`. Either accept current values from client or drop flat tiles from `/sparklines`. PR #79 follow-up. [backend]

### Spatial / positioning (design captured — [floor-position-estimation.md](design/floor-position-estimation.md))

- [2026-06-19] **Phase 1 — BYO precomputed floor positions.** `POST /assets/{id}/position` (`source='precomputed'`, floor-frame sibling of the existing lat/lon `external-position`) + shared `GET /assets/{id}/floor-path` read endpoint + `CRS.Simple` trail layer. Fills the headless `asset_positions` table; ~1 sprint, low risk; unblocks customers with their own location engine. Builds the seam Phase 2 reuses. Promote to a sprint when scheduling the floor-trail UX. [backend][ui]
- [2026-06-19] **Phase 2 — homegrown RSSI estimator.** `rssi_weighted_centroid` + recency-decay (`τ` half-life, `τ→0` = last-wins), hull-bounded, Option C server-side recompute tick (server time, rolling buffer), `τ`/`D` knobs in `tenants.position_strategy`. Multi-sprint, R&D; amends [ADR-024](adr/024-position-estimation.md). Gated on first sub-meter-positioning customer. [backend]
- [2026-06-19] **`[NEEDS WM]` v2 wire-format + snap simulator.** Estimator may want an additive `rpk` (peak-RSSI) wire field; needs WM protocol sign-off. A v2 snap simulator (wrap `WmV2Producer`, short `snap_period_s`, MQTT publish) is dev tooling that follows the WM conversation — current simulators are v1-HTTP (no snaps). [backend]

- [2026-07-25] **`external_locations` relay provenance column.** C-4Z66 stamps the relaying gateway in `metadata.relayed_by_device_id` (server-controlled). A dedicated `relayed_by_device_id UUID` column (indexed, queryable) is the durable form — a small migration + repo/response passthrough. Do it if relay-provenance queries or audits need it. [backend]
<!-- Add new items above this line. Oldest at bottom; remove when drained. -->
