# SuperMart Inventory Scenario & Stock-Levels Debugging

**Date:** 2026-06-13
**Status:** working reference (Sprint 58/59 demo-data session)
**Related:** [tracking-modes.md](tracking-modes.md), [sprint-59-demo-scenarios.md](sprint-59-demo-scenarios.md), [rfid-tag-data-model.md](rfid-tag-data-model.md), [../backlog.md](../backlog.md)

> Captures the "SuperMart as a multi-mode tenant" walkthrough plus the live
> debugging of empty Stock Levels and the two root causes found. "SuperMart" is
> a fictional big-box retail DC used purely as a demo narrative; the demo tenant
> slug remains `demo-wm-dc`.

---

## 1. SuperMart as a multi-mode tenant

TagPulse runs two tracking modes on one shared substrate, controlled by
`tenants.tracking_modes` (canonical doc: [tracking-modes.md](tracking-modes.md)):

| Mode | What it tracks | Subject | Identifier |
|---|---|---|---|
| `inventory` | Sellable goods, consumables | `stock_item` | **SGTIN** (GTIN + serial) |
| `asset` | Reusable equipment | `asset` | **GRAI / GIAI / SSCC** |

A single SuperMart DC tenant enables **both** modes:

- **Inventory mode** — clothing, shoes, food. Each item carries an SGTIN
  RFID tag. We care about *how many of SKU X are in zone Y* and *which lots
  are near expiry*.
- **Asset mode** — pallets (SSCC), forklifts and returnable totes
  (GRAI/GIAI). We care about *where this specific physical thing is right
  now*, not counts.

The same reader infrastructure and the same `/tag-reads` ingestion endpoint
feed both. The server decides which branch to run based on the EPC scheme
decoded from each read and the tenant's enabled modes.

### Identifier strategy
- **SGTIN** (Serialized GTIN): `urn:epc:id:sgtin:<company>.<item>.<serial>`.
  Decodes to a **GTIN-14**, which maps to a `products` row. The serial makes
  each physical unit unique → one `stock_item`.
- **SSCC** (Serial Shipping Container Code): a pallet/case. Asset-style.
- **GRAI / GIAI**: returnable/grantable assets (totes, forklifts).

---

## 2. Inventory data model (how counts work)

The hierarchy:

```
products (SKU, GTIN)
  └─ lots (lot_code, expiry)
       └─ stock_items (one per physical unit, bound to an EPC)
            └─ stock_movements (zone→zone transitions)

stock_levels  ← VIEW that derives per (product, lot, zone) counts
```

**Key invariant: quantity is never stored.** A `stock_item` is a single
unit. The `stock_levels` view counts items grouped by current zone. You get
"13 units of Milk in Cold Storage" by counting 13 `stock_items` whose
`current_zone_id` is Cold Storage — not by reading a `quantity` column.

**Stock items materialize server-side from tag reads.** The flow in
`IngestionService._enrich_with_inventory` (`src/tagpulse/ingestion/service.py`):

1. Decode the read's EPC → GTIN-14.
2. Gate: tenant must have `inventory` in `tracking_modes`.
3. Look up `product` by GTIN.
4. Look up the active `stock_item` bound to this EPC; **auto-create one if
   absent**.
5. Resolve the reader's fixed zone; on a zone change, append a
   `stock_movements` row and emit `SUBJECT_ZONE_CHANGED`.

So Stock Levels is entirely read-driven: no reads → no stock_items → empty
grid.

---

## 3. "Where are the size-10 shoes?" — operator UI flow

1. Open **Inventory → Stock Levels**.
2. Filter by product (the size-10 shoe SKU) — optionally by zone.
3. Read the resulting multi-zone table: each row is `(product, lot, zone,
   quantity)`. That tells you "8 pairs on the Pick Floor, 5 in Receiving,
   2 on the Shipping Dock."
4. Drill into **Stock Movements** to see the zone-transition history for
   those units.

The same data backs questions like "which milk lots expire this week"
(**Lot Expiry Queue**) and "what's physically in Cold Storage right now"
(**Sites & Zones → Boston DC**).

---

## 4. The debugging story — why Stock Levels was empty

The `demo-wm-dc` tenant had 4 products but **0 stock items, 0 stock-level
rows**. Two independent root causes:

### Root cause #1 — `--seed-only` skips the read stream
`scripts/simulate_inventory.py` `main()` does Step 0–3 (mode, site/devices,
zones, catalog) and then `if args.seed_only: return` **before** Step 4 (the
read stream). The demo composer was running it with `--seed-only`, so the
catalog existed but no reads ever flowed → nothing to materialize.

**Fix:** run the simulator *without* `--seed-only`.

### Root cause #2 — the tags-registry gate blocks auto-create (latent bug)
After running the read stream (256 reads, 0 failed, spread across all 4
zones) stock items were **still 0**. The blocker is the Sprint 50 / ADR 028
gate inside `_enrich_with_inventory`:

```python
epc = read.identity.epc
stock_item = await self._stock_repo.get_active_by_binding(tenant_id, "epc", epc)
if stock_item is None:
    if self._tag_repo is not None:
        tag = await self._tag_repo.get_by_epc(tenant_id, normalize_epc_hex(epc))
        if tag is None or tag.status not in {"registered", "active"}:
            stock_item_auto_create_blocked_counter.add(1, ...)
            return  # ← blocked here
```

The gate's intent: only auto-create a stock_item if the tenant has
*registered* this EPC in the `tags` table. Reasonable. **But it's buggy:**

- After `_normalize`, `read.identity.epc` is the **decoded GS1 URI**
  (`urn:epc:id:sgtin:0614141.200001.…`).
- The `tags` table is keyed by **hex** (`tags.epc_hex`,
  e.g. `3034257BF4C35040000186A0`).
- `get_by_epc` compares `normalize_epc_hex(uri)` (just uppercases the URI)
  against the hex column. **A URI never equals a hex string** → the lookup
  always misses → every SGTIN auto-create is blocked.

The result: **inventory Stock Levels has been broken since the gate landed.**
The path is under-tested — existing enrichment tests only exercise the
asset-binding branch with pre-seeded URIs, so the regression slipped
through.

There's also a secondary gap: `simulate_inventory.py` never registers its
EPCs as tags at all (it predates the gate), so even a *correct* gate would
need the simulator to register tags first.

### Side issues hit along the way
- `TagCreate.epc_hex` enforces `^[0-9A-F]{16,128}$` — **uppercase only**, and
  validated *before* `normalize_epc_hex` runs. The simulator's
  `_sgtin96_hex` emits lowercase, so a naive tag-registration helper got 422
  `string_pattern_mismatch`. Fix: `.upper()` before POST.
- Write endpoints are rate-limited to **300/min**. Bulk tag/stock-item
  creation needs throttling (~0.25 s/req) or it 429s.

---

## 5. The workaround applied (demo data, no prod code change)

Rather than patch production ingestion on the running stack, we materialized
the stock items directly so the demo would show real data:

1. **Create stock items bound to the decoded URI.** POST `/stock-items` with
   `binding_value = decode_epc_hex(epc_hex)[1]["uri"]`, `binding_kind="epc"`
   — the exact value `get_active_by_binding` will look up. (Script:
   [`scripts/seed_stock_items.py`](../../scripts/seed_stock_items.py), scaled
   to match `--units 80`.)
2. **Re-run the read stream.** Now `get_active_by_binding` finds the existing
   item *before* the tags gate, so the gate is skipped entirely; reads
   record zone observations and movements.

**Result:** 80 stock items, 12 stock-level rows across all 4 zones (80 units
on-hand), 182 movements. Stock Levels, Stock Movements, Lot Expiry Queue,
and Sites & Zones all populate.

> The two workaround scripts ([`scripts/seed_stock_items.py`](../../scripts/seed_stock_items.py),
> [`scripts/register_inventory_tags.py`](../../scripts/register_inventory_tags.py))
> carry an in-file banner and should be **removed once root cause #2 is fixed**.

---

## 6. Sprint 59 implications

This session surfaced a real defect, not just a demo-data hiccup. Candidate
backlog items (tracked in [../backlog.md](../backlog.md)):

1. **Fix the gate's EPC lookup.** Compare against `identity.epc_hex`
   (uppercased) rather than the decoded URI — or make `get_by_epc` accept
   either form / normalize both sides to a canonical key.
2. **Add regression coverage** for the inventory auto-create gate: a read
   for a *registered* SGTIN must create a stock_item; an *unregistered* one
   must be blocked. Current tests miss this branch.
3. **Make `simulate_inventory.py` register its EPCs as tags** (status
   `registered`) as part of catalog seeding, so the simulator works
   end-to-end against the post-Sprint-50 gate without manual steps.
4. **Document the seed→read ordering** so `--seed-only` isn't mistaken for a
   complete demo setup.

---

## Quick reference (demo tenant)
- Tenant ID: `241d9b81-59da-5fb7-8f78-f58200978566`
  (`uuid5(NAMESPACE_DNS, "demo-wm-dc.tagpulse.local")`)
- Reseed + emit key: `make demo-tenant`
- API: `http://localhost:8000` (local docker compose stack)
- Catalog: Vaccine-X (pharma), Milk 1L (near-expiry), Yogurt 4-pack, Cheese
  200g — 4 SKUs, 4 zones (Receiving Dock → Cold Storage → Pick Floor →
  Shipping Dock).
