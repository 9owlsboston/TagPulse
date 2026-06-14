# Demo Tour — Cold-Chain Inventory (`demo-inv-coldchain`)

The **inventory** demo profile stands up a single-domain, cold-chain
distribution-center tenant: a multi-lot perishable + pharma catalog
flowing Receiving → Cold Storage → Pick Floor → Shipping, with a
dedicated Quarantine / Hold zone and the expiry / low-stock events that
drive the inventory-management story.

This is one of the three demo tenants. For the **shared mechanics** —
the live simulator, driving several tenants at once, reset, dev tips,
and troubleshooting — see the [demo guide](demo-guide.md); everything
there applies to this tenant too. This page covers only what makes the
cold-chain tenant *different*.

## Seed it

```bash
make demo-inventory          # idempotent; ~2-3 min on a clean DB
make demo-inventory-reset    # drop it again (leaves the other demos alone)
```

`make demo-inventory` runs the composer with `--profile inventory`
([scripts/seed_demo_tenant.py](../../scripts/seed_demo_tenant.py)); the
cold-chain catalog + zones live in
[scripts/simulate_inventory.py](../../scripts/simulate_inventory.py)
under `SCENARIOS["coldchain"]`.

## Tenant identity

Deterministic `uuid5(NAMESPACE_DNS, "<slug>.tagpulse.local")`, like
every demo tenant, so re-runs converge.

| Property | Value |
|---|---|
| Name | `Cold-Chain Distribution Center` |
| Slug | `demo-inv-coldchain` |
| UUID | `281271a9-c54e-5203-a238-d485a6dfef26` |
| Admin login | `admin@demo-inv-coldchain.tagpulse.local` |

Login credentials are issued the same way as the combined tenant — one
API key per role, printed once at the end of the seed run. See
[demo-guide.md § Login credentials](demo-guide.md#login-credentials).

## What gets seeded

The inventory profile runs the composer's **inventory** steps and
**drops the asset roster and the cross-tenant transfer** (this tenant
tells one story — inventory — end to end):

| # | Step | Cold-chain specifics |
|---|---|---|
| 1 | `smoke_setup` | Tenant row, 3 users (admin / editor / viewer), telemetry model `rfid_reader`, the high-temp + zone rules. |
| 2 | `simulate_devices` | RFID reader devices registered against the tenant. |
| 3 | `simulate_inventory --scenario coldchain` | The cold-chain site, 5 zones, the multi-lot catalog, and per-unit movement (see below). |
| 4 | `backfill_history` | Historical reads so charts aren't empty on first load. |
| 5 | `seed_alerts` | Fresh + resolved alerts (mixed severity). |
| — | ~~`simulate_assets`~~ | **skipped** — no asset roster in this tenant. |
| — | ~~`seed_transfer`~~ | **skipped** — no cross-tenant transfer. |

### Site + zones

One site, **Cold-Chain Distribution Center** (`5 Cold Storage Row,
Boston, MA`), with a five-zone pipeline. The first four are the
forward flow; the last is a terminal hold zone:

| Zone | Reader | Role |
|---|---|---|
| Receiving Dock | `CC-Receiving` | inbound |
| Cold Storage | `CC-ColdStorage` | refrigerated hold |
| Pick Floor | `CC-PickFloor` | order picking |
| Shipping Dock | `CC-Shipping` | outbound |
| **Quarantine / Hold** | `CC-Quarantine` | divert / reject |

### Catalog (multi-lot, staggered expiries)

~13 lots across **4 categories** — vaccines/biologics, dairy, produce,
and dry goods. Two products (`Vaccine-X` and `Milk 1L`) carry **two
lots each** at different expiries so first-expiry-first-out picking is
visible:

| Category | Product | Lot | Expiry | Note |
|---|---|---|---|---|
| pharma/vaccine | Vaccine-X 0.5 mL vial | `VAX-2604-A` | 34 d | |
| pharma/vaccine | Vaccine-X 0.5 mL vial | `VAX-2604-B` | 6 d | near-expiry |
| pharma/biologic | Insulin 10 mL vial | `INS-2606` | 45 d | |
| pharma/biologic | Monoclonal Ab 5 mL | `MAB-0612` | 3 d | **critical** → quarantine divert |
| food/dairy | Milk 1L | `MILK-0501-A` | 4 d | near-expiry |
| food/dairy | Milk 1L | `MILK-0509-B` | 12 d | fresher lot |
| food/dairy | Yogurt 4-pack | `YOG-0428-B` | 15 d | |
| food/dairy | Cheese 200g | `CHS-0301-K` | 90 d | |
| food/produce | Strawberries 1 lb | `STR-0610` | 2 d | **near-expiry** → quarantine divert |
| food/produce | Lettuce, head | `LET-0611` | 5 d | |
| food/dry-goods | Rice 5 kg | `RICE-2026` | 365 d | **low-stock** (2 units) → reorder |
| food/dry-goods | Canned beans 400g | `BEAN-2027` | 730 d | |
| food/dry-goods | Pasta 500g | `PAS-2026` | 540 d | |

## The business scenario

The catalog is tuned to light up the inventory-management story on
every page:

- **Near-expiry lots** — six lots expire within two weeks (down to
  `STR-0610` at 2 days and `MAB-0612` at 3 days), so the
  expiring-soon view and `stock.expiring_within` rule have live signal.
- **Low-stock reorder** — `Rice 5 kg` (`RICE-2026`) is deliberately
  seeded at **2 units** so it sits below a reorder threshold while
  every other SKU is comfortably stocked.
- **Quarantine / hold** — half the units of the two riskiest lots
  (`MAB-0612` critical-expiry biologic, `STR-0610` near-expiry produce)
  divert **Receiving → Quarantine** and stop, instead of flowing
  forward. The Quarantine / Hold zone shows real occupancy, not an
  empty placeholder.
- **Forward flow** — the remaining units follow a randomized per-unit
  timeline through Receiving → Cold Storage → Pick Floor → Shipping,
  generating `subject.zone_changed` events and ENTER/TRANSFER/EXIT rows
  so per-zone counts reflect actual movement.

## Tour — what to click

After `make demo-inventory` (and ideally a multi-tenant
[`make sim-start`](demo-guide.md#driving-several-tenants-at-once) so the
dashboard moves):

1. **Inventory / Products** — 11 SKUs across pharma + food categories.
   Filter by category to tell the pharma-vs-perishable split.
2. **Lots** — 13 lots; sort by expiry to surface the near-expiry
   pharma + produce at the top.
3. **Stock Levels** — per-zone occupancy, including the
   **Quarantine / Hold** zone holding the diverted `MAB-0612` /
   `STR-0610` units. *(See the caveat below — live levels depend on the
   §59.6 fix.)*
4. **Sites + Zones** — the five-zone cold-chain pipeline; click
   Quarantine / Hold to confirm it's a real zone with reads, not empty.
5. **Rules + Alerts** — the high-temp excursion rule plus the
   expiry / low-stock signals the catalog is tuned for.
6. **Tag Reads / Devices** — the `CC-*` zone readers ticking as units
   move (with the simulator running).

## Honest scope

The cold-chain **catalog, lots, zones, and quarantine topology seed
correctly**. Live **Stock Levels** population, however, still depends on
the §59.6 ingest auto-create gate fix: the composer seeds inventory with
`--seed-only`, and the tag-registry gate blocks stock-item auto-create
until the corresponding EPCs are promoted to `active`. Until §59.6
lands, Stock Levels for this tenant may read empty even though the
products / lots / zones are present. The asset-fleet tenant is
unaffected (it binds EPCs to assets directly, no ingest gate). See the
[Sprint 59 design doc §59.6](../design/sprint-59-demo-scenarios.md) and
[backlog](../backlog.md).

## See also

- [demo-guide.md](demo-guide.md) — shared simulator, multi-tenant
  driving, reset, troubleshooting, dev tips.
- [demo-asset-tour.md](demo-asset-tour.md) — the asset-fleet domain
  tenant.
- [scripts/simulate_inventory.py](../../scripts/simulate_inventory.py)
  — `SCENARIOS["coldchain"]` is the source of truth for this catalog.
