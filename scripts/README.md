# `scripts/` — operator & demo-data tooling

This directory holds local/dev helpers: demo-tenant composers, simulators,
Azure (`azd-*`) operations wrappers, and one-off seeders. Scripts read config
from environment variables and target a running API (default
`http://localhost:8000`).

> **Note:** `scripts/` is intentionally **outside** the `make check` lint /
> typecheck gate (`make lint` / `mypy` cover `src tests clients/pi`). Keep new
> scripts consistent with the conventions below anyway — `httpx`, type hints,
> a module docstring with a usage block, and the standard env vars.

## Standard environment variables

Most demo-facing scripts accept the same config:

| Variable | Default | Meaning |
|---|---|---|
| `TAGPULSE_API_KEY` | _(required)_ | Tenant API key (`tp_…`). Never commit this. |
| `TAGPULSE_API_URL` | `http://localhost:8000` | API base URL. |
| `DEMO_TENANT_SLUG` | `demo-wm-dc` | Tenant slug; the tenant UUID is derived deterministically via `uuid5(NAMESPACE_DNS, "<slug>.tagpulse.local")`. |
| `TAGPULSE_TENANT_ID` | _(derived)_ | Explicit tenant UUID override. |

> **Why `demo-wm-dc`, not `demo-supermart`?** The slug is a **stable
> identifier**, not a brand label. Its `uuid5`-derived tenant id, the
> `tagpulse-demo-wm-dc-admin-key` Key Vault secret, and the Sprint 58 baseline
> measurements are all keyed off this exact string, so it is intentionally
> frozen even though the tenant's *display name* is now "SuperMart Distribution
> Center". Don't rename the slug to match the brand — it would change the tenant
> UUID and orphan the deployed KV secret. New domain tenants get fresh
> brand-aligned slugs (see [Sprint 59 plan](../docs/design/sprint-59-demo-scenarios.md)).

Get a demo key by running `make demo-tenant` (full seed; prints the key) or,
when you just need working credentials without re-seeding, `make demo-creds`
(rotates the demo admin key and reprints the login email + key). On a deployed
env the key is pulled from Key Vault instead.

> **Why rotate to "get" a key?** Local plaintext API keys are stored **hashed**
> (`users.api_key_hash`) and shown only once at issue time — there is no script
> that can read an existing key back. `make demo-creds` wraps
> `seed_demo_tenant.py --creds-only`, which rotates the admin key (and the
> editor/viewer keys) via `smoke_setup.py` with the correct frozen
> `--tenant-id` / `--admin-email`, then prints them. Pass `DEMO_KEEP_KEY=1` to
> reuse an already-exported `$TAGPULSE_API_KEY` instead of rotating.

## Demo-data scripts (added in `chore/demo-data-fixes`)

| Script | Kind | What it does |
|---|---|---|
| [`seed_nonperishable_skus.py`](seed_nonperishable_skus.py) | seeder | Adds 5 general-merchandise SKUs (shoes, jeans, TV, speaker, towels) with **no-expiry** lots, materializes stock items, and streams zone reads. Gives the demo a realistic mixed catalog alongside the perishable simulators. Idempotent. |
| [`verify_catalog.py`](verify_catalog.py) | check (read-only) | Lists products + categories, shows per-zone on-hand for the shoe SKU, and asserts non-perishable lots never leak into the Lot Expiry Queue. |
| [`check_devices_online.py`](check_devices_online.py) | check (read-only) | Lists devices with `connection_state` + `last_seen` age and flags which count as online in the Dashboard's 5-minute window. |
| [`cleanup_demo_stock_items.py`](cleanup_demo_stock_items.py) | cleanup | Retires stock items for the 5 non-perishable demo SKUs (PATCH `state=consumed`) so the seeder can re-materialize cleanly. Soft, not a hard delete: `stock_movements` has an `ON DELETE RESTRICT` FK (migration 021), so consuming a unit frees its EPC binding (partial unique index excludes terminal states) without orphaning the append-only ledger. Scoped to those SKUs only. |
| [`seed_stock_items.py`](seed_stock_items.py) | ⚠️ workaround | Materializes perishable stock items via direct `POST /stock-items` (binds by decoded URI). Works around the latent ingest gate bug. **Remove once that bug is fixed.** |
| [`register_inventory_tags.py`](register_inventory_tags.py) | ⚠️ workaround | Pre-registers the simulator's SGTIN EPCs in the tags registry. **Mostly superseded:** the composer now registers inventory EPCs automatically via the `seed_register_tags` step (see below), so a fresh `make demo-tenant` already has them. Keep this only as a manual one-off for a tenant seeded outside the composer. The ingest-gate bug it was meant to work around is separate (see note). |

The ⚠️ workaround scripts exist because of a latent ingest bug (the
auto-create gate compares the decoded GS1 URI against the hex-keyed `tags`
table, so registering hex tags does **not** by itself materialize stock
items). That bug and the simulator gaps these scripts paper over are tracked
in [`docs/backlog.md`](../docs/backlog.md) under the **Post-Sprint-58
demo-data chore cluster**.

## Demo composer (`seed_demo_tenant.py`)

`make demo-tenant` / `make demo-inventory` / `make demo-asset` drive
[`seed_demo_tenant.py`](seed_demo_tenant.py), which `subprocess`-runs the
sibling seeders/simulators in order (per `--profile`). Each step targets a
single script — the table below names them so a flag rename surfaces as a
fast composer failure rather than a silent skip (enforced by
`tests/unit/test_seed_demo_tenant.py`). Steps marked **combined-only** apply
to the WM-facing `demo-wm-dc` tenant; the neutral domain tenants skip them.

| Composer step | Script | Purpose |
|---|---|---|
| `smoke_setup` | [`smoke_setup.py`](smoke_setup.py) | Tenant + users + base site/zones/assets/rules. |
| `simulate_devices` | [`simulate_devices.py`](simulate_devices.py) | Reader devices. |
| `seed_register_tags` | [`seed_register_tags.py`](seed_register_tags.py) | Registers inventory SGTIN EPCs in the tags registry so the **Tags** KPI reflects the fleet. Deterministic (same serial scheme `simulate_inventory` streams); runs before the read stream. Inventory-seeding profiles only. |
| `simulate_inventory` | [`simulate_inventory.py`](simulate_inventory.py) | Products, lots, inventory read stream (per `--scenario`). |
| `simulate_assets` | [`simulate_assets.py`](simulate_assets.py) | Assets + tag bindings + seed reads. |
| `backfill_history` | [`backfill_history.py`](backfill_history.py) | Historical read density for charts. |
| `seed_alerts` | [`seed_alerts.py`](seed_alerts.py) | Open + resolved alert mix. |
| `seed_transfer` | [`seed_transfer.py`](seed_transfer.py) | One in-flight cross-tenant transfer. |
| `seed_ui_config` | [`seed_ui_config.py`](seed_ui_config.py) | WM presentation skin (`Device`→`Reader`, entity-first nav). **Combined-only.** |
| `seed_branding` | [`seed_branding.py`](seed_branding.py) | SuperMart logo kit (full + collapsed logos, teal accent). **Combined-only.** |

### Typical mixed-catalog demo flow

```bash
export TAGPULSE_API_KEY=tp_demo-wm-dc_...     # from `make demo-tenant`

make demo-tenant                              # perishable catalog + devices
python scripts/seed_nonperishable_skus.py     # add general-merch SKUs
python scripts/verify_catalog.py              # sanity-check the result
python scripts/check_devices_online.py        # confirm readers are "online"
```
