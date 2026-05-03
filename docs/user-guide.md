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

### Step 4 — Lay out sites & zones (asset mode)

**Where:** sidebar → **Sites & Zones**.

1. **Create a site** (e.g., "Boston DC", "Production Floor"). One per physical building.
2. **Create zones** within each site. Three kinds:
   - **`reader_bound`** — implicitly defined by which fixed readers see the tag (no polygon needed; pick the readers that bound the zone).
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
2. **Add a tag binding**: enter the EPC, TID, or device-internal tag ID printed on the RFID label. Bindings can be unbound and re-bound (for tag swaps); only one active binding per asset at a time.

Once bound, every tag read from the field automatically attributes to the asset. The Asset detail page shows the **Path** (last 24 h trail), **Covers Zones**, and the active binding.

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

### Step 8 — Add rules & alerts

**Where:** sidebar → **Rules** → **Create Rule**.

Useful starter rules:

| Rule | Condition | Action |
|------|-----------|--------|
| Reader offline | Absence > 5 minutes (any tag) | Notification |
| RSSI cliff | Rate change ≥ 50% in 10 min on `signal_strength` | Webhook to Slack |
| Asset left zone | `zone.exited` from "Cold storage", subject_kinds = `asset` | Email |
| Pallet idle in dock | `zone.dwell_exceeded` ≥ 60 min in "Dock-Door-A" | Notification |

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
   - **Metadata** — optional JSON for extra info like location or SKU.
3. Click **Submit**. The device is created with `active` status.

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

Telemetry models define the expected metrics schema for each device type (e.g., what readings an `rfid_reader` sends).

### Viewing Models

The table shows:
- **Device Type** — the type this model applies to.
- **Metrics** — count of defined metrics.
- **Created** — creation date.

Expand a row to see the full metrics list with columns: name, unit, min, max, and description.

### Creating a Model

1. Click **Create Model**.
2. Enter the **Device Type** (required).
3. Add metrics using the metric builder:
   - **Name** — metric identifier (e.g., `signal_strength`).
   - **Unit** — measurement unit (e.g., `dBm`).
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

- **Overview** — basic info plus the **Current Location** card (last known lat/lon, accuracy, recorded-at, source — `rfid` or `external`). The **Covers Zones** chip strip lists every zone the asset is currently inside (Sprint 15).
- **Bindings** — table of all bindings (kind = `epc` / `tid` / `device`, value, bound-at, unbound-at). Active bindings have an empty `unbound_at`. Add a binding via **Bind Tag** — only one active binding per asset at a time. Remove via **Unbind**; the historical row is preserved.
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

Left-hand list. Each site has Name, Address, Default Timezone. Click a site to manage its zones in the right-hand panel.

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
5. Save.

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

- **Lots** tab — manufacturing batches (lot_code, manufactured_at, expires_at). Lots with expiry dates feed the **Lot Expiry Queue**.
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

