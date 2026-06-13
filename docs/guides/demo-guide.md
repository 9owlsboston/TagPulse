# Demo Tenant Guide

A complete tour of the TagPulse demo tenant — what's in it, how it
behaves with and without the live simulator, and how to drive it for
screenshots, walkthroughs, design reviews, and Lighthouse / perf runs.

For the deployment side (laptop setup, Azure topology, on-call runbooks)
see [operator-quickstart.md](../operator-quickstart.md). This guide is
the *content* view of the same tenant — what an operator clicking
around in the UI will actually see.

## TL;DR

```bash
# 1. Bring the local stack up + migrate
docker compose up -d
alembic upgrade head

# 2. Seed the demo tenant (idempotent; ~2-3 min on a clean DB)
make demo-tenant

# 3. (Optional) start the live simulator so the dashboard isn't a snapshot
export TAGPULSE_API_KEY=$(make demo-tenant | tail -1 | awk -F= '{print $2}')
make sim-start

# 4. Open the UI
xdg-open http://localhost:3000     # Linux
open http://localhost:3000          # macOS
```

When you're done:

```bash
make sim-stop                # if you started the sim
make demo-tenant-reset       # drop the demo tenant + recipient
```

## What the demo tenant is for

A repeatable, deterministic, fully populated tenant used for:

- **Screenshots / walkthroughs.** Same data every time → diffable
  visual artifacts.
- **Design reviews.** Real-shaped data on every page so the team
  reviews against realistic density, not lorem-ipsum placeholders.
- **Lighthouse + stopwatch perf runs.** The Sprint 58 baseline at
  [docs/measurements/sprint-58-baseline.md](../measurements/sprint-58-baseline.md)
  was captured against this tenant.
- **Local development.** Hit a real-shaped API + UI without
  hand-rolling fixtures.

It is **not** for load testing (use `scripts/load_test.py`) and
**not** for staging or prod — the composer hard-refuses to run with
`ENVIRONMENT=prod`.

## Tenant identity

The tenant identity is deterministic (UUID5 over the slug), so every
operator / CI / machine converges to the same row.

| Property | Value |
|---|---|
| Name | `WM Distribution Center` |
| Slug | `demo-wm-dc` |
| UUID | `241d9b81-59da-5fb7-8f78-f58200978566` |
| Recipient tenant (for transfers) | `demo-wm-recipient` |

## Login credentials

`make demo-tenant` rotates one API key per role and prints them at the
end of the run. **Keys are only shown once** — capture them or rotate
with another `make demo-tenant` run.

| Role | Email | Use for |
|---|---|---|
| admin | `admin@demo-wm-dc.tagpulse.local` | full CRUD, transfers, rule edits |
| editor | `editor@example.com` | non-destructive edits |
| viewer | `viewer@example.com` | read-only — exercises the 403 paths |

The UI accepts either:

1. **Email + API key** (preferred — drives the role-based features).
2. **X-Tenant-ID alone** (viewer fallback, read-only).

If you missed the keys, run `make demo-tenant` again. It is idempotent;
it will rotate keys and reprint them.

## What gets seeded (static snapshot)

`make demo-tenant` runs [scripts/seed_demo_tenant.py](../../scripts/seed_demo_tenant.py),
which composes seven steps. Each step is idempotent (re-runs converge,
they do not duplicate).

| # | Step | What it creates |
|---|---|---|
| 1 | `smoke_setup` | Tenant row, 3 users (admin / editor / viewer), 1 site (`Bay Area HQ`), 2 zones (geofence `Bay Area West Block` + reader-bound `Sim-Reader-01 Dock`), 5 assets (`Sim-Pallet-01..05`) bound to `TAG0001..TAG0005`, telemetry model `rfid_reader` (temperature, humidity, battery_pct), 3 rules (high-temp threshold, zone entered, zone exited). |
| 2 | `simulate_devices` | 10 RFID reader devices (`Sim-Reader-01..10`) registered against the tenant. |
| 3 | `simulate_inventory` | 1 second site (`Boston DC`), 4 anchor devices (`DC-Receiving`, `DC-ColdStorage`, `DC-PickFloor`, `DC-Shipping`), 4 reader-bound zones, 4 products (vaccine, milk, yogurt, cheese), 4 lots. |
| 4 | `simulate_assets` | 12 additional assets (`Sim-Pallet-001..012`) bound to tag IDs + ~20 live reads to seed `last_seen`. |
| 5 | `backfill_history` | ~5,000 historical reads spread over the last 3 days. Many are rejected by the ingest clock window — expected; the seed tunes for "enough density to populate charts". |
| 6 | `seed_alerts` | 4 fresh open alerts (mixed severity) + 3 resolved alerts (timestamps 6h, 24h, 42h ago). |
| 7 | `seed_transfer` | 1 in-flight cross-tenant transfer to `demo-wm-recipient` with 3 EPCs. Bootstraps the tag registry rows + reads needed for the transfer to be eligible. |

Total wall-clock: ~2-3 min on a clean DB, ~20 sec on subsequent runs
(skips the backfill phase if `DEMO_SKIP_BACKFILL=1` is set).

## Static vs live data — what to expect

This is the single most important distinction. The seed is a
**snapshot**. The simulator keeps it **moving**.

| Dashboard tile | After `make demo-tenant` only | With `make sim-start` running |
|---|---|---|
| Devices online | **0 / 14** (no fresh reads) | **14 / 14** (simulator pushing) |
| Devices total | 14 | 14 |
| Reads / hour | ~85 (from backfill tail) | 200 by default (`SIM_RATE_PER_MIN`) |
| Open alerts (24h) | 0 — none fired yet against the seed | grows over time as sim trips the high-temp rule |
| Assets active | 17 | 17 |
| Tag transfers in flight | 3 | 3 |
| Tag reconciliation backlog | 60 | grows as live reads hit unregistered tags |
| Tags total | 3 (the bootstrap EPCs for transfers) | grows as registrar promotes new EPCs to `active` |
| Sites / Zones | 2 / 5 | 2 / 5 |
| Low-stock products | 0 | 0 |

> **Why devices show offline after a fresh seed:** the dashboard's
> `devices_online` query is a strict AND of "fresh `last_seen` (within
> 5 min)" AND "`connection_state = online`". The seed writes both, but
> as soon as wall-clock advances past the 5-min window the count
> drops to 0. Start `make sim-start` and they pop back online within
> seconds.

> **Why reads/hour > 0 even without the simulator:** the 5,000-read
> backfill window from step 5 lands inside the 1-hour bucket the tile
> queries, so it's non-zero immediately after the seed and decays as
> wall-clock advances.

## The continuous simulator

`make sim-start` runs [scripts/sim_loop.py](../../scripts/sim_loop.py)
inside the `sim` profile of [docker-compose.yml](../../docker-compose.yml).
It does roughly this each tick:

- Emits realistic tag reads against the demo tenant, gated by a token
  bucket (default **200 reads/min**, hard ceiling **600/min**).
- Applies a **shift schedule** — 1.5 × during ±30 min around 08:00 and
  13:00 local, 0.3 × during 20:00 – 06:00, 1.0 × otherwise.
- ~5 % chance per minute of taking one reader offline for 3–8 min
  (drives the "0/14 → 13/14" tile flicker that's hard to demo otherwise).
- Every 15 min, fires one high-temp read on a random device — keeps the
  alerts panel warm.

Knobs:

| Env var | Effect |
|---|---|
| `SIM_RATE_PER_MIN=400 make sim-start` | Push harder (capped at 600/min). |
| `SIM_DURATION=30m make sim-start` | Bounded run instead of indefinite. |
| `SIM_SEED=42 make sim-start` | Deterministic PRNG for reproducible runs. |

Observability:

```bash
make sim-status     # docker compose ps sim + last 50 log lines
make sim-stop       # stop and remove the sim container
```

## Tour — what to click

After `make demo-tenant` (and ideally `make sim-start`):

1. **Dashboard** (`/`) — 9 KPI tiles + 7-day sparklines. With the
   simulator running, the reads/hour line bends visibly within a
   minute. Click the "Active assets" tile to deep-link into the
   Assets page filtered by `status=active`.
2. **Assets** (`/assets`) — 17 rows. Sim-Pallet-001..012 are the
   "warehouse" assets; Sim-Pallet-01..05 are the "Bay Area" ones bound
   to TAG0001..TAG0005 (used by the geofence rule).
3. **Tag Reads** (`/tag-reads`) — pageable list with chart view.
   Toggle to the chart and watch it tick when the simulator runs.
4. **Devices** (`/device-registry`) — 14 readers across 3 sites.
5. **Sites + Zones** — Bay Area HQ (1 geofence + 1 reader-bound zone)
   + Boston DC (4 reader-bound zones).
6. **Rules + Alerts** — the high-temp rule and the two geofence rules
   are pre-provisioned; alerts arrive from the simulator's 15-min
   high-temp tick.
7. **Tag Transfers** — 1 outgoing in-flight transfer to
   `demo-wm-recipient` with 3 EPCs in `status='requested'`.
8. **Inventory / Products / Lots** — vaccines, milk, yogurt, cheese
   with associated stock items.
9. **Telemetry** — pre-provisioned `rfid_reader` model so charts
   render immediately.

## Reset and restart

The composer is idempotent, so re-running `make demo-tenant` is safe
on top of an existing seed. To start from a clean slate:

```bash
make sim-stop                # if running
make demo-tenant-reset       # deletes demo + recipient tenants + all rows
make demo-tenant             # rebuild from scratch
```

`make demo-tenant-reset` discovers tenant-scoped tables by walking
PostgreSQL FK metadata pointed at `tenants.id` (so non-standard FK
columns like `tag_transfers.from_tenant_id` and `.to_tenant_id` are
included), and retries deletes iteratively to resolve FK chains. It
refuses to run against anything that doesn't look local unless
`DEMO_RESET_FORCE=1` is set.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Devices show `0 / 14` online | No live reads in the last 5 min | `make sim-start` (or any `scripts/simulate_devices.py …` invocation) |
| `reads_per_hour` stuck at 0 | Simulator stopped or never started | `make sim-status` to confirm, `make sim-start` to relaunch |
| 401 on every API call | API key rotated by a re-seed | Re-login with the latest key from `make demo-tenant`'s final line |
| Dashboard tiles say "Unexpected token '<', '<!doctype'..." | UI proxy missing the new backend prefix | `cd ~/ws/TagPulse-UI && docker compose up -d --build --no-deps ui` after pulling the latest [nginx.conf](https://github.com/9owlsboston/TagPulse-UI/blob/main/nginx.conf) |
| `make demo-tenant-reset` fails with FK errors on `tenants` | View or non-standard FK column not in discovery | Already handled by the FK-walking reset; if it recurs, run `DEMO_RESET_FORCE=1 make demo-tenant-reset` |
| Assets page spins forever | Browser cached the old SPA bundle | Hard refresh (Ctrl+Shift+R) or open in an incognito window |
| `make demo-tenant` hangs on `[1/7] smoke_setup` | API container restarting / unhealthy | `docker compose ps` to confirm; the composer auto-retries the API health probe for 30 s before failing |

## Developer tips

### Backend source edits

[docker-compose.yml](../../docker-compose.yml) sets `PYTHONPATH=/app/src`
on the `app` and `worker` services so uvicorn's `--reload` picks up
edits to `src/tagpulse/...` on the host without rebuilding the image.
Without this override, Python imports from the wheel baked into
`/usr/local/lib/python3.12/site-packages/`, which the bind mount does
not shadow.

```bash
# Edits to src/tagpulse/... auto-reload via uvicorn (~1-2s)
# Worker code: no --reload, so:
docker compose restart worker
```

### UI source edits

The UI ships as a built bundle inside its container, so edits require
a rebuild:

```bash
cd ~/ws/TagPulse-UI
# … edit src/…
cd ~/ws/TagPulse && docker compose up -d --build --no-deps ui
```

For a tight inner-loop, run the UI's Vite dev server (`npm run dev`)
on port 5173 against the local API on port 8000 — the API's
`CORS_ORIGINS` already lists `http://localhost:5173`.

### Adding a new role / second tenant

Use [scripts/smoke_setup.py](../../scripts/smoke_setup.py) directly
with a different `--tenant-slug` + `--admin-email` — it handles the
upsert + key issuance for a single tenant. The demo composer is built
on top of it; reuse the same shape if you want a parallel "test-corp"
or "demo-mfg" tenant alongside the WM one.

## See also

- [operator-quickstart.md](../operator-quickstart.md) — laptop +
  Azure topology, on-call paths, env-cluster commands.
- [docs/measurements/sprint-58-baseline.md](../measurements/sprint-58-baseline.md)
  — API-side latency baseline captured against this tenant.
- [docs/roadmap.md](../roadmap.md) Sprint 58 — design rationale for
  the demo tenant + simulator deliverables.
- [ADR 008 — Multi-tenancy strategy](../adr/008-multi-tenancy-strategy.md)
  — the tenant isolation model the demo tenant exercises.
