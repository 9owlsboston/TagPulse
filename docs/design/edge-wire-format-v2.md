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
| `tmp` | number | float32, 2 dp | -40.00 .. +85.00 (°C) | optional | Mean tag-die temperature. **Omit field entirely if no temp sensor** (do not send `null` — saves 6 B per message). |
| `hum` | number | float32, 1 dp | 0.0 .. 100.0 (%RH) | optional | Mean tag humidity. Same rule as `tmp`. |
| `empty` | boolean | `true` | only valid value | conditional | **Only valid on `t=0` (snap) when the field is empty.** Single-message snap with `empty:true` and no `epc`/`rssi`/`cnt`. See §3.4. |

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

6. **`tmp` / `hum` aggregation window.** Is `tmp` / `hum` averaged over `cnt` reads within this cycle, or over a longer window (e.g., last 60 s)? Recommend: average over the `cnt` reads in this cycle, consistent with `rssi`.

7. **Clock source on reader.** NTP-synced? GNSS-derived? If clock can drift > 5 min, the §6 `clock_skew` rejection will fire spuriously. Need to know firmware's clock reliability before locking the rejection threshold.

8. **Rate limit per reader.** What's the max `t=1` / `t=2` per second the reader can emit? Affects our ingest rate-limit config and Mosquitto's per-client message rate cap. Recommend documenting the expected peak (e.g., "≤100 messages/s sustained, ≤500/s burst").

9. **Multi-antenna semantics.** If the same EPC is read on antenna 1 *and* antenna 2 in the same cycle, does the reader emit one message (which antenna in `an`?) or two? Recommend: one message, with `an` = strongest-RSSI antenna. Document explicitly.

10. **Behavior at exactly the wrap boundary.** `seq` wraps `2^32 - 1 → 0`. Server treats this as reboot (§3.6). At 1 cycle/s that's a ~136-year horizon — practical only across reader replacements. Acceptable; flag in docs.

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

1. ⚠️ **Single subscriber replica assumption.** Snap-window buffering is in-process memory. If we ever run >1 `MQTTSubscriber` pod, messages for the same `(sn, seq)` could land on different pods and reconciliation breaks. Mitigations: pin to 1 replica per-broker for v2 (acceptable until customer volume justifies sharding), OR move snap window to Redis (new dependency — out of scope for this sprint).

2. ⚠️ **Snap window timeout vs. snapshot completeness.** 10 s timeout (§3.6) is a guess. Too short → partial snaps marked complete, real EPCs spuriously marked gone, next snap re-marks them present (annoying flapping in `signaling.tag_disappeared`). Too long → reconciliation lag. Need tuning data from real readers post-pilot.

3. ⚠️ **`tag_presence` table unbounded growth.** `status='gone'` rows accumulate forever. Needs a cleanup policy: TTL `gone` rows older than 30 days (configurable), or compaction into a colder summary table. Out of scope for v2.0; add to backlog.

4. ⚠️ **Per-tenant scoping of `(sn, seq)` state.** Server's per-reader sequence tracking lives in process memory. Subscriber restart loses it — first cycle after restart will be incorrectly flagged as "gap" until the next snap arrives (which is fine because the snap fixes it, but we'll see noise in `gap_total` on every deploy). Acceptable, documented.

5. ⚠️ **Two-table writes per add/snap are not transactional with each other across replicas.** Within one DB transaction, fine. But if `tag_reads` ingestion path is on a different DB pool (Sprint 13b multi-tier), they could split. We're not in that situation today; flag if we ever go multi-tier.

6. ⚠️ **Clock-skew rejection (§6) interacts badly with mobile readers.** A truck-mounted reader with intermittent GNSS may have ±minutes of clock drift naturally. 5-min threshold may be too tight. Recommend: make threshold per-reader configurable in `devices.configuration` JSONB, default 5 min, ratchet down to 60 s for fixed installations once we have data.

7. ⚠️ **`signaling.tag_disappeared` event-bus volume.** A reader entering a high-churn area (e.g., a dock door with constant pallet movement) could emit thousands of disappear events per minute. Need to confirm Sprint 41's event bus + future on-disappearance rule kinds can absorb that without rate-limiting. Out of scope for this sprint but flag to ADR 026.

8. ⚠️ **`sub` for `(sn, epc)` last-seen on a *different* reader.** If EPC X was last present at reader A, then physically moves to reader B's range without reader A ever seeing it leave (e.g., reader A powered off), reader A never emits `sub`. Reader B emits `add`. `tag_presence` will show the EPC as present at *both* readers simultaneously until reader A's next snap (or until reader A is removed from the system). Acceptable; document. The Sprint 41 OverlappingZones processor is the eventual answer to "which reader is the authoritative location for an EPC right now."

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
- [ ] WM has confirmed §8 Q6 (`tmp` / `hum` aggregation window)
- [ ] WM has provided clock-source answer for §8 Q7
- [ ] WM has provided expected message-rate ceiling for §8 Q8
- [ ] WM has confirmed §8 Q9 (multi-antenna emission rule)
- [ ] Internal review: §9.2 #1 single-subscriber-replica trade-off accepted
- [ ] Internal review: §9.2 #3 `tag_presence` growth policy accepted as backlog
- [ ] ADR 025 + ADR 026 drafted and reviewed
- [ ] Roadmap entry for Sprint 46 added to [docs/roadmap.md](../roadmap.md)
