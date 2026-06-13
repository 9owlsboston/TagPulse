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
| [`register_inventory_tags.py`](register_inventory_tags.py) | ⚠️ workaround | Pre-registers the simulator's SGTIN EPCs in the tags registry so the gate passes. Alternative to `seed_stock_items.py`. **Remove once the gate bug is fixed.** |

The two ⚠️ workaround scripts exist only because of a latent ingest bug (the
auto-create gate compares the decoded GS1 URI against the hex-keyed `tags`
table). That bug and the simulator gaps these scripts paper over are tracked in
[`docs/backlog.md`](../docs/backlog.md) under the **Post-Sprint-58 demo-data
chore cluster**.

### Typical mixed-catalog demo flow

```bash
export TAGPULSE_API_KEY=tp_demo-wm-dc_...     # from `make demo-tenant`

make demo-tenant                              # perishable catalog + devices
python scripts/seed_nonperishable_skus.py     # add general-merch SKUs
python scripts/verify_catalog.py              # sanity-check the result
python scripts/check_devices_online.py        # confirm readers are "online"
```
