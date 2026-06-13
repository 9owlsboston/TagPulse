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

### Post-Sprint-58 demo-data chore cluster (discovered 2026-06-13)

Surfaced while enriching the `demo-wm-dc` tenant with non-perishable SuperMart
SKUs. Scripts + ADR landed on `chore/demo-data-fixes`; the prod bug + sim gaps
below remain open and feed Sprint 59 §59.3.

- [2026-06-13] **BUG (latent, prod):** inventory stock-item auto-create gate (Sprint 50 / ADR 028) looks up the *decoded* GS1 URI (`urn:epc:id:sgtin:…`) against the *hex-keyed* `tags.epc_hex` column → never matches → every SGTIN auto-create is blocked → Stock Levels stays empty. Fix: gate should key off `identity.epc_hex` (uppercased), or `get_by_epc` should accept either form. Under-tested (only the asset-binding path is covered). [backend]
- [2026-06-13] **SIM GAP — serial alignment:** any seeder that materializes stock items via direct `POST /stock-items` MUST use the same serial scheme as `simulate_inventory._build_units` → `(product_idx+1)*100_000 + unit_idx`. Mismatched serials produce different EPCs, so the streamed reads never bind the units and stock shows as zone `unassigned`. Bit me with a `(idx+30)` offset; cost a full reseed. [backend]
- [2026-06-13] **SIM GAP — dwell vs heartbeat window:** `_build_units` per-stage dwell is `uniform(duration*0.10, duration*0.30)`, so a long `--duration` (e.g. 1800s) leaves downstream readers idle far past the dashboard's 5-min online window → devices stick at e.g. 11/14. Workaround is looping short runs (`--duration 90`). Consider a max-dwell cap or a heartbeat-only tick so all readers stay "online" regardless of duration. **Surfaces as the "0 active devices" hero-metric regression on a cold-open static tenant — see Sprint 59 §59.7.** [backend]
- [2026-06-13] **APP BUG — `?force=true` stock-item delete 500s on moved units:** `DELETE /stock-items/{id}?force=true` only bypasses the `in_stock` state guard, then hard-`DELETE`s; the `ON DELETE RESTRICT` FK `stock_movements_stock_item_id_fkey` (migration 021) rejects it with an unhandled `IntegrityError` → 500 + dropped connection. Broken for any unit that has moved. `cleanup_demo_stock_items.py` now soft-retires (PATCH `state=consumed`) to sidestep it. Needs an ADR on force-delete semantics (cascade ledger vs soft-delete vs remove) + a route-level `IntegrityError`→409. See Sprint 59 §59.6. [backend]
- [2026-06-13] **(done in `chore/demo-data-fixes`)** Promoted the working `/tmp` seeders into `scripts/` with docstrings + a `scripts/README.md`: `seed_nonperishable_skus.py`, `verify_catalog.py`, `check_devices_online.py`, `cleanup_demo_stock_items.py` (soft-retire via `state=consumed`), plus the two gate-bug workarounds `seed_stock_items.py` / `register_inventory_tags.py` (marked obsolete-once-fixed). Good basis for the Sprint 59 catalog-depth work. [backend]
- [2026-06-13] **SuperMart as validation vehicle:** use SuperMart business use cases/scenarios to drive demo-data design and *exercise app capability to surface gaps* (the gate bug + sim gaps above are the first finds). Feed into Sprint 59 scenario design. See [docs/design/supermart-inventory-scenario.md](design/supermart-inventory-scenario.md). [idea]

### UI

- [2026-06-13] **Standardize list-page column filters** per [ADR-030](adr/030-list-page-column-filters.md): a shared `makeEnumFilterColumn` factory (checkbox + `filterSearch`) for low-cardinality columns, with the client-vs-server rule (server-paginated lists must drive a query param, not client `onFilter`). Migrate the ~10 list pages incrementally; `AssetList` is the reference. First concrete ask: a `category` filter on the Products list (needs `GET /products?category=` server-side). [ui]

### General

- [2026-05-25] Normalize `reads-per-hour` sparkline `v` to reads/hr (currently bucket-volume, ~6× headline number with default `bucket_hours=6`); or rename the tile-id semantics. PR #79 follow-up. [backend]
- [2026-05-25] Eliminate double `get_summary()` per Dashboard load — `/sparklines` re-runs the 13-query summary that the UI already fetched via `/summary`. Either accept current values from client or drop flat tiles from `/sparklines`. PR #79 follow-up. [backend]

<!-- Add new items above this line. Oldest at bottom; remove when drained. -->
