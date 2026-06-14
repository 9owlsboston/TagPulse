# Demo Tour — Returnable Asset Fleet (`demo-asset-fleet`)

The **asset** demo profile stands up a single-domain, high-value /
returnable asset-tracking tenant: a named roster of forklifts, totes,
and IBC containers moving through a geofenced yard, with the
geofence-breach and gone-dark events that drive the "where is my
equipment?" story.

This is one of the three demo tenants. For the **shared mechanics** —
the live simulator, driving several tenants at once, reset, dev tips,
and troubleshooting — see the [demo guide](demo-guide.md); everything
there applies to this tenant too. This page covers only what makes the
asset-fleet tenant *different*.

## Seed it

```bash
make demo-asset          # idempotent; ~2-3 min on a clean DB
make demo-asset-reset    # drop it again (leaves the other demos alone)
```

`make demo-asset` runs the composer with `--profile asset`
([scripts/seed_demo_tenant.py](../../scripts/seed_demo_tenant.py)); the
fleet roster + zone topology live in
[scripts/simulate_assets.py](../../scripts/simulate_assets.py) under
`SCENARIOS["fleet"]`.

## Tenant identity

Deterministic `uuid5(NAMESPACE_DNS, "<slug>.tagpulse.local")`, like
every demo tenant, so re-runs converge.

| Property | Value |
|---|---|
| Name | `Returnable Asset Fleet` |
| Slug | `demo-asset-fleet` |
| UUID | `7a7446a1-649c-5518-9ed9-b232ad5e1ed7` |
| Admin login | `admin@demo-asset-fleet.tagpulse.local` |

Login credentials are issued the same way as the combined tenant — one
API key per role, printed once at the end of the seed run. See
[demo-guide.md § Login credentials](demo-guide.md#login-credentials).

## What gets seeded

The asset profile runs the composer's **asset** steps and **drops the
inventory catalog** (this tenant tells one story — asset tracking — end
to end):

| # | Step | Asset-fleet specifics |
|---|---|---|
| 1 | `smoke_setup` | Tenant row, 3 users (admin / editor / viewer), telemetry model `rfid_reader`, the high-temp + zone rules. |
| 2 | `simulate_devices` | RFID reader devices registered against the tenant. |
| 3 | `simulate_assets --scenario fleet` | The geofenced site, 7 zones, 3 categories, the 24-asset roster, and the movement pass (see below). |
| 4 | `backfill_history` | Historical reads so charts aren't empty on first load. |
| 5 | `seed_alerts` | Fresh + resolved alerts (mixed severity). |
| 6 | `seed_transfer` | 1 in-flight cross-tenant transfer (asset handed to a recipient tenant). |
| — | ~~`simulate_inventory`~~ | **skipped** — no products / lots / stock in this tenant. |

### Site + zones

One site, **Returnable Asset Fleet Yard** (`9 Logistics Park, Boston,
MA`), with seven zones. The first is a geofenced authorized area; the
last is the exit zone used for the breach narrative:

| Zone | Reader | Role |
|---|---|---|
| **Authorized Area** | `AF-Authorized` | geofenced — assets *should* stay inside |
| Receiving Yard | `AF-Receiving` | inbound |
| Wash Bay | `AF-WashBay` | cleaning |
| Staging | `AF-Staging` | pre-dispatch |
| Loading Dock | `AF-Loading` | outbound |
| Maintenance Bay | `AF-Maintenance` | service |
| **Yard / Exit** | `AF-Exit` | exit — a read here trips the geofence rule |

### Roster (24 named assets, 3 categories)

| Category | `category_type` | Assets |
|---|---|---|
| Forklift | `object` | `FORK-01` … `FORK-06` (6) |
| Reusable Tote | `rti_container` | `TOTE-01` … `TOTE-10` (10) |
| IBC Container | `liquid_container` | `IBC-01` … `IBC-08` (8) |

Each asset is bound to its EPC(s) directly, so the Assets / Zones /
zone-changed pages populate cleanly with **no dependency on the ingest
auto-create gate**.

## The business scenario

The movement pass is tuned to light up the asset-tracking story:

- **Geofence breach** — `FORK-06` is routed to the **Yard / Exit**
  zone, tripping the geofence rule so the alerts panel and the
  zone-exited story have live signal.
- **Gone dark ("where is X?")** — `IBC-08` deliberately receives **no
  reads**, so it sits with a stale `last_seen` for the
  inactivity / missing-asset demo.
- **Normal circulation** — the remaining assets move between the
  authorized area, wash bay, staging, loading, and maintenance zones,
  generating `subject.zone_changed` events and populating the
  zone-changed feed.
- **Transfer in flight** — one asset is handed to a recipient tenant
  via the cross-tenant transfer path (reuses `seed_transfer.py`).

## Tour — what to click

After `make demo-asset` (and ideally a multi-tenant
[`make sim-start`](demo-guide.md#driving-several-tenants-at-once) so the
dashboard moves):

1. **Assets** (`/assets`) — 24 rows across forklifts / totes / IBCs.
   Filter by category; note `IBC-08`'s stale `last_seen` (the gone-dark
   asset).
2. **Sites + Zones** — the seven-zone yard. Open **Authorized Area**
   (geofenced) and **Yard / Exit** to see the breach path.
3. **Rules + Alerts** — the geofence rule fired by `FORK-06`'s exit
   read; the alerts panel has the breach plus the seeded mixed-severity
   alerts.
4. **Zone changes / Tag Reads** — the `subject.zone_changed` feed as
   assets circulate (with the simulator running).
5. **Tag Transfers** — the in-flight outbound transfer to the
   recipient tenant.
6. **Devices** — the `AF-*` zone readers across the yard.

## See also

- [demo-guide.md](demo-guide.md) — shared simulator, multi-tenant
  driving, reset, troubleshooting, dev tips.
- [demo-inventory-tour.md](demo-inventory-tour.md) — the cold-chain
  inventory domain tenant.
- [scripts/simulate_assets.py](../../scripts/simulate_assets.py) —
  `SCENARIOS["fleet"]` is the source of truth for this roster + topology.
