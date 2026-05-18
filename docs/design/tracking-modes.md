# Design Document: Tracking Modes — Asset vs Inventory

**Date:** 2026-05-02
**Status:** proposed
**Related:** [asset-tracking-gap-analysis.md](asset-tracking-gap-analysis.md), [assets-and-zones.md](assets-and-zones.md), [mobile-carriers-and-manifests.md](mobile-carriers-and-manifests.md), [rfid-tag-data-model.md](rfid-tag-data-model.md), [data-models.md](../data-models.md)

---

## 1. Problem Statement

TagPulse and TagPulse-UI must serve **two distinct domains** that look superficially similar (both use RFID, readers, zones) but ask very different questions:

- **Asset tracking** — "Where is forklift #12 right now? Where has it been?"
- **Inventory tracking** — "How many units of SKU `0614141-12345` are in zone *Cold-Storage-A*? Which lots expire next week?"

The Sprint 15 plan as written ([assets-and-zones.md](assets-and-zones.md)) only models the first. If we ship that as-is, an inventory user has no first-class place to put SKUs, lots, expirations, or stock levels and has to misuse the `assets` table (one row per physical unit, no SKU hierarchy, no aggregations). Conversely, modeling everything as inventory loses the per-asset history view that asset operators need.

This design proposes **two coexisting tracking modes** sitting on a shared substrate (tags, reads, zones, locations, events) so a single tenant can run either or both modes, and the platform can flex per deployment.

---

## 2. Comparison

| Dimension | Asset tracking | Inventory tracking |
|---|---|---|
| Subject of tracking | A specific physical thing (forklift, tool, returnable container) | A unit of stock — typically one tag per item, but identity is "this is *one of* SKU X, lot Y" |
| Cardinality | Hundreds → low thousands per tenant | Thousands → millions per tenant; high churn |
| Tag lifecycle | Long-lived; tag may outlive any single deployment cycle | Often single-use (sticker on a carton); discarded with the item |
| EPC schemes (typical) | GIAI, GRAI (returnable asset codes) | SGTIN (item-level), SSCC (logistics units) |
| Identity stability | TID often preferred (re-encodable EPCs) | EPC is the business identifier; serialized SGTIN is unique |
| User memory matters? | Rarely (asset attributes live in the platform DB) | Frequently (lot, batch, expiration date, manufacture date) |
| Primary question | "Where is X?" (point-in-time + history) | "How many of SKU X are in Y?" (aggregate) and "What's expiring?" |
| Zone semantics | Asset → 0 or 1 current zone; transitions matter | SKU → count per zone; ENTER/EXIT becomes IN/OUT inventory movement |
| Rule examples | `zone.entered`, `dwell_exceeded`, `not_seen_for_N_min` | `stock.below_threshold`, `expiring_within_N_days`, `unexpected_in_zone` |
| Typical UI | Map + asset detail + path | Stock-by-location grid + lot/batch detail + expiry queue |

The substrate is shared (`tag_reads`, `zones`, `sites`, MQTT, RLS, edge contract) but the **business object** differs.

### 2.1 FAQ — "Why doesn't `assets` have a `quantity` column?"

This question comes up often enough to anchor it here.

**It doesn't, and it won't.** The two modes are split precisely so that quantity-of-alike-things lives in the *inventory* layer, not the *asset* layer.

- In **asset mode**, each row in `assets` is one physical thing (Forklift-12, Pallet-A47). Counting alike assets is a query: `SELECT count(*) FROM assets WHERE category_id = '<forklift-category>'`. There is no aggregate quantity to store.
- In **inventory mode**, `products` is the SKU/catalog row and `stock_items` is one row per tagged unit. On-hand quantity is **derived** by counting `stock_items` with the matching `product_id` and a "present" location state — it is never stored as a column.
- For **un-serialized bulk goods** (a bin of 500 unmarked widgets, a tank of fluid), neither model fits. That's the **Kit / BOM / bulk-aggregate** backlog item; if it lands, it gets its own entity, not a `quantity` column on `assets`.

Why not just add `assets.quantity` and be flexible? Because it would silently break:

- The `asset_tag_bindings` invariant (one active tag per asset — see [assets-and-zones.md §3.2](assets-and-zones.md)).
- The `asset_current_location` view (one location per row — a quantity-N asset has no single location).
- Rule semantics (`zone.entered` for an asset of quantity 50 means *what* exactly?).
- The cross-mode hierarchy (truck-as-asset carrying inventory — see [mobile-carriers-and-manifests.md §4](mobile-carriers-and-manifests.md)).

If a stakeholder asks for an `assets.quantity` field, the right response is **"you're describing inventory mode — those tags should bind as `stock_items` instead."**

---

## 3. Decision — Shared Substrate, Two Domain Layers

We keep one ingestion pipeline and one zone/event model. We split the **subject of tracking** into two siblings:

> **Multi-mode tenants are first-class.** A tenant can run `['asset']`, `['inventory']`, or `['asset', 'inventory']` simultaneously. The canonical mixed scenario is a fleet of forklifts (assets) moving pallets of consumer goods (inventory) through the same sites and zones. See §9 for the tenant flag and §8 for how the UI sidebar adapts.

```
shared substrate
├── tag_reads            (Sprint 1; TID/EPC/user-memory in Sprint 14)
├── sites, zones         (Sprint 15)
├── EventBus topic: subject.zone_changed   (renamed from asset.zone_changed; kind discriminator)
└── edge contract        (Sprint 16)

domain layer A — asset tracking      domain layer B — inventory tracking
├── assets                            ├── products            (SKU catalog)
├── asset_tag_bindings                ├── lots                (production batch / expiry)
└── view: asset_current_location      ├── stock_items         (per-tag inventory unit)
                                       ├── stock_movements     (derived from zone changes)
                                       └── view: stock_levels  (current count per product/zone/lot)
```

Both layers are optional per tenant. A tenant config flag (`tracking_modes: ['asset', 'inventory']`) controls which API surfaces and UI pages are exposed; the substrate is always on.

### 3.1 Why not unify into one "subject" table?

We considered a single `tracked_subject` table with a `kind` discriminator. It collapses the schema but:

- Inventory needs SKU + lot hierarchy (one product → many lots → many stock_items). Assets don't.
- Inventory queries are aggregations (counts); asset queries are per-row lookups. Indexing strategies diverge.
- UI flows are sufficiently different that a discriminator-per-row would force conditional rendering everywhere.

Two tables, shared substrate, is cleaner.

### 3.2 Renaming the zone-change event

Sprint 15 originally specified `asset.zone_changed`. We rename to **`subject.zone_changed`** with payload:

```json
{
  "tenant_id": "...",
  "subject_kind": "asset" | "stock_item",
  "subject_id": "<asset_id or stock_item_id>",
  "from_zone_id": "...", "to_zone_id": "...",
  "device_id": "...", "tag_id": "...", "epc": "...", "tid": "...",
  "timestamp": "..."
}
```

Sprint 15 implements `subject_kind='asset'`. Sprint 15b adds `subject_kind='stock_item'`. Rules engine matches on `subject_kind` (existing rule definitions only see `asset` events; new inventory rules opt into `stock_item`).

---

## 4. Inventory Domain Layer (new)

### 4.1 `products` — SKU catalog

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID FK | RLS |
| `sku` | VARCHAR(64) | Tenant-unique business code |
| `gtin` | VARCHAR(14) | Optional; GS1 GTIN-14 |
| `name` | VARCHAR(255) | |
| `category` | VARCHAR(64) | Free-form |
| `unit` | VARCHAR(20) | `each` \| `case` \| `pallet` |
| `attributes` | JSONB | Free-form (color, size, hazardous flag, …) |
| `created_at` / `updated_at` | TIMESTAMPTZ | |

**Unique:** `(tenant_id, sku)`.

### 4.2 `lots` — production batch / expiry

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID FK | RLS |
| `product_id` | UUID FK → products.id | |
| `lot_code` | VARCHAR(64) | Manufacturer batch identifier |
| `manufactured_at` | TIMESTAMPTZ | Optional |
| `expires_at` | TIMESTAMPTZ | Optional; drives `stock.expiring_soon` rules |
| `metadata` | JSONB | |

**Unique:** `(tenant_id, product_id, lot_code)`.

### 4.3 `stock_items` — per-tag inventory unit

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID FK | RLS |
| `product_id` | UUID FK → products.id | |
| `lot_id` | UUID FK → lots.id | NULL allowed (non-lot-tracked products) |
| `tag_id` | VARCHAR(256) | EPC URI (typical SGTIN) |
| `binding_kind` | VARCHAR(8) | `epc` \| `tid` (defaults `epc` for inventory) |
| `state` | VARCHAR(20) | `in_stock` \| `in_transit` \| `consumed` \| `expired` \| `lost` |
| `current_zone_id` | UUID NULL | Maintained by ingestion |
| `first_seen_at` | TIMESTAMPTZ | |
| `last_seen_at` | TIMESTAMPTZ | |
| `consumed_at` | TIMESTAMPTZ NULL | Set when state → consumed |

**Partial unique:** `(tenant_id, binding_kind, tag_id) WHERE state NOT IN ('consumed','expired','lost')`.
**Index:** `(tenant_id, product_id, lot_id, current_zone_id)` for stock-level aggregation.

> **Naming note.** The actual column is `binding_value` (the table is new in Sprint 15b and ships with the right name from day one); the `tag_id` references in this section predate the rename decision — read them as `binding_value`.

A `stock_item` is created **automatically** by the ingestion service when a tag read arrives whose EPC decodes to a known SGTIN belonging to a registered `product`, and no active `stock_item` for that tag exists yet. Manual creation via API is also supported (CSV import).

### 4.4 `stock_movements` — append-only ledger

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID FK | RLS |
| `stock_item_id` | UUID FK | |
| `from_zone_id` | UUID NULL | |
| `to_zone_id` | UUID NULL | NULL = exit (consumed/lost) |
| `movement_type` | VARCHAR(20) | `enter` \| `exit` \| `transfer` \| `consume` |
| `quantity` | INTEGER | Always 1 for serialized inventory; reserved for future case/pallet aggregation |
| `device_id` | UUID NULL | Source reader |
| `occurred_at` | TIMESTAMPTZ | |

Hypertable on `occurred_at`. Drives historical stock-by-location queries and audit.

### 4.5 `stock_levels` — view

```sql
CREATE VIEW stock_levels AS
SELECT
    si.tenant_id,
    si.product_id,
    si.lot_id,
    si.current_zone_id,
    COUNT(*) AS quantity
FROM stock_items si
WHERE si.state = 'in_stock'
GROUP BY si.tenant_id, si.product_id, si.lot_id, si.current_zone_id;
```

Materialize later if cardinality demands.

---

## 5. Ingestion Pipeline — Mode Awareness

A single ingestion path serves both modes. After validating and persisting `tag_reads`:

1. **Asset path** (always on if asset mode enabled): look up `asset_tag_bindings` → if found, evaluate zone change → emit `subject.zone_changed` with `subject_kind='asset'`.
2. **Inventory path** (always on if inventory mode enabled):
   - Look up `stock_items` by `(tag_id, binding_kind)`.
   - If not found and the EPC decodes to a known `product.gtin`, create the `stock_item` (state `in_stock`, with `lot_id` if `tag_data.lot` is present and matches a registered `lot`).
   - If found, update `last_seen_at` and `current_zone_id`; on zone change, append a `stock_movement` row and emit `subject.zone_changed` with `subject_kind='stock_item'`.

Both paths are short and run inline. Hot-path SKU lookup is cached at the service layer (LRU keyed by `gtin`).

A read is **not required** to map to either an asset or a stock item. Unmapped reads still land in `tag_reads` and surface in Data Explorer.

---

## 6. APIs

```
# Inventory (new — Sprint 15b)
POST   /products
GET    /products                       (filter: category, q)
GET    /products/{id}                  (+ current stock summary)
PATCH  /products/{id}
DELETE /products/{id}                  (only if no active stock_items)

POST   /products/{id}/lots
GET    /products/{id}/lots             (filter: expiring_within_days)

GET    /stock-items                    (filter: product_id, lot_id, zone_id, state)
GET    /stock-items/{id}               (+ recent movements)
PATCH  /stock-items/{id}               (state transitions: consume, mark lost)

GET    /stock-levels                   (aggregated; group_by: product, lot, zone)
GET    /stock-movements                (filter: product_id, zone_id, since, until)

GET    /tenant/config                  (returns tracking_modes)
PATCH  /tenant/config                  (admin only)
```

Permissions follow the existing role matrix — viewer reads, editor mutates stock items / lots, admin manages products and tenant config.

---

## 7. Rules Engine — Inventory Conditions

New condition types (Sprint 15b, evaluated by the existing rules engine):

```yaml
- type: stock.below_threshold
  product_id: <UUID>
  zone_id: <UUID> | null      # null = anywhere
  threshold: 50

- type: stock.expiring_within
  product_id: <UUID> | null   # null = any product
  days: 14
  cooldown_s: 86400

- type: stock.unexpected_in_zone
  zone_id: <UUID>
  allowed_product_ids: [<UUID>, ...]
```

Producers:

- `below_threshold` — periodic worker (60 s) scans `stock_levels` against each rule.
- `expiring_within` — daily worker scans `lots` joined to `stock_items.state='in_stock'`.
- `unexpected_in_zone` — fires on `subject.zone_changed` with `subject_kind='stock_item'` when the SKU isn't in the allow-list.

`zone.entered`/`zone.exited`/`zone.dwell_exceeded` (Sprint 17a) gain a `subject_kind` filter so the same rule fabric serves both modes.

---

## 8. UI Parity

Shared:

- **Sites & Zones** (Sprint 15) — used by both modes.
- **Map** (Sprint 17a) — rendering layer takes `subject_kind`; asset markers and stock-density heat-tiles share the canvas with a layer toggle.
- **Data Explorer** — gains "Subject" column showing the resolved asset or stock_item link.

Asset-only (Sprint 15):

- **Assets** page, **Asset detail** with path timeline.

Inventory-only (Sprint 15b — new):

- **Products** page — catalog list, SKU detail with stock-by-zone bar chart.
- **Lots** sub-page per product — expiry queue, lot detail.
- **Stock Levels** page — pivot grid (rows: product, columns: zone, cells: count); CSV export.
- **Stock Movements** page — chronological ledger filterable by product / zone / time range.
- **Inventory rules** in the rule wizard (extends existing wizard).

Sidebar entries are filtered by `tenant.config.tracking_modes` so a pure-asset tenant doesn't see inventory pages, and vice versa.

---

## 9. Tenant Configuration

```sql
ALTER TABLE tenants
  ADD COLUMN tracking_modes JSONB NOT NULL
    DEFAULT '["asset"]';   -- array of: 'asset' | 'inventory'
```

Default is `['asset']` to preserve current behavior. Switching to `['inventory']` or `['asset','inventory']` is admin-only and audited.

**Enabling** a mode is additive and safe — it exposes additional API routes and UI pages, no data migration required.

**Disabling** a mode (e.g., dropping `'inventory'` from the array while `stock_items` exist) is **blocked** by the admin endpoint when any active subject of that kind exists for the tenant. The operator must first retire all `assets` (status='retired') / unbind all `stock_items` before the mode can be removed. A `force=true` flag is reserved for support escalations and writes a high-severity audit row; it never deletes data, only hides the UI/API surface. This guard prevents an admin from silently orphaning a populated domain layer.

The flag drives:

- API surface (404 on inventory routes when not enabled).
- UI sidebar / page registration.
- Whether the ingestion service runs the inventory branch.

---

## 10. Phasing

| Sprint | Scope | Notes |
|---|---|---|
| **15 (asset)** | `assets`, `asset_tag_bindings`, `sites`, `zones`, `subject.zone_changed` (asset kind), Assets UI | Already designed; **rename** event topic before implementation |
| **15b (inventory)** | `products`, `lots`, `stock_items`, `stock_movements`, `stock_levels` view, inventory APIs + UI | New sprint, sibling to 15 |
| **17a (geofencing/map)** | Polygon zones, map UI with **both** asset markers and stock-density layers | Slight expansion of original 17a UI scope |
| later | Cross-mode features: pallet-of-cases hierarchy (asset containing stock_items), inventory cycle counts, kit/BOM models | Backlog |

Sprint 15 and 15b can run in either order or in parallel — both depend on the shared substrate from Sprint 14 but not on each other.

---

## 11. Decisions & Open Questions

### Resolved

| # | Question | Decision |
|---|---|---|
| 1 | Unify stock_items + assets into a generic "subject" table? | **No** — reasons in §3.1. Watch for duplication pain after Sprint 15b ships, but don't pre-emptively unify. |
| 2 | Lot inference from `tag_data` — extend `telemetry_models` or new table? | **New `tag_data_mappings` table.** `telemetry_models` describes numeric metrics (units, ranges, quarantine); `tag_data_mappings` describes string keys that resolve to domain entities (lot, batch, expiry, mfg date, serial). Different shape, different lifecycle, different consumers. Schema: `(tenant_id, scope_kind ∈ {tenant, device_type, product}, scope_id, semantic_field, tag_data_key, transform)` with most-specific scope wins at ingest. Naturally supports per-product overrides for tenants whose suppliers use different conventions. New planned migration 020b ([data-models.md](../data-models.md#migrations)); admin UI gets a "Tag data fields" sub-tab on the Tenant Settings page ([admin-ui.md](admin-ui.md) §3). |
| 3 | Case / pallet hierarchy modeling? | **`parent_stock_item_id` self-FK** for v1. Separate `containment` table only if a customer needs many-to-many or temporal containment edges. |
| 4 | Cycle counts & reconciliation? | **Backlog** — out of scope for Sprint 15b; tracked in roadmap. |
| 5 | Mixed-mode rules (one rule across asset + stock_item)? | **No** — one rule = one `subject_kind` for evaluation simplicity. Operators compose two rules instead. |
| 6 | Data Explorer: true pivot grid for inventory? | **Paginated table for v1.** Pivot/grid component if customers ask after using the table. |

### Still open

_(none currently — all open questions resolved.)_
