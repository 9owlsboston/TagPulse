# TagPulse Reader-to-Edge LAN-Side Contract — Specification

> **Status: Proposed (Sprint 47, May 2026).** Companion spec to
> [edge-wire-format-v2.md](edge-wire-format-v2.md) §8.4. Ratified by
> [ADR-027](../adr/027-reader-to-edge-contract.md).

| | |
|---|---|
| **Status** | Proposed v0.1 |
| **Date** | 2026-05-23 |
| **Authors** | TagPulse backend (Boston Owls) |
| **External collaborator** | WM (RFID reader firmware, experimental) |
| **Scope** | Reader hardware → Pi-gateway (`clients/pi/tagpulse_edge/`) LAN-side data exchange. **Not** the cellular-side MQTT contract — that is v2 (see [edge-wire-format-v2.md](edge-wire-format-v2.md)). |
| **Implementation sprint (proposed)** | Sprint 47 — Pi-gateway producer reference implementation + this spec. Vendor (WM) firmware conformance is independent of this spec's ship date. |

---

## 1. Goals and non-goals

### Goals

1. **Define the minimal interface a TagPulse-compatible reader MUST expose** so a co-located `tagpulse_edge` Pi-gateway can translate the reader's output into v2 MQTT (per [edge-wire-format-v2.md](edge-wire-format-v2.md) §1.5 Shape 2).
2. **Resolve [edge-wire-format-v2.md §8.4](edge-wire-format-v2.md#84-open--wm-reader-to-edge-lan-side-contract) Q-LAN-1 .. Q-LAN-7** so the Pi-gateway implementer has unambiguous inputs.
3. **Preserve the per-(EPC, antenna) granularity** the cellular-side wire requires (v2 §2.2, §8 Q9).
4. **Stay vendor-agnostic.** The Pi-gateway implementation MUST work with any reader satisfying this contract; this is not a WM-specific binding.

### Non-goals

1. Cellular-side wire format — that is v2 (out of scope here).
2. Reader-firmware internals — register protocols, antenna mux strategies, RF tuning.
3. Sensor-tag commissioning — out of scope; this contract carries already-commissioned reads.
4. Server → reader configuration push — out of scope (cellular v2.1 concern).

---

## 2. Transport (Q-LAN-1)

A conformant reader MUST expose **exactly one** of the following transports. The Pi-gateway selects per-reader via configuration (`clients/pi/tagpulse_edge/config.py` keys, Sprint 47):

| Transport | Pi-gateway selector | Notes |
|---|---|---|
| **TCP line-stream** (preferred) | `reader_transport: tcp`, `reader_host: …`, `reader_port: …` | One UTF-8 line per record, `\n` delimited. Reader is the server, Pi connects. Reconnect with full-jitter backoff. **Default.** |
| **File watch** | `reader_transport: file`, `reader_file_path: …` | Reader appends records to a file; Pi tails. Useful for reader SKUs that only know how to write to local storage. Pi tolerates file rotation. |
| **Serial / USB-CDC** | `reader_transport: serial`, `reader_device: …`, `reader_baud: …` | For directly-cabled SKUs. Same line-stream framing as TCP. |

UDP and broadcast transports are explicitly excluded — they cannot guarantee ordering or delivery, and the cycle-diff state on the Pi requires both.

The Pi-gateway MUST treat the reader connection as unreliable: any disconnect, file-rotation gap, or serial-port hang is recoverable. On reconnection the gateway discards its current-cycle buffer (which may be partial) and starts fresh at the next cycle boundary (see §3.4).

---

## 3. Record schema (Q-LAN-2)

### 3.1 Record format

CSV, one record per line. UTF-8, no BOM. Field separator: comma. Decimal separator: period. No quoting (fields must not contain commas or newlines).

```
record_kind,cycle_id,reader_ts_ms,antenna,epc,rssi,read_count[,tmp,hum]
```

| Column | Type | Required | Notes |
|---|---|---|---|
| `record_kind` | string | always | Enum, see §3.3. Drives parser dispatch. |
| `cycle_id` | uint64 | on `read` and `cycle_end` records | Reader-local monotonic counter; never persisted by Pi. Used **only** to group reads into cycles within one LAN session. Wraps at `2^64-1`. Reset across reader reboots is OK — Pi handles per §3.4. |
| `reader_ts_ms` | uint64 | always | Reader's monotonic-since-boot millisecond counter (NOT wall-clock; see §3.2). |
| `antenna` | uint8 | on `read` records | 0..255. 0 = unknown/muxed (matches v2 §2.2). |
| `epc` | hex string | on `read` records | Uppercase hex, 8..124 chars, even length. Pi rejects malformed per v2 §6 mappings. |
| `rssi` | int16 | on `read` records | dBm, -127..0. |
| `read_count` | uint16 | on `read` records | Reads of this EPC at this antenna during this cycle. 1..65535. |
| `tmp` | float | optional | °C, -40..85. **Omit the column entirely** (`,,` collapsed to `,`) if not available — do NOT emit a sentinel. See §3.5 (Q-LAN-3). |
| `hum` | float | optional | %RH, 0..100. Same rule. |

### 3.2 Timestamps (Q-LAN-2 continued)

`reader_ts_ms` is the reader's monotonic boot counter, not a wall-clock. The **Pi-gateway owns wall-clock stamping**: at cycle-end the Pi records its own `datetime.now(UTC)` and emits that as the v2 `ts` field on every wire message for the cycle. This places the clock-discipline burden (v2 §8 Q7) on the Pi (NTP-synced) instead of the reader, which is the only practical place to put it for low-end reader SKUs that have no RTC.

If the reader reboots mid-stream, `reader_ts_ms` jumps backwards. The Pi detects this (next `read` record's `reader_ts_ms` < last seen) and treats it as a session boundary (§3.4) — flushes current cycle, resets diff state.

### 3.3 `record_kind` enum

| Kind | Meaning | Required follow-up |
|---|---|---|
| `read` | One (EPC, antenna) observation aggregated over the cycle | Cycle MUST be closed by a `cycle_end` with matching `cycle_id`. |
| `cycle_end` | Reader has finished one inventory cycle | All `read` records with this `cycle_id` are now complete. Pi runs diff (§3.4) and emits v2 messages. |
| `cycle_empty` | Reader inventoried but saw zero EPCs in this cycle | Equivalent to a `cycle_end` with zero preceding `read` records. **MUST** be emitted; otherwise the Pi cannot distinguish "empty field" from "reader hung" (v2 §3.4). |
| `reset` | Reader is about to power-cycle or reload firmware | Pi flushes current cycle, treats next record as a new session. Optional — Pi tolerates absence (detected via `reader_ts_ms` jump). |
| `error` | Reader-internal error (cabling, antenna fault, RF noise) | Logged to Pi metrics; Pi MAY emit a v2 heartbeat/error message in v2.1. Pi does NOT propagate to current cycle. Reserved for future use. |

Forward compatibility: Pi MUST log + ignore unknown `record_kind` values. Reader vendors MAY add kinds without breaking existing Pi versions.

### 3.4 Cycle boundary semantics

A cycle is closed by a `cycle_end` (or `cycle_empty`) record with the same `cycle_id` as the preceding `read` records. **Pi-side state:**

- Buffer `read` records keyed by `(cycle_id, antenna, epc)`. Duplicate keys within a cycle are an error — Pi logs and keeps the last one.
- On `cycle_end` / `cycle_empty`: snapshot the buffer, diff against the previous cycle's snapshot, hand the diff to the v2 producer (`clients/pi/tagpulse_edge/wm_v2_producer.py`, Sprint 47).
- On any disconnect / `reset` / `reader_ts_ms` regression: **discard** the current incomplete buffer. The reader will re-inventory; the v2 producer's snap-on-reconnect rule (v2 §3.3 trigger 3) re-syncs the cellular side. No partial cycle is ever emitted.

### 3.5 Sensor failure encoding (Q-LAN-3)

When a sensor read fails for a sensor-equipped tag, the reader MUST **omit the `tmp` / `hum` column entirely** rather than emitting a sentinel value (`-999`, `nan`, blank with two commas, etc.). The Pi propagates this directly to the v2 wire (v2 §2.2: omit, do not `null`).

This means a reader that supports sensor tags MUST always emit the trailing two columns when a read succeeds, and MUST always omit them when a read fails. A reader that does not support sensor tags simply never emits the columns.

Negative test: if a reader emits `,,` (empty trailing columns), the Pi rejects the record with a `lan_sensor_empty_string` metric and drops it. If a reader emits `,nan,` or `,-999,`, same treatment with `lan_sensor_sentinel` metric. These are reader-firmware bugs; the Pi refuses to guess intent.

### 3.6 Empty-cycle signalling (Q-LAN-4)

Already covered by `cycle_empty` in §3.3. **The reader MUST emit `cycle_empty` for cycles that saw zero EPCs.** Without this, the Pi cannot fulfill v2 §3.4 (empty snapshot). A reader that cannot emit `cycle_empty` MUST instead emit a `cycle_end` followed by a zero-`read` reader behavior — but the absence of any record is **not** a valid signal.

---

## 4. Per-SKU capability inventory (Q-LAN-5)

Each reader SKU MUST publish a static capability descriptor (JSON, separate from the LAN-side stream) consumed by the Pi at startup:

```json
{
  "sku": "wm-reader-v1-experimental",
  "antenna_count": 4,
  "rssi_range_dbm": [-90, -20],
  "supports_sensor_tmp": true,
  "supports_sensor_hum": true,
  "supports_cycle_empty": true,
  "supports_reset_record": false,
  "max_epcs_per_cycle": 5000
}
```

The Pi uses this to:

- Validate incoming records (reject `antenna > antenna_count - 1` with a `lan_capability_violation` metric).
- Decide whether to set the v2 §6 snap soft-cap warning at `max_epcs_per_cycle` vs the spec default (5000).
- Pre-flight-check that the reader can satisfy the v2 contract (e.g., `supports_cycle_empty: false` is a Pi startup error — that reader cannot be used with v2).

Capability descriptor location: by convention `/etc/tagpulse/reader-capability.json` on the Pi, or specified via `EdgeConfig.reader_capability_path`. Vendor ships it alongside firmware.

---

## 5. Reset and reboot signalling (Q-LAN-6)

Two paths, in order of preference:

1. **Explicit `reset` record** (preferred). Reader emits one `reset` record immediately before power-cycling / firmware reload. Pi flushes current cycle, marks next records as new session.
2. **Implicit via `reader_ts_ms` regression** (fallback). If Pi sees a `read` or `cycle_end` record whose `reader_ts_ms` is less than the last seen value (with a small grace window of 1 s to absorb minor clock drift on cheaper readers), Pi treats it as a session boundary.

Either path triggers identical Pi behaviour: current cycle discarded, diff state cleared, v2 producer's `begin_session()` called so the next emitted v2 message is a snap (v2 §3.3 trigger 3).

---

## 6. Header / field-name corrections (Q-LAN-7)

The reference WM sample CSV at `https://github.com/weimin-peng/hello-world/blob/main/data.csv` emits a header with `issi` (typo for `rssi`). **Conformant readers MUST use `rssi`.** The Pi-gateway's CSV parser keys on the `§3.1` column ordering, not on header names — but header rows, when present, are validated; a header containing `issi` is rejected at startup with `lan_header_typo` and the Pi refuses to start. Vendors MUST fix this before claiming conformance.

The Pi-gateway accepts both "header-less" streams (most common — reader just emits data records) and "header-once-per-session" streams. Repeated headers mid-stream are rejected.

---

## 7. Conformance

A reader is **TagPulse-LAN-conformant** when:

1. It emits records satisfying §3.1 (CSV schema, every required column present, types in range).
2. It uses one of the §2 transports.
3. It emits `cycle_end` or `cycle_empty` for every cycle (no orphan `read` records, no missing cycle boundaries).
4. It publishes a capability descriptor per §4.
5. It signals reset per §5 (explicit `reset` or `reader_ts_ms` monotonicity).
6. It uses the correct field names per §6.

A separate vendor test harness is **out of scope for Sprint 47.** The Pi-gateway's own integration tests (Sprint 47) exercise this contract against a fake reader implemented in `tests/conformance/test_lan_reader_fake.py` — vendors are expected to run their firmware against the same harness via a TCP/file shim.

---

## 8. Out of scope (deferred to v0.2+ of this spec)

- Reader-side configuration ack (RSSI floor, antenna mask, dwell time). Currently Pi-side only via `EdgeConfig`.
- Per-tag detail beyond `(epc, antenna, rssi, cnt, tmp, hum)`. TID, user memory, EPC bank — possible v0.2 additions when sensor-tag commissioning hits production.
- Authentication of the LAN side itself. Today the Pi-reader cable is trusted (physical / LAN-isolated network). A future v0.2 may add a shared secret for TCP-transport SKUs deployed over shared infrastructure.

---

## 9. Relationship to existing docs

- **[edge-wire-format-v2.md](edge-wire-format-v2.md)** — the cellular-side wire format this spec feeds. Sprint 46 (shipped).
- **[edge-device-contract.md](edge-device-contract.md)** — the v1 cellular contract from Sprint 16. The Pi-gateway's v1 path (still default for non-WM SKUs) targets that contract; this spec is for v2-mode operation only.
- **[ADR-025](../adr/025-edge-wire-format-v2.md)** — wire format v2 ratification.
- **[ADR-027](../adr/027-reader-to-edge-contract.md)** — this spec's ratification ADR.

---

## 10. Review checklist (pre-promotion out of Proposed)

- [ ] Q-LAN-1 transport — resolved in §2 (TCP / file / serial)
- [ ] Q-LAN-2 CSV schema + timestamps — resolved in §3.1, §3.2
- [ ] Q-LAN-3 sensor failure encoding — resolved in §3.5
- [ ] Q-LAN-4 empty-cycle signalling — resolved in §3.6
- [ ] Q-LAN-5 capability descriptor — resolved in §4
- [ ] Q-LAN-6 reset / reboot signalling — resolved in §5
- [ ] Q-LAN-7 header typo — resolved in §6
- [ ] At least one reader vendor (WM) reviews and signs off
- [ ] Pi-gateway reference impl lands (Sprint 47 Phase B) and validates the schema end-to-end against a fake-reader harness
