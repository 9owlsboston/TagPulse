# TagPulse Edge Wire Format v2 — Specification

> **Status: DRAFT v0.2 — pre-review.** This document is a working draft for review with WM (RFID reader firmware partner) before any code, schema, or ADR commits. **Nothing in this document is binding on either side until both parties sign off.** Open questions in §8 must be resolved before the spec is promoted out of draft.

| | |
|---|---|
| **Status** | Draft v0.2 |
| **Date** | 2026-05-23 |
| **Authors** | TagPulse backend (Boston Owls) |
| **External collaborator** | WM (RFID reader firmware) |
| **Supersedes (additively)** | TagPulse Edge Wire Format v1 (canonical `TagReadCreate`, Sprint 14; see [docs/guides/device-developer-guide.md](../guides/device-developer-guide.md)) |
| **Scope** | RFID reader → MQTT broker (`devices/{tenant_id}/{device_id}/tag-reads`) → TagPulse `MQTTSubscriber`. JSON over MQTT. Binary protocol explicitly out of scope for v2. |
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

---

## 2. Wire format

### 2.1 Envelope

One JSON object per MQTT publish. Flat — no nesting except `null`-allowed value fields. UTF-8, no BOM, whitespace SHOULD be omitted.

### 2.2 Fields

**Presence conventions** (apply to the "Required on" column below):

- **Required** — the JSON key MUST appear in every message of the listed type(s). Receivers reject if missing.
- **Optional** — the JSON key is **omitted entirely** when the value is absent. Senders MUST NOT emit `"key":null` for optional fields; receivers reject explicit `null` on optional sensor fields (`tmp`, `hum`) with DLQ `reason="explicit_null"`. See §6.
- **Nullable** — applies only to `lat` / `lon`. The key MUST appear, and `null` is the valid "no GNSS fix" value. (We keep these required-but-nullable rather than optional so a missing-key message is unambiguously malformed, not "no fix.")
- **Conditional** — see the row's own notes (`empty` is the only example).

Examples:

```jsonc
// Reader with temp sensor, this cycle had a successful reading:
{"t":1,"sn":123,"seq":12346,"ts":1716489732001,"lat":41.40338,"lon":2.17403,"an":1,
 "epc":"E2801160AAAA","rssi":-48,"cnt":2,"tmp":23.45,"hum":41.2}

// Reader with NO temp sensor (or sensor failed this cycle) — tmp/hum keys absent:
{"t":1,"sn":123,"seq":12346,"ts":1716489732001,"lat":41.40338,"lon":2.17403,"an":1,
 "epc":"E2801160AAAA","rssi":-48,"cnt":2}

// Reader with no GNSS fix — lat/lon present and explicitly null:
{"t":1,"sn":123,"seq":12346,"ts":1716489732001,"lat":null,"lon":null,"an":1,
 "epc":"E2801160AAAA","rssi":-48,"cnt":2}

// MALFORMED — explicit null on an optional field (rejected, DLQ reason=explicit_null):
{"t":1,"sn":123,"seq":12346,"ts":1716489732001,"lat":41.40338,"lon":2.17403,"an":1,
 "epc":"E2801160AAAA","rssi":-48,"cnt":2,"tmp":null}
```

| Field | JSON type | Wire encoding | Range / format | Required on | Notes |
|---|---|---|---|---|---|
| `t` | integer | uint8 | `0` = snap, `1` = add, `2` = sub | **all** | Message type discriminator. Integer enum, not string. Reserved: `3` = heartbeat (v2.1), `4` = error (v2.1). |
| `sn` | integer **or** string | uint32 OR ASCII string ≤ 32 chars | depends on reader serial format | **all** | Reader identifier. Integer if reader serials are numeric; string if hardware-stamped. Locked per deployment in §8 Q1. |
| `seq` | integer | uint32 | 0 .. 4 294 967 295 | **all** | Per-reader monotonic cycle counter. Bumps **once per cycle**, shared across all messages in that cycle. Wrap is treated as reboot. |
| `ts` | integer | uint64 | Unix epoch milliseconds, UTC | **all** | Cycle timestamp. All messages with same `seq` share one `ts`. Server-side reject if drift > 5 minutes (configurable). |
| `lat` | number \| null | float64, 5 dp | -90.0 .. +90.0 | snap, add | Reader latitude (WGS84). `null` if no GNSS fix. MAY be omitted on `sub`. |
| `lon` | number \| null | float64, 5 dp | -180.0 .. +180.0 | snap, add | Reader longitude. Same rules as `lat`. |
| `an` | integer | uint8 | 0 .. 255 (0 = unknown/muxed) | snap, add | Antenna port number. MAY be omitted on `sub`. |
| `epc` | string | uppercase hex | 8 .. 124 hex chars (32–496 bits) | snap, add, sub | Electronic Product Code. No `0x` prefix, no whitespace. Server validates length is even. |
| `rssi` | integer | int16 | -127 .. 0 (dBm) | snap, add | Mean signal strength across `cnt` reads. Integer dBm — fractional precision dropped for bandwidth. |
| `cnt` | integer | uint16 | 1 .. 65535 | snap, add | Raw reads aggregated into this message during the cycle. |
| `tmp` | number | float32, 2 dp | -40.00 .. +85.00 (°C) | optional | Mean tag-die temperature for this cycle: `Σ(per-read temp) / cnt`. Same averaging window as `rssi`. **Omit field entirely if the reader has no temp sensor or no temp reading was available this cycle** (do not send `null` — saves 6 B per message). |
| `hum` | number | float32, 1 dp | 0.0 .. 100.0 (%RH) | optional | Mean tag humidity for this cycle: `Σ(per-read humidity) / cnt`. Same rule as `tmp`. |
| `empty` | boolean | `true` | only valid value | conditional | **Presence-set signal only.** Valid solely on `t=0` (snap) when the reader's RF field has zero EPCs. Single-message snap with `empty:true` and no `epc`/`rssi`/`cnt`. Do NOT use to indicate missing `tmp`/`hum` — omit those fields instead (see above). See §3.4. |

**Reserved field names (forward compatibility):** `v` (envelope version), `hb` (heartbeat-specific), `err` (error-specific), `cfg` (config echo). Senders MUST NOT use these in v2.0.

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

A *cycle* is one RFID inventory round on the reader, identified by `(sn, seq)`. The reader runs cycles continuously (typical: every 1–10 s). Each cycle decides what to publish based on **diff against the previous cycle**:

| Tag state this cycle | Tag state last cycle | Message emitted |
|---|---|---|
| Present | Present | **nothing** (steady state) |
| Present | Absent | one `t=1` (add) |
| Absent | Present | one `t=2` (sub) |
| Present | (no prior — boot) | full `snap` (see §3.3) |

A "no-change" cycle emits **zero messages**. The reader does NOT send keep-alive on quiet cycles — keep-alive is the periodic snapshot (§3.3).

### 3.2 Atomicity within a cycle

All messages with the same `(sn, seq)` are one atomic set. The reader SHOULD publish them back-to-back, but the server does not require them to be contiguous in MQTT delivery order (other readers' messages may interleave). The server groups by `(sn, seq)` regardless of arrival order.

### 3.3 Snapshot triggers

The reader MUST emit a snapshot under any of these conditions, whichever occurs first:

1. **Time-based:** every 300 s (5 min) of wall-clock time since the last snapshot.
2. **Cycle-based:** every 100 cycles since the last snapshot.
3. **Event-based:** as the **first messages of any new MQTT session** — after boot, reconnect, or any session-resume — before any `t=1` / `t=2` deltas.

Snapshot cadence (300 s / 100 cycles) is the default; SHOULD be server-configurable in v2.1 via a separate config topic.

### 3.4 Empty snapshot

If the reader has zero EPCs in field at snapshot time:

```json
{"t":0,"sn":123,"seq":48000,"ts":1716489731123,"lat":41.40338,"lon":2.17403,"an":1,"empty":true}
```

One message, `empty:true`, no `epc` / `rssi` / `cnt`. This is the only way the server can distinguish "reader sees nothing" from "reader is dead." Without it, an empty-field snapshot would emit zero messages — indistinguishable from a no-op cycle — and the server would never mark previously-present EPCs as gone.

**Scope of `empty:true`:** strictly a *presence-set* signal — "my RF field has zero EPCs right now." It is **not** a general-purpose "this message omits optional fields" marker. In particular:

- Missing `tmp` / `hum` on a normal `add` or `snap` message → just omit the fields (§2.2). Do **not** set `empty:true`.
- A snap message that has EPCs but no environmental sensor data → carries `epc` / `rssi` / `cnt` as normal, omits `tmp` / `hum`, **no** `empty` field.
- `empty:true` together with any `epc` / `rssi` / `cnt` field → server rejects (DLQ `reason="empty_with_payload"`).

This is load-bearing: the server's reconciliation algorithm (§4.2) treats `empty:true` as "mark every currently-`present` EPC for this reader as `gone`." Setting it for any reason other than a truly empty RF field would wipe the presence set.

### 3.5 Sub for never-seen EPC

If the server receives a `t=2` (sub) for an `(sn, epc)` it has no `present` row for: log + counter, do not raise an error. This is normal during a sync window — a `sub` arrived for an EPC the server lost via a missed `add`. The next snapshot will reconcile.

### 3.6 Sequence number semantics

| Server observation | Meaning | Action |
|---|---|---|
| `seq == last_seq + 1` | Normal forward progress | Process message normally |
| `seq == last_seq` | Same cycle, additional message | Group with current cycle |
| `seq > last_seq + 1` and `t != 0` | Gap (missed cycle) | Discard message, increment `gap_total`, mark presence for `sn` as *suspect* until next snap completes |
| `seq > last_seq + 1` and `t == 0` | Gap *and* recovery in one — fine | Process snapshot, reconcile (§4.2), clear suspect flag |
| `seq < last_seq` | Reader reboot or counter wrap | Treat as new session; mark presence for `sn` as *suspect*; wait for snapshot |
| `seq == 0` | Cold boot | Same as reboot |

The *suspect* flag means: presence rows for this reader are still queryable for dashboard purposes, but tagged "may be stale, awaiting reconciliation." Cleared on next complete snapshot.

### 3.7 `t=0` vs `t=1` — wire-shape twins, semantic opposites

`t=0` (snap) and `t=1` (add) messages are **nearly identical on the wire** — same required fields (`sn`, `seq`, `ts`, `lat`, `lon`, `an`, `epc`, `rssi`, `cnt`), same encoding, same size. The **only** wire difference is the value of the `t` discriminator. The semantic difference is large and load-bearing:

| Property | `t=0` (snap) | `t=1` (add) |
|---|---|---|
| **Meaning of message presence** | "EPC X is in my field right now" | "EPC X just appeared (wasn't here last cycle)" |
| **Meaning of message absence within the cycle** | "Any EPC not listed in this cycle's snap is **gone** — wipe it from presence" | Silent — says nothing about other EPCs |
| **Triggers reconciliation?** | **Yes** — closes a snap window, runs the diff in §4.2, may emit `tag_disappeared` for EPCs not present | **No** — point update only |
| **Cycle is atomic in?** | The **full set** of `t=0` messages for one `(sn, seq)`. Server buffers until complete (§3.6) | This single message |
| **Emitted on every cycle?** | No — only on snap triggers (§3.3) or empty field (§3.4) | Only when an EPC transitions absent → present |

The takeaway: **a missing `t=1` for an EPC is benign** (next snap reconciles within snap cadence), but **a missing message from a `t=0` set is dangerous** (server treats the absent EPC as gone and fires `tag_disappeared`). This asymmetry is why §3.3's snap-on-reconnect rule and §3.4's `empty:true` requirement are load-bearing for the self-heal property: the snap message set is the *complete-truth declaration*, and any incompleteness propagates as false "gone" events.

Common confusion: "if every cycle was a snap, we'd never need `t=1`/`t=2`." True — and that's exactly the **snap-only profile** in §3.8. The point of `t=1`/`t=2` is bandwidth, not correctness; the point of `t=0` is correctness, not bandwidth.

### 3.8 Reader profiles — what firmware needs to support

Three firmware profiles are first-class on this protocol. Conformance tests MUST NOT require profile 1 of all readers — the lower profiles are valid implementations targeting different reader classes.

**Profile A — Delta (full v2).** The default this spec is designed around.

- Emits `t=0` snap on triggers per §3.3 (boot, reconnect, periodic time/cycle)
- Emits `t=1` / `t=2` deltas between snaps
- Requires per-cycle diff state on the reader (last-cycle EPC set in memory)
- Bandwidth: minimal in steady state
- Target hardware: WM's reader, mid-range and above

**Profile B — Snap-only.** Acceptable for readers that can't maintain per-cycle diff state.

- Emits `t=0` snap **every cycle** (no `t=1`, no `t=2`)
- Each cycle is a complete declaration of current field
- Server reconciles every cycle (§4.2), not just on snap triggers — `last_seq` advances on every snap
- `empty:true` (§3.4) still required when field is empty
- Bandwidth: ~N× higher than Profile A in steady state (where N = EPC count); acceptable for short-range / low-EPC-count readers (e.g., handheld scanners, single-pallet zones)
- No firmware state beyond "what's in my field right now this cycle"
- **Server treats Profile B identically to Profile A** — there is no profile flag on the wire. A reader that only ever sends `t=0` is just a reader whose snap cadence is "every cycle." The §3.6 sequence rules still apply (`t=0` with gap is fine).
- Target hardware: low-end / handheld / battery readers

**Profile C — Legacy / v1 streaming.** Existing readers staying on the v1 wire format indefinitely.

- Emits canonical `TagReadCreate` per-read (no `t` field at all)
- v1 path in `_handle_tag_read` handles these unchanged (§4.3)
- Does NOT populate `tag_presence` — presence model is v2-only
- Target hardware: any reader from before v2, or partners who don't want to implement deltas

**Mixed-fleet operation.** All three profiles can run simultaneously against the same broker / subscriber / tenant. The recognizer (presence of integer `t` field) routes v1 vs. v2 per-message. Within v2, Profile A and Profile B are indistinguishable to the server. No tenant-level or device-level profile config is needed.

**For WM specifically:** we're asking for Profile A, but if any SKU in WM's lineup can't maintain diff state, Profile B is a fully-supported fallback — no spec changes needed, no server changes needed, just emit `t=0` every cycle.

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
suspect       BOOLEAN NOT NULL DEFAULT FALSE
last_seq      BIGINT NOT NULL
last_rssi     SMALLINT
last_antenna  SMALLINT
PRIMARY KEY (tenant_id, device_id, epc)
```

Indexes:

- `idx_tag_presence_active ON (tenant_id, device_id) WHERE status='present'` — drives the "what's at this reader right now" query.
- `idx_tag_presence_tenant_epc ON (tenant_id, epc) WHERE status='present'` — drives "where is this EPC now."

RLS enabled per repo convention (no session GUC; explicit `WHERE tenant_id = :tenant_id` in every query).

### 4.2 Snapshot reconciliation algorithm

When the server has collected all messages for a snapshot `(sn, seq)` — detected by either (a) the snap window timeout (10 s after the last snap message for this seq) or (b) the next `seq > current_seq` arrives:

```
snap_epcs := { msg.epc for msg in window where msg.t == 0 }
present_epcs := SELECT epc FROM tag_presence
                WHERE tenant_id=? AND device_id=? AND status='present'

to_mark_present := snap_epcs
to_mark_gone    := present_epcs - snap_epcs

UPSERT tag_presence ... status='present', last_seen=ts, last_seq=seq, suspect=FALSE
  for each epc in to_mark_present

UPDATE tag_presence SET status='gone', last_seen=ts
  WHERE (tenant_id, device_id, epc) in to_mark_gone

EMIT signaling.tag_appeared    for each (gone → present) transition
EMIT signaling.tag_disappeared for each (present → gone) transition
```

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

- `t == 0` → buffer into snap window for `(sn, seq)`; insert `tag_reads` row; await window close.
- `t == 1` → upsert `tag_presence` (`present`, bump `last_seen`); insert `tag_reads` row; emit `tag_appeared` if transition.
- `t == 2` → update `tag_presence` (`gone`, set `last_seen`); no `tag_reads` row; emit `tag_disappeared`.
- unknown `t` → reject, DLQ with `reason='unknown_type'`.

### 4.4 Mapping to existing models

| v2 wire | `TagReadCreate` (inserts) | `tag_presence` (upserts) |
|---|---|---|
| `sn` → lookup `devices.id` | `device_id` | `device_id` |
| `ts` | `timestamp` | `last_seen`, `first_seen` (on insert) |
| `lat`, `lon` | `location.latitude`, `location.longitude`; `location.source = "reader_gnss"` | — |
| `an` | `reader_antenna` | `last_antenna` |
| `epc` | `tag_id` AND `identity.epc_hex` | `epc` |
| `rssi` | `signal_strength` (cast to float) | `last_rssi` |
| `cnt`, `tmp`, `hum` | `sensor_data` JSONB: `{"read_count":cnt,"avg_temp_c":tmp,"avg_humidity_pct":hum}` | — |
| `seq` | (not stored on `tag_reads`) | `last_seq` |
| `t` | (not stored; determines code path) | (determines `status` column) |

### 4.5 SN → device_id resolution

Two-stage lookup, both per-tenant:

1. **Primary:** `SELECT id FROM devices WHERE tenant_id = ? AND (metadata->>'serial')::text = ?`.
2. **Fallback:** if `sn` is uuid-shaped, attempt direct match on `devices.id`.

Failure → reject, DLQ with `reason='device_not_found'`. The MQTT JWT's `device_id` claim MUST match the resolved `device_id` — mismatch → reject, DLQ with `reason='sn_jwt_mismatch'`. This is the load-bearing identity guarantee; the wire `sn` is for human convenience, the JWT is the trust root.

---

## 5. Examples

### 5.1 Steady-state cycle (nothing changed)

Reader cycle at `seq=12345`, 50 EPCs in field, all present last cycle:

**Wire:** *(zero messages)*

**Server state:** unchanged. `tag_presence` rows for these EPCs retain prior `last_seen` (a few seconds stale, acceptable). `last_seq` for this reader stays at the last *transmitted* cycle, not 12345.

### 5.2 Cycle with 5 new tags and 3 departures

Reader cycle at `seq=12346`, 50 EPCs from prior cycle, 3 gone, 5 newly present (52 in field this cycle):

**Wire (8 messages):**

```json
{"t":1,"sn":123,"seq":12346,"ts":1716489732001,"lat":41.40338,"lon":2.17403,"an":1,"epc":"E2801160AAAA","rssi":-48,"cnt":2}
{"t":1,"sn":123,"seq":12346,"ts":1716489732001,"lat":41.40338,"lon":2.17403,"an":1,"epc":"E2801160BBBB","rssi":-52,"cnt":3}
{"t":1,"sn":123,"seq":12346,"ts":1716489732001,"lat":41.40338,"lon":2.17403,"an":2,"epc":"E2801160CCCC","rssi":-44,"cnt":4}
{"t":1,"sn":123,"seq":12346,"ts":1716489732001,"lat":41.40338,"lon":2.17403,"an":2,"epc":"E2801160DDDD","rssi":-51,"cnt":2}
{"t":1,"sn":123,"seq":12346,"ts":1716489732001,"lat":41.40338,"lon":2.17403,"an":1,"epc":"E2801160EEEE","rssi":-60,"cnt":1}
{"t":2,"sn":123,"seq":12346,"ts":1716489732001,"epc":"E2801160FFFF"}
{"t":2,"sn":123,"seq":12346,"ts":1716489732001,"epc":"E2801160GGGG"}
{"t":2,"sn":123,"seq":12346,"ts":1716489732001,"epc":"E2801160HHHH"}
```

Total wire: ~720 B for an 8-message cycle (vs. ~9 KB if we re-sent all 52 every cycle).

**Server effect:**

- 5 inserts into `tag_reads`.
- 5 upserts into `tag_presence` (`present`, new rows or status transitions).
- 3 updates in `tag_presence` (`gone`).
- 5 `signaling.tag_appeared` events emitted.
- 3 `signaling.tag_disappeared` events emitted.

### 5.3 Periodic snapshot (cycle 12500, 52 EPCs in field)

Five minutes have elapsed since the last snapshot. Reader emits 52 `t=0` messages all sharing `seq=12500, ts=...`:

**Wire:** 52 messages × ~190 B = ~10 KB.

**Server effect:**

- Server opens snap window on first message.
- Buffers all 52 EPCs.
- Window closes when message 53 (next `seq`) arrives OR after 10 s timeout.
- Reconciliation: compares the 52 EPCs against current `present` rows for this reader. If they match: bump `last_seen`, no events. If a `tag_presence` row was `present` but the EPC is not in the snap: mark `gone`, emit `tag_disappeared`. (This is how dropped `sub` messages from prior cycles get healed.)
- Also: 52 `tag_reads` rows inserted (for the time-series audit trail).

### 5.4 Empty snapshot (reader field empty)

```json
{"t":0,"sn":123,"seq":12600,"ts":1716490031000,"lat":41.40338,"lon":2.17403,"an":1,"empty":true}
```

**Server effect:**

- Snap window opens with empty snap set.
- Window closes on next `seq` or timeout.
- Reconciliation: every currently-`present` row for this reader → marked `gone`, `last_seen = ts`. Fan-out of `tag_disappeared` events.

### 5.5 Reader reboot

Reader power-cycles at cycle 13000. After boot, `seq` resets to 0 (or is restored from flash). MQTT session reconnects. **First** messages after reconnect are a full snapshot:

```json
{"t":0,"sn":123,"seq":0,"ts":1716490200000,"lat":41.40338,"lon":2.17403,"an":1,"epc":"E2801160AAAA","rssi":-48,"cnt":1}
... 49 more ...
```

**Server effect:**

- `seq=0` with prior `last_seq=12999` → reboot detected → mark presence for `sn` as `suspect`.
- Snap window opens, collects EPCs.
- Reconciliation: anything `present` not in snap → `gone`. Anything in snap not `present` → insert `present`. Suspect flag cleared.

### 5.6 Subscriber outage and recovery

Subscriber pod restarts during reader cycle 13050. Mosquitto buffers messages (QoS 1, `clean_session=false`). Subscriber reconnects, broker replays buffered messages. Some messages from cycles 13050–13055 are received out of order. Cycle 13100 is a scheduled snap.

**Server effect:**

- Replayed messages process normally. Per-cycle grouping by `seq` makes order irrelevant within a cycle.
- If any `t=1` / `t=2` arrives with `seq > last_seq + 1`: discard, mark suspect.
- Snap at 13100 reconciles whatever drift occurred. Self-heal.

### 5.7 Lost `sub` (the failure snap exists to fix)

Reader cycle 13200: EPC `E2801160AAAA` leaves the field. Reader sends `t=2` for it. Message is lost (QoS 1 should prevent this but assume firmware bug or session timeout). Server never sees the sub. `tag_presence` shows EPC as still `present`.

For the next 5 minutes (or 100 cycles), the server is wrong — dashboards show the EPC at this reader when it isn't there.

Cycle 13300: snapshot. EPC is not in snap set. Reconciliation marks it `gone`, emits `tag_disappeared`. **Server self-heals.** Maximum incorrect-state window: snapshot cadence (5 min default).

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
| Missing `lat` / `lon` / `an` on `t=0` / `t=1` | Reject | Yes | `...{reason="missing_required_field"}` |
| `empty:true` with any `epc` / `rssi` / `cnt` field present | Reject | Yes | `...{reason="empty_with_payload"}` |
| `empty:true` on `t=1` or `t=2` (only valid on `t=0`) | Reject | Yes | `...{reason="empty_wrong_type"}` |
| Explicit `null` on optional sensor field (`tmp`, `hum`) | Reject | Yes | `...{reason="explicit_null"}` |
| `t=2` for never-seen EPC | Log debug + counter only; do not reject | No | `tagpulse_mqtt_wm_sub_no_presence_total` |
| `seq` gap on `t=1` / `t=2` | Discard message; mark suspect | No | `tagpulse_mqtt_wm_gap_total{sn}` |
| `seq` rollback / reset to 0 | Mark suspect; wait for snap | No | `tagpulse_mqtt_wm_reboot_total{sn}` |
| Snap window timeout (no closing message in 10 s) | Process partial snap with what was received | No | `tagpulse_mqtt_wm_snap_timeout_total{sn}` |

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
| **Recommended max payload** | 1 KB per message | Broker hard limit is 256 KB; we don't expect anywhere near it. |

---

## 8. Open questions (to confirm with WM)

1. **`sn` type — integer or string?** Drives the type column in §2.2 and the lookup logic in §4.5. Recommend: integer if reader serials are stamped numerically (e.g., `123`), string if alphanumeric (e.g., `"RDR-000123"`). Lock per-deployment.

2. **`seq` persistence across reboot — flash-backed or reset to 0?** Either works. If flash-backed, monotonic across reboots gives nicer audit trails; if reset, we always start with a snap so it's fine. Document whichever firmware does.

3. **Snapshot cadence configurability.** Are 300 s / 100 cycles defaults acceptable for v2.0? Server-side config push deferred to v2.1.

4. **Snapshot emission on reconnect — does firmware support it?** *This is the load-bearing question.* If firmware can't emit a full snap as the first messages of a new MQTT session, self-heal degrades and we'd need to fall back to "every cycle is a full snapshot" — which kills the bandwidth model.

5. **Empty-field snapshot (`empty:true`) — does firmware support it?** Easy to do but easy to forget in firmware. Without it, the server cannot distinguish "reader sees nothing" from "reader is dead."

6. ~~**`tmp` / `hum` aggregation window.**~~ **RESOLVED (WM, 2026-05-23):** `tmp` = `Σ(per-read temp) / cnt`, `hum` = `Σ(per-read humidity) / cnt`, averaged over the `cnt` reads in this cycle — consistent with `rssi`. Recorded in §2.2.

7. **Clock source on reader.** NTP-synced? GNSS-derived? If clock can drift > 5 min, the §6 `clock_skew` rejection will fire spuriously. Need to know firmware's clock reliability before locking the rejection threshold.

8. **Rate limit per reader.** What's the max `t=1` / `t=2` per second the reader can emit? Affects our ingest rate-limit config and Mosquitto's per-client message rate cap. Recommend documenting the expected peak (e.g., "≤100 messages/s sustained, ≤500/s burst").

9. **Multi-antenna semantics.** If the same EPC is read on antenna 1 *and* antenna 2 in the same cycle, does the reader emit one message (which antenna in `an`?) or two? Recommend: one message, with `an` = strongest-RSSI antenna. Document explicitly.

10. **Behavior at exactly the wrap boundary.** `seq` wraps `2^32 - 1 → 0`. Server treats this as reboot (§3.6). At 1 cycle/s that's a ~136-year horizon — practical only across reader replacements. Acceptable; flag in docs.

11. **Sensor-error vs. no-sensor disambiguation.** §2.2 says omit `tmp` / `hum` both when the reader has no sensor *and* when this cycle had no successful sensor read. Does firmware need to distinguish these on the wire (e.g., for diagnostic dashboards showing "sensor configured but failing")? If yes, recommend an optional `sensor_err` string field in v2.1 (`"tmp_timeout"`, `"hum_out_of_range"`, etc.) rather than overloading `empty`. If no (current default), no spec change needed.

12. **Snap terminator message support.** §9.2 #2 describes the snap-window-completeness problem: server today closes the window on next-seq OR 10 s timeout, both of which have failure modes. The cleanest fix is a sentinel message — reader emits `{"t":0,"sn":...,"seq":...,"end":true}` after the last EPC of a snap, server closes immediately on receipt. Cost: 1 extra ~50 B message per snap. Falls back gracefully to the timeout path if the terminator is lost. **Question for WM:** can firmware emit this terminator reliably? If yes, we add it to v2.1 and the 10 s timeout becomes a defensive backstop rather than the primary close trigger.

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

- **Scenario.** Snap-window buffering for `(sn, seq)` lives in `MQTTSubscriber` process memory. If two subscriber replicas are simultaneously subscribed to the broker, MQTT shared-subscription semantics route messages across both replicas round-robin. Messages for the same snap end up split across two heap buffers, neither of which sees the complete EPC set.
- **Symptoms.** Each replica's reconciliation runs against a *subset* of the real snap → both fire spurious `signaling.tag_disappeared` for EPCs the other replica buffered. `tagpulse_mqtt_wm_snap_timeout_total` spikes (windows never close cleanly because the "missing" messages went to the other pod). Dashboards flap. `tag_presence` rows oscillate `present` → `gone` → `present` on the snap cadence.
- **Mitigation today.** Pinned to one replica.
  - **K8s framing (forward-compatible).** Run the `MQTTSubscriber` Deployment with `replicas: 1` and `strategy.type: Recreate`.
  - **Current implementation (Azure Container Apps).** The worker is an ACA container app, not a K8s Deployment — the data model is identical but ACA layers in three deployment-specific caveats:
    - **Replica pinning is enforced in Bicep.** [deploy/azure/bicep/workload.bicep](../../deploy/azure/bicep/workload.bicep) sets `minReplicas: 1, maxReplicas: 1` on the worker container app (the API app next to it is `1..3` because it's stateless — *do not copy that pattern to the worker*). A code comment in the bicep should call out *why* the worker can't scale horizontally, so a future operator doesn't "fix" the cap.
    - **ACA rolling-revision deploys briefly run two replicas.** ACA's revision model is rolling, not `Recreate`. During every deploy there's a ~30–60 s window where both old and new revisions are active, both subscribed to the broker, both buffering snap windows independently — the exact failure mode this concern warns about, just transient. Protocol self-heal recovers within one snap cadence (5 min default); operators should expect a short burst of `tag_disappeared` / `tag_appeared` flapping on every deploy. Runbook addendum. Optional hardening: `az containerapp revision deactivate` the old revision before activating the new one, accepting ~15–30 s of ingest pause (Mosquitto QoS 1 + `clean_session=false` buffer through it per §7).
    - **Scale-to-zero is dangerous.** Never let the worker's `minReplicas` drift to 0 — cold-start drops all in-flight snap windows and races with broker session restore. Bicep enforces `minReplicas: 1` today; treat as load-bearing.
- **Long-term.** When per-broker volume justifies sharding:
  - **On ACA.** No stable replica IDs / ordinals (unlike a K8s StatefulSet) → "sticky shared subscription by replica ID" is *not available*. Only viable path: move snap-window state to Redis (new Azure Cache for Redis dependency — not deployed today). Reconciler becomes stateless; any replica can close any window.
  - **On AKS (if we ever migrate).** StatefulSet + sticky shared subscription by ordinal becomes available as a lighter alternative to Redis.

#### 2. Snap window timeout vs. snapshot completeness

- **Scenario.** Server doesn't know how many messages a snap will contain (no count on the wire). It closes the window on whichever fires first: (a) next-seq arrives, or (b) 10 s timeout (§3.6). The 10 s is a guess. Both failure modes are real and asymmetric.
- **Symptoms.**
  - **Timeout too short** — slow link, snap partially delivered when timeout fires. Reconciliation runs with subset → marks late-arriving EPCs as `gone` → stragglers arrive seconds later and re-mark them `present`. **Flapping every snap on slow readers.** Watch for: `tag_disappeared` immediately followed by `tag_appeared` for the same EPC within < 30 s, repeating on the snap cadence. Downstream rules fire twice, false alerts go out.
  - **Timeout too long** — snap completed in 200 ms, reader then quiet for 4 min, window held open. Reconciliation deferred → dashboards show stale `present` rows for that long. Worker heap grows unbounded if a reader misbehaves and never emits next-seq. Crash blast radius widens (more lost in-flight state per deploy — interacts with #4).
- **Asymmetry.** Flapping is recurring and user-visible; stale state is gradual and bounded. Bias is "err toward longer" for production tuning, but capped by deploy-loss surface area from #4.
- **Mitigation today.** Hardcoded 10 s. New OTel counter `tagpulse_mqtt_wm_snap_timeout_total{sn}` will surface which readers are hitting timeout vs. clean next-seq close — that's the tuning signal post-pilot.
- **Long-term.** Three options, ranked:
  1. **Snap terminator message** (preferred, see §8 Q12). Reader emits a final `{"t":0,"sn":...,"seq":...,"end":true}` after the last EPC. Server closes window on receipt. Cost: 1 extra message (~50 B) per snap. Falls back to timeout if terminator is lost. Requires firmware support — added as an open question to WM.
  2. **Per-reader adaptive timeout.** Track P99 inter-snap-message gap per reader, set timeout = `max(10s, 3 × P99)`. Adjusts automatically; cost is per-reader stats in memory.
  3. **Configurable per-reader override** in `devices.configuration` JSONB. Default 10 s, operators tune for known-slow readers. Lowest implementation cost.

#### 3. `tag_presence` table unbounded growth

- **Scenario.** Every EPC ever seen at a reader gets a row that stays forever — `gone` rows never delete. A tenant with 1M unique EPCs over a year has 1M `tag_presence` rows even if only 200 are present at any instant.
- **Symptoms.** Slow `tag_presence` queries over time (indexes still help, but page cache effectiveness degrades). Bloated logical backups. The `idx_tag_presence_tenant_epc … WHERE status='present'` partial index keeps hot-path queries fast, but `SELECT … WHERE tenant_id=? AND epc=?` (no status filter) full-table scans escalate. Migration costs rise.
- **Mitigation today.** None — `gone` rows accumulate. Acceptable for v2.0 (pilot scale, ~10K EPCs/tenant/month).
- **Long-term.** Two options:
  1. **TTL job.** Periodic `DELETE FROM tag_presence WHERE status='gone' AND last_seen < now() - interval '30 days'` (configurable). Simple. Loses long-term "was this tag ever at this reader" history.
  2. **Compaction to cold table.** Move aged `gone` rows to `tag_presence_history` summary table (one row per `(tenant, device, epc)` lifetime with `seen_count`, `first_seen`, `last_seen`). Preserves history at lower cost. More implementation work.
  - Backlog entry against ADR 026.

#### 4. Per-reader `(sn, seq)` state lost on subscriber restart

- **Scenario.** Server tracks `last_seq` per reader in process memory (not persisted). Worker restart (deploy, crash, ACA revision swap) → state is empty → first `t=1` / `t=2` from any reader after restart is `seq > 0` with `last_seq = None`, looks like a gap.
- **Symptoms.** Burst of "gap" handling on every deploy: messages discarded, `tagpulse_mqtt_wm_gap_total{sn}` spikes per reader, presence marked `suspect` for every active reader until each one's next snap arrives. Snap cadence is 5 min default → up to 5 min of stale/suspect data after restart, plus the in-flight snap window losses from #1/#2.
- **Mitigation today.** Accepted and documented. Snap mechanism self-heals within one snap cadence. The deploy-time burst is expected and operators are trained to ignore short `gap_total` spikes immediately following deploys (runbook addendum).
- **Long-term.** Persist `last_seq` per `(tenant, device)` either in Redis (with snap window in #1 long-term) or as a column on `devices.runtime_state` JSONB. Updated on every message — write amplification is the tradeoff. Probably comes bundled with the Redis migration when #1 forces it.

#### 5. Two-table writes are not cross-pool transactional

- **Scenario.** A `t=1` (add) writes to both `tag_reads` (hypertable) and `tag_presence` (new table). Today both run inside the same `AsyncSession` → one DB transaction → atomic. **But if `tag_reads` is ever migrated to a separate DB pool** (e.g., Sprint 13b multi-tier with TimescaleDB on a dedicated cluster), the two writes split across pools and lose atomicity. A crash between them leaves `tag_reads` populated and `tag_presence` stale, or vice versa.
- **Symptoms (only if we go multi-tier).** Dashboard tag count disagrees with raw audit query (`SELECT count(distinct epc) FROM tag_reads WHERE …` vs. `SELECT count(*) FROM tag_presence WHERE status='present' AND …`). Inconsistency persists until next snap reconciles `tag_presence` (5 min default). `tag_reads` audit trail stays trustworthy throughout.
- **Mitigation today.** Single pool — non-issue. Both writes ride one `AsyncSession.commit()`.
- **Long-term.** If multi-tier comes: either (a) use the outbox pattern (write a single row to `tag_reads`-pool outbox table in the same transaction, async dispatcher applies the `tag_presence` update), or (b) accept the inconsistency window because snap reconciliation bounds it anyway. (b) is simpler and matches the spec's general "self-heal beats consensus" philosophy.

#### 6. Clock-skew rejection vs. mobile readers

- **Scenario.** §6 rejects messages where `ts` drifts > 5 min from server clock. Truck-mounted / battery / intermittent-GNSS readers can naturally drift minutes between fixes. A reader that comes online after a clock jump uploads a backlog of events all stamped with stale `ts` → server rejects every one of them → entire backlog dropped + DLQ.
- **Symptoms.** `tagpulse_mqtt_wm_rejections_total{reason="clock_skew"}` spikes for one `sn`. The reader appears to be "silent" from dashboard perspective even though it's actively publishing. DLQ fills with that reader's payload. Operator sees ingest rate drop with no obvious cause.
- **Mitigation today.** Fixed 5-min threshold. Acceptable for fixed-installation readers (dock doors, gates). **Hostile to mobile readers** — flag clearly in deployment docs that this default presumes infrastructure-grade clocks.
- **Long-term.** Per-reader threshold in `devices.configuration` JSONB (`{"mqtt": {"clock_skew_seconds": 900}}`). Default 5 min. Ratchet down to 60 s for fixed installations once we have field data; raise to 15+ min for mobile fleets. Lookup happens once per device on subscriber-side device cache load — no hot-path penalty.

#### 7. `signaling.tag_disappeared` event-bus volume

- **Scenario.** A dock-door reader watching constant pallet movement sees dozens of EPCs appear and disappear per second. Each transition fans out a `signaling.tag_appeared` / `tag_disappeared` event. At 50 churn events/s sustained, that's 4.3M events/day from one reader. Per ADR 010 (internal event bus), the bus is in-process async — back-pressure on slow consumers stalls publish.
- **Symptoms.** Subscriber latency rises (event publish blocks message handling). `tagpulse_event_bus_lag_seconds` grows on the `signaling.*` consumers. Downstream rule processors (Sprint 47+ on-disappearance rules) fall further behind real-time. In the limit: subscriber message buffer fills, broker backs off, ingest stalls system-wide for one noisy reader.
- **Mitigation today.** No rate limiting. Acceptable only at pilot scale (≤ 10 active readers, modest churn). **Not safe for production-scale rollout of high-churn readers without #7's long-term fix.**
- **Long-term.** Three layered options:
  1. **Coalesce in reconciler.** If an EPC transitions `present → gone → present` within N seconds (configurable, default 30 s), suppress both events. Implementation lives in the reconciler, before the event bus sees them.
  2. **Per-reader rate limit on `signaling.*` emission.** Token bucket per `sn`. When exceeded, drop with counter (`tagpulse_signaling_dropped_total{sn, reason="rate_limit"}`).
  3. **Move event bus to durable queue** (Service Bus / Event Hubs). Decouples producer from consumer entirely. Largest scope change; defer until volume justifies it.
  - Flagged for ADR 026. **Mandatory** decision before high-churn readers go to production.

#### 8. EPC simultaneously `present` at two readers

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
- **Phase C — Subscriber.** v2 branch in `_handle_tag_read`. New module `src/tagpulse/ingestion/presence_reconciler.py` for snap-window buffering + reconciliation. Two new event-bus topics.
- **Phase D — Tests.** Conformance + integration coverage for all 7 scenarios in §5; explicit lost-`sub` recovery test; snap-window timeout test; reboot test.
- **Phase E — Observability.** New OTel counters per §6. Dashboard tile for presence-state size, snap cadence, gap rate.
- **Phase F — Docs.** Update [docs/guides/device-developer-guide.md](../guides/device-developer-guide.md) with v2 alongside v1. CHANGELOG entry. Operator runbook addendum for the new "what's at this reader now" presence-table query.

---

## 11. Review checklist (pre-promotion out of DRAFT)

- [ ] WM has answered §8 Q1 (`sn` type)
- [ ] WM has confirmed §8 Q4 (snap-on-reconnect supported in firmware)
- [ ] WM has confirmed §8 Q5 (empty-field snap supported in firmware)
- [x] WM has confirmed §8 Q6 (`tmp` / `hum` aggregation window) — 2026-05-23, total/cnt per cycle
- [ ] WM has provided clock-source answer for §8 Q7
- [ ] WM has provided expected message-rate ceiling for §8 Q8
- [ ] WM has confirmed §8 Q9 (multi-antenna emission rule)
- [ ] WM has answered §8 Q11 (need on-wire sensor-error vs. no-sensor disambiguation?)
- [ ] WM has answered §8 Q12 (snap terminator `{"end":true}` supported in firmware?)
- [ ] Internal review: §9.2 #1 single-subscriber-replica trade-off accepted
- [ ] Internal review: §9.2 #2 snap-timeout failure modes accepted; tuning plan post-pilot agreed
- [ ] Internal review: §9.2 #3 `tag_presence` growth policy accepted as backlog
- [ ] Internal review: §9.2 #7 event-bus volume mitigation path agreed before high-churn rollout
- [ ] ADR 025 + ADR 026 drafted and reviewed
- [ ] Roadmap entry for Sprint 46 added to [docs/roadmap.md](../roadmap.md)
