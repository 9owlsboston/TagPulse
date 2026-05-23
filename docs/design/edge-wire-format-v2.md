# TagPulse Edge Wire Format v2 — Specification

> **Status: DRAFT v0.2 — pre-review.** This document is a working draft for review with WM (RFID reader firmware partner) before any code, schema, or ADR commits. **Nothing in this document is binding on either side until both parties sign off.** Open questions in §8 must be resolved before the spec is promoted out of draft.

| | |
|---|---|
| **Status** | Draft v0.2 |
| **Date** | 2026-05-23 |
| **Authors** | TagPulse backend (Boston Owls) |
| **External collaborator** | WM (RFID reader firmware, experimental; protocol co-designer) |
| **Supersedes (additively)** | TagPulse Edge Wire Format v1 (canonical `TagReadCreate`, Sprint 14; see [docs/guides/device-developer-guide.md](../guides/device-developer-guide.md)) |
| **Scope** | MQTT producer → broker (`devices/{tenant_id}/{device_id}/tag-reads`) → TagPulse `MQTTSubscriber`. JSON over MQTT. The producer is **unspecified** — it MAY be an RFID reader emitting v2 directly, or a Pi-class gateway (`clients/pi/tagpulse_edge/`) translating from a reader's native LAN-side output. See §1.5. Binary protocol explicitly out of scope for v2. |
| **Implementation sprint (proposed)** | Sprint 46 (unscheduled — see [docs/roadmap.md](../roadmap.md)) |
| **Related ADRs (proposed)** | ADR 025 — Edge wire format v2 (this spec). ADR 026 — Server-side tag presence model (storage decision in §4). |

---

## 1. Goals and non-goals

### Goals

1. **Minimize per-message bandwidth** for cellular / LTE-M backhauled readers.
2. **Stream deltas, not snapshots,** in steady state — when nothing changes, nothing is sent.
3. **Self-heal** server-side presence state from any combination of dropped messages, reader reboot, broker outage, or subscriber restart.
4. **Coexist** with v1 wire format (canonical `TagReadCreate` from Sprint 14) — both supported indefinitely, recognized by structural shape.
5. **Stay JSON.** Human-readable, debuggable with `mosquitto_sub`, parseable by the existing Pydantic flow.

### Non-goals

1. Full binary protocol (deferred to v3, gated on measured bandwidth need).
2. Server → reader configuration push (separate topic; out of scope for this spec).
3. Replacing HTTP `POST /tag-reads/batch` shape — that path stays on v1 forever.
4. Cryptographic tag authentication (Gen2v2 Authenticate — see [docs/roadmap.md](../roadmap.md) backlog).
5. **The reader-to-gateway LAN-side contract** (when a Pi-gateway producer is used). That is a separate spec — see `docs/design/reader-to-edge-contract.md` (TBD). v2 is the cellular/cloud-side MQTT contract only.

---

## 1.5 Producer architecture — who emits v2 MQTT

v2 is a **producer-agnostic wire format.** The spec does not constrain what kind of device terminates the MQTT connection — it only constrains what bytes are on the wire. Three concrete shapes are first-class:

```
Shape 1 — Reader-direct (high-end SKUs with embedded Linux + cellular modem):

    [RFID reader firmware]  --MQTT v2 over cellular-->  [TagPulse subscriber]

Shape 2 — Pi-gateway (low-end SKUs + experimental / homegrown readers):

    [reader]  --LAN: native format-->  [Pi: tagpulse_edge]  --MQTT v2 over cellular-->  [TagPulse subscriber]
            (CSV, serial, USB-CDC, TCP, etc.)            (JSON over MQTT QoS 1)

Shape 3 — Mixed fleet (the realistic deployment):

    Some readers run Shape 1, others run Shape 2, against the same broker / tenant.
    Server cannot tell them apart — both produce identical v2 bytes.
```

**Why this matters for the spec.** Several v2 features (snap-on-reconnect, empty-cycle snap with `epcs:[]`, NTP clock, rate limiting, offline buffering, dedup) require *some* producer-side state and intelligence. v2 assigns those responsibilities to **"the producer,"** not to "the reader." Whoever terminates MQTT — embedded firmware or Pi gateway — owns them.

**The Pi-gateway reference implementation** (`clients/pi/tagpulse_edge/`) already handles all producer-side responsibilities: cycle aggregation from a per-read input stream, diff state, MQTT QoS 1, reconnect with backoff, NTP-grade clocking, SQLite ring-buffer offline storage, ENTER/EXIT dedup, heartbeat. For Shape 2 / Shape 3 deployments it is the reference, and the v2 spec is its *output* contract.

**For WM specifically.** WM's experimental reader emits a native LAN-side format (per-read CSV rows, per-antenna headers — see `docs/design/reader-to-edge-contract.md`). In current deployments that stream is consumed by a `tagpulse_edge` Pi gateway, which produces v2 MQTT. WM remains the **protocol co-designer** for v2 — they brought the delta concept and understand the cellular bandwidth pain point — even when they are not the MQTT terminator in production. If WM ships a SKU that terminates MQTT itself in the future (Shape 1), the same v2 spec applies unchanged.

**Conformance.** A v2 producer is anything that emits the bytes specified in §2–§4. The §3.8 "reader profiles" (Delta / Snap-only / Legacy) describe the producer's behavior, not the underlying hardware. A Pi gateway implementing Profile A is just as conformant as a reader implementing Profile A natively.

---

## 2. Wire format

### 2.1 Envelope

One JSON object per MQTT publish. Flat — no nesting except `null`-allowed value fields. UTF-8, no BOM, whitespace SHOULD be omitted.

### 2.2 Fields

A v2 message has an **envelope** of top-level keys. For `t=0` (snap) the envelope carries an `epcs[]` array of **per-EPC entries** — one entry per (EPC, antenna) observation in the current cycle. For `t=1` (add) and `t=2` (sub), the per-EPC fields are flattened into the envelope — each add/sub message describes exactly one EPC observation. This keeps every wire message self-contained and atomic at the MQTT layer (§3.2).

**Presence conventions** (apply to the "Required on" column below):

- **Required** — the JSON key MUST appear in every message / entry of the listed type(s). Receivers reject if missing.
- **Optional** — the JSON key is **omitted entirely** when the value is absent. Senders MUST NOT emit `"key":null` for optional fields; receivers reject explicit `null` on optional sensor fields (`tmp`, `hum`) with DLQ `reason="explicit_null"`. See §6.
- **Nullable** — applies only to `lat` / `lon`. The key MUST appear, and `null` is the valid "no GNSS fix" value. (We keep these required-but-nullable rather than optional so a missing-key message is unambiguously malformed, not "no fix.")

Examples:

```jsonc
// t=0 snap with 3 observations (one EPC seen on two antennas):
{"t":0,"sn":123,"ts":1716489732001,"lat":41.40338,"lon":2.17403,
 "epcs":[
   {"an":1,"epc":"E2801160AAAA","rssi":-48,"cnt":2,"tmp":23.45,"hum":41.2},
   {"an":1,"epc":"E2801160BBBB","rssi":-52,"cnt":1},
   {"an":2,"epc":"E2801160AAAA","rssi":-61,"cnt":1}
 ]}

// t=0 snap, empty RF field — empty array, no special flag:
{"t":0,"sn":123,"ts":1716489732001,"lat":41.40338,"lon":2.17403,"epcs":[]}

// t=1 add — one message per appearing EPC, self-contained:
{"t":1,"sn":123,"ts":1716489732001,"lat":41.40338,"lon":2.17403,"an":1,
 "epc":"E2801160CCCC","rssi":-50,"cnt":1,"tmp":23.5,"hum":41.0}

// t=2 sub — one message per departing EPC, minimal:
{"t":2,"sn":123,"ts":1716489732001,"epc":"E2801160FFFF"}

// Reader with no GNSS fix — lat/lon present and explicitly null:
{"t":0,"sn":123,"ts":1716489732001,"lat":null,"lon":null,"epcs":[]}

// MALFORMED — explicit null on an optional field (rejected, DLQ reason=explicit_null):
{"t":1,"sn":123,"ts":1716489732001,"lat":41.40338,"lon":2.17403,"an":1,
 "epc":"E2801160CCCC","rssi":-50,"cnt":1,"tmp":null}
```

**Envelope fields** (top-level keys):

| Field | JSON type | Wire encoding | Range / format | Required on | Notes |
|---|---|---|---|---|---|
| `t` | integer | uint8 | `0` = snap, `1` = add, `2` = sub | **all** | Message type discriminator. Integer enum, not string. Reserved: `3` = heartbeat (v2.1), `4` = error (v2.1). |
| `sn` | integer | uint32 | numeric reader serial | **all** | Reader identifier. Resolves to `device_id` per §4.5. String form reserved for future deployments — see §8 Q1. |
| `ts` | integer | uint64 | Unix epoch milliseconds, UTC | **all** | Message timestamp. Server-side reject if drift > 5 minutes (configurable per §6, §8 Q7). |
| `lat` | number \| null | float64, 5 dp | -90.0 .. +90.0 | t=0, t=1 | Reader latitude (WGS84). `null` if no GNSS fix. MAY be omitted on `t=2`. |
| `lon` | number \| null | float64, 5 dp | -180.0 .. +180.0 | t=0, t=1 | Reader longitude. Same rules as `lat`. |
| `epcs` | array | JSON array | 0 .. ~5000 entries (see §7) | **t=0 only** | EPC observations for this cycle. Each entry has the per-EPC fields below. **Empty array (`[]`) means RF field is empty** — server reconciles to all-gone per §4.2. Forbidden on t=1, t=2. |
| `epc` | string | uppercase hex | 8 .. 124 hex chars | t=1, t=2 | EPC for this add/sub. Top-level on t=1/t=2 only; inside `epcs[]` entries on t=0. |
| `an` | integer | uint8 | 0 .. 255 (0 = unknown/muxed) | t=1 | Antenna port. MAY be omitted on `t=2`. Inside `epcs[]` entries on t=0. |
| `rssi` | integer | int16 | -127 .. 0 (dBm) | t=1 | See per-EPC table for definition. Inside `epcs[]` entries on t=0. |
| `cnt` | integer | uint16 | 1 .. 65535 | t=1 | See per-EPC table. Inside `epcs[]` entries on t=0. |
| `tmp`, `hum` | number | see per-EPC | see per-EPC | optional on t=1 | See per-EPC table. Inside `epcs[]` entries on t=0. |

**Per-EPC entry fields** (objects inside `epcs[]` on t=0; flattened into the envelope on t=1):

| Field | JSON type | Wire encoding | Range / format | Required | Notes |
|---|---|---|---|---|---|
| `an` | integer | uint8 | 0 .. 255 | yes | Antenna port that observed this EPC. |
| `epc` | string | uppercase hex | 8 .. 124 hex chars (32–496 bits) | yes | Electronic Product Code. No `0x` prefix, no whitespace. Server validates length is even. |
| `rssi` | integer | int16 | -127 .. 0 (dBm) | yes | Mean signal strength across `cnt` reads at this antenna in this cycle. Integer dBm — fractional precision dropped. |
| `cnt` | integer | uint16 | 1 .. 65535 | yes | Raw reads aggregated into this entry during the cycle. |
| `tmp` | number | float32, 2 dp | -40.00 .. +85.00 (°C) | optional | Mean tag-die temperature: `Σ(per-read temp) / cnt`. **Omit entirely if no successful sensor read was available this cycle** (do not send `null`). See §8 Q11 for the wire-is-lossy rationale. |
| `hum` | number | float32, 1 dp | 0.0 .. 100.0 (%RH) | optional | Mean tag humidity: `Σ(per-read humidity) / cnt`. Same rule as `tmp`. |

**Multi-antenna observation.** If the same EPC is read on more than one antenna in the same cycle, t=0 contains **multiple `epcs[]` entries with the same `epc` and different `an`** (see example above). Server reconciliation keys on `(sn, epc)` and merges them. For t=1, the producer emits one message per (EPC, antenna) pair newly appearing this cycle. See §8 Q9.

**Reserved field names (forward compatibility):** `v` (envelope version), `hb` (heartbeat-specific), `err` (error-specific), `cfg` (config echo), `seq` (former per-cycle counter, removed in the v2.0 KISS pass — see §8 "Removed in KISS pass"). Senders MUST NOT use these in v2.0.

### 2.3 Field name justification

Short names are a deliberate bandwidth optimization. Within JSON, field-name bytes dominate per-message overhead at small payload sizes. Trade is one-time documentation cost vs. per-message savings forever.

| Field | Long form considered | Bytes saved per message |
|---|---|---|
| `t` | `type` | 3 |
| `sn` | `serial_number` | 11 |
| `an` | `antenna` | 5 |
| `tmp` | `avg_temperature` | 12 |
| `hum` | `avg_humidity` | 9 |
| `cnt` | `read_count` | 7 |

Typical message saves ~45 B vs. long names. At 100 messages/min × 50 readers × 24 h, that's ~6 MB/day saved per fleet.

---

## 3. Semantics

### 3.1 The cycle model

A *cycle* is one RFID inventory round on the producer (typical: every 1–10 s). Each cycle decides what to publish based on **diff against the previous cycle**:

| Tag state this cycle | Tag state last cycle | Message emitted |
|---|---|---|
| Present | Present | **nothing** (steady state) |
| Present | Absent | one `t=1` (add) per (EPC, antenna) |
| Absent | Present | one `t=2` (sub) per EPC |
| Present | (no prior — boot) | full `t=0` snap (see §3.3) |

A "no-change" cycle emits **zero messages**. The producer does NOT send keep-alive on quiet cycles — keep-alive is the periodic snapshot (§3.3).

There is no per-cycle counter on the wire. Cycles are not addressable as such — each message stands alone, identified by its `(sn, ts)` and (for snap) its `epcs[]` payload. The server has no need to glue messages across a cycle boundary because no v2 message ever spans one.

### 3.2 Message atomicity

Every v2 message is **self-contained**: a complete observation that the server can process in isolation without buffering or cross-message stitching.

- A `t=0` snap is one MQTT message carrying the full per-cycle EPC set inside `epcs[]`. Reconciliation (§4.2) runs on message receipt. MQTT QoS 1 (§7) guarantees the message is delivered at least once; partial-snap states are not representable on the wire.
- A `t=1` add is one MQTT message describing one (EPC, antenna) appearance.
- A `t=2` sub is one MQTT message describing one EPC departure.

Messages from different producers (or from the same producer at different times) may interleave arbitrarily in MQTT delivery order; the server tolerates any ordering. There is no "cycle in progress" state on the server.

### 3.3 Snapshot triggers

The producer MUST emit a snapshot under any of these conditions, whichever occurs first:

1. **Time-based:** every 300 s (5 min) of wall-clock time since the last snapshot.
2. **Cycle-based:** every 100 cycles since the last snapshot.
3. **Event-based:** as the **first message of any new MQTT session** — after boot, reconnect, or any session-resume — before any `t=1` / `t=2` deltas.

Each trigger produces exactly one `t=0` MQTT message containing the current full EPC set (possibly an empty array — see §3.4). Snapshot cadence (300 s / 100 cycles) is the default; SHOULD be server-configurable in v2.1 via a separate config topic.

### 3.4 Empty snapshot

If the producer has zero EPCs in field at snapshot time, it emits a snap with an empty array:

```json
{"t":0,"sn":123,"ts":1716489731123,"lat":41.40338,"lon":2.17403,"epcs":[]}
```

This is the only way the server can distinguish "producer sees nothing" from "producer is dead." Without it, an empty-field snapshot would emit no MQTT message at all and previously-present EPCs would never be marked gone.

**`epcs:[]` is structurally unambiguous.** Earlier drafts used an `empty:true` boolean flag; v2.0 dropped it in favor of the empty array, which is impossible to confuse with anything else: the snap-reconciliation algorithm (§4.2) treats *any* `t=0` as the complete-truth declaration for that producer at that instant, so an empty array reconciles every currently-`present` EPC for this producer to `gone`. There is no "presence flag" to misset.

### 3.5 Sub for never-seen EPC

If the server receives a `t=2` (sub) for an `(sn, epc)` it has no `present` row for: log + counter, do not raise an error. This is normal during a sync window — a `sub` arrived for an EPC the server lost via a missed `add`. The next snapshot will reconcile.

### 3.6 Reboot and out-of-order handling

With no per-cycle counter on the wire, the server has nothing to gap-detect against — gaps in t=1/t=2 streams are invisible by design, and the periodic snap (§3.3) is the recovery mechanism. The server's posture toward producer-side anomalies:

| Server observation | Meaning | Action |
|---|---|---|
| `t=0` arrives | Producer is asserting full truth about its current field | Run reconciliation per §4.2 unconditionally — this is the recovery primitive |
| `t=1` / `t=2` arrives for a known producer | Normal delta | Apply per §4.3 |
| `t=1` / `t=2` arrives for an unknown / never-seen `sn` | Producer started mid-stream | Apply best-effort; the producer's next snap (which §3.3 guarantees as the first message of every session) will reconcile any incidental drift |
| `ts` jumps backwards or producer goes silent then resumes | Reboot, clock fix, link recovery — opaque on the wire | No special handling. Wait for the next snap, which §3.3 mandates as the first message of every new session. Snap reconciles |

Producer reboot is therefore **not a distinct server-side concept**: it's just "a producer that hasn't published in a while now publishes a snap." The snap-on-reconnect rule (§3.3 trigger 3) is what makes this safe — without it, a rebooted producer streaming only `t=1` deltas would never resynchronize the server's view.

A `t=2` sub for an EPC the server has no `present` row for is handled per §3.5 — log + counter, no error.

### 3.7 `t=0` vs `t=1` — different shapes, opposite semantics

`t=0` (snap) and `t=1` (add) describe overlapping facts ("this EPC is at this antenna right now") but with very different scopes:

| Property | `t=0` (snap) | `t=1` (add) |
|---|---|---|
| **Wire shape** | Envelope + `epcs[]` array of N entries | Envelope with EPC fields flattened — describes exactly one (EPC, antenna) |
| **Meaning of being in the message** | "This is my complete EPC set right now" | "This EPC just appeared (wasn't here last cycle)" |
| **Meaning of an EPC being *absent* from the message** | "Not in my `epcs[]` → currently **gone** — wipe from presence" | Silent — says nothing about other EPCs |
| **Triggers reconciliation?** | **Yes** — runs the diff in §4.2 on receipt; may emit `tag_disappeared` for EPCs no longer listed | **No** — point update only |
| **Atomic in?** | One MQTT message (whole `epcs[]` arrives or doesn't — QoS 1 ensures delivery) | One MQTT message (one EPC) |
| **Emitted on every cycle?** | No — only on snap triggers (§3.3) or empty field (§3.4) | Only when an EPC transitions absent → present |

The takeaway: **a missing `t=1` for an EPC is benign** (next snap reconciles within snap cadence), but **a missing `t=0` message is dangerous** (the next snap *is* what brings the server back into sync). MQTT QoS 1 + `clean_session=false` (§7) is what makes snap delivery reliable enough to depend on; the snap-on-reconnect rule (§3.3 trigger 3) is what bounds the worst-case staleness.

Common confusion: "if every cycle was a snap, we'd never need `t=1`/`t=2`." True — and that's exactly the **snap-only profile** in §3.8. The point of `t=1`/`t=2` is bandwidth; the point of `t=0` is correctness.

### 3.8 Producer profiles — what the MQTT producer needs to support

Three producer profiles are first-class on this protocol. "Producer" here means the entity that terminates MQTT and emits v2 bytes — could be reader firmware (Shape 1, §1.5) or a Pi-gateway (Shape 2). Conformance tests MUST NOT require Profile A of all producers — the lower profiles are valid implementations targeting different hardware / cost classes.

**Profile A — Delta (full v2).** The default this spec is designed around.

- Emits `t=0` snap on triggers per §3.3 (boot, reconnect, periodic time/cycle)
- Emits `t=1` / `t=2` deltas between snaps
- Requires per-cycle diff state on the producer (last-cycle EPC set in memory)
- Bandwidth: minimal in steady state
- Target producer: Pi-gateway (`tagpulse_edge`, default), mid-to-high-end reader firmware

**Profile B — Snap-only.** Acceptable for readers that can't maintain per-cycle diff state.

- Emits `t=0` snap **every cycle** (no `t=1`, no `t=2`)
- Each cycle is a complete declaration of current field
- Server reconciles on every snap (§4.2)
- Empty cycles still emit `{"epcs":[]}` per §3.4
- Bandwidth: ~N× higher than Profile A in steady state (where N = EPC count); acceptable for short-range / low-EPC-count deployments (e.g., handheld scanners, single-pallet zones)
- No producer state beyond "what's in my field right now this cycle"
- **Server treats Profile B identically to Profile A** — there is no profile flag on the wire. A producer that only ever sends `t=0` is just a producer whose snap cadence is "every cycle."
- Target producer: minimal Pi-gateway configs, low-end / handheld / battery reader firmware

**Profile C — Legacy / v1 streaming.** Existing producers staying on the v1 wire format indefinitely.

- Emits canonical `TagReadCreate` per-read (no `t` field at all)
- v1 path in `_handle_tag_read` handles these unchanged (§4.3)
- Does NOT populate `tag_presence` — presence model is v2-only
- Target producer: any pre-v2 reader, or partners who don't want to implement deltas

**Mixed-fleet operation.** All three profiles can run simultaneously against the same broker / subscriber / tenant. The recognizer (presence of integer `t` field) routes v1 vs. v2 per-message. Within v2, Profile A and Profile B are indistinguishable to the server. No tenant-level or device-level profile config is needed.

**For Shape 2 / WM deployments specifically:** the Pi-gateway implements Profile A by default; the underlying WM reader doesn't need to know about profiles or deltas — it just streams per-read observations on the LAN side and the Pi handles aggregation. If a future reader SKU implements MQTT termination directly (Shape 1) and can't maintain diff state, Profile B is a fully-supported fallback — no spec changes needed.

---

## 4. Server-side behavior

### 4.1 Storage model

Two tables, both new (column or new-table additions in the implementation sprint):

**`tag_reads`** (existing hypertable) — gets one row per `t=0` (snap) or `t=1` (add) message. **No row** for `t=2` (sub). Mapping per §4.4 below. Snapshots and adds are observations of an EPC being present at a time and place; `tag_reads` is the right home for them.

**`tag_presence`** (NEW table — proposed Alembic migration `042_tag_presence.py`):

```
tenant_id     UUID NOT NULL
device_id     UUID NOT NULL          -- resolved from sn
epc           VARCHAR(124) NOT NULL  -- uppercase hex
first_seen    TIMESTAMPTZ NOT NULL
last_seen     TIMESTAMPTZ NOT NULL
status        VARCHAR(16) NOT NULL   -- 'present' | 'gone'
last_rssi     SMALLINT
last_antenna  SMALLINT
PRIMARY KEY (tenant_id, device_id, epc)
```

No `last_seq` / `suspect` columns: there is no per-cycle counter on the wire (§3.1) and no buffered-snap state to be suspicious about — reconciliation either runs (snap arrived) or it doesn't (server falls back on the next snap, bounded by snap cadence).

Indexes:

- `idx_tag_presence_active ON (tenant_id, device_id) WHERE status='present'` — drives the "what's at this reader right now" query.
- `idx_tag_presence_tenant_epc ON (tenant_id, epc) WHERE status='present'` — drives "where is this EPC now."

RLS enabled per repo convention (no session GUC; explicit `WHERE tenant_id = :tenant_id` in every query).

### 4.2 Snapshot reconciliation algorithm

Reconciliation runs **synchronously on receipt of every `t=0` message** — no buffering window, no cross-message stitching. The snap message itself carries the complete EPC set (§3.4); the server treats it as the authoritative current state for this producer at `ts`:

```
snap_epcs := { entry.epc for entry in msg.epcs }    -- may be empty
present_epcs := SELECT epc FROM tag_presence
                WHERE tenant_id=? AND device_id=? AND status='present'

to_mark_present := snap_epcs
to_mark_gone    := present_epcs - snap_epcs

UPSERT tag_presence ... status='present', last_seen=ts,
                        last_rssi=entry.rssi, last_antenna=entry.an
  for each entry in msg.epcs                       -- on conflict by (sn, epc) prefer max(rssi)

UPDATE tag_presence SET status='gone', last_seen=ts
  WHERE (tenant_id, device_id, epc) in to_mark_gone

EMIT signaling.tag_appeared    for each (gone | absent) → present transition
EMIT signaling.tag_disappeared for each present → gone transition
```

Multi-antenna entries for the same EPC inside one `epcs[]` collapse to one `present` row via the upsert's `ON CONFLICT` clause (highest `rssi` wins for `last_rssi` / `last_antenna`; presence itself is binary).

Two new event-bus topics added to `src/tagpulse/events/protocol.py`:

- `Topic.SIGNALING_TAG_APPEARED`
- `Topic.SIGNALING_TAG_DISAPPEARED`

These join `Topic.SIGNALING_ATTRIBUTION_SETTLED` (Sprint 41) as inputs to future `signaling.<event_type>.on_appearance` / `on_disappearance` rule kinds (Sprint 47+, not part of this spec).

### 4.3 Per-message handler routing

In `_handle_tag_read` (see [src/tagpulse/ingestion/mqtt_subscriber.py](../../src/tagpulse/ingestion/mqtt_subscriber.py)), recognize v2 by presence of integer `t` field:

```python
if isinstance(raw, dict) and isinstance(raw.get("t"), int):
    await self._handle_wm_v2_message(tenant_id, device_id, raw, message)
    return
# else: existing v1 paths unchanged
```

Then dispatch on `t`:

- `t == 0` → run reconciliation per §4.2 immediately; insert one `tag_reads` row per entry in `epcs[]`.
- `t == 1` → upsert `tag_presence` (`present`, bump `last_seen`); insert `tag_reads` row; emit `tag_appeared` if transition.
- `t == 2` → update `tag_presence` (`gone`, set `last_seen`); no `tag_reads` row; emit `tag_disappeared`.
- unknown `t` → reject, DLQ with `reason='unknown_type'`.

### 4.4 Mapping to existing models

| v2 wire | `TagReadCreate` (inserts) | `tag_presence` (upserts) |
|---|---|---|
| `sn` → lookup `devices.id` | `device_id` | `device_id` |
| `ts` | `timestamp` | `last_seen`, `first_seen` (on insert) |
| `lat`, `lon` | `location.latitude`, `location.longitude`; `location.source = "reader_gnss"` | — |
| `an` (envelope or entry) | `reader_antenna` | `last_antenna` |
| `epc` (envelope or entry) | `tag_id` AND `identity.epc_hex` | `epc` |
| `rssi` (envelope or entry) | `signal_strength` (cast to float) | `last_rssi` |
| `cnt`, `tmp`, `hum` | `sensor_data` JSONB: `{"read_count":cnt,"avg_temp_c":tmp,"avg_humidity_pct":hum}` | — |
| `epcs[]` (t=0 only) | one `TagReadCreate` per entry | drives §4.2 reconciliation |
| `t` | (not stored; determines code path) | (determines `status` column) |

### 4.5 SN → device_id resolution

Two-stage lookup, both per-tenant:

1. **Primary:** `SELECT id FROM devices WHERE tenant_id = ? AND (metadata->>'serial')::text = ?`.
2. **Fallback:** if `sn` is uuid-shaped, attempt direct match on `devices.id`.

Failure → reject, DLQ with `reason='device_not_found'`. The MQTT JWT's `device_id` claim MUST match the resolved `device_id` — mismatch → reject, DLQ with `reason='sn_jwt_mismatch'`. This is the load-bearing identity guarantee; the wire `sn` is for human convenience, the JWT is the trust root.

---

## 5. Examples

### 5.1 Steady-state cycle (nothing changed)

50 EPCs in field, all present last cycle, no new arrivals or departures:

**Wire:** *(zero messages)*

**Server state:** unchanged. `tag_presence` rows for these EPCs retain prior `last_seen` (a few seconds stale, acceptable). The producer's next periodic snap will refresh `last_seen` on all rows.

### 5.2 Cycle with 5 new tags and 3 departures

50 EPCs from prior cycle, 3 gone, 5 newly present (52 in field this cycle):

**Wire (8 messages):**

```json
{"t":1,"sn":123,"ts":1716489732001,"lat":41.40338,"lon":2.17403,"an":1,"epc":"E2801160AAAA","rssi":-48,"cnt":2}
{"t":1,"sn":123,"ts":1716489732001,"lat":41.40338,"lon":2.17403,"an":1,"epc":"E2801160BBBB","rssi":-52,"cnt":3}
{"t":1,"sn":123,"ts":1716489732001,"lat":41.40338,"lon":2.17403,"an":2,"epc":"E2801160CCCC","rssi":-44,"cnt":4}
{"t":1,"sn":123,"ts":1716489732001,"lat":41.40338,"lon":2.17403,"an":2,"epc":"E2801160DDDD","rssi":-51,"cnt":2}
{"t":1,"sn":123,"ts":1716489732001,"lat":41.40338,"lon":2.17403,"an":1,"epc":"E2801160EEEE","rssi":-60,"cnt":1}
{"t":2,"sn":123,"ts":1716489732001,"epc":"E2801160FFFF"}
{"t":2,"sn":123,"ts":1716489732001,"epc":"E2801160GGGG"}
{"t":2,"sn":123,"ts":1716489732001,"epc":"E2801160HHHH"}
```

Total wire: ~700 B for the 8 messages (vs. ~9 KB if every cycle re-sent all 52 EPCs).

**Server effect:**

- 5 inserts into `tag_reads`.
- 5 upserts into `tag_presence` (`present`, new rows or status transitions).
- 3 updates in `tag_presence` (`gone`).
- 5 `signaling.tag_appeared` events emitted.
- 3 `signaling.tag_disappeared` events emitted.

### 5.3 Periodic snapshot (52 EPCs in field)

Five minutes have elapsed since the last snapshot. Producer emits **one** `t=0` message carrying all 52 entries in `epcs[]`:

```json
{"t":0,"sn":123,"ts":1716489862001,"lat":41.40338,"lon":2.17403,
 "epcs":[
   {"an":1,"epc":"E2801160AAAA","rssi":-48,"cnt":3,"tmp":23.45,"hum":41.2},
   {"an":1,"epc":"E2801160BBBB","rssi":-52,"cnt":2},
   {"an":2,"epc":"E2801160CCCC","rssi":-44,"cnt":4,"tmp":23.49,"hum":41.0},
   ... 49 more entries ...
 ]}
```

**Wire:** one MQTT message, ~5–6 KB (52 entries × ~100 B/entry inside `epcs[]`).

**Server effect:**

- Reconciliation runs immediately on receipt (no buffering, no window).
- Compares the 52 EPCs against current `present` rows for this `(tenant, device)`. Matches bump `last_seen`, no events. Rows that were `present` but absent from the snap are marked `gone`, fire `signaling.tag_disappeared` \u2014 this is how dropped `sub` messages from prior cycles get healed.
- 52 `tag_reads` rows inserted (one per entry, for the time-series audit trail).

### 5.4 Empty snapshot (field empty)

```json
{"t":0,"sn":123,"ts":1716490031000,"lat":41.40338,"lon":2.17403,"epcs":[]}
```

**Server effect:**

- Reconciliation runs immediately. `snap_epcs` is empty.
- Every currently-`present` row for this `(tenant, device)` is marked `gone`, `last_seen = ts`. Fan-out of `tag_disappeared` events.

### 5.5 Producer reboot

Producer power-cycles. MQTT session reconnects. Per \u00a73.3 trigger 3, the **first** message of the new session is a snap:

```json
{"t":0,"sn":123,"ts":1716490200000,"lat":41.40338,"lon":2.17403,
 "epcs":[
   {"an":1,"epc":"E2801160AAAA","rssi":-48,"cnt":1},
   ... 49 more ...
 ]}
```

**Server effect:**

- Reboot is opaque on the wire (\u00a73.6) \u2014 server has no `last_seq` to compare. None needed.
- Reconciliation runs immediately on the snap: anything `present` not in `epcs[]` \u2192 `gone`. Anything in `epcs[]` not `present` \u2192 insert `present`.
- Subsequent `t=1` / `t=2` deltas from the reborn producer are applied normally.

### 5.6 Subscriber outage and recovery

Subscriber pod restarts. Mosquitto buffers messages for the duration (QoS 1, `clean_session=false`, \u00a77). Subscriber reconnects, broker replays buffered messages.

**Server effect:**

- Replayed messages process individually. Each is self-contained (\u00a73.2) so out-of-order arrival is harmless: an `add` for an EPC that's already `present` is a no-op upsert; a `sub` for an unknown EPC is logged per \u00a73.5.
- If any `t=1` / `t=2` was lost entirely (e.g., broker buffer overflow at QoS 1's edge), the producer's next periodic snap (within snap cadence, default 5 min) reconciles. Self-heal.\n\n### 5.7 Lost `sub` (the failure snap exists to fix)\n\nProducer publishes a `t=2` for `E2801160AAAA`. Message is lost (QoS 1 should prevent this but assume a transient session-loss edge case). Server never sees the sub. `tag_presence` shows the EPC as still `present`.\n\nFor the next 5 minutes (or 100 cycles), the server is wrong \u2014 dashboards show the EPC at this reader when it isn't there.\n\nNext scheduled snap arrives. The EPC is not in `epcs[]`. Reconciliation marks it `gone`, emits `tag_disappeared`. **Server self-heals.** Maximum incorrect-state window: snapshot cadence (5 min default).

---

## 6. Error handling

| Condition | Action | DLQ? | OTel counter |
|---|---|---|---|
| `t` field missing | Reject | Yes | `tagpulse_mqtt_wm_rejections_total{reason="missing_type"}` |
| `t` value not in `{0,1,2}` | Reject | Yes | `...{reason="unknown_type"}` |
| `epc` invalid (odd length, non-hex, out of range) | Reject | Yes | `...{reason="invalid_epc"}` |
| `sn` not registered for tenant | Reject | Yes | `...{reason="device_not_found"}` |
| JWT `device_id` ≠ resolved `device_id` from `sn` | Reject + audit log | Yes | `...{reason="sn_jwt_mismatch"}` |
| `ts` drift > 5 min from server clock | Reject | Yes | `...{reason="clock_skew"}` |
| Missing `lat` / `lon` on `t=0` / `t=1` (omitted key, not `null`) | Reject | Yes | `...{reason="missing_required_field"}` |
| `epcs` field present on `t=1` / `t=2` (forbidden) | Reject | Yes | `...{reason="epcs_wrong_type"}` |
| `epcs` field missing on `t=0` (must be at least `[]`) | Reject | Yes | `...{reason="missing_required_field"}` |
| `epcs[]` entry missing `an` / `epc` / `rssi` / `cnt` | Reject whole message | Yes | `...{reason="invalid_snap_entry"}` |
| `epcs[]` length above soft cap (default 5000) | Process + log warning | No | `tagpulse_mqtt_wm_snap_large_total{sn}` |
| Explicit `null` on optional sensor field (`tmp`, `hum`) | Reject | Yes | `...{reason="explicit_null"}` |
| `t=2` for never-seen EPC | Log debug + counter only; do not reject | No | `tagpulse_mqtt_wm_sub_no_presence_total` |

All rejection paths route through the existing `_record_rejection` + `_persist_mqtt_drop` infrastructure (Sprint 28 C3). New counters added to `src/tagpulse/core/otel_metrics.py`.

---

## 7. MQTT and transport requirements

| Setting | Value | Justification |
|---|---|---|
| **Topic** | `devices/{tenant_id}/{device_id}/tag-reads` | Existing, unchanged. Tenant + device are UUIDs; `sn` in payload is verified to resolve to the same `device_id`. |
| **QoS** | **1** (was 0 in v1) | Without QoS 1, lost `sub` messages corrupt presence state until next snap — too long. |
| **Clean session** | `false` | Broker queues messages during reconnect; replay restores in-flight cycles. |
| **Retain flag** | `false` | A retained `add` would re-add stale EPCs on every new subscriber connection. Catastrophic. |
| **Keep-alive** | 60 s | Reasonable for cellular; broker drops dead sessions within ~90 s. |
| **TLS** | Required (`mqtts://` on 8883) | Per Sprint 17b cert scaffolding. Reader uses `client.tls_set(ca_certs=tls_ca, cert_reqs=ssl.CERT_REQUIRED)`. mTLS rollout per Sprint 17c. |
| **Payload encoding** | UTF-8 JSON, no BOM | Whitespace SHOULD be omitted. |
| **Recommended max payload** | ~10 KB per message (snap-driven); 1 KB typical for t=1/t=2 | Broker hard limit is 256 KB. A snap with ~100 entries × ~100 B = ~10 KB, well within limits. Soft-warn (no reject) at 5000 entries per §6. |

---

## 8. Open questions

Resolutions below are organized by **who owns each question** under the Shape C producer architecture (§1.5). Most questions that originally read as "for WM firmware" are actually producer-side concerns — and under Shape 2 (Pi-gateway), the producer is `clients/pi/tagpulse_edge/`, which we own. Only questions about protocol semantics or the WM reader's LAN-side output remain WM-facing, and the LAN-side ones move to `docs/design/reader-to-edge-contract.md` (TBD).

### 8.1 Resolved — producer-agnostic protocol decisions

These are decisions about the v2 wire bytes themselves. Co-designed with WM as protocol partner; binding on any producer (reader-direct or Pi-gateway).

| # | Question | Resolution | Authority |
|---|---|---|---|
| Q1 | `sn` type — integer or string? | **Integer** (uint32). Producers stamp a numeric reader ID. Strings remain reserved in §2.2 for future deployments with alphanumeric serials but are not used in v2.0. | TagPulse + WM, 2026-05-23 |
| Q6 | `tmp` / `hum` aggregation window | `Σ(per-read value) / cnt` per cycle — same window as `rssi`. Recorded in §2.2. | WM, 2026-05-23 |
| Q9 | Multi-antenna emission | **One entry per (EPC, antenna) pair per cycle.** If EPC X is read on antennas 1 and 2 in the same cycle, the producer emits two `epcs[]` entries (on t=0) or two `t=1` messages with the same `ts`, different `an`, and each `rssi` reflects that antenna's reads. Rationale: location triangulation downstream needs per-antenna RSSI, and aggregating across antennas would lose that signal. Server-side reconciliation (§4.2) keys on `(sn, epc)` not `(sn, an, epc)` — multi-entry duplicates within a snap are merged. | TagPulse + WM, 2026-05-23 |
| Q11 | Sensor-error vs. no-sensor on the wire | **Omit `tmp` / `hum` in both cases.** The v2 wire is intentionally lossy here — it carries the value when the producer has one to emit and stays silent otherwise. Diagnostic detail (per-EPC sensor-read failure rate, capability inference) is producer-local concern, exposed via producer-side metrics, not on the cellular wire. Rationale: passive RFID sensor tags fail reads for many physics-driven reasons (collision, attenuation, tag-side timeout) that aren't binary "sensor broken" signals — overloading the wire with a `sensor_err` enum would invite false-positive ops noise. Revisit in v2.1 if dashboards need it; for now, the producer keeps a local rolling counter. | TagPulse, 2026-05-23 |

### 8.2 Resolved — producer-side responsibilities

These are concerns about producer state and behavior, not about wire bytes. Under Shape 2 (Pi-gateway) the producer is `clients/pi/tagpulse_edge/`, which already implements all of these (`clients/pi/README.md`). Under Shape 1 (reader-direct), the reader must implement them. Either way, the v2 spec just requires the bytes to come out right.

| # | Question | Resolution | Authority |
|---|---|---|---|
| Q3 | Snapshot cadence defaults | **300 s OR 100 cycles, whichever comes first**, as v2.0 default for both Shape 1 and Shape 2 producers. Per-device configurable via producer config (Pi-gateway: `clients/pi/tagpulse_edge/config.py`; reader-direct: vendor config). Server-side config push deferred to v2.1. | TagPulse, 2026-05-23 |
| Q4 | Snap on reconnect | **Required of all producers.** Pi-gateway reference impl emits a snap as the first message of every new MQTT session (already in `transport.py`). Reader-direct producers MUST do the same — if a reader-direct SKU can't, it falls back to Profile B (snap-every-cycle, §3.8), which makes reconnect-snap automatic. No "reconnect without snap" path is permitted in v2. | TagPulse, 2026-05-23 |
| Q5 | Empty-field snap | **Required of all producers.** Pi-gateway reference impl detects empty cycle from input stream and emits the `"epcs":[]` snap. Reader-direct producers MUST do the same. Without it, the server cannot distinguish "field empty" from "producer dead" (§3.4). | TagPulse, 2026-05-23 |
| Q7 | Clock source | Producer MUST emit `ts` within ±5 min of true UTC (§6 `clock_skew` rejection threshold). Pi-gateway: NTP via `systemd-timesyncd` (default on Raspberry Pi OS); offline-buffered messages use the cycle's observed time at capture, not at later send. Reader-direct: NTP-synced or GNSS-derived. Producers that cannot maintain ±5 min should not be deployed against v2. | TagPulse, 2026-05-23 |
| Q8 | Rate limit per producer | Producer-side throttle: ≤100 msgs/s sustained, ≤500/s burst per producer. Pi-gateway enforces via batched `transport.py` flush. Reader-direct producers self-limit. Mosquitto per-client cap configured to match. Above-burst messages are buffered (Pi-gateway: SQLite ring; reader-direct: vendor decision). | TagPulse, 2026-05-23 |

### 8.3 Removed in the v2.0 KISS pass

The following questions appeared in earlier drafts and were resolved by **deleting the underlying wire feature** rather than answering them. Recorded here so historic references in commits, ADRs, and the conformance test suite remain decipherable.

| # | Original question | Resolution |
|---|---|---|
| Q2 | `seq` persistence across reboot | **Moot.** `seq` removed from the wire (§3.1). Producer reboot is opaque to the server; the snap-on-reconnect rule (§3.3 trigger 3) is what brings the server back into sync. |
| Q10 | `seq` wrap behavior at `2^32 − 1 → 0` | **Moot.** No counter to wrap. |
| Q12 | Snap terminator (`{"end":true}`) | **Moot.** A snap is one MQTT message carrying the full `epcs[]` array (§3.4) — there is nothing to terminate. The original concern (snap-window close ambiguity) does not exist when there is no snap window. |

### 8.4 Open — WM reader-to-edge LAN-side contract

These questions are about WM's reader-to-Pi LAN-side output (per-read CSV, per-antenna headers — see the sample at `https://github.com/weimin-peng/hello-world/blob/main/data.csv`). They are out of scope for v2 (which is the MQTT cellular-side contract) and belong in a companion spec.

- **Q-LAN-1** — Transport: serial / USB-CDC / TCP / file watch / UDP? Per-SKU or uniform?
- **Q-LAN-2** — CSV schema: what does the `--` separator between blocks delimit (cycle boundary, antenna boundary, both)? Is there a per-row timestamp, or is timing implicit in arrival order?
- **Q-LAN-3** — Sensor read failure encoding: when a sensor read fails for an inventoried tag, does WM (a) omit the row entirely, (b) emit the row with empty `temp` / `humidity` cells, or (c) emit a sentinel value (`-999`, etc.)? The Pi-gateway needs this to correctly apply the v2 §2.2 omit-vs-present rule.
- **Q-LAN-4** — Empty-cycle signaling: how does the reader indicate "this cycle saw zero EPCs" on the LAN stream? (Needed for Pi-gateway to emit the `"epcs":[]` snap per §3.4.)
- **Q-LAN-5** — Per-SKU capability inventory: which antenna count, sensor types, RSSI dynamic range per WM SKU? (Pi-gateway uses this to validate input and to know what fields can ever appear.)
- **Q-LAN-6** — Reader-side reset / reboot signaling: is there an explicit marker on the LAN stream, or does the Pi infer from gaps / sequence discontinuities?
- **Q-LAN-7** — Header typo: the sample CSV header reads `issi` — confirm this is RSSI (and ideally fix to `rssi` in firmware).

**Action:** these move to `docs/design/reader-to-edge-contract.md` (new doc, TBD). Out of scope for v2 promotion.

---

## 9. Concerns

These are the open / uncomfortable items, listed explicitly so they don't get lost in iteration.

### 9.1 Concerns addressed by this spec

1. ✅ **Bandwidth efficiency** — delta model + short field names + integer enums + epoch-ms timestamps. ~70% reduction vs. v1 in steady state.
2. ✅ **Self-heal from message loss** — periodic + reconnect + cycle snapshots reconcile drift.
3. ✅ **Cross-message stitching avoided** — every message is self-contained; no header/payload split.
4. ✅ **Coexistence with v1** — recognition by structural shape (`t` field is integer = v2).
5. ✅ **Identity grounded in JWT** — `sn` is convenience; trust root is JWT `device_id` claim.

### 9.2 Concerns this spec does NOT fully solve

Each entry below uses the same structure so a developer touching the relevant code path knows exactly what can go wrong, what they'll see in metrics/logs, what we've done about it today, and what the long-term plan is. **Read the concern for the area you're modifying before changing code.**

#### 1. Single subscriber replica assumption

- **Scenario.** Reconciliation reads-then-writes against `tag_presence` per-snap (§4.2). With two subscriber replicas simultaneously subscribed to the broker, MQTT shared-subscription semantics round-robin messages across both. Two snaps from different readers process in parallel against the same database — fine. But two **deltas** (`t=1` / `t=2`) for the same EPC arriving simultaneously at different replicas can race on the `tag_presence` upsert, and two **snaps** from the same reader arriving in rapid succession (e.g., reconnect immediately followed by periodic snap) can race their reconciliations.
- **Symptoms.** Lost-update on `last_rssi` / `last_antenna` (winner is unpredictable). Spurious extra `signaling.tag_appeared` / `tag_disappeared` events if both replicas observe a transition between read and write. Generally low-impact because the next snap reasserts truth, but downstream rules that key on event order may misfire.
- **Mitigation today.** Pinned to one replica.
  - **K8s framing (forward-compatible).** Run the `MQTTSubscriber` Deployment with `replicas: 1` and `strategy.type: Recreate`.
  - **Current implementation (Azure Container Apps).** Worker pinned to `minReplicas: 1, maxReplicas: 1` in [deploy/azure/bicep/workload.bicep](../../deploy/azure/bicep/workload.bicep) (the API app next to it is `1..3` because it's stateless — *do not copy that pattern to the worker*).
    - **ACA rolling-revision deploys briefly run two replicas.** ACA's revision model is rolling, not `Recreate`. During every deploy there's a ~30–60 s window where both old and new revisions are active, both subscribed to the broker, both processing messages independently. Self-heal recovers within one snap cadence (5 min default); operators should expect a short burst of `tag_disappeared` / `tag_appeared` flapping on every deploy.
    - **Scale-to-zero is dangerous.** Never let the worker's `minReplicas` drift to 0 — cold-start drops broker subscription, missing every message published during the gap until reconnect-and-snap. Bicep enforces `minReplicas: 1` today; treat as load-bearing.
- **Long-term.** When per-broker volume justifies sharding: shared subscription with one consumer per shard, each shard owning a `(tenant, device)` partition so the upsert never races. On ACA, no stable replica ordinals — would require an external coordinator (Redis) or migrating the worker to AKS.

#### 2. `tag_presence` table unbounded growth

- **Scenario.** Every EPC ever seen at a reader gets a row that stays forever — `gone` rows never delete. A tenant with 1M unique EPCs over a year has 1M `tag_presence` rows even if only 200 are present at any instant.
- **Symptoms.** Slow `tag_presence` queries over time (indexes still help, but page cache effectiveness degrades). Bloated logical backups. The `idx_tag_presence_tenant_epc … WHERE status='present'` partial index keeps hot-path queries fast, but `SELECT … WHERE tenant_id=? AND epc=?` (no status filter) full-table scans escalate. Migration costs rise.
- **Mitigation today.** None — `gone` rows accumulate. Acceptable for v2.0 (pilot scale, ~10K EPCs/tenant/month).
- **Long-term.** Two options:
  1. **TTL job.** Periodic `DELETE FROM tag_presence WHERE status='gone' AND last_seen < now() - interval '30 days'` (configurable). Simple. Loses long-term "was this tag ever at this reader" history.
  2. **Compaction to cold table.** Move aged `gone` rows to `tag_presence_history` summary table (one row per `(tenant, device, epc)` lifetime with `seen_count`, `first_seen`, `last_seen`). Preserves history at lower cost. More implementation work.
  - Backlog entry against ADR 026.

#### 3. Two-table writes are not cross-pool transactional

- **Scenario.** A `t=1` (add) writes to both `tag_reads` (hypertable) and `tag_presence` (new table). Today both run inside the same `AsyncSession` → one DB transaction → atomic. **But if `tag_reads` is ever migrated to a separate DB pool** (e.g., Sprint 13b multi-tier with TimescaleDB on a dedicated cluster), the two writes split across pools and lose atomicity. A crash between them leaves `tag_reads` populated and `tag_presence` stale, or vice versa.
- **Symptoms (only if we go multi-tier).** Dashboard tag count disagrees with raw audit query (`SELECT count(distinct epc) FROM tag_reads WHERE …` vs. `SELECT count(*) FROM tag_presence WHERE status='present' AND …`). Inconsistency persists until next snap reconciles `tag_presence` (5 min default). `tag_reads` audit trail stays trustworthy throughout.
- **Mitigation today.** Single pool — non-issue. Both writes ride one `AsyncSession.commit()`.
- **Long-term.** If multi-tier comes: either (a) use the outbox pattern (write a single row to `tag_reads`-pool outbox table in the same transaction, async dispatcher applies the `tag_presence` update), or (b) accept the inconsistency window because snap reconciliation bounds it anyway. (b) is simpler and matches the spec's general "self-heal beats consensus" philosophy.

#### 4. Clock-skew rejection vs. mobile readers

- **Scenario.** §6 rejects messages where `ts` drifts > 5 min from server clock. Truck-mounted / battery / intermittent-GNSS readers can naturally drift minutes between fixes. A reader that comes online after a clock jump uploads a backlog of events all stamped with stale `ts` → server rejects every one of them → entire backlog dropped + DLQ.
- **Symptoms.** `tagpulse_mqtt_wm_rejections_total{reason="clock_skew"}` spikes for one `sn`. The reader appears to be "silent" from dashboard perspective even though it's actively publishing. DLQ fills with that reader's payload. Operator sees ingest rate drop with no obvious cause.
- **Mitigation today.** Fixed 5-min threshold. Acceptable for fixed-installation readers (dock doors, gates). **Hostile to mobile readers** — flag clearly in deployment docs that this default presumes infrastructure-grade clocks.
- **Long-term.** Per-reader threshold in `devices.configuration` JSONB (`{"mqtt": {"clock_skew_seconds": 900}}`). Default 5 min. Ratchet down to 60 s for fixed installations once we have field data; raise to 15+ min for mobile fleets. Lookup happens once per device on subscriber-side device cache load — no hot-path penalty.

#### 5. `signaling.tag_disappeared` event-bus volume

- **Scenario.** A dock-door reader watching constant pallet movement sees dozens of EPCs appear and disappear per second. Each transition fans out a `signaling.tag_appeared` / `tag_disappeared` event. At 50 churn events/s sustained, that's 4.3M events/day from one reader. Per ADR 010 (internal event bus), the bus is in-process async — back-pressure on slow consumers stalls publish.
- **Symptoms.** Subscriber latency rises (event publish blocks message handling). `tagpulse_event_bus_lag_seconds` grows on the `signaling.*` consumers. Downstream rule processors (Sprint 47+ on-disappearance rules) fall further behind real-time. In the limit: subscriber message buffer fills, broker backs off, ingest stalls system-wide for one noisy reader.
- **Mitigation today.** No rate limiting. Acceptable only at pilot scale (≤ 10 active readers, modest churn). **Not safe for production-scale rollout of high-churn readers without #7's long-term fix.**
- **Long-term.** Three layered options:
  1. **Coalesce in reconciler.** If an EPC transitions `present → gone → present` within N seconds (configurable, default 30 s), suppress both events. Implementation lives in the reconciler, before the event bus sees them.
  2. **Per-reader rate limit on `signaling.*` emission.** Token bucket per `sn`. When exceeded, drop with counter (`tagpulse_signaling_dropped_total{sn, reason="rate_limit"}`).
  3. **Move event bus to durable queue** (Service Bus / Event Hubs). Decouples producer from consumer entirely. Largest scope change; defer until volume justifies it.
  - Flagged for ADR 026. **Mandatory** decision before high-churn readers go to production.

#### 6. EPC simultaneously `present` at two readers

- **Scenario.** EPC X is at reader A. Reader A loses power before its next snap. EPC X is physically moved into reader B's range. Reader B emits `t=1` for EPC X → `tag_presence` now has *two* `present` rows for EPC X, one per reader. Reader A is offline so its `tag_presence` never gets reconciled — could stay stale for hours / days / forever (until reader A returns or is decommissioned).
- **Symptoms.** "Where is EPC X?" query returns two readers. Location-based rules (e.g., "alert if pallet leaves zone") behave nondeterministically depending on which reader's row the rule reads. Dashboard heatmaps double-count.
- **Mitigation today.** None at the wire-format / presence-model layer — this is fundamentally a *distributed observation* problem, not a wire-format problem. Operators handle it manually: when a reader is decommissioned, run a script to bulk-`gone` all its `present` rows.
- **Long-term.** Sprint 41's **OverlappingZones processor** is the eventual answer. It treats `tag_presence` as observations from multiple sensors and computes a per-EPC authoritative location based on (a) recency, (b) RSSI, (c) zone topology configuration. `tag_presence` becomes the raw observation layer; a derived `tag_location` view is the user-facing truth. Out of scope for this spec; explicit dependency for ADR 026 to call out.

### 9.3 Concerns surfaced by the spec but deferred

1. ⚙️ **Server → reader config push** (snapshot cadence, RSSI floor, antenna mask, etc.). New topic `devices/{tenant}/{device}/config`. v2.1 of this spec.
2. ⚙️ **Heartbeat (`t=3`) and reader-error (`t=4`) message types.** v2.1.
3. ⚙️ **Binary wire format (v3).** Gated on measured bandwidth justifying the cost.
4. ⚙️ **Multi-reader presence consolidation** ("where is this EPC across the whole fleet right now"). Distinct from `tag_presence` (per-reader); needs a second view or a cross-reader rollup. Backlog.
5. ⚙️ **EPC base64 encoding** (~30% smaller than hex on the wire). Considered, dropped from v2.0 for human-readability; revisit if bandwidth becomes a problem.

---

## 10. Implementation plan (Sprint 46, proposed)

- **Phase A — Spec finalization.** Resolve §8 open questions with WM. Land ADR 025 (wire format) + ADR 026 (server-side presence model). Promote this document out of DRAFT.
- **Phase B — Schema.** Alembic migration `042_tag_presence.py`. Pydantic models for v2 messages in new `src/tagpulse/ingestion/wm_wire_format.py`.
- **Phase C — Subscriber.** v2 branch in `_handle_tag_read`. New module `src/tagpulse/ingestion/presence_reconciler.py` for synchronous reconciliation on snap receipt (no window state). Two new event-bus topics.
- **Phase D — Tests.** Conformance + integration coverage for all 7 scenarios in §5; explicit lost-`sub` recovery test; large-snap (~1000 entries) test; reboot test.
- **Phase E — Observability.** New OTel counters per §6. Dashboard tile for presence-state size, snap cadence, snap entry-count distribution.
- **Phase F — Docs.** Update [docs/guides/device-developer-guide.md](../guides/device-developer-guide.md) with v2 alongside v1. CHANGELOG entry. Operator runbook addendum for the new "what's at this reader now" presence-table query.

---

## 11. Review checklist (pre-promotion out of DRAFT)

**§8 protocol decisions** (all RESOLVED 2026-05-23 under Shape C producer architecture, §1.5; KISS pass dropped Q2/Q10/Q12, see §8.3):

- [x] §8 Q1 `sn` type — integer
- [x] §8 Q3 snap cadence — 300 s / 100 cycles default
- [x] §8 Q4 snap on reconnect — required of all producers (Pi-gateway reference impl handles it)
- [x] §8 Q5 empty-field snap — required of all producers (`"epcs":[]`)
- [x] §8 Q6 `tmp` / `hum` aggregation — total/cnt per cycle (WM, 2026-05-23)
- [x] §8 Q7 clock source — producer must hold ±5 min; Pi-gateway via NTP
- [x] §8 Q8 rate limit — producer-side throttle, ≤100/s sustained
- [x] §8 Q9 multi-antenna — one entry per (EPC, antenna) pair (WM, 2026-05-23)
- [x] §8 Q11 sensor-error encoding — wire is lossy by design; diagnostics stay producer-local
- [x] §8.3 KISS-pass removals (Q2 / Q10 / Q12) documented

**§9.2 internal concerns:**

- [ ] §9.2 #1 single-subscriber-replica trade-off accepted
- [ ] §9.2 #2 `tag_presence` growth policy accepted as backlog
- [ ] §9.2 #3 two-table-write race accepted; mitigation path documented
- [ ] §9.2 #5 event-bus volume mitigation path agreed before high-churn rollout

**Companion / follow-up:**

- [ ] `docs/design/reader-to-edge-contract.md` drafted (covers §8.4 Q-LAN-1..Q-LAN-7, WM-facing)
- [x] ADR 025 (wire format) + ADR 026 (server-side presence model) drafted and reviewed
- [ ] Roadmap entry for Sprint 46 added to [docs/roadmap.md](../roadmap.md)
