# TagPulse Domain Concepts 101

> **Audience:** anyone new to TagPulse trying to build a mental model of how
> devices, tags, assets, lots, stock items, bindings, and locations relate.
> Plain-English first; schema details live in [docs/data-models.md](../data-models.md);
> screen-by-screen UI walkthroughs live in [docs/user-guide.md](../user-guide.md).
>
> **TL;DR:** Devices observe tags. Tags are passive identifiers. Bindings
> translate a tag into the business object you actually care about (an asset,
> or a stock item that rolls up into a lot and a product). Location lives on
> the device and is *derived* for everything else through bindings + reads.

---

## The seven primitives

Two are universal infrastructure (Device, Tag), one is the glue (Binding), and the rest are the *business objects* the platform tracks — split between asset-tracking mode (`Asset`) and inventory-tracking mode (`Product` → `Lot` → `Stock Item`).

| Concept | Mode | What it is | Lives forever? | Example |
|---|---|---|---|---|
| **Device** | both | A piece of hardware that reports to TagPulse — typically an RFID reader. Has a known install position (lat/lon) or moves with a carrier. Self-reports telemetry. | Yes | `tpdev-reader-boston-dc-01` |
| **Tag** | both | A passive RFID label with a unique ID (EPC and/or TID). Doesn't report anything itself — it's just a serial number that gets *read* by a device. | Yes (until physically destroyed) | EPC `E280689400…` |
| **Asset** | asset-tracking | A trackable thing (pallet, forklift, tool). Identity is stable; the tag attached to it can be replaced. | Yes | `Sim-Pallet-03` |
| **Product** | inventory | A catalog entry / SKU. Describes "what kind of thing" — name, GTIN, unit, attributes. Doesn't refer to any physical unit. | Yes | "Pfizer COVID-19 Vaccine 5mL vial", GTIN `00301694512347` |
| **Lot** | inventory | A manufacturing batch of a product. Carries `manufactured_at` / `expires_at`; the unit of recall and cold-chain compliance. | Until expiry / depletion | Lot `VAX-2604-A`, mfg 2026-04-26, exp 2027-10-26 |
| **Stock item** | inventory | An individual physical unit within a lot (one specific vial). Short-lived: produced → moved → consumed/sold/expired. | No | Vial serial `…sgtin:0301694.512347.000042` |
| **Binding** | both | The row that says "this tag identifies this asset/stock_item right now." When you swap tags, you close the old binding and open a new one. | No (point-in-time) | `(tag=E2806894…, asset=Sim-Pallet-03, active 2026-04-12 → present)` |

**Bindings only point at `Asset` or `Stock Item`** — never at a `Product` or `Lot`. Those two are reached *transitively*: tag → stock_item → lot → product. That's the key shape difference between the two tracking modes.

---

## Why bindings have three kinds (`epc` / `tid` / `device`)

A binding row needs to know *what kind of identifier* the `binding_value` is, because tag reads carry **multiple identifiers** and matching needs to pick the right one.

| Binding kind | Matches against | When you'd use it |
|---|---|---|
| `epc` | `tag_reads.epc` | The label is encoded with a meaningful EPC (SGTIN, GIAI, etc.) — common in inventory/serialization workflows where the manufacturer assigns the EPC. |
| `tid` | `tag_reads.tid` | You can't trust the EPC (writable, may be reprogrammed) but the chip's factory-burned TID is immutable — common for high-value asset tracking and anti-counterfeit. |
| `device` | `tag_reads.tag_id` (the device's own perceived ID for the tag) | Bridge case for **mobile readers / carrier devices** (Sprint 17–19 work): the "tag" is actually a device that reports for itself, so the binding points at the device ID rather than a passive label. See [design/mobile-carriers-and-manifests.md](../design/mobile-carriers-and-manifests.md). |

**Could it collapse to one kind?** In a greenfield design, yes — pick EPC and force everyone to encode meaningfully. In practice, real RFID deployments need all three because (a) some tags don't have a usable EPC, (b) some workflows can't trust EPC, and (c) the carrier-device case has no passive tag at all.

The `asset_current_location` view's matcher reflects this exactly:

```sql
(binding_kind='epc'    AND tr.epc    = binding_value)
OR (binding_kind='tid'    AND tr.tid    = binding_value)
OR (binding_kind='device' AND tr.tag_id = binding_value)
```

---

## Where does location live?

**Location is a property of the device, not the tag or the asset.**

| Entity | Has location? | How |
|---|---|---|
| **Device** | ✅ Yes — it's the source of truth | Either fixed install lat/lon, or a moving GPS for mobile carriers |
| **Tag** | ❌ No (it's passive) | Borrows the device's location implicitly each time it's read |
| **Asset / Stock item** | ⚙️ Derived | Looked up via the active binding: tag → binding → asset; location comes from the device that did the read |

The flow:

```
Device (known position)
   │
   │ scans tag (EPC/TID/tag_id)
   ▼
tag_reads row (device_id, epc, tid, tag_id, lat, lon, timestamp)
   │
   │ binding lookup
   ▼
Asset OR Stock Item
   │
   └─► current location = the device's position at read time
```

That's why fixing `tr.reader_id → tr.device_id` in `asset_current_location` (PR #20) was a real bug: the column name drift broke the whole derivation chain and `GET /assets/{id}/path` returned 500.

---

## Asset tracking: assets and bindings

Asset mode is the **flat** tracking model — one row per trackable thing, no catalog hierarchy above it.

```
Asset  (one row per pallet / forklift / tool)
   │
   └── bound to one or more tags (EPC/TID/device) over time
```

Each asset is a long-lived business object with a stable identity (`external_ref`, e.g. `Sim-Pallet-03`). The tag attached to it is **swappable** — when a tag fails or gets re-labeled, you close the old binding and open a new one. The asset row stays.

### Tag binding in asset mode — where the glue sits

The binding lives in a **dedicated `asset_tag_bindings` table**, with point-in-time `active_from` / `active_to` columns. One asset can have many binding rows over its lifetime — only one active at a time per `binding_kind`.

```
Tag (EPC / TID / device-id)
   │
   │  asset_tag_bindings
   │  (binding_kind='epc'|'tid'|'device', binding_value=…, active_from … active_to)
   ▼
Asset
   (Sim-Pallet-03)
```

Why a separate table (vs. inline columns like stock_items)?

- Assets are **long-lived and re-tagged** — you need history, not just "current tag."
- A single asset can carry **multiple identifier kinds simultaneously** (e.g., the chip's TID *and* a written EPC) — one binding row per kind.
- Binding lifecycle (`active_from` / `active_to`) drives time-travel queries: "where was Pallet-03 on April 12 at 3pm?" requires knowing which tag was bound to it then.

### How to add a binding to an asset

Two-step: create the asset, then add bindings (one per call). You can repeat step 2 as tags get swapped.

```bash
# 1) Create the asset (binding-free)
curl -X POST "$API/assets" \
  -H "X-API-Key: $KEY" -H "X-Tenant-ID: $TID" \
  -H "Content-Type: application/json" \
  -d '{"external_ref": "Sim-Pallet-03", "name": "Sim-Pallet-03", "category_id": "<pallet-category-uuid>"}'

# 2) Add a binding (repeat any time you swap tags)
curl -X POST "$API/assets/$AID/bindings" \
  -H "X-API-Key: $KEY" -H "X-Tenant-ID: $TID" \
  -H "Content-Type: application/json" \
  -d '{"binding_value": "TAG0003", "binding_kind": "device"}'
```

| Field | Notes |
|---|---|
| `binding_value` | The identifier the read should match against (EPC / TID hex / device tag_id) |
| `binding_kind` | `epc`, `tid`, or `device` — see [the three kinds explained above](#why-bindings-have-three-kinds-epc--tid--device) |

To **swap a tag**, just POST a new binding with the new value — the previous active binding for that kind is closed automatically (`active_to` = now). To unbind without replacing, `DELETE /assets/{id}/bindings/{binding_id}`.

### Asset location flow

```
Device → tag_reads → asset_tag_bindings (active row) → Asset
                                                          │
                                                          └─► asset_current_location view
                                                                  └─► GET /assets/{id}/current-location
                                                                  └─► GET /assets/{id}/path  (24h trail)
```

The `asset_current_location` SQL view (migration 024) merges the latest `tag_reads` row per active binding **UNION** the latest `external_locations` row (TMS-pushed positions, e.g. Samsara), with `latest_position_source` letting the UI render "via Reader-12" vs "via Samsara."

---

## Inventory tracking: products, lots, stock items

The same "tag → binding → business object" pattern applies to inventory, but with a **3-level hierarchy** instead of one flat asset:

```
Product  (the SKU — "Pfizer COVID-19 Vaccine 5mL vial")
   │
   └── Lot  (a manufacturing batch — "lot VAX-2604-A, made 2026-04-26, expires 2027-10-26")
          │
          └── Stock Item  (a single physical unit — "vial #00042 in this lot")
                 │
                 └── bound to a tag (EPC/TID) → tracked in space & time
```

Each level answers a different business question:

| Level | Answers | Example | Cardinality |
|---|---|---|---|
| **Product** | "What is this thing in our catalog?" | Pfizer COVID-19 5mL vial, GTIN `00301694512347` | ~hundreds per tenant |
| **Lot** | "Which manufacturing batch?" — drives recall, expiry, cold-chain compliance | Lot `VAX-2604-A`, mfg 2026-04-26, exp 2027-10-26 | ~thousands |
| **Stock Item** | "Which specific physical unit?" — drives serialization, chain-of-custody, anti-counterfeit | Vial serial `…sgtin:0301694.512347.000042` | ~millions |

### Why three levels?

Pharma/retail/food regulations push you down the hierarchy:

- **Recall a product** ("all Pfizer vials") → too broad, kills business
- **Recall a lot** ("only batch VAX-2604-A made on bad-fridge day") → realistic, what FDA actually issues
- **Track a unit** ("vial #00042 was at hospital X on date Y") → DSCSA, EU FMD, drug serialization compliance

If you only had "asset," you couldn't answer "show me everything from the contaminated lot still in transit" without joining a bunch of metadata.

### Tag binding in inventory — where the glue sits

The binding **always attaches a tag to a `stock_item`** — never to a `lot`, never to a `product`. Lot and Product are reached *transitively* via foreign keys on the stock item.

> **Implementation note — inventory bindings are inline, not a separate table.**
> Unlike asset tracking (which has a dedicated `asset_tag_bindings` table with point-in-time `active_from` / `active_to` rows), `stock_items` carries the binding inline as two columns: **`binding_value`** and **`binding_kind`** (`'epc'` or `'tid'`). One stock item ↔ one tag, for the lifetime of that physical unit. If you need to swap tags, you either update the columns or retire the stock item and create a new one — there's no second binding row.

```
Tag (EPC: urn:epc:id:sgtin:0301694.512347.000042)
   │
   │  stock_items.binding_value / binding_kind  (inline columns)
   ▼
Stock Item  ──FK──►  Lot  ──FK──►  Product
   (vial #00042)     (VAX-2604-A)   (Pfizer 5mL vial)
```

So in inventory mode the **glue is the `stock_items.binding_value` column itself**. Concretely:

- Each physical vial gets its own tag, encoded with an SGTIN-96 (the EPC standard for serialized trade items: `<company prefix>.<item ref>.<serial>`).
- That EPC string is written to `stock_items.binding_value` when the stock item is created, with `binding_kind='epc'`.
- Once the tag is read, the system walks **stock_item → lot → product** automatically via the FKs. You get expiry, recall status, and cold-chain history all at once.

**Why bindings can't sit at the lot or product level:**

- A `Lot` is thousands of units — there's no single tag for "the whole batch." Aggregations roll *up* from the units that have been read.
- A `Product` is a catalog entry, not a physical thing. It has no location and no tag.
- Putting the binding at the unit level is what makes serialized recall (DSCSA / EU FMD) actually work.

### How to add a binding to a stock item

You don't call a separate `/bindings` endpoint like in asset mode — you create the stock item *with* its binding in one shot:

```bash
curl -X POST "$API/inventory/stock-items" \
  -H "X-API-Key: $KEY" -H "X-Tenant-ID: $TID" \
  -H "Content-Type: application/json" \
  -d '{
        "product_id": "<product-uuid>",
        "lot_id": "<lot-uuid>",
        "binding_value": "urn:epc:id:sgtin:0301694.512347.000042",
        "binding_kind": "epc"
      }'
```

Required fields:

| Field | Required | Notes |
|---|---|---|
| `product_id` | ✅ | The catalog entry this unit is an instance of |
| `binding_value` | ✅ | The tag identifier (EPC URI for SGTIN-96, or raw TID hex) |
| `binding_kind` | ✅ | `epc` (default) or `tid` |
| `lot_id` | optional | Skip for non-lot-tracked products (rare in regulated industries) |
| `parent_stock_item_id` | optional | Use when this unit is contained in a parent (e.g., a vial inside a tray) — see [mobile-carriers design](../design/mobile-carriers-and-manifests.md) |
| `metadata` | optional | Free-form JSON |

Lookup is performed via `stock_items.get_active_by_binding(kind="epc"|"tid", value=…)` during ingestion (see [`src/tagpulse/ingestion/service.py`](../../src/tagpulse/ingestion/service.py) `_mirror_tag_borne_sensors`).

> **Heads up:** `PATCH /inventory/stock-items/{id}` does **not** currently allow changing `binding_value` / `binding_kind` — see [`StockItemUpdate`](../../src/tagpulse/models/schemas.py) (only `state`, `lot_id`, `parent_stock_item_id`, `metadata` are mutable). If you need to re-tag, you `POST` a new stock item and mark the old one `state='lost'` or `'consumed'`.

### Inventory location flow

Identical to assets, just one extra hop:

```
Device → tag_reads → binding (EPC) → StockItem → Lot → Product
                                          │
                                          └─► stock_movements row (ENTER zone, …)
                                                  └─► stock_levels view recomputes
```

Subject-scoped telemetry fan-out (Sprint 19 work) writes the same temperature reading to **three telemetry rows**: one keyed on `device`, one on `stock_item`, one on `lot` — so a cold-chain rule on `lot.temperature_c > 8°C` fires for **the lot as a whole** even though only one unit's tag was scanned.

#### How a sensor reading on a tag becomes an alert

```
   Sensor-tag MQTT (v2)                External telemetry
   tenants/<t>/devices/<d>/tag-reads   tenants/<t>/devices/<d>/telemetry
   {epc, ts, tmp, hum, cnt, ...}       {subject_kind, metric, value, ...}
              |                                    |
              v                                    |
   MqttSubscriber (v2 dispatch)                    |
              |                                    |
              v                                    |
   IngestionService.ingest_reading                 |
              |                                    |
     +--------+--------+                           |
     v                 v                           |
   tag_reads        _mirror_tag_borne_sensors      |
   tag_presence            |                       |
                           v                       v
                   +----------------------------------+
                   |       telemetry_readings         |
                   | (subject_kind, metric, value...) |
                   +----------------+-----------------+
                                    v
                       Topic.TELEMETRY_RECORDED
                                    v
                    rules engine (telemetry.threshold)
                                    v
                                  alert
```

Two pipes, one bridge. Sensor-tag readings (cold-chain stickers, etc.)
ride the v2 tag-reads pipe and get mirrored into `telemetry_readings`
automatically — you do **not** stand up a second telemetry producer
for them. External telemetry (gateways, separate sensor devices) uses
the dedicated telemetry pipe. Both converge at `telemetry_readings`,
and a `telemetry.threshold` rule on `avg_temperature` fires regardless
of which pipe the value came in on. The wire→metric mapping for the
v2 path is fixed by [edge-wire-format-v2 §4.6](../design/edge-wire-format-v2.md);
the full four-producer list is in [ADR-015 §2](../adr/015-telemetry-rules-and-deprecation.md).

---

## End-to-end workflow comparison

The shape of the workflow is the same in both modes — register hardware, define the business object, bind tags, ingest reads. Only the middle step differs.

> **Everything in this guide is doable from the UI.** The cURL examples are shown because they're the most precise way to describe the request body and required fields, but each is backed by an equivalent screen in [TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI). The rightmost column below maps every API call to its UI entry point. Use the API for scripting, simulators, and CI; use the UI for day-to-day operations.
>
> **Step-by-step UI walkthroughs** for the same workflows live in [docs/user-guide.md](../user-guide.md) — see [First-Time Admin Workflow](../user-guide.md#first-time-admin-workflow) (steps 5/6a/6b cover devices → assets → products/lots/stock-items end-to-end), the [Devices](../user-guide.md#devices), [Assets](../user-guide.md#assets) (incl. [Tag-binding lifecycle](../user-guide.md#tag-binding-lifecycle)), and [Inventory](../user-guide.md#inventory) sections for screen-by-screen detail.

| Step | Asset mode | Inventory mode | Frontend (TagPulse-UI) — asset / inventory |
|---|---|---|---|
| **1. Register hardware** | `POST /devices` | `POST /devices` (identical) | **Devices** page → "New device" — same screen for both modes |
| **2. Define business object** | `POST /assets` | `POST /inventory/products` *then* `POST /inventory/lots` (two-level catalog) | **Assets** page → "New asset" / **Products** page → "New product"; open the product, "New lot" |
| **3. Bind tag → object** | `POST /assets/{id}/bindings` (separate call; can repeat over time as tags are swapped) | `POST /inventory/stock-items` (`binding_value` + `binding_kind` are part of the create body — one-shot per physical unit) | Asset detail page → **Bindings** tab → "Add binding" / Lot detail page → **Stock items** section → "Add stock item" (binding fields in the same form) |
| **4. Push reads** | `tag-reads` topic / endpoint (`tenants/{tid}/devices/{did}/tag-reads`) | Identical — same topic / endpoint, no inventory-specific ingest | Not a UI action — devices push directly. UI surfaces the result on **Map** page, **Asset detail → Path** tab, **Lot detail → Cold-chain** card, **Stock Levels** page |
| **Resolution path (server-side)** | `tag → active binding → asset` | `tag → stock_item (inline binding) → lot → product` | — |

So the missing third step in your inventory list is: **create stock items, which is also where you bind the tag.** In inventory mode, "define the physical instance" and "register its tag" are one operation, not two.

One more nuance worth knowing: in inventory mode the catalog is **two-level** (`Product` → `Lot`) before you get to the bound thing (`StockItem`). Asset mode is flat — the `Asset` itself is the bound thing.

### Side-by-side: where the binding attaches

| Mode | Where binding lives | Cardinality | How to add (API) | How to add (UI) |
|---|---|---|---|---|
| Asset tracking | `asset_tag_bindings` table (separate, point-in-time rows) | One asset ↔ many bindings over time | `POST /assets/{id}/bindings` | Asset detail → Bindings tab → "Add binding" |
| Inventory tracking | `stock_items.binding_value` + `binding_kind` (inline columns) | One stock item ↔ one tag (lifetime) | `POST /inventory/stock-items` (binding fields in create body) | Lot detail → Stock items → "Add stock item" (binding fields in same form) |

---

## Asset Tracking vs Inventory Tracking — comparison

| Concept | Asset Tracking (Sim-Pallet-03 world) | Inventory Tracking (vials/yogurt/cheese world) |
|---|---|---|
| **Top-level "thing"** | `Asset` (one row per pallet/tool/forklift) | `Product` (one row per SKU) |
| **Mid-level** | — (flat) | `Lot` (mfg batch) |
| **Physical unit** | The asset itself | `StockItem` (one row per physical unit) |
| **Bound to tag via** | `asset_tag_bindings` (kind: epc/tid/device) | `stock_items` binding (kind: epc/tid) — almost always EPC |
| **Lifecycle** | Long-lived; reused across many trips | Short-lived; produced → moved → consumed/sold/expired |
| **Cardinality** | Hundreds–thousands per tenant | Millions of stock items, thousands of lots |
| **Cares about expiry?** | No | **Yes** — `lots.expires_at` drives FEFO picking, near-expiry reports |
| **Cares about cold chain?** | Rarely | **Yes** — `lot.cold_chain_breach` rule template fires off telemetry |
| **Movement modeling** | "Where is asset X right now?" — single point | `stock_movements` ENTER/TRANSFER/EXIT rows per zone, plus per-unit state machine (in_stock / shipped / consumed / damaged) |
| **Aggregation question** | "Where is each asset?" | "How many units of product P, lot L, are in zone Z?" — **stock levels** |
| **Recall workflow** | n/a | "Quarantine all stock items in lot VAX-2604-A" → mass state change |
| **Identity model** | Often `device` kind (asset = the tracker itself) | Almost always `epc` (SGTIN-96 from manufacturer's GS1 prefix) |
| **Subject-kind for telemetry/rules** | `asset` | `lot` (cold chain), `stock_item` (per-unit) |
| **UI surface** | Map page, Asset detail Path tab | Stock Levels page, Lot detail (Cold-chain card), Product catalog |

### Quick mental model

- **Asset tracking** = "where is this thing?" — one row, one identity, lives forever.
- **Inventory tracking** = "where are *all the things* that share this batch/SKU, and is any of them about to expire / been recalled / fallen out of cold chain?" — three rows, hierarchical identity, ephemeral.

Same plumbing (devices observe tags; bindings translate; views derive location), but inventory adds the **product → lot → stock_item** hierarchy because regulations and operations need answers at all three granularities.

---

## See also

- [docs/user-guide.md](../user-guide.md) — screen-by-screen UI walkthroughs ([First-Time Admin Workflow](../user-guide.md#first-time-admin-workflow), [Devices](../user-guide.md#devices), [Assets](../user-guide.md#assets), [Inventory](../user-guide.md#inventory))
- [docs/data-models.md](../data-models.md) — schema-level reference for every table
- [docs/design/rfid-tag-data-model.md](../design/rfid-tag-data-model.md) — what RFID tags actually carry on the wire
- [docs/design/mobile-carriers-and-manifests.md](../design/mobile-carriers-and-manifests.md) — `binding_kind='device'` and mobile-reader containment
- [docs/design/subject-scoped-telemetry.md](../design/subject-scoped-telemetry.md) — how telemetry fans out across device/asset/lot/stock_item subjects
- [docs/adr/013-telemetry-subject-scoping.md](../adr/013-telemetry-subject-scoping.md) — why `subject_kind` exists
