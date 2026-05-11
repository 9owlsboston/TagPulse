# TagPulse Quick Start Guide

Get the full TagPulse platform running locally in under 5 minutes.

---

## First-time setup vs day-to-day workflow

Steps **1–5b are one-time setup** per workspace (or per fresh DB). After the first run-through, your steady-state cycle is much shorter.

### Day-to-day (after first-time setup)

```bash
# Terminal 1 — backend
cd ~/TagPulse && make run

# Terminal 2 — frontend
cd ~/TagPulse-UI && npm run dev

# Terminal 3 — push data when you want it
export TAGPULSE_API_KEY=tp_test-corp_<hex>
python scripts/simulate_devices.py \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  --devices 5 --interval 2
```

### When to repeat earlier steps

| Step | Re-run? | Why |
|------|---------|-----|
| **1. Clone repos** | Once | On disk after the first clone. |
| **2. `docker compose up -d db mqtt`** | After reboot or `compose down` | Idempotent — already-running containers stay up. |
| **3. `pip install -e ".[dev]"`** | After pulling new deps | Skip otherwise. |
| **3. `alembic upgrade head`** | After pulling new migrations | Skips already-applied revisions. |
| **4. `make run` + `npm run dev`** | Every dev session | These are the foreground processes. |
| **5. Seed tenant** | Once per DB | The `INSERT INTO tenants` is **not** idempotent — re-running fails on the duplicate UUID. Only re-run after wiping the DB. |
| **5b. Bootstrap admin** | Once per DB (or after key loss) | User upsert is idempotent; key generation rotates the key on re-run. Use [`scripts/smoke_setup.py --regenerate-key`](../scripts/smoke_setup.py) as the safe shortcut. |

### Common scenarios

| Situation | What to repeat |
|-----------|----------------|
| Pulled new code | Step 3 only (`pip install` + `alembic upgrade head`) |
| Rebooted machine | Step 2 + step 4 |
| `docker compose down -v` (volumes wiped) | Steps 2 → 3 (migrations) → 5 → 5b |
| Lost admin API key | `python scripts/smoke_setup.py --regenerate-key` (also rotates editor + viewer if they exist) |
| Want a clean slate | `docker compose down -v && docker compose up -d db mqtt && alembic upgrade head && python scripts/smoke_setup.py --full` |

> **The shortcut for steps 5 + 5b**: `python scripts/smoke_setup.py` (or `--full` to also seed zones, telemetry model, rules, role users, and the Sprint 19 subject-telemetry opt-in). Idempotent and safe to re-run.

---

## Prerequisites

- Docker + Docker Compose
- Python 3.12+ (backend development)
- Node.js 20+ (frontend development)
- Git
- Two terminal windows/tabs

### Corporate proxy (WSL)

If you're behind a corporate proxy (e.g. `wsl-gsa-proxy`), it may intercept requests to `localhost`. Add this to your `~/.bashrc` (or run before each session):

```bash
export NO_PROXY=localhost,127.0.0.1
```

Alternatively, replace `localhost` with `127.0.0.1` in any `curl` or browser URL throughout this guide. The device simulator (`scripts/simulate_devices.py`) also requires this variable:

```bash
NO_PROXY=localhost,127.0.0.1 python scripts/simulate_devices.py --tenant-id <UUID> --devices 5 --interval 2
```

---

## 1. Clone Both Repos

```bash
git clone https://github.com/9owlsboston/TagPulse.git
git clone https://github.com/9owlsboston/TagPulse-UI.git
```

---

## 2. Start Infrastructure

```bash
cd TagPulse
docker compose up -d db mqtt
```

Wait for the DB health check (~5 seconds):

```bash
docker compose exec db pg_isready -U tagpulse
```

---

## 3. Install Backend + Run Migrations

```bash
cd TagPulse
pip install -e ".[dev]"
alembic upgrade head
```

---

## 4. Two-Terminal Development Workflow

### Terminal 1 — Backend API (port 8000)

```bash
cd ~/TagPulse
make run
```

Verify:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

### Terminal 2 — Frontend (port 5173)

```bash
cd ~/TagPulse-UI
npm install        # first time only
npm run dev
```

Open http://localhost:5173 in your browser.

Both servers hot-reload — edit Python or React and changes appear instantly.

API docs: http://localhost:8000/docs

---

## 5. Seed a Tenant

```bash
docker compose exec db psql -U tagpulse -d tagpulse -c "
INSERT INTO tenants (id, name, slug, plan, status)
VALUES (
  '11111111-1111-1111-1111-111111111111',
  'Test Corp',
  'test-corp',
  'standard',
  'active'
);
"
```

---

## 5b. Bootstrap an Admin API Key

A fresh tenant has no users, so the UI cannot log in via the **API Key** tab and the `Authorization: Bearer …` header doesn't work for any privileged API call. Bootstrap the first admin once:

```bash
# 1. Insert an admin user (idempotent — re-running just upserts the role)
docker compose exec -T db psql -U tagpulse -d tagpulse -c "
INSERT INTO users (id, tenant_id, email, name, role, status)
VALUES (gen_random_uuid(), '11111111-1111-1111-1111-111111111111',
        'admin@example.com', 'Admin', 'admin', 'active')
ON CONFLICT (tenant_id, email) DO UPDATE SET role = 'admin', status = 'active';
"

# 2. Generate a key (prints KEY=, PREFIX=, HASH= — copy them)
python -c "
from tagpulse.core.user_auth import generate_api_key
raw, prefix, h = generate_api_key('test-corp')
print(f'KEY={raw}')
print(f'PREFIX={prefix}')
print(f'HASH={h}')
"

# 3. Attach the hash + prefix to the user (paste the values from step 2)
docker compose exec -T db psql -U tagpulse -d tagpulse -c "
UPDATE users
SET api_key_hash='<HASH from step 2>',
    api_key_prefix='<PREFIX from step 2>'
WHERE tenant_id='11111111-1111-1111-1111-111111111111'
  AND email='admin@example.com';
"
```

> **Store the `KEY` value securely** — only the SHA-256 hash is kept in the DB; the plaintext key is never recoverable. Lost keys must be regenerated via the UI (**Admin → Users → Regenerate API Key**) or by repeating step 2/3.

**Log into the UI:**

1. Open http://localhost:5173 (use `127.0.0.1` if behind a localhost-intercepting proxy).
2. Pick the **API Key** tab.
3. Email: `admin@example.com`
4. API Key: paste the `KEY` value from step 2 (looks like `tp_test-corp_<hex>`).
5. Click **Sign In** — you'll land on the dashboard with the **admin** badge in the header and the full sidebar (Tenant Settings, Users, Audit Log).

**Use the same key for API calls:**

```bash
curl -H "Authorization: Bearer tp_test-corp_<your-hex>" \
     http://localhost:8000/admin/audit-logs
```

---

---

## 6. Device Simulator

TagPulse includes a device simulator that creates fake RFID readers and sends continuous tag reads.

> **Authentication:** Device creation, tag-read ingestion, and telemetry POSTs require an **admin or editor** API key (a bare `X-Tenant-ID` header authenticates as `viewer` only). Bootstrap one via [Step 5b](#5b-bootstrap-an-admin-api-key) above, then either pass `--api-key tp_test-corp_<hex>` to every script or export it once for the shell:
>
> ```bash
> export TAGPULSE_API_KEY=tp_test-corp_<your-hex>
> ```
>
> All simulator scripts (`simulate_devices.py`, `simulate_assets.py`, `simulate_inventory.py`, `load_test.py`) accept `--api-key` and fall back to `$TAGPULSE_API_KEY`.

### Seed devices + run continuous simulation

```bash
cd ~/TagPulse
python scripts/simulate_devices.py \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  --devices 5 \
  --interval 2
```

This will:
1. Create 5 simulated RFID readers (Sim-Reader-01 through 05)
2. Send a tag read from each device every ~2 seconds (±30% jitter)
3. Pick from 50 random tag IDs with realistic signal strength (-80 to -20 dBm)
4. Include sensor data (temperature always; humidity 30%, battery 10% of reads)
5. Simulate real-world noise: 10% chance a device skips a cycle, 5% chance a read drops
6. Run until you press Ctrl+C

### Seed devices only (no continuous reads)

```bash
python scripts/simulate_devices.py \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  --devices 10 \
  --seed-only
```

### Run for a fixed duration

```bash
python scripts/simulate_devices.py \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  --devices 3 \
  --interval 1 \
  --duration 60    # Run for 60 seconds then stop
```

### Simulator options

| Option | Default | Description |
|--------|---------|-------------|
| `--tenant-id` | (required) | Tenant UUID |
| `--devices` | 3 | Number of simulated RFID readers to create |
| `--interval` | 2.0 | Seconds between reads per device |
| `--duration` | 0 | Run for N seconds (0 = forever, Ctrl+C to stop) |
| `--seed-only` | false | Create devices and exit (no tag reads) |
| `--with-gps` | false | Attach a random-walk GPS `location` to every read (exercises geofence rules and the Map) |
| `--motion` | `random` | GPS motion model when `--with-gps` is set: `random` (forklift wander, ±15° jitter) or `vehicle` (mostly-straight runs with occasional 90° turns, ±3° jitter) |
| `--tags` | 50 | Size of the synthetic tag pool — set this to match the number of bound assets (e.g. `--tags 5` if `smoke_setup.py --assets 5`) so every bound asset receives a steady cadence of reads |
| `--api-key` | `$TAGPULSE_API_KEY` | Admin/editor Bearer key (required for device create + ingest since Sprint 12) |

---

## 6b. Asset Tracking Smoke Test

> **TL;DR:** run `smoke_setup.py` once, then `simulate_devices.py --with-gps`, then open the Map.

Asset position is **not** a telemetry metric — it arrives via one of three sources, all merged by the `asset_current_location` SQL view:

| Source | How it arrives | Use case |
|--------|----------------|----------|
| `rfid` (reader-bound) | Implicit — fixed reader sees the tag → asset is in that reader's zone. | Indoor warehouse, fixed readers. |
| `gps` (embedded) | Optional `location: {latitude, longitude, ...}` field on the tag-read payload. | Mobile/handheld readers with GPS. |
| `external` (out-of-band) | `POST /assets/{id}/external-position` — independent of any tag read. | TMS push, manual check-in, BLE beacon. |

### The two-script smoke test (recommended)

The smoke test is split into **setup** (one shot, idempotent) and **run** (data-push loop). You don't need to register assets, sites, or zones manually — the setup script does it for you.

#### Step 1 — One-shot bootstrap

```bash
python scripts/smoke_setup.py            # minimum: tenant + admin + assets
python scripts/smoke_setup.py --full     # populate every sidebar page
```

That single command, on a fresh tenant, will:

1. Upsert the demo tenant (`test-corp`, `11111111-1111-1111-1111-111111111111`).
2. Upsert the admin user (`admin@example.com`) and **issue an API key** — printed to stdout.
3. Enable **asset tracking** mode on the tenant (`PATCH /tenant/config`).
4. Create 5 assets (`Sim-Pallet-01` … `Sim-Pallet-05`) bound to `TAG0001` … `TAG0005` so the Map populates as soon as the data-push loop starts.

With `--full` it additionally provisions:

5. **Sites & Zones** → site `Bay Area HQ`, geofence zone `Bay Area West Block` (polygon covering the western half of the smoke-test walk box). If `Sim-Reader-*` devices already exist, also a reader-bound zone `Sim-Reader-01 Dock`.
6. **Telemetry model** for `rfid_reader` (`temperature`, `humidity`, `battery_pct`) so the Telemetry page renders charts as the simulator pushes `sensor_data`.
7. **Rules** → high-temperature threshold (>30 C, notification) + `zone.entered` and `zone.exited` notifications on the geofence zone.
8. **Role users** → `editor@example.com` (role `editor`) and `viewer@example.com` (role `viewer`), each with their own API key. Use these to verify the UI's role gating (Create/Delete buttons hidden for viewers) and that the API returns `403 Requires role: admin, editor` for viewer write attempts.
9. **Subject-scoped telemetry opt-in** (Sprint 19) → adds `lot` and `stock_item` to `tenants.telemetry_subject_kinds` via `PATCH /tenant/config`, so `simulate_devices.py --cold-chain` actually persists lot/stock_item readings and the Sprint 20 `lot.cold_chain_breach` rule can fire. Also probes the legacy `GET /telemetry-models/{device_type}` path and asserts the Sprint 28 H6 final removal (now a plain 404 — the Sprint 21 410 Gone tombstone was retired after a full retention window). Use `GET /telemetry-models/device/{device_type}` instead.

> Run `--with-subject-telemetry` standalone if you only want the Sprint 19/21 toggle without the rest of `--full`.

Together, `--full` populates **Map, Sites & Zones, Telemetry, Rules, Alerts** with non-empty data within ~1 minute of starting `simulate_devices.py --with-gps` (the wandering assets cross the geofence boundary every couple of minutes → `zone.entered` / `zone.exited` events → Alerts).

> **Note:** geofence point-in-polygon evaluation is gated by `GEOFENCE_EVALUATION_ENABLED` ([core/config.py](../src/tagpulse/core/config.py); off by default in production until the bbox index is live). It is force-enabled in `make run` and the dev `docker-compose.yml`. If you start uvicorn another way and the Alerts page stays empty, export `GEOFENCE_EVALUATION_ENABLED=true` before launching the API.

Output ends with a copy-pasteable block that contains both **UI login credentials** and the shell env for the simulator:

```text
============================================================
Smoke setup complete.
============================================================

UI login credentials:
  URL:      http://localhost:5173
  Email:    admin@example.com
  API key:  tp_test-corp_<hex>
  (NOTE: this is the only time the full key is shown — save it now)

Shell env for the simulator scripts:

  export TAGPULSE_API_KEY=tp_test-corp_<hex>

Run the data-push loop:

  python scripts/simulate_devices.py \
    --tenant-id 11111111-1111-1111-1111-111111111111 \
    --devices 5 --tags 5 --interval 2 --with-gps

Then open the Map in the UI and pan to the Bay Area
(~37.7749, -122.4194). Markers should appear within 5 seconds.
```

Use the **Email + API key** pair to sign into the web UI at `http://localhost:5173` (override with `TAGPULSE_UI_URL`). The same key, exported as `TAGPULSE_API_KEY`, authenticates the simulators.

The script is **safe to re-run** — it never deletes data, only upserts. If the admin already has a key whose plaintext you've lost, re-run with `--regenerate-key` to rotate.

| Option | Default | Description |
|--------|---------|-------------|
| `--tenant-id` | `11111111-…` | Tenant UUID |
| `--tenant-slug` | `test-corp` | Tenant slug (used in API-key prefix) |
| `--admin-email` | `admin@example.com` | Admin user email |
| `--assets` | 5 | Assets to create + bind (`Sim-Pallet-NN` ↔ `TAG000N`) |
| `--binding-prefix` | `TAG` | Bind to `TAG0001…TAG000N` (matches `simulate_devices.py`'s tag pool) |
| `--binding-kind` | `device` | Binding kind for the synthetic tags |
| `--regenerate-key` | false | Rotate the admin API key even if one exists |
| `--with-zones` | false | Create site `Bay Area HQ` + geofence zone `Bay Area West Block` (+ reader-bound zone if Sim-Reader devices exist) |
| `--with-telemetry-model` | false | Define the `rfid_reader` telemetry model (temperature + humidity + battery_pct) |
| `--with-rules` | false | Create demo rules: high-temperature threshold + zone.entered/exited notifications |
| `--with-roles` | false | Create one user per role (`admin@`, `editor@`, `viewer@example.com`), each with its own API key, for testing role gating and 403 enforcement |
| `--full` | false | Shortcut for `--with-zones --with-telemetry-model --with-rules --with-roles` — populates every sidebar page and provisions all three role users |

The script reads `TAGPULSE_SMOKE_DB_URL` (default `postgresql://tagpulse:secret@localhost:5432/tagpulse`) for the bootstrap DB writes and `TAGPULSE_API_URL` (default `http://localhost:8000`) for the HTTP calls.

#### Step 2 — Run the data-push loop

```bash
export TAGPULSE_API_KEY=tp_test-corp_<hex>   # from step 1's output

python scripts/simulate_devices.py \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  --devices 5 --tags 5 --interval 2 --with-gps
```

`--tags 5` constrains the tag pool to `TAG0001`…`TAG0005` so every read hits one of the 5 assets `smoke_setup.py` bound. If you bumped `--assets`, raise `--tags` to match (and `--devices` if you want one device per asset). Without `--tags`, only ~10% of reads hit a bound tag and the Map appears under-populated.

Each read includes a GPS fix anchored to a San Francisco city block (~110 m radius around `37.7749, -122.4194`):

```json
{
  "tag_id": "TAG0001",
  "timestamp": "...",
  "signal_strength": -45.0,
  "location": {"latitude": 37.77492, "longitude": -122.41945, "accuracy_m": 5.0, "source": "gps"}
}
```

Leave this running in its own terminal.

#### Step 3 — Watch the Map

1. Sidebar → **Map**.
2. Pan/zoom to **San Francisco** (`37.7749, -122.4194`) — or zoom out to world view, then click any marker → **Zoom here**.
3. Within ~5 s (SSE refresh) you should see:
   - **5 markers** wandering inside a small block, one per bound asset.
   - **Asset name** on hover; click → popup with **Open detail →** and **View manifest →**.
   - Markers reposition every couple of seconds as new GPS-tagged reads land.

Make sure the **Assets** layer checkbox in the map header is checked. Map config (tile provider) can be changed in **Tenant Settings → Map**; default is OpenStreetMap with a dev banner.

#### Step 4 — See the trail / replay history

The Map doesn't draw a continuous polyline in live mode. Two ways to see the trace:

| View | What you get |
|------|--------------|
| **Map → time slider** (bottom of page) | Drag back up to **24 h**. Each marker repositions to where the asset was at that timestamp (via `GET /assets/{id}/path`). |
| **Assets → [asset] → Path** tab | Tabular `(timestamp, source, zone)` list, newest first — the canonical trail view. |

#### Step 5 (optional) — Add a geofence to fire zone events

1. Sidebar → **Sites & Zones** → create a site if you don't have one.
2. **Add Zone** → Name `Block Center`, Kind `geofence`.
3. The draw map appears on the configured tile layer. Click 4–5 vertices around `37.7749, -122.4194` to enclose roughly half the random-walk block. **Done** to close.
4. Save. Within seconds you should see `zone.entered` / `zone.exited` events as your simulated assets walk in and out of the polygon.
5. (Optional) Sidebar → **Rules** → Create Rule → Condition `zone.entered` on `Block Center` → Action `notification` → Save. New entries to **Alerts** within seconds.

#### Verify via API

```bash
TENANT=11111111-1111-1111-1111-111111111111

# Latest tag reads have GPS attached
curl -H "Authorization: Bearer $TAGPULSE_API_KEY" -H "X-Tenant-ID: $TENANT" \
     "http://localhost:8000/tag-reads?limit=3" | python -m json.tool | grep -E 'tag_id|latitude'

# An asset's current location is populated
ASSET_ID=$(curl -s -H "Authorization: Bearer $TAGPULSE_API_KEY" -H "X-Tenant-ID: $TENANT" \
  "http://localhost:8000/assets" | python -c "import sys,json;print(json.load(sys.stdin)[0]['id'])")
curl -H "Authorization: Bearer $TAGPULSE_API_KEY" -H "X-Tenant-ID: $TENANT" \
     "http://localhost:8000/assets/$ASSET_ID" | python -m json.tool

# 24-hour path
curl -H "Authorization: Bearer $TAGPULSE_API_KEY" -H "X-Tenant-ID: $TENANT" \
     "http://localhost:8000/assets/$ASSET_ID/path?since=2026-05-03T00:00:00Z"
```

#### Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Map is blank but tag reads are flowing | Tags not bound to any asset — re-run `smoke_setup.py`. |
| Fewer markers than `--devices` (e.g. 4 of 5) | Tag pool larger than bound asset count — add `--tags N` matching `--assets`. |
| Markers don't appear in San Francisco | Map centered elsewhere — pan to `37.7749, -122.4194` or click any marker → **Zoom here**. |
| Markers visible but don't move | Simulator is not running with `--with-gps`, or the SSE channel dropped — reload the Map page. |
| `403 Requires role: admin, editor` | `$TAGPULSE_API_KEY` not exported (see step 1's output). |
| Geofence rule never fires | The polygon doesn't overlap the random-walk box around `37.7749, -122.4194` — redraw it bigger. |
| `Create Asset` button missing in UI | Asset tracking not enabled — re-run `smoke_setup.py` (it idempotently turns it on). |

### Alternative — Reader-bound zones (no GPS, indoor warehouse)

For the standard fixed-reader scenario without GPS. Drives `zone.entered` / `zone.exited` as assets "move" between readers, and the Map snaps markers to reader positions.

```bash
# 1. Bootstrap (creates assets & bindings)
python scripts/smoke_setup.py --assets 10

export TAGPULSE_API_KEY=tp_test-corp_<hex>   # from step 1 output

# 2. Seed 4 devices to act as zone "anchors"
python scripts/simulate_devices.py \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  --devices 4 --seed-only

# 3. In the UI: Sites & Zones → Add Zone → kind = reader_bound,
#    assign each zone to one or more of those devices.

# 4. Drive reader-hop transitions
python scripts/simulate_assets.py \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  --assets 10 --readers 4
```

| Option | Default | Description |
|--------|---------|-------------|
| `--tenant-id` | (required) | Tenant UUID |
| `--assets` | 10 | Number of simulated assets to ensure exist |
| `--readers` | 4 | Number of existing devices to use as zone anchors |
| `--interval` | 1.0 | Seconds between reads |
| `--iterations` | (forever) | Stop after N reads |
| `--api-key` | `$TAGPULSE_API_KEY` | Admin/editor Bearer key |

### Alternative — External position push (no RFID)

For an asset with no on-board reader (e.g., a truck reporting via TMS):

```bash
TENANT=11111111-1111-1111-1111-111111111111
ASSET_ID=$(curl -s -H "Authorization: Bearer $TAGPULSE_API_KEY" -H "X-Tenant-ID: $TENANT" \
  "http://localhost:8000/assets" | python -c "import sys,json;print(json.load(sys.stdin)[0]['id'])")

curl -X POST \
  -H "Authorization: Bearer $TAGPULSE_API_KEY" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{
    "latitude": 37.7749,
    "longitude": -122.4194,
    "recorded_at": "2026-05-04T12:00:00Z",
    "source": "tms",
    "accuracy_meters": 10.0
  }' \
  "http://localhost:8000/assets/$ASSET_ID/external-position"
```

The fix appears immediately on the asset's **Current Location** card and **Map** marker, badged `via tms`.

---

## 6c. Inventory Tracking Smoke Test

> Requires **Inventory tracking** enabled in the UI: **Tenant Settings → General → Inventory tracking**.

The inventory simulator stages a small distribution-center scenario end-to-end so the UI views actually exercise the inventory pipeline (not just `tag_reads`). Use this when you want **Products**, **Lots**, **Lot Expiry Queue**, **Stock Levels**, and **Stock Movements** populated without wiring real readers.

### Scenario — "Boston DC"

The simulator idempotently provisions:

* **1 site** — `Boston DC`
* **4 reader-bound zones**, each anchored to its own simulated reader:
  Receiving Dock → Cold Storage → Pick Floor → Shipping Dock
* **4 distinct products** with **distinct lot codes per product** (no shared `LOT-A`):

  | SKU | Product | Lot code | Expires in | Default units |
  |-----|---------|----------|------------|---------------|
  | `SKU-VAX-X-05ML`   | Vaccine-X 0.5 mL vial | `VAX-2604-A` | 30 d | 10 |
  | `SKU-MILK-1L`      | Milk 1L               | `MILK-0501`  | **4 d** ← near-expiry | 12 |
  | `SKU-YOGURT-4PK`   | Yogurt 4-pack         | `YOG-0428-B` | 15 d | 10 |
  | `SKU-CHEESE-200G`  | Cheese 200g           | `CHS-0301-K` | 90 d | 8 |

* **40 physical units** (sum of `units` above) with **stable SGTIN-96 EPC serials** so re-runs reuse existing `stock_items` instead of inflating to thousands of one-shot rows.
* **A tenant-scope `tag_data_mapping`** so ingestion decodes `tag_data.lot` into the matching `lot_code`.

Each unit is given a randomised per-unit timeline through the warehouse:

```
Receiving Dock ──▶ Cold Storage ──▶ Pick Floor ──▶ Shipping Dock
   (all units)        (all units)       (~70%)         (~50%)
```

Reads happen at the **zone-bound reader for that stage**, so the UI's per-zone occupancy on Stock Levels reflects real movement instead of dumping everything into an "unzoned" bucket. Stage transitions emit `subject.zone_changed` events and `stock_movements` ENTER / TRANSFER / EXIT rows.

### Run it (local)

```bash
python scripts/simulate_inventory.py \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  --duration 240
```

No `--devices` flag — the simulator owns its own readers and zones by name (`DC-Receiving`, `DC-ColdStorage`, `DC-PickFloor`, `DC-Shipping`).

### Run it against deployed Azure (`azd-job`)

The simulator is bundled into the api/tools image, so once you've deployed an env you can run the full scenario in-VNet via [`scripts/azd-job.sh`](../scripts/azd-job.sh). The tools job pulls `TAGPULSE_API_KEY` from Key Vault automatically — no `--api-key` needed:

```bash
scripts/azd-job.sh dev simulate_inventory.py -- \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  --duration 240
```

Prereqs (one-time per env):

* `azd up` (or the equivalent provision + deploy) has succeeded for the env.
* The demo tenant has been seeded — typically: `scripts/azd-job.sh dev smoke_setup.py -- --full --with-roles --with-subject-telemetry --regenerate-key`.
* Tenant Settings → General → **Inventory tracking** is enabled (Tracking Modes UI, or `PATCH /tenant/config`).

The wrapper streams stdout back to your terminal so you'll see the running per-zone status line (`Receiving=…  Cold Storage=…  Pick Floor=…  Shipping=…`). `--duration` is **not optional** when running against deployed envs — leaving it open-ended saturates the api until the operator notices.

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--tenant-id` | (required) | Tenant UUID |
| `--duration` | 240 | Total simulation seconds; movements are spread over this window |
| `--units` | 40 | Override total stock-unit pool (proportionally scaled across the 4 lots) |
| `--tick` | 0.5 | Scheduler sleep interval in seconds |
| `--seed-only` | false | Create site / zones / devices / products / lots / mapping and exit (no reads) |
| `--seed` | (none) | Random seed for reproducible runs |
| `--api-key` | `$TAGPULSE_API_KEY` | Admin/editor Bearer key (required for site/zone/product/lot writes; auto-injected from KV inside `azd-job`) |

The script is **idempotent** — re-running reuses the existing site, zones, devices, products, lots, mapping, and stock items.

### Verify in the UI

| View | What you should see |
|------|---------------------|
| **Sites & Zones → Boston DC** | 4 reader-bound zones, each with its anchor reader; current occupants update as the run progresses. |
| **Products** | The 4 SKUs above with distinct GTIN-14s and categories. |
| **Products → [SKU] → Lots** | One lot per product with the lot code and expiry shown in the table above. |
| **Lot Expiry Queue** | The Milk lot (`MILK-0501`) lights up in the **7 days** filter; other lots in the **30/90 days** filters. |
| **Stock Levels** | Per-zone counts shifting from Receiving → Cold Storage → Pick Floor → Shipping across the run. |
| **Stock Movements** | ENTER / TRANSFER / EXIT rows with from/to zones. |
| **Admin → Tag Data Mappings** | The seeded `tag_data.lot → lot_code` mapping. |

### Verify via API

```bash
# Lots expiring in next 30 days (Milk should appear)
curl -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  "http://localhost:8000/inventory/lot-expiry?within_days=30"

# Stock levels pivot
curl -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  http://localhost:8000/inventory/stock-levels

# Recent movements
curl -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  "http://localhost:8000/inventory/stock-movements?limit=20"
```

### Trigger an expiry alert

Create a `stock.expiring_within` rule before (or during) the run:

```bash
curl -X POST \
  -H "Authorization: Bearer tp_test-corp_<your-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Lot expiring soon",
    "condition_type": "stock.expiring_within",
    "condition_config": {"within_days": 30},
    "action_type": "notification",
    "action_config": {}
  }' \
  http://localhost:8000/rules
```

The next worker tick will flag the simulator's lots and you'll see entries in **Alerts**.

---

## 7. Manual Testing

### Register a device manually

```bash
curl -X POST \
  -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  -H "Content-Type: application/json" \
  -d '{"name": "Manual-Reader-01", "device_type": "rfid_reader"}' \
  http://localhost:8000/device-registry
```

### Ingest a tag read (HTTP)

```bash
curl -X POST \
  -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "<DEVICE_ID>",
    "tag_id": "TAG001",
    "timestamp": "2026-04-26T12:00:00Z",
    "signal_strength": -45.0
  }' \
  http://localhost:8000/tag-reads
```

### Ingest a tag read (MQTT)

```bash
docker compose exec mqtt mosquitto_pub \
  -t "tenants/11111111-1111-1111-1111-111111111111/devices/<DEVICE_ID>/tag-reads" \
  -m '{"tag_id": "TAG002", "timestamp": "2026-04-26T12:01:00Z", "signal_strength": -50.0}'
```

---

## 8. Verify the Full Flow

```bash
# List tag reads
curl -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  http://localhost:8000/tag-reads

# Device health
curl -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  http://localhost:8000/device-health

# Analytics (read frequency)
curl -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  http://localhost:8000/analytics/read-frequency

# Prometheus metrics
curl http://localhost:8000/metrics

# Readiness check
curl http://localhost:8000/health/ready
```

---

## 9. Create a Rule + Test Alerting

```bash
# Create a threshold rule
curl -X POST \
  -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Weak signal alert",
    "condition_type": "threshold",
    "condition_config": {"field": "signal_strength", "operator": "lt", "value": -60},
    "action_type": "notification",
    "action_config": {}
  }' \
  http://localhost:8000/rules

# Ingest a tag read with weak signal (triggers the rule)
curl -X POST \
  -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "<DEVICE_ID>",
    "tag_id": "TAG003",
    "timestamp": "2026-04-26T12:02:00Z",
    "signal_strength": -75.0
  }' \
  http://localhost:8000/tag-reads

# Check alerts
curl -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  http://localhost:8000/alerts
```

### Cold-chain breach (Sprint 20 telemetry-threshold rule)

Subject-scoped telemetry rules fire on `telemetry_readings` rows whose
`metric_value` crosses a threshold. Built-in template
`lot.cold_chain_breach` lights up a refrigerated-dairy alert end-to-end
without writing JSON by hand.

```bash
TENANT=11111111-1111-1111-1111-111111111111

# 1. Opt the tenant into lot-scoped telemetry (Sprint 19).
#    Default is ["device"]; the rule will be ACCEPTED but never match
#    until "lot" is in the list. (Skip this step if you've already run
#    `python scripts/smoke_setup.py --full` or
#    `--with-subject-telemetry` -- it does the same PATCH for you and
#    also probes the legacy GET /telemetry-models/{device_type} path,
#    which now 404s after Sprint 28 H6 removed the Sprint 21 410
#    tombstone.)
curl -X PATCH \
  -H "Authorization: Bearer $TAGPULSE_API_KEY" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{"telemetry_subject_kinds": ["device", "lot"]}' \
  http://localhost:8000/tenant/config

# 2. Pull the template (defaults: temperature_c > 8 C, 15 min cooldown).
curl -H "Authorization: Bearer $TAGPULSE_API_KEY" -H "X-Tenant-ID: $TENANT" \
  http://localhost:8000/rule-templates/lot.cold_chain_breach

# 3. POST the template body to /rules (edit value/cooldown_s first if you want).
curl -X POST \
  -H "Authorization: Bearer $TAGPULSE_API_KEY" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Refrigerated dairy cold-chain",
    "condition_type": "telemetry.threshold",
    "condition_config": {
      "subject_kind": "lot",
      "metric_name": "temperature_c",
      "operator": "gt",
      "value": 8.0,
      "cooldown_s": 900
    },
    "action_type": "notification",
    "action_config": {}
  }' \
  http://localhost:8000/rules

# 4. Drive the breach. simulate_devices.py --cold-chain spins up a
#    synthetic milk lot, binds an EPC, and drifts temperature 4 C -> 9 C
#    (default period 10 s; tune with --cold-chain-period). The lot/product/
#    binding are upserted on first run -- safe to re-run.
python scripts/simulate_devices.py \
  --tenant-id $TENANT \
  --devices 2 --tags 5 --interval 2 \
  --cold-chain --cold-chain-period 5

# 5. Watch the alert land.
curl -H "Authorization: Bearer $TAGPULSE_API_KEY" -H "X-Tenant-ID: $TENANT" \
  "http://localhost:8000/alerts?status=firing"
```

The alert appears within ~7-8 minutes (8 C threshold, 0.05 C/cycle drift,
5 s period). Asset-temperature works the same way using template
`asset.high_temperature` (defaults: subject_kind=`asset`,
`temperature_c > 60 C`, 10 min cooldown).

---

## Alternative: Full Stack with Docker Compose

Run everything in containers (no local Python/Node needed):

```bash
cd TagPulse
docker compose up -d
```

Run migrations (required on first start):

```bash
pip install -e ".[dev]"    # needed for alembic CLI
DATABASE_URL=postgresql+asyncpg://tagpulse:secret@localhost:5432/tagpulse alembic upgrade head
```

Seed a test tenant:

```bash
docker compose exec db psql -U tagpulse -d tagpulse -c "
INSERT INTO tenants (id, name, slug, plan, status)
VALUES (
  '11111111-1111-1111-1111-111111111111',
  'Test Corp',
  'test-corp',
  'standard',
  'active'
);
"
```

| Service | URL |
|---------|-----|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |

---

## Service Reference

| Service | Port | Purpose |
|---------|------|---------|
| Frontend (dev) | 5173 | Vite dev server with hot-reload |
| Frontend (prod) | 3000 | nginx serving built React SPA |
| Backend API | 8000 | FastAPI (all REST endpoints) |
| API Docs | 8000/docs | Swagger UI (auto-generated) |
| Health | 8000/health/ready | Readiness probe (DB + MQTT + EventBus) |
| Health Detail | 8000/health/detail | Queue sizes, meter status |
| Metrics | 8000/metrics | Prometheus / OpenTelemetry |
| TimescaleDB | 5432 | `psql -U tagpulse -d tagpulse` |
| MQTT Broker | 1883 | Mosquitto (`mosquitto_pub` / `mosquitto_sub`) |

---

## Quality Gates

### Backend

```bash
cd TagPulse
make check    # lint + typecheck + test (140 tests)
```

### Frontend

```bash
cd TagPulse-UI
npm run check    # lint + typecheck + test
```

---

## Load Testing

TagPulse includes a load test harness for stress testing the ingestion pipeline.

### Burst test — max throughput

```bash
python scripts/load_test.py \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  -w 10 --total 1000
```

Sends 1,000 reads as fast as possible with 10 concurrent workers.

### Sustained rate test

```bash
python scripts/load_test.py \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  -w 20 --rps 200 --duration 60
```

Holds 200 req/s for 60 seconds (12,000 total reads).

### Ramp-up test

```bash
python scripts/load_test.py \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  -w 30 --rps 50 --ramp 50 --ramp-step 10 --rps-max 500 --duration 120
```

Starts at 50 rps, adds 50 every 10 seconds, caps at 500 rps.

### Load test options

| Option | Default | Description |
|--------|---------|-------------|
| `--tenant-id` | (required) | Tenant UUID |
| `-w`, `--workers` | 10 | Concurrent async workers |
| `--devices` | 20 | Number of load-test devices to create |
| `--total` | 0 | Total requests (0 = use `--duration`) |
| `--duration` | 30 | Test length in seconds |
| `--rps` | 0 | Target requests/sec (0 = max throughput) |
| `--ramp` | 0 | Increase rps by this amount each step |
| `--ramp-step` | 10 | Seconds between ramp increases |
| `--rps-max` | 10x rps | Cap when ramping |
| `--api-key` | `$TAGPULSE_API_KEY` | Admin/editor Bearer key (required for device create + ingest) |

Results include throughput, p50/p95/p99 latencies, status codes, and error breakdown.

---

## Remote IoT Testing with ngrok

Expose your local TagPulse instance to the internet so real IoT devices (or remote simulators) can send data without deploying to a cloud host.

### Prerequisites

- [ngrok](https://ngrok.com/) installed and authenticated (`ngrok config add-authtoken <TOKEN>`)
- TagPulse backend running locally on port 8000
- MQTT broker running locally on port 1883

### Tunnel the HTTP API (for devices using REST ingestion)

```bash
ngrok http 8000
```

ngrok prints a public URL like `https://a1b2c3d4.ngrok-free.app`. Devices can now push tag reads over the internet:

```bash
curl -X POST \
  -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "<DEVICE_ID>",
    "tag_id": "TAG-REMOTE-001",
    "timestamp": "2026-04-26T14:00:00Z",
    "signal_strength": -42.0
  }' \
  https://a1b2c3d4.ngrok-free.app/tag-reads
```

API docs are also available at `https://a1b2c3d4.ngrok-free.app/docs`.

### Tunnel MQTT (for devices using MQTT ingestion)

```bash
ngrok tcp 1883
```

ngrok prints a TCP address like `tcp://0.tcp.ngrok.io:12345`. Point your RFID reader's MQTT client at it:

| Setting | Value |
|---------|-------|
| Broker host | `0.tcp.ngrok.io` |
| Broker port | `12345` (from ngrok output) |
| Topic | `tenants/{tenant_id}/devices/{device_id}/tag-reads` |
| QoS | 1 |

Test from any remote machine:

```bash
mosquitto_pub \
  -h 0.tcp.ngrok.io -p 12345 \
  -t "tenants/11111111-1111-1111-1111-111111111111/devices/<DEVICE_ID>/tag-reads" \
  -m '{"tag_id": "TAG-REMOTE-002", "timestamp": "2026-04-26T14:01:00Z", "signal_strength": -55.0}'
```

### Tunnel both simultaneously

Open two terminal tabs:

```bash
# Terminal A — HTTP API
ngrok http 8000

# Terminal B — MQTT broker
ngrok tcp 1883
```

Or use an ngrok config file (`~/.config/ngrok/ngrok.yml`):

```yaml
version: 3
tunnels:
  tagpulse-api:
    proto: http
    addr: 8000
  tagpulse-mqtt:
    proto: tcp
    addr: 1883
```

Then start both tunnels at once:

```bash
ngrok start tagpulse-api tagpulse-mqtt
```

### Security considerations

- **ngrok free tier** URLs are public — anyone with the URL can hit your API. Use a Bearer API key (`Authorization: Bearer tp_{slug}_{hex}`, see Step 5b "Bootstrap an Admin API Key") to restrict access.
- **Do not** leave tunnels running unattended in production.
- For persistent testing, consider ngrok's reserved domains (`ngrok http --url=tagpulse.ngrok-free.app 8000`) so the URL stays stable across restarts.
- MQTT tunnels are unauthenticated by default. Add Mosquitto `password_file` or `allow_anonymous false` in `docker/mosquitto.conf` before exposing over the internet.

### Verify remote ingestion

After sending data through ngrok, confirm it arrived:

```bash
curl -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  http://localhost:8000/tag-reads?tag_id=TAG-REMOTE-001
```

---

## Tear Down

```bash
cd TagPulse
docker compose down       # Stop containers
docker compose down -v    # Stop + wipe all data (fresh start)
```
