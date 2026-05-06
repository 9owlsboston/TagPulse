# TagPulse User Guide

## Table of Contents

- [Getting Started](#getting-started)
- [First-Time Admin Workflow](#first-time-admin-workflow)
- [Dashboard](#dashboard)
- [Devices](#devices)
- [Telemetry](#telemetry)
- [Telemetry Models](#telemetry-models)
- [Rules](#rules)
- [Alerts](#alerts)
- [Integrations](#integrations)
- [Assets](#assets)
- [Sites & Zones](#sites--zones)
- [Map](#map)
- [Inventory](#inventory)
- [Tenant Settings](#tenant-settings)
- [Usage & Quotas](#usage--quotas)
- [User Management](#user-management)
- [Audit Log](#audit-log)

---

## Getting Started

### Authentication Model

TagPulse uses **password-less, API-key-based authentication**. There are no username/password prompts — access is controlled by tenant ID and optional API keys.

**Two ways to authenticate:**

| Method | How | Role | Use Case |
|--------|-----|------|----------|
| **Tenant ID** | Switch to the **Tenant ID** tab on the login screen | `viewer` (read-only) | Browsing dashboards and data |
| **API Key** | Use the **API Key** tab with email + API key | Assigned role (`admin`, `editor`, or `viewer`) | Full access in the UI and API |

When you log in through the UI with just a tenant ID, you are automatically a **viewer** — you can see everything but cannot create or modify resources (devices, rules, integrations, etc.).

### Logging In

The login screen has two tabs:

**API Key tab** (default) — for full role-based access:
1. Open `http://localhost:3000` (Docker) or `http://localhost:5173` (dev server).
2. Enter your **Email** and **API Key** (e.g., `tp_test-corp_...`).
3. Click **Sign In**. You'll be logged in with your assigned role (admin/editor/viewer).
4. The header shows your name, role badge, and tenant name.

**Tenant ID tab** — for read-only browsing:
1. Switch to the **Tenant ID** tab.
2. Enter your tenant UUID (e.g., `11111111-1111-1111-1111-111111111111`).
3. Click **Continue as Viewer**.

Click **Logout** in the top-right to end your session. JWT sessions expire after 1 hour — you'll be redirected to login automatically.

### Upgrading to Full Access

To get write access, you need a user account with an API key.

**Via the UI (recommended):** If you're already logged in as an admin, navigate to **Users** in the sidebar → **Create User** → then generate an API key from the user detail page.

**Via the API:** Use curl with an existing admin API key:

```bash
# 1. Create a user
curl -X POST http://localhost:8000/users \
  -H "Authorization: Bearer <your-admin-api-key>" \
  -H "Content-Type: application/json" \
  -d '{"email": "colleague@example.com", "name": "New User", "role": "editor"}'

# 2. Generate an API key for the new user (note the user ID from step 1)
curl -X POST http://localhost:8000/users/<user-id>/api-key \
  -H "Authorization: Bearer <your-admin-api-key>"
```

The API key (format: `tp_{slug}_{random}`) is returned **once** — store it securely. Use it as a Bearer token for authenticated requests.

> **Bootstrap tip:** For a fresh tenant with no users yet, bootstrap the first admin with an API key:
> ```bash
> # 1. Create the admin user (or find existing one)
> USER_ID=$(docker compose exec -T db psql -U tagpulse -d tagpulse -tAc "
> INSERT INTO users (id, tenant_id, email, name, role, status)
> VALUES (gen_random_uuid(), '11111111-1111-1111-1111-111111111111',
>         'admin@example.com', 'Admin', 'admin', 'active')
> ON CONFLICT (tenant_id, email) DO UPDATE SET role = 'admin'
> RETURNING id;
> ")
>
> # 2. Generate and store an API key
> python -c "
> from tagpulse.core.user_auth import generate_api_key
> raw_key, prefix, key_hash = generate_api_key('test-corp')
> print(f'API_KEY={raw_key}')
> print(f'PREFIX={prefix}')
> print(f'HASH={key_hash}')
> " | while IFS='=' read -r k v; do eval "$k=$v"; done
>
> docker compose exec -T db psql -U tagpulse -d tagpulse -c "
> UPDATE users SET api_key_hash='$HASH', api_key_prefix='$PREFIX'
> WHERE id='$USER_ID';
> "
>
> echo "Your admin API key: $API_KEY"
> echo "Store it securely — it cannot be retrieved again."
> ```
> Use the printed key as `Authorization: Bearer <key>` for all admin operations.

### Navigation

The sidebar on the left provides access to all sections: Dashboard, Devices, Telemetry, Telemetry Models, Rules, Alerts, Integrations, and Usage.

### User Roles

| Role     | Permissions                                      |
|----------|--------------------------------------------------|
| `admin`  | Full access — manage users, devices, and config  |
| `editor` | Register/update devices, create rules/integrations |
| `viewer` | Read-only access to data and dashboards          |

---

## First-Time Admin Workflow

You just logged in as an admin against a fresh tenant. The sidebar is full of options but every list is empty. Here's the recommended sequence to get from "empty" to "live data flowing in" — pick the asset, inventory, or both branches that match what you're tracking.

> **Time estimate:** ~15 minutes for the simulator path; longer if you're wiring real readers.

### Step 1 — Choose your tracking modes

**Where:** sidebar → **Tenant Settings**.

Toggle **Asset tracking** and/or **Inventory tracking** on. These flags drive which sidebar entries appear:

| Mode | Unlocks sidebar entries |
|------|-------------------------|
| `asset` | **Assets**, **Sites & Zones**, **Map** |
| `inventory` | **Products**, **Lot Expiry**, **Stock Levels**, **Stock Movements** |

You can change this later — disabling a mode just hides the UI; existing data is preserved.

#### Telemetry subject opt-in (Sprint 19)

In the same panel, **Telemetry subjects** controls which non-device subjects ingestion fans tag-borne telemetry out to. Defaults to `["device"]` (Sprint 14 behavior — every reading lands keyed on the reporting reader). Tick **`asset`** to also key cold-chain readings to the asset that carries the tag, **`lot`** for lot-level cold-chain (the most common cold-chain shape), or **`stock_item`** for serial-level. Without the opt-in the rules editor still accepts a `telemetry.threshold` rule on those subjects, but no event will ever match. The flip propagates to all API workers within ~30 s (the writing worker sees it immediately).

### Step 2 — (Optional) Configure the map provider

**Where:** sidebar → **Tenant Settings** → **Map** tab. Only relevant if you enabled **Asset tracking**.

By default the **Map** page falls back to OpenStreetMap (a dev-only banner is shown in the footer). For production:

- Pick a tile provider (MapTiler, Mapbox, Azure Maps, etc.).
- Paste your tile-URL template and (if required) an API key.
- The page persists the config to `tenant.map_config` — `Save`, then reload **Map**.

### Step 3 — Define telemetry models

**Where:** sidebar → **Telemetry Models** → **Create Model**.

Tell TagPulse what metrics each device type sends. For an `rfid_reader`:

| Name | Unit | Min | Max | Description |
|------|------|-----|-----|-------------|
| `signal_strength` | dBm | -100 | 0 | RSSI of the most recent read |
| `temperature` | °C | -40 | 85 | Reader internal temperature |
| `battery` | % | 0 | 100 | (Mobile readers only) |

Skipping this step is fine — ingestion still works — but rule conditions and the Telemetry chart get a much better experience when models exist.

> **Subject-scoped models (Sprint 18+).** Each model carries a `subject_kind` (defaults to `device`). For a cold-chain milk lot, define a `lot`-scoped model named `temperature_c` with min/max `-30/30` — the rules editor's **Cold-chain breach (lot)** template will surface it as a target. The `device_type` field is **required only when** `subject_kind = device`; for `asset` / `lot` / `stock_item` models it must be omitted (the API rejects the combination). The legacy `GET /telemetry-models/{device_type}` endpoint is removed in Sprint 21 — use `GET /telemetry-models/device/{device_type}`.

### Step 4 — Lay out sites & zones (asset mode)

**Where:** sidebar → **Sites & Zones**.

1. **Create a site** (e.g., "Boston DC", "Production Floor"). One per physical building.
2. **Create zones** within each site. Three kinds:
   - **`reader_bound`** — implicitly defined by which fixed readers see the tag (no polygon needed; pick the readers that bound the zone). **Requires the readers to be registered first** — if you haven't done Step 5 yet, create the site now and come back to add `reader_bound` zones after registering devices (or start with `geofence` / `virtual` zones, which have no device dependency).
   - **`geofence`** — draw a polygon on the map. Click vertices, **Undo** if you misclick, **Done** to close. The server validates the GeoJSON.
   - **`virtual`** — admin-only logical grouping (e.g., "Cold storage").

Zones power the **Map**, geofence rules (Step 8), and dwell tracking.

### Step 5 — Register devices

**Where:** sidebar → **Devices** → **Register Device**.

For each physical reader:

- **Name** — human-friendly (e.g., `Dock-Door-A`).
- **Device Type** — must match a telemetry model (e.g., `rfid_reader`).
- **Mobility** — `fixed` (warehouse readers), `mobile` (handhelds, vehicle-mounted), or `unknown`.
- **Metadata** — JSON freeform (location, asset tag, owner email, etc.).

After save, the device detail page shows a **Token** (paste into your reader's TagPulse client config) and, optionally, a **Cert** card if you're using mTLS — admins can attach an X.509 PEM there (only the SHA-256 thumbprint + RFC 4514 subject are stored; the PEM is discarded immediately).

> **No physical readers yet?** Skip to Step 7 — the device simulator (`scripts/simulate_devices.py`) creates fake readers and feeds tag reads on its own.

### Step 6a — Asset tracking: define what you're tracking

**Where:** sidebar → **Assets**.

1. **Create an asset** (e.g., "Forklift #4", "Pallet PLT-00123").
2. **Add a tag binding**: enter the EPC, TID, or device-internal tag ID printed on the RFID label. An asset can hold **multiple active bindings** at once (e.g. a primary EPC label plus a TID, or a redundant backup tag). The uniqueness rule is per *value*, not per asset: a given `binding_value` can only be active on **one asset per tenant** at a time. Bindings can be unbound and re-bound for tag swaps.

Once bound, every tag read from the field automatically attributes to the asset. The Asset detail page shows the **Path** (last 24 h trail), **Covers Zones**, and all active bindings.

### Step 6b — Inventory tracking: products, lots, stock items

**Where:** sidebar → **Products**, then **Stock Levels**.

1. **Create a product** (SKU + display name). Optionally add a **lot** (manufacturing batch with expiry date).
2. (Sprint 15b) **Tag-data mappings** under **Admin → Tag Data Mappings** let you specify how raw EPC bits map to product-and-lot. Skip this initially — you can also create stock items manually.
3. **Stock items** (per-tag inventory units) appear automatically as soon as bound tags are read; the **Stock Levels** view rolls them up by product/zone.
4. (Sprint 15b) Use the new **`parent_stock_item_id`** field on `POST /stock-items` (or `PATCH /stock-items/{id}`) when packing cases into pallets — this builds the case/pallet containment tree the manifest API will traverse.

The **Lot Expiry** queue surfaces lots within 30 days of expiry (configurable per product).

### Step 7 — Generate live data

**With real readers:** point them at `mqtt://<your-broker>:1883/tenants/<tenant-id>/devices/<device-id>/tag-reads` using the device token from Step 5.

**With the simulator** (no hardware needed):

```bash
cd ~/TagPulse
python scripts/simulate_devices.py \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  --devices 5 \
  --interval 2
```

Within seconds the **Dashboard** "Reads Today" widget will start incrementing and the **Telemetry** line chart will fill in.

> **Asset tracking?** Position is not a telemetry metric — it travels via three sources (`rfid`, `gps`, `external`). Pick the path that matches your scenario:
>
> - **Reader-bound zones** (indoor warehouse) — run [`scripts/simulate_assets.py`](../scripts/simulate_assets.py) to randomise reader hops and fire `zone.entered` / `zone.exited`.
> - **GPS-tagged reads** (geofences) — re-run `simulate_devices.py` with `--with-gps` to embed lat/lon in every read.
> - **External push** (no reader, e.g., TMS) — `POST /assets/{id}/external-position` with `{latitude, longitude, recorded_at, source}`.
>
> See [docs/quickstart.md → 6b. Asset Tracking Smoke Test](quickstart.md#6b-asset-tracking-smoke-test) for full commands.

> **Inventory tracking?** Run [`scripts/simulate_inventory.py`](../scripts/simulate_inventory.py) to seed a 3-SKU catalog with near-expiry lots, register a `lot_code` tag-data mapping, and stream SGTIN-96 EPCs so **Products**, **Lot Expiry**, **Stock Levels**, and **Stock Movements** all populate. See [docs/quickstart.md → 6c. Inventory Tracking Smoke Test](quickstart.md#6c-inventory-tracking-smoke-test) for full commands.

### Step 8 — Add rules & alerts

**Where:** sidebar → **Rules** → **Create Rule**.

Useful starter rules:

| Rule | Condition | Action |
|------|-----------|--------|
| Reader offline | Absence > 5 minutes (any tag) | Notification |
| RSSI cliff | Rate change ≥ 50% in 10 min on `signal_strength` | Webhook to Slack |
| Asset left zone | `zone.exited` from "Cold storage", subject_kinds = `asset` | Email |
| Pallet idle in dock | `zone.dwell_exceeded` ≥ 60 min in "Dock-Door-A" | Notification |
| Cold-chain breach | `telemetry.threshold` on `subject_kind=lot`, `temperature_c > 8`, cooldown 15 min | Notification + webhook |

The last row is a Sprint 20 built-in template — see [Rules → Templates](#rules) for the one-click create flow. Other Sprint 20 templates: **Asset over-temperature** (`subject_kind=asset`, `temperature_c > 60`).

Triggered alerts appear under **Alerts** with severity color-coding; click **Acknowledge** to clear.

### Step 9 — Push events outward

**Where:** sidebar → **Integrations** → **Create Integration**.

Three types:

- **Webhook** — POST JSON to any HTTPS endpoint (Slack, PagerDuty, your ERP).
- **SSE** — long-lived stream consumers can subscribe to in-browser.
- **Export** — scheduled CSV/JSON dump (cron schedule, format).

The **Deliveries** tab on each integration shows attempts, status, and the last error if any.

### Step 10 — Invite your team

**Where:** sidebar → **Users** → **Create User**.

For each teammate: email + name + role (`viewer` for read-only, `editor` for everyday operators, `admin` for ops/security). Then open the user detail page → **Generate API Key** → copy the key from the one-time banner and send it via your password manager.

See [User Management](#user-management) below for full lifecycle (regenerate, revoke, deactivate).

### Step 11 — Verify the audit trail

**Where:** sidebar → **Audit Log** → **Device security events** preset.

You should see entries for the device approvals, token rotations, and (if you used the cert workflow) `device.cert_attached`. This is the canonical place to come back to after any incident — see [Audit Log](#audit-log) below.

### You're live

At this point the platform is fully bootstrapped. Day-to-day operations live in **Dashboard**, **Map**, **Alerts**, and **Audit Log**. Configuration changes go through **Tenant Settings**, **Telemetry Models**, **Rules**, and **Integrations** — every change is captured in the audit log automatically.

---

## Dashboard

The dashboard provides a real-time overview of your IoT deployment. Widgets are **draggable and resizable** — arrange them to suit your workflow.

### Widgets

| Widget               | Description                                                    |
|----------------------|----------------------------------------------------------------|
| **Total Devices**    | Count of all registered devices                                |
| **Reads Today**      | Tag reads aggregated per hour for the current day               |
| **Open Alerts**      | Number of unacknowledged alerts                                |
| **Anomaly Count**    | Anomalies detected by the analytics module                     |
| **Recent Alerts**    | Last 5 open alerts showing device, severity, message, and time |
| **Device Health**    | Per-device health cards: reads/hour, error rate, connection state |
| **Live Event Counter** | Real-time count of incoming tag reads (updates via SSE)      |

Data refreshes automatically — the live event counter updates in real time, while other widgets refresh every 30 seconds.

---

## Devices

### Viewing Devices

Navigate to **Devices** to see all registered readers.

**Columns:** Name, Type, Status (active/decommissioned), Connection State (online/offline), Last Seen.

**Filtering:**
- Type in the search box to filter by device name.
- Use the status dropdown to show only active or decommissioned devices.

Click any row to open the device detail page.

### Registering a New Device

1. Click the **Register Device** button.
2. Fill in the form:
   - **Name** — a descriptive label (required, max 255 characters).
   - **Device Type** — e.g., `rfid_reader` (required).
   - **Firmware Version** — optional.
   - **Metadata** — optional JSON for hardware, network, ownership, and maintenance attributes (see the recommended template below).
3. Click **Submit**. The device is created with `active` status.

#### Recommended `metadata` template

The `metadata` field is freeform JSON, but TagPulse recommends the following common-denominator schema, which aligns with **GS1 EPCIS / CBV**, **OPC UA for Auto-ID** (IEC 62541-100), **W3C WoT Thing Description**, and **Azure DTDL** (IoT Plug and Play). Use as much or as little of it as fits your deployment.

```json
{
  "manufacturer": "Zebra",
  "model": "FX9600",
  "serial_number": "FX9600-21A1234567",
  "firmware_version": "3.16.18",
  "hardware_revision": "Rev C",

  "antennas": [
    { "port": 1, "label": "North", "polarization": "circular", "gain_dbi": 6, "cable_loss_db": 1.2 },
    { "port": 2, "label": "South", "polarization": "circular", "gain_dbi": 6, "cable_loss_db": 1.2 }
  ],

  "rf_region": "FCC",
  "protocols": ["EPC-Gen2v2", "ISO-18000-63"],
  "tx_power_dbm": 30,

  "indoor_position": { "floor": 1, "x_m": 12.4, "y_m": 8.1 },

  "network": {
    "mac": "00:1A:2B:3C:4D:5E",
    "ip": "10.4.12.45",
    "uplink": "ethernet"
  },

  "ownership": {
    "owner_email": "ops@example.com",
    "cost_center": "WH-OPS-100",
    "asset_tag": "IT-04421",
    "purchase_date": "2024-08-15",
    "warranty_expires": "2027-08-15"
  },

  "maintenance": {
    "last_serviced": "2026-02-10",
    "next_service_due": "2026-08-10",
    "install_date": "2024-09-02"
  },

  "tags": ["dock-door", "inbound", "high-traffic"]
}
```

> **What does NOT belong in `device.metadata`** — anything describing **where** the reader sits in your facility hierarchy. Site name, site code, GLN of the read point, zone name, lat/lon, address, etc. all belong on the **Site** or **Zone** entity (see [Sites & Zones](#sites--zones) below for their own `metadata` templates). The reader's location is **derived** from the `reader_bound` zones it belongs to — keeping it out of `device.metadata` avoids two sources of truth that drift apart.
>
> The one exception is `indoor_position` (floor + local x/y inside the building) — that's a property of the physical mount point, not the zone, so it stays on the device.

**Vertical-specific alignment** — if your customer already standardises on one of these frameworks, mirror its field names so exports/integrations are zero-friction:

| Framework | Mirror these field names |
|-----------|--------------------------|
| **GS1 EPCIS 2.0 / CBV** (retail, supply chain) | Use `gln` on **Site/Zone** (not on device); reader exports map to `readPoint` and `bizLocation`. |
| **OPC UA for Auto-ID** (manufacturing, OT) | `manufacturer`, `model`, `serial_number`, `hardware_revision`, `firmware_version`, `antennas[].label` map 1:1 to `RfidReaderDeviceType` properties. |
| **W3C WoT Thing Description** | The whole block can be embedded under `properties.metadata` of the device's TD JSON-LD. |
| **Azure DTDL / IoT Plug and Play** | `manufacturer`, `model`, `serial_number`, `firmware_version` map to the standard `dtmi:azure:DeviceManagement:DeviceInformation;1` interface. |
| **MQTT Sparkplug B** | Birth-certificate `Node Control/Hardware Make`, `…/Model`, `…/Serial Number`, `…/HW Version`, `…/SW Version` map to the corresponding fields. |

### Device Detail

The detail page has three tabs:

**Overview** — name, type, status, connection state, firmware, metadata (as formatted JSON). Admins see a **Decommission** button to retire a device.

**Telemetry** — a line chart of signal strength for the device's last 100 reads.

**Health** — reads/hour, error rate (%), connection state, and last seen timestamp.

---

## Telemetry

### Telemetry Dashboard

Navigate to **Telemetry** for an aggregate view of tag read activity.

- **Line chart** with one series per device, bucketed by hour.
- Use the **device selector** to focus on a single device or view all.
- Use the **time range picker** to adjust the window.

The chart updates in real time as new tag reads arrive.

### Data Explorer

Click **Explore** (from the Telemetry page) for detailed data access.

**Filters:**
- **Device** — select a specific device or all.
- **Tag ID** — search and multi-select specific tags.
- **Signal Strength** — set a min/max range.
- **Time Range** — start and end date-time.
- **Limit** — number of results (1–1000, default 100).

**Views:**
- **Table** — columns: Tag ID, Device, Timestamp, Signal Strength. Sortable and paginated (100 per page).
- **Chart** — line chart of signal strength over time.

Toggle between views using the button in the toolbar.

**Export:** Click **Export to CSV** to download the current result set.

---

## Telemetry Models

Telemetry models define the expected metrics schema for each subject type (e.g., what readings an `rfid_reader` device sends, or what `temperature_c` looks like at the lot level for cold-chain).

### Viewing Models

The table shows:
- **Subject kind** — `device` (default) / `asset` / `lot` / `stock_item` (Sprint 18+).
- **Device Type** — the device type this model applies to (only meaningful when `subject_kind = device`; empty for the other kinds).
- **Metrics** — count of defined metrics.
- **Created** — creation date.

Expand a row to see the full metrics list with columns: name, unit, min, max, and description.

### Creating a Model

1. Click **Create Model**.
2. Pick the **Subject kind** (defaults to `device`). For non-device kinds, the matching opt-in must be enabled in **Tenant Settings → Telemetry subjects** for ingestion to actually fan out to that kind.
3. Enter the **Device Type** — **required only when** `subject_kind = device`; for `asset` / `lot` / `stock_item` it must be omitted (the API rejects the combination).
4. Add metrics using the metric builder:
   - **Name** — metric identifier (e.g., `signal_strength`, `temperature_c`).
   - **Unit** — measurement unit (e.g., `dBm`, `°C`).
   - **Min / Max** — acceptable value range.
   - **Description** — what this metric measures.
4. Click **Add Metric** to add more, or the remove button to delete one.
5. Click **Save**.

### Deleting a Model

Click the **Delete** button on the model row. This removes the schema definition but does not affect existing telemetry data.

---

## Rules

Rules let you define automated conditions that trigger alerts when your telemetry data meets certain criteria.

### Viewing Rules

The rules table shows: Name, Condition Type, Action Type, and Enabled status.

- Toggle the **Enabled** switch to activate or deactivate a rule without deleting it.
- Click **Edit** to modify or **Delete** to remove a rule.

### Creating a Rule

Click **Create Rule** to open the multi-step wizard.

**Step 1 — Condition:**
- **Name** — a descriptive rule name (required).
- **Description** — optional notes.
- **Condition Type:**
  - **Threshold** — triggers when a field crosses a value. Configure: operator (`>`, `<`, `==`), field, and threshold value.
  - **Absence** — triggers when no read is received within X minutes. Optionally filter by tag ID.
  - **Rate Change** — triggers on sudden changes. Configure: window (minutes) and change percent (e.g., 50%).
  - **Telemetry threshold** (`telemetry.threshold`, Sprint 20) — fires on a `telemetry_readings` row whose `metric_value` crosses a threshold. Configure: **subject_kind** (`device` / `asset` / `lot` / `stock_item` — must be in **Tenant Settings → Telemetry subjects**), **metric_name** (e.g., `temperature_c`), **operator** (`gt` / `lt` / `gte` / `lte` / `eq`), **value**, **cooldown_s** (default 600 s), and an optional **subject_id** to pin the rule to one instance. Cooldown is per `(rule, subject_id)` so leaving `subject_id=null` does not suppress alerts across distinct lots / assets.
  - **Zone entered / exited / dwell** (Sprint 17) — geofence transitions; configure target zone and (for dwell) the duration threshold.
  - **Stock expiring within** — lot expiry alerts; configure `within_days`.

**Templates (Sprint 20).** `GET /rule-templates` returns pre-filled `RuleCreate` payloads the wizard offers as one-click starting points: **`lot.cold_chain_breach`** (subject_kind=lot, `temperature_c > 8 °C`, 15 min cooldown) and **`asset.high_temperature`** (subject_kind=asset, `temperature_c > 60 °C`, 10 min cooldown). The UI greys out a template if the tenant has not opted that subject_kind in.

**Step 2 — Action:**
- **Webhook** — sends a POST request to a URL you specify.
- **Email** — sends a notification email to a recipient address.
- **Notification** — creates an internal alert (no extra config needed).

**Step 3 — Scope:**
- Apply the rule to a **specific device** or **all devices** (global).

**Step 4 — Review:**
- Review the full configuration, toggle **Enabled**, and click **Save**.

---

## Alerts

Navigate to **Alerts** to see all triggered alerts.

### Alert Table

**Columns:** Time, Message, Severity (critical/warning/info), Status (open/acknowledged), Device.

- Severity is color-coded: **red** = critical, **orange** = warning, **blue** = info.
- Use the **Status filter** to show All, Open, or Acknowledged alerts.
- Alerts are sorted newest-first by default (20 per page).

### Acknowledging an Alert

Click the **Acknowledge** button on any open alert to mark it as handled. Acknowledged alerts remain in the history for audit purposes.

---

## Integrations

Integrations push TagPulse events to external systems.

### Viewing Integrations

The table shows: Name, Type, Events subscribed, Health status, Enabled toggle, and Last Triggered timestamp.

- **Health** is color-coded: **green** = healthy, **orange** = degraded, **red** = error.
- Toggle **Enabled** to start/stop delivery.
- Click **Deliveries** to view the delivery log for an integration.
- Click **Delete** to remove an integration.

### Creating an Integration

1. Click **Create Integration**.
2. Fill in:
   - **Name** — a descriptive label (required).
   - **Type** — Webhook, SSE, or Export.
   - **Events** — comma-separated event types to subscribe to (e.g., `tag_read.created, alert.triggered`).
3. Configure type-specific settings:

| Type        | Settings                                              |
|-------------|-------------------------------------------------------|
| **Webhook** | **URL** — the HTTPS endpoint to receive POST payloads |
| **SSE**     | **Max Connections** — optional (1–1000)                |
| **Export**   | **Schedule** — cron expression (e.g., `0 0 * * *` for daily), **Format** — CSV or JSON |

4. Click **Save**.

### Delivery Log

Click **Deliveries** on any integration to see its delivery history.

**Columns:** Time, Event, Status (success/pending/failed), Attempts, Response Code, Error.

Use this log to troubleshoot failed deliveries — the error column shows the failure reason and the attempts column shows retry count.

---

## Assets

> **Requires** the `asset` tracking mode (Tenant Settings → Asset tracking).

An **asset** is a real-world thing you want to track — a forklift, a returnable container, a high-value pallet. Assets are linked to one or more **tag bindings** (the EPC/TID/device-tag printed on the RFID label) so every read in the field can attribute back to the asset.

### Asset list

**Columns:** Name, Type, External Ref, Status (`active` / `decommissioned`), Updated. Sort by name; type-ahead search filters in place.

Click **Create Asset** to add one. Required: Name. Optional: Asset Type (free text — `forklift`, `pallet`, `container`, …), External Ref (your ERP/WMS ID).

### Asset detail

Three-tab layout:

- **Overview** — basic info plus the **Current Location** card (last known lat/lon, accuracy, recorded-at, source — `rfid` or `external`). The **Covers Zones** chip strip lists every zone the asset is currently inside (Sprint 15). When **Tenant Settings → Telemetry subjects** includes `asset`, an extra **Latest telemetry** card lists the most recent reading per metric (Sprint 19); `GET /assets/{id}` populates the `latest_telemetry` field with one entry per `metric_name` (Sprint 21 server-side cache: 30 s, so an F5-mash does not hammer the hypertable).
- **Bindings** — table of all bindings (kind = `epc` / `tid` / `device`, value, bound-at, unbound-at). Active bindings have an empty `unbound_at`. Add a binding via **Bind Tag**. An asset may hold multiple active bindings simultaneously (e.g. EPC + TID, or redundant labels); uniqueness is enforced on `(tenant_id, binding_value) WHERE unbound_at IS NULL`, so the same `binding_value` cannot be active on two assets in the same tenant. Remove via **Unbind**; the historical row is preserved.
- **Path** — the recent location trail. Each row shows time, source, and zone (if inside one). The **Map** page shows the same trail visually with a 24-hour time slider.

### Tag-binding lifecycle

Bindings are append-only and time-boxed:

1. **Bind** — sets `bound_at = now()`, leaves `unbound_at` null.
2. **Unbind** — sets `unbound_at = now()`. The same binding value can later be re-bound to a different (or the same) asset.
3. **Cross-tenant collisions** — if another tenant has an active binding for the same `binding_value`, you'll see an admin-only warning (count only — never the other tenant's identity, per the design's tenant-isolation guarantee).

---

## Sites & Zones

> **Requires** the `asset` tracking mode.

**Sites** are physical buildings; **zones** are subdivisions within them (a dock door, a cold-storage room, a virtual perimeter). Zones power the **Map** overlay, geofence rules (`zone.entered` / `zone.exited` / `zone.dwell_exceeded`), and stock-by-zone aggregation.

### Sites

Left-hand list. Each site has Name, Address, Default Timezone, and an optional `metadata` JSONB field. Click a site to manage its zones in the right-hand panel.

#### Recommended `Site.metadata` template

Use `Site.metadata` for facility-wide attributes that apply to **everything in the building** — not to one zone or one reader. The GLN of the building is the canonical example.

```json
{
  "gln": "0614141999996",            // GS1 GLN of the facility (13 digits)
  "site_code": "BOS-DC-01",          // your internal site/WMS code
  "facility_type": "distribution_center",
  "operator": "Acme Logistics",
  "region": "NA-East",
  "opened_at": "2018-03-12"
}
```

### Zones — three kinds

| Kind | What it is | When to use |
|------|------------|-------------|
| **`reader_bound`** | Implicit — defined by which fixed readers see the tag. Pick the reader IDs that bound the zone. | Indoor warehouse zones where readers cover the floor. No GPS needed. |
| **`geofence`** | A polygon drawn on the map (GeoJSON `Polygon`). | Outdoor yards, loading lots, anywhere with GPS-equipped tags or external locators. |
| **`virtual`** | Admin-defined logical grouping (no readers, no polygon). | Cross-cutting categories like "Cold storage" or "Hazmat". |

### Creating a zone

1. Pick a site, click **Add Zone**.
2. **Name** + **Kind**.
3. For `reader_bound`: pick the readers from the multi-select.
4. For `geofence`: a draw map appears. Click vertices in order, **Undo** removes the last, **Clear** starts over, **Done** auto-closes the ring. The server validates the GeoJSON (reject self-intersecting, < 3 vertices, etc.).
5. (Optional) Add `metadata` — see template below.
6. Save.

#### Recommended `Zone.metadata` template

Use `Zone.metadata` for attributes that describe the **specific area** — not the whole building (use `Site.metadata`) and not a particular reader (use `Device.metadata`).

```json
{
  "gln": "0614141999989",            // GS1 sub-location GLN, if you assign them
  "ext_zone_code": "DOCK-A",         // your WMS/ERP zone identifier
  "function": "inbound_dock",        // inbound_dock | outbound_dock | staging | storage | cold_storage | quarantine | …
  "environment": {
    "temperature_min_c": 2,
    "temperature_max_c": 8,
    "humidity_max_pct": 65
  },
  "capacity": {
    "max_pallets": 240,
    "area_m2": 480
  },
  "tags": ["cold-chain", "fda-controlled"]
}
```

### Virtual zones — when to use them

Virtual zones have no readers and no polygon — they're pure logical groupings. Use them whenever you need to slice assets, stock, rules, or reports by a dimension that **isn't tied to a physical boundary**.

| Use case | Example virtual zone | Why it's virtual |
|----------|---------------------|------------------|
| **Cross-cutting category** | `Cold-chain (≤ 4°C)` spanning multiple coolers across two sites | The category is real, but no single polygon or reader set captures it. |
| **Compliance / regulatory bucket** | `FDA-controlled`, `ITAR`, `Hazmat-Class-3` | Drives audit-log filtering and rule scoping; membership is a property, not a place. |
| **Ownership / consignment** | `Consigned-Acme-Corp`, `Customer-owned-returns` | Same physical warehouse, different financial owner — needed for billing reports. |
| **Lifecycle state** | `In maintenance`, `Quarantine`, `Pending disposal`, `Loaner pool` | Operational state that travels with the asset regardless of location. |
| **Rule scoping shorthand** | `Critical assets`, `VIP shipments` | Attach a rule to the virtual zone instead of enumerating asset IDs; just add/remove members to change rule coverage. |
| **Reporting roll-up** | `All cold-chain areas` (umbrella over 3 `reader_bound` coolers + 1 `geofence` outdoor pad) | Executive dashboards want one number, not three. |
| **Pre-physical placeholder** | `New wing — Q3 build-out` | Lets you wire up rules and integrations before readers are installed; later swap to `reader_bound`/`geofence` without rebuilding the wiring. |
| **Sandbox / test bucket** | `QA-simulator`, `Demo-fleet` | Production reports filter it out; keeps simulator data from polluting KPIs. |
| **FEFO / picking pool** | `Expiring-this-week-pick-list` | Inventory operations grouping driven by lot-expiry, not physical location. |
| **Security / access control** | `High-value` (jewelry, electronics, controlled substances) | Triggers stricter alerts (`zone.exited` → page on-call) without revealing the physical safe location. |

Membership is managed by attaching assets (or stock items) to the virtual zone — the `Covers Zones` chip strip on the [Asset detail](#asset-detail) page shows everything an asset belongs to, physical or virtual.

Zones are tenant-scoped — each tenant has its own private set; nothing crosses the tenant boundary.

---

## Map

> **Requires** the `asset` tracking mode.

Provider-agnostic Leaflet map driven by your **Tenant Settings → Map** config. Default: OpenStreetMap with a dev-only banner in the footer.

### Layout

- **Header** — layer checkboxes: **Assets**, **Zones**, **Stock density** (the third layer requires `inventory` mode and overlays geofence polygons with red shading scaled to total stock quantity).
- **Body** — the map. Geofence polygons are drawn from `polygon_geojson`; zone names appear on hover. Asset markers show the asset name on hover; mobile-mobility assets get a colored ring.
- **Time slider** (bottom) — drag back up to **24 hours** to replay positions. The slider re-resolves every visible asset's position via `GET /assets/{id}/path`.
- **Footer** — always shows the tile-provider attribution.

### Asset popups

Click a marker:

- Asset name + last-seen timestamp.
- **Open detail →** jumps to [Asset detail](#asset-detail).
- **View manifest →** opens a tree modal of the recursive child-asset manifest (per the carriers-and-manifests design — useful when one asset "carries" others, e.g., a pallet of cases). Empty children render a graceful "not carrying any child assets" state.

### Stock-density overlay (`inventory` mode)

With **Stock density** checked, every geofence zone is shaded red proportional to total stock units inside it (aggregated from `GET /inventory/stock-levels`). Hover for the exact quantity. Useful for spotting hot zones at a glance.

---

## Inventory

> **Requires** the `inventory` tracking mode.

Four pages plus an admin sub-page for tag-data mappings.

### Products

**Columns:** SKU, Name, GTIN, Category, Unit (`each` / `case` / `pallet`).

Click **Create Product** to add one. SKU + Name are required; GTIN, category, and unit are optional. Click a row to open **Product detail**, which shows:

- **Lots** tab — manufacturing batches (lot_code, manufactured_at, expires_at). Lots with expiry dates feed the **Lot Expiry Queue**. Click a lot to see its detail page; when **Tenant Settings → Telemetry subjects** includes `lot`, a **Latest telemetry** card lists the most recent reading per metric (Sprint 19), surfacing on-tag temperature for cold-chain. `GET /lots/{lot_id}` populates the `latest_telemetry` field; results are cached server-side for 30 s (Sprint 21).
- **Stock items** tab — every individual tag bound to this product, with its current zone and state.

### Lot Expiry

**Filter:** Next 24h / 7 days / 30 days / 90 days / All lots (default 30 days). Sorted by expiry ascending so the most-urgent lot is at the top.

**Columns:** Lot code, Product, Expires at, Notes.

Use this to drive FEFO (first-expired-first-out) picking instructions, write-off lists, or expiry-driven discount campaigns.

### Stock Levels

A pivot table: rows are **Product**, columns are **Zones** plus a **Total** column. Each cell is the count of in-stock items.

- The leftmost zone column is `unassigned` — items that haven't been seen in any zone yet.
- **Export CSV** in the toolbar dumps the current pivot.
- Filter by product or zone via the toolbar selectors.

The **Map** page's stock-density overlay uses the same data.

### Stock Movements

Append-only ledger of every movement event.

**Columns:** Occurred at, Movement type (`receive` / `move` / `pick` / `consume` / `adjust`), Stock item, From zone, To zone, Quantity, Device.

Filter by product, zone, or movement type. Use this when reconciling physical counts against TagPulse's view.

### Tag Data Mappings (admin)

**Where:** sidebar → **Tag Data Mappings** (admin only).

Defines how raw EPC bits decompose into semantic fields (`product_sku`, `lot_code`, …). Two scopes:

- **Tenant-wide** — applies to every product unless overridden.
- **Per product** — overrides for a specific SKU.

Fields:

- **Tag-data key** — the raw key the device emits (e.g., `epc.gtin14`).
- **Semantic field** — what it maps to (`product_sku`, `lot_code`, `serial`, …).
- **Transform** — optional Python-style expression for re-formatting.

Without mappings, ingestion still works — the system just treats the tag as opaque and you create stock items manually. With mappings, stock items materialize automatically as soon as a bound tag is read.

### Case/pallet containment (Sprint 15b)

Use the `parent_stock_item_id` field on `POST /stock-items` (or `PATCH`) to record a case packed into a pallet, or a pallet loaded onto a trailer. The chain is recursive — a pallet's manifest will traverse all the way down to individual cases. The **Map** page's manifest pop-out (see [Map](#map) above) renders this tree.

---

## Tenant Settings

> **Admin only.**

Two-tab page: **General** (tracking modes) and **Map** (tile provider).

### General tab — tracking modes

Two switches:

- **Asset tracking** — unlocks **Assets**, **Sites & Zones**, **Map**.
- **Inventory tracking** — unlocks **Products**, **Lot Expiry**, **Stock Levels**, **Stock Movements**.

At least one mode must remain enabled (the UI rejects disabling both with a toast). Disabling a mode hides the sidebar entries but **does not delete data** — re-enabling restores access.

#### Telemetry subjects (Sprint 19)

A multi-select alongside the tracking-mode switches. Controls which non-device subjects ingestion fans tag-borne telemetry out to. Default `["device"]`; tick `asset`, `lot`, or `stock_item` to opt in. Persists to `tenants.telemetry_subject_kinds` and is recorded in the audit log as `tenant.config.update`.

- **Why opt-in?** Fan-out doubles the ingestion write rate per subject and changes the rules-engine surface area; opt-in keeps the default tenant on Sprint 14's device-only behaviour.
- **Convergence.** The writing worker invalidates its cache immediately; sibling API workers converge within ~30 s (Sprint 21 `SUBJECT_KINDS_CACHE` TTL). No restart required.
- **Effect on rules.** A `telemetry.threshold` rule on a non-opted subject_kind is **accepted** by the rules editor but never matches — the readings are not produced. Always flip the opt-in **before** authoring the rule.

### Map tab — tile provider

Provider-agnostic — supply a tile-URL template and (optional) API key. Examples:

| Provider | Template |
|----------|----------|
| OpenStreetMap (default) | `https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png` |
| MapTiler | `https://api.maptiler.com/maps/streets-v2/{z}/{x}/{y}.png?key=YOUR_KEY` |
| Mapbox | `https://api.mapbox.com/styles/v1/mapbox/streets-v12/tiles/{z}/{x}/{y}?access_token=YOUR_TOKEN` |
| Azure Maps | `https://atlas.microsoft.com/map/tile?api-version=2.0&tilesetId=microsoft.base.road&zoom={z}&x={x}&y={y}&subscription-key=YOUR_KEY` |

**Attribution** is required — paste the provider's required string into the Attribution field; the **Map** footer always renders it.

Save to persist. Every change is recorded in the **Audit Log** as `tenant.map_config.update` (the JSON diff of before/after is captured automatically).

---

## Usage & Quotas

Navigate to **Usage** to monitor your tenant's platform consumption.

### Daily Usage Chart

A bar chart showing daily usage across all dimensions: API reads, API writes, ingestion, rule evaluations, alerts fired, and webhook deliveries.

### Usage Summary Table

| Dimension            | Quota Limit  |
|----------------------|--------------|
| API Reads            | 100,000      |
| API Writes           | 50,000       |
| Ingestion            | 1,000,000    |
| Rule Evaluations     | 500,000      |
| Alerts Fired         | 10,000       |
| Webhook Deliveries   | 50,000       |
| SSE Connections      | 1,000        |
| Export Volume         | 100,000      |

Each dimension shows a progress bar indicating the percentage of quota used. The bar turns **red** when usage reaches 90% or more.

Use the date range filter to view historical usage trends.

---

## User Management

> **Admin only** — the Users section is visible only to admin users.

Navigate to **Users** in the sidebar to manage your tenant's user accounts.

### Viewing Users

The table shows: Name, Email, Role (admin/editor/viewer), Status (active/inactive), API Key prefix, and Created date.

### Creating a User

1. Click **Create User**.
2. Fill in:
   - **Email** — required, must be a valid email address.
   - **Name** — required.
   - **Role** — select Admin, Editor, or Viewer (default: Viewer).
3. Click **Create**.

The user is created but has no API key yet — generate one from the user detail page.

### Editing a User

Click **Edit** on any user row to open the detail page.

- **Name** and **Role** can be changed inline. Click **Save Changes** to apply.
- Click **Deactivate** to disable the account (the user can no longer authenticate). Click **Reactivate** to restore access.

### API Key Management

On the user detail page, the **API Key** card shows:

- **Current key prefix** (e.g., `tp_test-c...`) or "No key generated".
- **Generate API Key** — creates a new key. The full key is displayed **once** with a copy button. Store it securely.
- **Regenerate API Key** — replaces the existing key with a new one. The old key is immediately invalidated.
- **Revoke Key** — removes the key entirely. The user will need a new key to authenticate.

> **Important:** API keys cannot be retrieved after creation. If a user loses their key, an admin must generate a new one.

### Recommended Workflow: Onboarding a New User

1. Navigate to **Users** → click **Create User** → fill in email, name, and role → click **Create**.
2. Click **Edit** on the new user row to open their detail page.
3. In the **API Key** card, click **Generate API Key**.
4. Copy the key using the copy button and send it to the user securely (e.g., password manager, encrypted message).
5. The user logs in via the **API Key** tab with their email and the generated key.

> **Tip:** If you close the key banner without copying it, click **Regenerate API Key** to create a new one (the previous key is invalidated).

**For bulk onboarding via the API:**

```bash
# Create user and generate key in one flow
USER_ID=$(curl -s -X POST http://localhost:8000/users \
  -H "Authorization: Bearer <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"email":"new@example.com","name":"New User","role":"editor"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['id'])")

curl -s -X POST "http://localhost:8000/users/$USER_ID/api-key" \
  -H "Authorization: Bearer <admin-key>"
```

### Key Lifecycle Summary

| **Action** | When to use |
|--------|-------------|
| **Generate** | New user needs access for the first time |
| **Regenerate** | Key was lost, or you want to rotate keys periodically |
| **Revoke** | User leaves the team, or the key is compromised |
| **Deactivate user** | Temporarily block all access (key stays but won't work) |

---

## Audit Log

> **Admin only** — the Audit Log is visible only to admin users.

Navigate to **Audit Log** in the sidebar to review the tenant-scoped audit trail. Every privileged action (device approval, token rotation, certificate attach, tenant-config change, user create/update, etc.) is recorded with the actor, the affected resource, a JSON diff of the change, and a UTC timestamp.

### Reading the table

Columns:

- **Timestamp** — when the action was committed (sortable; newest first by default).
- **Action** — color-coded tag (e.g., `device.token_rotated`, `device.cert_attached`, `device.approved`, `device.rejected`, `tenant.map_config.update`, `user.create`).
- **Resource** — `<resource_type>:<resource_id>` (copy-friendly).
- **User** — actor user ID, or `system` for automated actions (e.g., scheduled token rotations).
- **Changes** — truncated JSON peek; hover for the full pretty-printed diff.

### Preset filters

The **Segmented** selector at the top of the page narrows the view server-side:

| Preset | What it shows |
|--------|---------------|
| **All** | Every audit entry for the tenant (most recent 200). |
| **Device security events** | Only `device.token_rotated`, `device.cert_attached`, `device.approved`, `device.rejected` — the canonical "who touched device identity" view for security review and incident response. |
| **Tenant config** | `tenant.update`, `tenant.map_config.update`. |
| **User management** | `user.create`, `user.update`, `user.delete`. |

Presets translate to the backend `?actions=` query parameter on `GET /admin/audit-logs` (comma-separated list). You can hit the same endpoint directly with any combination of action names.

### Investigation workflow

1. **Suspect a credential leak?** → select **Device security events** to see every recent token rotation and cert attach. Cross-reference the **User** column against the staff who should have rotated those credentials.
2. **Investigating a misconfiguration?** → select **Tenant config**, find the offending `tenant.map_config.update`, hover the **Changes** column to see the JSON diff, and roll back the relevant fields via **Tenant Settings**.
3. **Reviewing onboarding/offboarding?** → select **User management** to confirm the right roles were assigned and that decommissioned accounts were marked inactive.

> **Note:** Audit entries are append-only and tenant-scoped — no admin can see or modify another tenant's log, and no API surface lets you delete an entry. For long-horizon retention, export periodically via the API (`GET /admin/audit-logs?limit=1000&offset=...`).

