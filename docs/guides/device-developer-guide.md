# Device Developer & Operator Guide

> **Audience:** anyone connecting a real RFID reader (or any sensor gateway) to a
> deployed TagPulse instance — whether you're (a) the operator pointing a
> commercial reader at the broker, or (b) a firmware/software developer writing a
> custom edge agent.
>
> **TL;DR** for someone who just wants to send their first read:
> ```
> mosquitto_pub -h tpdev-mqtt-mwig6fst.centralus.azurecontainer.io -p 1883 \
>   -u <device_id> -P <device_token> \
>   -t 'tenants/<tenant_id>/devices/<device_id>/tag-reads' \
>   -m '{"tag_id":"E2806894...","timestamp":"2026-05-08T10:15:30Z","signal_strength":-52.0}'
> ```
>
> **Wire-shape note:** on MQTT, ``device_id`` comes from the topic and is
> NOT required (or expected) in the body. The subscriber accepts a single
> object (above), the same object wrapped in an array, or a batch array
> (HTTP-style); a body-supplied ``device_id`` is silently ignored in
> favour of the topic-derived one. See [§3.2 Payload shapes](#32-payload-shapes-one-example-each).
> Read on for what each of those values is, where to get them, and the rest of
> the wire contract.

---

## 1. Concepts in one screen

| Term | What it is |
|---|---|
| **Tenant** | The company / org. All data is scoped to a `tenant_id` (UUID). One tenant per customer. |
| **Device** | One physical reader / gateway. Has its own `device_id` (UUID) + Bearer token. Provisioned by the tenant admin. |
| **Tag read** | One observation of a tag by a reader. The primary event TagPulse ingests. |
| **Telemetry** | A numeric metric reading (temperature, battery, RSSI…) tied to a *subject* (device, asset, lot, stock_item, zone). |
| **Location** | A GPS / dead-reckoned position fix. Separate topic so a moving reader can report itself. |
| **Status / heartbeat** | A 60-second liveness ping carrying connection state, firmware version, and buffer depth. |
| **Subject** | The thing telemetry is *about*. RFID readers usually attribute to `device`; once the tag→asset binding is known, the backend fans out reads to `asset`/`lot`/`stock_item`. |

**Two transports, one schema.** MQTT is push-by-default and the recommended path
for real readers. HTTP is the same payload shape and is provided as a fallback
for batch uploads, restricted networks, or one-shot tooling.

**The contract is enforced.** [`docs/design/edge-device-contract.md`](../design/edge-device-contract.md)
is the authoritative spec — clock window, dedup window, ENTER/EXIT semantics,
batching limits, heartbeat cadence. The backend rejects events outside the
clock window and rate-limits payload size; the rest is a conformance test
suite ([`tests/conformance/`](../../tests/conformance/)) every blessed reader
must pass.

---

## 2. Provisioning: getting credentials before you can publish

You need three things:

1. **`tenant_id`** — the UUID of your tenant. Ask your TagPulse admin.
2. **`device_id`** — registered once via the admin UI **Devices → New Device**
   *or* via `POST /device-registry`:
   ```bash
   curl -X POST https://<api-fqdn>/device-registry \
     -H "Authorization: Bearer <admin-api-key>" \
     -H "Content-Type: application/json" \
     -d '{"name":"dock-bay-12","device_type":"rfid_reader","firmware_version":"0.4.2"}'
   # → {"id":"<device_id>","token":"tpd_xxx_yyyyyyyy", ...}
   ```
3. **`device_token`** — returned **once** at registration. If you lose it, the
   admin must rotate via `POST /device-registry/{device_id}/rotate-token`
   (the previous token is immediately invalidated, no grace period).

> **Token shape.** Device tokens look like `tpd_<prefix>_<secret>`. The `tpd_`
> prefix distinguishes them from user API keys (`tp_…`).

A reader's MQTT credentials are simply `username = device_id`, `password = device_token`.
On HTTP, send `Authorization: Bearer <device_token>`.

---

## 3. MQTT 90-second primer

If you've never used MQTT: it's a publish/subscribe protocol over TCP (port
1883 plain, 8883 TLS). A *broker* is a tiny server that holds topic
subscriptions; clients publish *messages* to *topics*; other clients
subscribed to those topics receive them. There is no request/response — fire
and forget, with optional QoS for delivery guarantees.

What that means concretely for TagPulse:

- The TagPulse backend has an MQTT *worker* subscribed to
  `tenants/+/devices/+/+` (one wildcard for tenant, one for device, one for
  the message kind). Anything you publish there gets ingested.
- **You only ever publish.** You don't subscribe to receive commands —
  cloud-to-device commands are on the roadmap (Sprint 26+) but not live.
- **Topics are pre-fixed by your tenant + device.** The broker ACL refuses
  publishes to topics that don't match your `device_id`. You can't
  accidentally write to another device's stream.
- **Use QoS 1.** TagPulse's broker accepts QoS 0/1/2; the reference client
  uses **QoS 1** (at-least-once) which is the right trade-off — the backend
  is idempotent on `(device_id, tag_id, timestamp)` so duplicates are
  deduplicated. QoS 2 doubles the round trips for no extra value.
- **Set a Last Will and Testament (LWT).** Publish-on-disconnect message
  declared at connect time. TagPulse expects it on the `…/status` topic
  with `{"connection_state":"offline"}` so the dashboard can reflect a
  yanked-cable reader within seconds.

### 3.1 Topics

```
tenants/{tenant_id}/devices/{device_id}/tag-reads      # primary RFID stream
tenants/{tenant_id}/devices/{device_id}/telemetry      # device-keyed metrics
tenants/{tenant_id}/devices/{device_id}/location       # GPS fixes
tenants/{tenant_id}/devices/{device_id}/status         # heartbeat + LWT
tenants/{tenant_id}/devices/{device_id}/events         # diagnostic events

# Subject-scoped (when the integration has already resolved tag → asset):
tenants/{tenant_id}/subjects/{subject_kind}/{subject_id}/telemetry
# subject_kind ∈ {device, asset, lot, stock_item, zone}
```

### 3.2 Payload shapes (one example each)

`tag-reads` — single object (canonical) **or** an array of up to 100
objects (batch). On MQTT, ``device_id`` comes from the topic; if you
include it in the body it is silently dropped in favour of the
topic-derived UUID, so a misrouted publish cannot smuggle reads under
another device.

```json
{
  "tag_id": "E280689400005000A1B2C3D4",
  "timestamp": "2026-05-08T10:15:30.123Z",
  "signal_strength": -52.0,
  "reader_antenna": 1,
  "tag_data": {"epc_hex": "300833B2DDD9014000000000"}
}
```

Batch form (same per-element shape, array wrapper, max 100 elements):

```json
[
  {"tag_id": "E280…A1", "timestamp": "2026-05-08T10:15:30.000Z", "signal_strength": -52.0},
  {"tag_id": "E280…B2", "timestamp": "2026-05-08T10:15:30.050Z", "signal_strength": -49.5}
]
```

`telemetry` (device-keyed, single reading):
```json
{
  "device_id": "00000000-...-0002",
  "timestamp": "2026-05-08T10:15:30Z",
  "metric_name": "temperature_c",
  "metric_value": 22.4,
  "unit": "°C"
}
```

`location` (single GPS fix):
```json
{
  "device_id": "00000000-...-0002",
  "timestamp": "2026-05-08T10:15:30Z",
  "latitude": 37.7749,
  "longitude": -122.4194,
  "accuracy_m": 4.5,
  "source": "gps"
}
```

`status` (heartbeat — also used as LWT body with `connection_state:"offline"`):
```json
{
  "timestamp": "2026-05-08T10:15:30Z",
  "connection_state": "online",
  "firmware_version": "0.4.2",
  "uptime_s": 12345,
  "queue_depth": 0,
  "buffer_bytes": 0
}
```

### 3.3 Send your first message

With `mosquitto_pub` (the canonical MQTT CLI; install via `apt install
mosquitto-clients` or `brew install mosquitto`):

```bash
TENANT=11111111-1111-1111-1111-111111111111
DEVICE=00000000-0000-0000-0000-000000000002
TOKEN='tpd_xxx_yyyyyyyy'
BROKER=tpdev-mqtt-mwig6fst.centralus.azurecontainer.io

mosquitto_pub -h "$BROKER" -p 1883 \
  -u "$DEVICE" -P "$TOKEN" \
  -q 1 \
  -t "tenants/$TENANT/devices/$DEVICE/tag-reads" \
  -m '{"tag_id":"E2806894TEST","timestamp":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'","signal_strength":-52.0}'
```

Confirm it landed:
```bash
curl -s "https://$API_FQDN/tag-reads?limit=5" \
  -H "Authorization: Bearer <admin-api-key>" | jq '.[] | {tag_id,timestamp,device_id}'
```

### 3.4 v2 wire format — presence-oriented (Sprint 46+)

> **Status:** v1 (shown in §3.2/§3.3) and v2 are both supported indefinitely
> per [spec §9.1 #4](../design/edge-wire-format-v2.md). New firmware should
> prefer v2; existing v1 deployments keep working with no changes.
>
> **Authoritative spec:** [`docs/design/edge-wire-format-v2.md`](../design/edge-wire-format-v2.md).
> This section is the producer-facing summary.

v2 is a presence-oriented schema: instead of streaming every tag observation
as an independent row, the reader maintains a local view of "which EPCs are
currently in field" and publishes three message kinds:

| `t` | Kind | When to publish |
|---|---|---|
| `0` | **Snap** | Full current state of the field. Once per snap cadence (default **300 s** or every **100 read cycles**, whichever fires first), plus immediately after MQTT reconnect to resync the subscriber. |
| `1` | **Appeared** | One EPC just entered the field (first cycle the antenna saw it after being absent). |
| `2` | **Disappeared** | One EPC just left the field (exit-timeout elapsed with no read). |

All three flow on the **same topic** as v1 (`tenants/{tenant}/devices/{device}/tag-reads`).
The integer `t` field at the envelope is the discriminator — the subscriber
routes by `t` and falls through to v1 parsing only when `t` is absent.

#### Minimal examples

`t=0` snap (two EPCs in field, no GPS):
```json
{
  "t": 0, "sn": 42, "ts": 1716489732001,
  "lat": null, "lon": null,
  "epcs": [
    {"an": 1, "epc": "E2801160AAAA1111", "rssi": -52, "cnt": 7},
    {"an": 2, "epc": "E2801160BBBB2222", "rssi": -61, "cnt": 3}
  ]
}
```

`t=1` appeared:
```json
{
  "t": 1, "sn": 43, "ts": 1716489734120,
  "lat": 37.7749, "lon": -122.4194,
  "an": 1, "epc": "E2801160CCCC3333", "rssi": -49, "cnt": 1
}
```

`t=2` disappeared (only the EPC; no antenna/rssi):
```json
{ "t": 2, "sn": 44, "ts": 1716489750200, "epc": "E2801160AAAA1111" }
```

#### MQTT settings (same as v1)

| Setting | Value |
|---|---|
| Topic | `tenants/{tenant_id}/devices/{device_id}/tag-reads` |
| QoS | `1` (at-least-once) |
| Retain | `false` |
| `clean_session` | `false` (so the broker queues messages across short disconnects) |
| TLS | per environment (1883 plain on dev; 8883 TLS once Sprint 24 lands) |
| Auth | `username = device_id`, `password = device_token` (unchanged) |

#### Producer responsibilities

1. **EPC encoding** — uppercase hex, no separators. Mixed-case is rejected
   (`invalid_epc`). Two-character minimum length, no upper bound (spec).
2. **`sn`** — monotonic per-device sequence number. Used by the subscriber
   for log correlation and (future Phase) replay-window detection. 32-bit
   unsigned wrap is fine.
3. **`ts`** — Unix epoch milliseconds (integer). UTC.
4. **`lat`/`lon`** — omit or send `null` when no fix. **Never** send `null`
   for optional sensor fields you simply don't have on this hardware
   (`rssi`, `cnt`, `an`); just omit them. Spec §6 reason `explicit_null`
   rejects messages that explicitly null an unsupported field, so the
   subscriber can distinguish "no GPS" from "config drift".
5. **Snap cadence** — fire a `t=0` snap every 300 s or every 100 read
   cycles (whichever comes first), AND on every successful reconnect.
   Without snaps, a missed `t=2` leaves the subscriber's `tag_presence`
   row stuck at `present` until the next snap heals it.
6. **Multi-antenna entries** — a snap can list the same EPC under multiple
   `an` values in the same `epcs[]` array; that's a feature, not a duplicate.
7. **Soft cap** — snaps with more than 5,000 entries are accepted but logged
   (`tagpulse_mqtt_wm_snap_large_total{sn}`). Above that, your reader is
   probably either misconfigured or saturating its tag inventory loop.

#### Reference implementation

Shape 2 (Pi-style edge agent) — see [`clients/pi/tagpulse_edge/`](../../clients/pi/tagpulse_edge/).
The agent maintains in-memory presence state, fires `t=1`/`t=2` deltas as
they happen, and emits a `t=0` snap on cadence or reconnect. Configuration
knobs (snap interval, exit timeout, soft cap) live in
[`tagpulse_edge/config.py`](../../clients/pi/tagpulse_edge/config.py).

#### Inspecting what the subscriber stored

Each v2 message updates `tag_presence` (one row per `(tenant, device, epc)`).
To see what's currently visible at a reader:

```sql
SELECT epc, status, last_seen, last_rssi, last_antenna
FROM tag_presence
WHERE tenant_id = '11111111-1111-1111-1111-111111111111'
  AND device_id = '00000000-0000-0000-0000-000000000002'
  AND status = 'present'
ORDER BY last_seen DESC;
```

Operator runbook with more queries + counter cheatsheet:
[`docs/runbooks/wm-wire-format-v2.md`](../runbooks/wm-wire-format-v2.md).

---

## 4. HTTP API quick reference

Same payloads as MQTT, plus a `/batch` endpoint. Useful when:

- the device is behind a corporate proxy that blocks port 1883
- you're building a one-shot data import tool
- you're using `curl` to debug

**Auth header:** `Authorization: Bearer <device_token>` (or a tenant-level API key
for tooling).

| Endpoint | Method | Body | Notes |
|---|---|---|---|
| `/tag-reads` | POST | `TagReadCreate` (single object) | 201 + persisted row |
| `/tag-reads/batch` | POST | `[TagReadCreate, ...]` | 201 + `{ingested, rejected}` count |
| `/telemetry` | POST | `TelemetryBatch` (single batch) | 201 |
| `/telemetry/readings/ingest` | POST | `TelemetryReadingIngest` | Subject-scoped; admin/editor scope |

Schemas live in [`src/tagpulse/models/schemas.py`](../../src/tagpulse/models/schemas.py)
and the live OpenAPI spec is at `https://<api-fqdn>/docs` (interactive Swagger
UI) or `…/openapi.json`.

Example — single read via HTTP:
```bash
curl -X POST "https://$API_FQDN/tag-reads" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "'$DEVICE'",
    "tag_id": "E2806894TEST",
    "timestamp": "'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'",
    "signal_strength": -52.0
  }'
```

---

## 5. Reference Python edge client (`clients/pi`)

If you're writing a custom agent in Python, **start from
[`clients/pi/`](../../clients/pi/)** rather than rolling your own MQTT loop.
It already implements the contract (dedup, ENTER/EXIT, batching, offline
buffer, reconnect with full-jitter backoff, NTP-aware clock checks).

Despite the directory name, it has nothing Pi-specific — runs on any Linux
or macOS host with Python 3.11+.

```bash
cd clients/pi
python3 -m venv .venv && . .venv/bin/activate
pip install -e .

python -m examples.run_reader \
  --tenant-id "$TENANT" \
  --device-id "$DEVICE" \
  --broker-host "$BROKER" \
  --token "$TOKEN"
```

The example uses a fake hardware loop. To wire up a real reader, you call
`agent.submit_tag_read(RawTagRead(...))` from your reader's read callback —
the agent handles everything else.

```python
from datetime import UTC, datetime
from uuid import UUID
from tagpulse_edge import EdgeAgent, EdgeConfig, RawTagRead

config = EdgeConfig(
    tenant_id=UUID(tenant_id),
    device_id=UUID(device_id),
    broker_host=broker,
    broker_port=1883,
    username=device_id,
    password=device_token,
    buffer_path="/var/lib/tagpulse/edge.sqlite",
    firmware_version="my-reader-1.0.0",
)

with EdgeAgent(config) as agent:
    while True:
        for read in my_reader.read_tags():        # vendor SDK
            agent.submit_tag_read(RawTagRead(
                tag_id=read.epc,
                antenna=read.antenna,
                signal_strength=read.rssi,
                observed_at=datetime.now(UTC),
            ))
```

The agent's defaults match the contract: 5 s dedup window, 10 s exit
timeout, 60 s heartbeat, 100 MB / 24 h offline buffer. Every knob is in
[`tagpulse_edge/config.py`](../../clients/pi/tagpulse_edge/config.py) and
also overridable via the device's server-side `configuration` JSON.

---

## 6. Azure `dev` cheat-sheet

Concrete endpoints for the deployed development environment as of this
writing:

| Resource | Value |
|---|---|
| API base | `https://tpdev-api.kindflower-bfd1de4e.centralus.azurecontainerapps.io` |
| OpenAPI / Swagger | `https://tpdev-api.kindflower-bfd1de4e.centralus.azurecontainerapps.io/docs` |
| MQTT broker (host) | `tpdev-mqtt-mwig6fst.centralus.azurecontainer.io` |
| MQTT port (plain) | `1883` |
| MQTT TLS | not yet — Sprint 17c (mTLS) and Sprint 24 (EMQX cutover) |
| Demo tenant `id` | `11111111-1111-1111-1111-111111111111` (slug `test-corp`, seeded by `scripts/smoke_setup.py`) |

> The MQTT broker is plain TCP for now. **Don't ship production credentials
> on this dev broker.** Token rotation via the admin UI invalidates the old
> token immediately, so leaks are recoverable.

For other environments, look up live FQDNs:
```bash
az containerapp show -n tpdev-api -g tagpulse-dev-rg \
  --query 'properties.configuration.ingress.fqdn' -o tsv
az container show -n tpdev-mqtt -g tagpulse-dev-rg \
  --query 'ipAddress.fqdn' -o tsv
```

---

## 7. End-to-end smoke from a laptop

A fast way to validate "my creds work + my data is landing" without any real
hardware:

```bash
# 1. seed/refresh the demo tenant + admin key (idempotent)
export TAGPULSE_API_URL=https://tpdev-api.kindflower-bfd1de4e.centralus.azurecontainerapps.io
python scripts/smoke_setup.py --full --with-roles --with-subject-telemetry
# prints: export TAGPULSE_API_KEY=tp_...

# 2. fire a synthetic load via the bundled simulator (HTTP path)
python scripts/simulate_devices.py \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  --devices 3 --interval 2 --tags 10 --with-gps

# 3. confirm reads landed
curl -s "$TAGPULSE_API_URL/tag-reads?limit=10" \
  -H "Authorization: Bearer $TAGPULSE_API_KEY" | jq 'length'
```

If you'd rather exercise the **MQTT** path end-to-end, the conformance
harness in [`tests/conformance/`](../../tests/conformance/) doubles as a
reference: each test publishes against a live broker and asserts the row
shows up in the API.

---

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `mosquitto_pub` exits 0 but no row in `/tag-reads` | Wrong topic — typo in tenant or device UUID | Topic must be exactly `tenants/<tenant_uuid>/devices/<device_uuid>/tag-reads`. The broker silently drops publishes that don't match an ACL. |
| `Connection refused` on port 1883 | Corporate firewall blocks MQTT | Use the HTTP `/tag-reads/batch` endpoint over 443; switch to MQTT once the broker moves to TLS on 8883 (Sprint 24). |
| 401 Unauthorized on HTTP | Stale or wrong token | Admin runs `POST /device-registry/{device_id}/rotate-token` and re-distributes; old token is invalid immediately. |
| 400 with `event_too_old` / `event_in_future` | Device clock drifted >24h or >5min | Sync NTP. The contract rejects out-of-window events to keep the hypertable from getting cluttered with bogus timestamps. See [edge-device-contract §3.5](../design/edge-device-contract.md). |
| Reads land but no asset shows in the UI | Tag→asset binding missing | Either bind via the admin UI or `POST /assets/{id}/bindings`. The simulator's `--with-gps` mode auto-binds `TAG0001…` to its synthetic assets. |
| Heartbeat says `online` but data stops | Reader's offline buffer is full | Increase `buffer_max_bytes` in the device's `configuration` JSON, or look for a clock-skew loop dropping every event before it's published. |

---

## 9. Where to go next

| You want to… | Read this |
|---|---|
| Read the authoritative wire contract | [`docs/design/edge-device-contract.md`](../design/edge-device-contract.md) |
| Understand RFID hardware choices | [`docs/refs/edge-hardware-and-rfid-primer.md`](../refs/edge-hardware-and-rfid-primer.md) |
| Understand the EPC / TID tag-data model | [`docs/design/rfid-tag-data-model.md`](../design/rfid-tag-data-model.md) |
| Set up the dev stack on a laptop | [`docs/quickstart.md`](../quickstart.md) |
| Operate the deployed cloud stack | [`docs/runbooks/azure-first-deploy.md`](../runbooks/azure-first-deploy.md) |
| See the rules engine that fires on incoming reads | [`docs/runbooks/subject-scoped-telemetry.md`](../runbooks/subject-scoped-telemetry.md) |
| File a conformance test for your custom firmware | [`tests/conformance/`](../../tests/conformance/) |
