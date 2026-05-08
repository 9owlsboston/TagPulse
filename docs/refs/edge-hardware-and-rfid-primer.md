# Reference: Edge Hardware & RFID 101

**Status:** reference (non-normative)
**Date:** 2026-05-02
**Related:** [docs/design/edge-device-contract.md](../design/edge-device-contract.md), [docs/design/rfid-tag-data-model.md](../design/rfid-tag-data-model.md), [docs/design/asset-tracking-gap-analysis.md](../design/asset-tracking-gap-analysis.md), [docs/refs/IoT.md](IoT.md)

This document is a primer for engineers building or evaluating TagPulse-compatible edge devices. It is **non-normative** — the on-the-wire requirements live in [edge-device-contract.md](../design/edge-device-contract.md). Use this doc to:

- Get a working mental model of how RFID actually works on the wire.
- Understand which hardware platforms are realistic for a TagPulse edge node.
- Plan how non-RFID peripherals (cameras, BLE, environmental sensors, GPS, scales, PLCs) integrate with the same edge-device contract.

---

## 1. RFID 101 — Just Enough to Be Dangerous

### 1.1 Frequency bands (the only choice that really matters up front)

| Band | Frequency | Read range | Typical use | Notes |
|---|---|---|---|---|
| LF | 125 / 134 kHz | ~10 cm | Animal ID, access cards | Slow, immune to water/metal interference |
| HF / NFC | 13.56 MHz | ~10 cm | Payment, library books, smartphones | ISO 14443, ISO 15693, NFC Forum |
| **UHF (Gen2)** | **860–960 MHz** | **0.3–10 m** | **Supply chain, asset tracking, retail** | **TagPulse default** |
| Active / 2.4 GHz | 2.4 GHz | 30–100 m | Real-time location systems (RTLS) | Battery-powered tags |

TagPulse's reference scope is **UHF Gen2** (EPCglobal Gen2v2 / ISO 18000-63). Everything below assumes UHF unless noted.

### 1.2 Reader anatomy

```
+--------------------+         +-----------+
| Reader (host MCU/  | <-----> | Antenna 1 |
|  SBC + RF module)  |  coax   +-----------+
|                    | <-----> | Antenna 2 |
|  - LLRP / vendor   |  coax   +-----------+
|    SDK over IP     |         | ...       |
|  - GPIO / serial   |         +-----------+
|  - Ethernet / Wi-Fi|
+--------------------+
        |
        | (LLRP, vendor SDK, in-process call)
        v
   TagPulse edge agent  --(MQTT / HTTP)-->  TagPulse backend
```

- **Antennas are dumb radiators**; the reader is the smart part. One reader typically drives 2–8 antennas multiplexed in time.
- Each tag reply carries `(EPC, antenna_port, RSSI, timestamp, [phase])`. RSSI alone is not distance — it is "did this antenna hear that tag well right now."
- Readers spit out a **lot** of duplicates (10–100 reads/sec per tag in the field). The edge contract's dedup + ENTER/EXIT rules ([edge-device-contract.md §3.3](../design/edge-device-contract.md)) exist exactly to control that.

#### Reader vs edge agent — logical, not physical

The Reader and "TagPulse edge agent" boxes above are a **logical** separation. In practice three topologies all map to the same diagram, and the on-the-wire contract is identical for all three:

| Topology | Where the reader hardware loop runs | Where the edge agent runs | Interface between them |
|---|---|---|---|
| **Co-located** *(default today)* | Same Python process | Same Python process | In-process call: `agent.submit_tag_read(...)` |
| **Sidecar on same host** | Vendor SDK / daemon process | Separate process on the same SBC/gateway | Local socket, named pipe, or shared queue |
| **Reader-integrated** | Inside the reader firmware (e.g., Impinj R700 hosted apps, Zebra IoT Connector) | On the reader itself | Vendor-specific in-process API |

The TagPulse backend cannot tell them apart — it only sees MQTT/HTTP from a `device_id`.

#### Implementation status

The edge agent **already exists** as the Python package at [`clients/pi/`](../../clients/pi/) (despite the legacy path name, the code is hardware-agnostic — see [edge-hardware-and-rfid-primer §2](#2-reference-hardware-options)). Modules in place today:

| Module | Role |
|---|---|
| `tagpulse_edge.agent.EdgeAgent` | Orchestrator with `submit_tag_read` / `submit_telemetry` / `submit_location` |
| `tagpulse_edge.dedup.PresenceTracker` | Dedup window + ENTER/EXIT state machine |
| `tagpulse_edge.buffer.Outbox` | SQLite WAL ring buffer (size + age bounded, restart-safe) |
| `tagpulse_edge.clock.ClockGuard` | UTC normalization + max-age / max-skew validation |
| `tagpulse_edge.transport.MqttTransport` | paho-mqtt wrapper with full-jitter exponential backoff and LWT |
| `tagpulse_edge.config.EdgeConfig` | All knobs in one dataclass; loadable from JSON |

Shipped since the original primer was written:

- **Sprint 14** — `submit_telemetry` / `submit_location` are wired end-to-end through `MqttTransport`, and `submit_tag_read` carries `epc` / `epc_hex` / `tid` / `tag_data` ([`tagpulse_edge.events.RawTagRead`](../../clients/pi/tagpulse_edge/events.py), [`tagpulse_edge.agent.EdgeAgent`](../../clients/pi/tagpulse_edge/agent.py)).
- **Sprint 16** — broker-side 401 / forced-disconnect surfaces as `TokenRevokedError` via the `on_token_revoked` callback ([`tagpulse_edge.transport.MqttTransport`](../../clients/pi/tagpulse_edge/transport.py)); a conformance harness scaffold lives at [`tests/conformance/`](../../tests/conformance/) covering dedup §3.3, clock §3.5, heartbeat/LWT §3.6, and offline buffer §3.7.
- **Sprint 17b** — client-side mTLS is in the transport (`use_tls` + `tls_ca_path` / `tls_cert_path` / `tls_key_path` → `paho.tls_set(..., cert_reqs=ssl.CERT_REQUIRED)`); backend stores `devices.cert_thumbprint` and accepts `POST /device-registry/{id}/cert`.

Still open (tracked elsewhere — see [docs/roadmap.md](../roadmap.md)):

- **Hot-swap on token rotation** — the transport detects revocation and notifies the embedder, but there's no `MqttTransport.update_token()` to swap credentials in place; embedders currently rebuild the transport. Filed against the post-Sprint-16 backlog.
- **Conformance coverage gaps** — telemetry/location publish paths and the token-revoke callback are not yet exercised by [`tests/conformance/`](../../tests/conformance/).
- **Sprint 17c — broker-side mTLS rollout** — Mosquitto `cafile`/`certfile`/`keyfile`, `mosquitto-go-auth` HTTP backend → `/internal/mqtt-auth`, and the `tenants.require_mtls` opt-in flag. Client-side scaffolding is ready; broker enforcement is its own sprint.

### 1.3 Tag memory banks (Gen2)

Every Gen2 tag has four memory banks (see also [rfid-tag-data-model.md](../design/rfid-tag-data-model.md) for how TagPulse maps these into columns):

| Bank | Name | Contents | Notes |
|---|---|---|---|
| 00 | Reserved | Kill / access passwords | Rarely read |
| 01 | **EPC** | Electronic Product Code | Default identifier; what most readers return |
| 10 | **TID** | Tag ID — chip serial assigned at manufacture | Immutable; ideal for anti-cloning |
| 11 | **User** | Free-form bytes (16 B – several KB) | Application data, batch codes, sensor logs |

Sensor-enabled tags (RFMicron / Axzon Magnus-S, EM Microelectronic em|sense, Asygn) return temperature / moisture / strain **in the EPC reply itself** or in user memory — no extra protocol round-trip. TagPulse handles those via the `tag_data` JSONB column and the tag-borne sensor mirror (decision **D4** in [rfid-tag-data-model.md](../design/rfid-tag-data-model.md)).

### 1.4 EPC identifier schemes (GS1)

The 96-bit EPC encodes one of several schemes:

| Scheme | What it identifies | Example |
|---|---|---|
| **SGTIN-96 / 198** | Serialized GTIN — a unique instance of a SKU | Retail item, case |
| **SSCC-96** | Serial Shipping Container Code — pallet/container | Logistics unit |
| **GIAI-96 / 202** | Global Individual Asset Identifier | Returnable asset (forklift, IT equipment) |
| **GRAI-96 / 170** | Global Returnable Asset Identifier (with serial) | Reusable transport item |

The TagPulse EPC decoder ([Sprint 14 task](../roadmap.md), `tagpulse.rfid.epc`) parses the prefix bits and exposes `epc_scheme` + structured `epc_decoded` (company prefix, item ref, serial). Asset-tracking mode binds against GIAI/GRAI; inventory mode binds against SGTIN/SSCC.

### 1.5 Reader interface protocols

- **LLRP** (Low-Level Reader Protocol, EPCglobal) — vendor-neutral, TCP/IP, the closest thing to a standard. Supported by Impinj, Zebra, Alien, ThingMagic. Recommended whenever possible.
- **Vendor SDKs** — Impinj `Octane SDK` (Java/.NET/C++), Zebra `RFID3 / EMDK`, ThingMagic `Mercury API` (C/C#/Java). More features (GPIO, autonomous mode, custom commands), less portability.
- **Serial / USB CDC** — small embedded modules (M6e Nano, Yanzeo, Chafon). Cheap and fine for kiosk/handheld use.
- **HID keyboard wedge** — handheld scanners pretending to be a keyboard. Not a real integration target; mention only because customers will sometimes propose it.

The TagPulse edge agent treats the reader as opaque: a hardware loop in your code calls `agent.submit_tag_read(...)` for each cleaned read. Whether you got there via LLRP, a vendor SDK, or serial bytes is your problem, not the contract's.

---

## 2. Reference Hardware Options

The current reference target is a Raspberry Pi-class SBC (hence the legacy `clients/pi/` path), but the contract is hardware-agnostic. Below is the menu of realistic platforms grouped by tier.

### 2.1 Linux SBCs — drop-in for the current Python stack

| Board | SoC / RAM | Power (typ. / peak) | Price (board only, USD) | Why pick it | Watch out for |
|---|---|---|---|---|---|
| **Raspberry Pi 4 / 5** | BCM2711 / BCM2712, 2–8 GB | 3–5 W / 7–12 W (Pi 5 needs the official 27 W USB-C PD PSU under load) | $35–$80 | Massive ecosystem, fTPM on Pi 5, cheapest path | Consumer-grade reliability, no real industrial temp range |
| **Radxa Rock 5B / 5C** | RK3588(S), 4–32 GB | 4–6 W / 10–15 W | $80–$200 | Faster than Pi 5, real PCIe, M.2 NVMe | Smaller community, Rockchip BSP quirks |
| **Orange Pi 5 / Zero 3** | RK3588S / H618 | 2–5 W / 8–12 W (Zero 3 ~1–3 W) | $30–$130 | Cheaper than Pi for similar specs | OS image quality varies |
| **BeagleBone Black / AI-64** | Sitara AM3358 / TDA4VM | 1–2 W (BBB) / 5–10 W (AI-64) | $55 (BBB) / $200–$230 (AI-64) | **PRU subsystem** for hard-real-time GPIO; very long industrial availability | Lower CPU than Pi |
| **NVIDIA Jetson Nano / Orin Nano** | ARM + Maxwell/Ampere GPU | 5–10 W (Nano) / 7–15 W configurable (Orin Nano) | $150 (Nano, EOL) / $250–$500 (Orin Nano dev kit) | On-device CV (camera + RFID fusion, license-plate, pose) | Overkill + power-hungry for pure RFID |
| **LattePanda 3 Delta / UP Squared / ODROID-H4** | x86 (N5105 / N100 / N97) | 6–10 W idle / 15–25 W peak | $200–$400 | Runs Windows-only vendor SDKs natively; standard PC tooling | Larger, more power than ARM SBCs |

These all run mainline Debian/Ubuntu/Yocto; the existing `tagpulse_edge` Python package installs unchanged.

> **Power budgeting notes.** Numbers above are the *board* draw, not the system. Add the reader (a UHF reader like Impinj Speedway/R700 alone draws 12–25 W via PoE+), antennas (passive, no draw), USB peripherals (GPS ~0.3 W, USB camera 1–2.5 W, LTE modem 1–3 W idle / 5 W TX), and any storage (NVMe ~3–5 W under load). For a typical Pi-class node with reader + GPS + cellular, plan for a **15–30 W** sustained budget and a 30–50 W PSU headroom. PoE-powered industrial gateways (§2.2) often integrate this; see also the [edge-device-contract.md](../design/edge-device-contract.md) clock and heartbeat rules, which assume a device that *stays powered* — battery-only operation is an MCU-tier problem (§2.3).

> **Pricing notes.** Board-only MSRP at typical distributors (DigiKey, Mouser, Arrow, Seeed) as of mid-2026; street prices fluctuate with stock. **Add ~$30–$80 per node** for PSU + microSD/eMMC + enclosure + PoE HAT, and another **$40–$200** for a small NVMe if you need it. Industrial gateways (§2.2) start around **$300** and run to **$1500+**; reader-integrated hosts (§2.4) move the SBC cost to zero but add to the reader cost.

### 2.2 Industrial / fanless gateways — production fleets

For DIN-rail mounting, wide temperature range, real TPM 2.0, and 5+ year availability:

| Vendor | Model line | Notes |
|---|---|---|
| **Advantech** | UNO-2000, ARK-1000 series | Wide selection, x86 + ARM |
| **Siemens** | IOT2050 | Industrial Pi-equivalent, TPM, M12 |
| **Dell** | Edge Gateway 3200 / 5200 | x86, Ubuntu Core, TPM 2.0 |
| **Eurotech** | ReliaGATE 10/15 | Cellular options, TPM, FIPS-validated builds |
| **Moxa** | UC-2100 / UC-8100 | -40 °C…+70 °C, ARM Cortex-A |
| **Lanner / Axiomtek / Kontron** | Various | Customizable, defense/utility targets |

**This tier is the natural target for ADR-011 Phase 2 (mTLS) and Phase 3 (hardware root of trust).**

### 2.3 Microcontrollers — small footprint, no Linux

For battery-powered, sealed, or very-cost-sensitive nodes. Requires a slimmer C/C++/Rust client that conforms to the same wire contract; no Python.

| Platform | Strengths | Notes |
|---|---|---|
| **ESP32-S3 / ESP32-C6** | Wi-Fi + BLE + (C6) Thread/Zigbee; cheapest path; Secure Boot v2; flash encryption | No Linux; reuse `tagpulse_edge` *protocol*, not the package |
| **Nordic nRF9160** | LTE-M / NB-IoT; Arm CryptoCell + secure element | Vehicle / forklift readers without Wi-Fi |
| **STM32U5 / STM32H7** | TrustZone, optional STSAFE secure element, industrial temp | Strong identity story, large MCU ecosystem |
| **NXP i.MX RT crossover** | MCU power envelope, MPU-class performance, EdgeLock SE05x SE | Good middle ground when Linux is overkill |

A C-language conformance harness ([edge-device-contract.md §3.10](../design/edge-device-contract.md)) is the unblocker for this tier; not in scope for Sprint 16, but the contract is already MCU-portable.

### 2.4 Reader-integrated platforms — no separate SBC

Some readers run their own apps directly on the reader, eliminating a whole hardware tier:

| Reader | Hosting model | Notes |
|---|---|---|
| **Impinj R700** | Embedded Linux + custom apps via Impinj IoT Interface | Closest to an "all-in-one" TagPulse node |
| **Zebra FX9600 / FX7500** | Zebra IoT Connector, on-reader apps | Production-grade; Zebra-native |
| **Chainway / Atid handheld terminals** | Android | Termux + Python possible; Android-native port preferred long-term |

Trade-off: tighter coupling to one vendor, but one less device to provision/monitor. Worth it for greenfield single-vendor deployments.

### 2.5 Power & PoE options

PoE (Power over Ethernet) is the preferred power path for fixed-mount edge nodes — one cable, no wall wart, switch-side power management. Three IEEE classes matter:

| Standard | Common name | Budget at the device | Use for |
|---|---|---|---|
| 802.3af | PoE | ~13 W | BeagleBone, very small SBCs, low-power readers |
| **802.3at** | **PoE+** | **~25 W** | **Pi 4/5, Radxa, most production RFID readers** |
| 802.3bt | PoE++ (Type 3/4) | 60 / 90 W | Industrial gateways powering downstream devices, Jetson Orin |

#### SBCs (§2.1) — PoE only via add-on

| Board | Native PoE | Add-on path |
|---|---|---|
| Raspberry Pi 4 / 5 | No | Official PoE+ HAT (Pi 4) / PoE+ HAT for Pi 5 |
| Radxa Rock 5B | No | Optional Radxa PoE HAT (verify board rev) |
| Orange Pi 5 / Zero 3 | No | Third-party HAT on some SKUs only |
| BeagleBone Black / AI-64 | No | PoE cape (802.3af; tight for AI-64) |
| NVIDIA Jetson Nano / Orin Nano | No | Carrier boards (Seeed reComputer, etc.) — PoE+ minimum, PoE++ recommended for Orin |
| LattePanda / UP Squared / ODROID-H4 | No | External **PoE → 12 V DC splitter** to the barrel jack |

#### Industrial gateways (§2.2) — PoE common as a SKU option

PoE PD (powered-device) input is a standard option on **Advantech UNO**, **Moxa UC-2100 / UC-8100**, **Eurotech ReliaGATE**, **Siemens IOT2050** (via expansion), and most **Lanner / Axiomtek / Kontron** boxes. Some SKUs additionally act as PoE PSE (source) to power a downstream camera, AP, or reader. Check the part-number suffix per vendor — PoE is rarely the default.

#### RFID readers themselves — many are PoE-powered

This is the cleanest "one cable" pattern, especially for §2.4 reader-integrated topologies:

| Reader | PoE class |
|---|---|
| Impinj Speedway R420 / R700 | PoE+ (802.3at) |
| Zebra FX9600 | PoE+ |
| Zebra FX7500 | PoE |
| Alien ALR-F800 | PoE+ |

#### Three clean PoE deployment patterns

1. **Reader-integrated** (§2.4) — PoE+ to the reader, agent runs on the reader. **One cable, one device.**
2. **PoE+ to a Pi/Radxa with HAT** — agent on the SBC, reader on its own PoE+ drop or USB-fed by the SBC. Two cables typical.
3. **PoE++ midspan to an industrial gateway** — gateway powers itself and re-injects PoE downstream to the reader/camera. Two devices, one cable from the switch.

> **Sanity check.** If the device's peak draw (board + reader + USB peripherals) is ≥13 W, plan for **PoE+** at minimum. If you're driving a downstream device or a Jetson Orin, plan for **PoE++** or a separate DC injector. The §2.1 power-budgeting note applies — PoE switches enforce per-port budgets and will cut power if you exceed them.

### 2.6 Mapping to the device-identity roadmap (ADR-011)

| Phase | Linux SBC | Industrial gateway | MCU | Reader-integrated |
|---|---|---|---|---|
| **Phase 1** — rotatable token | yes | yes | yes | yes |
| **Phase 2** — mTLS | yes | **best fit** | yes (mbedTLS) | depends on reader OS |
| **Phase 3** — HW root of trust | Pi 5 fTPM only | **best fit** (TPM 2.0) | STSAFE / CryptoCell / SE05x | depends on reader OS |

Pi-class boards have the weakest Phase-3 story — exactly why the contract is hardware-agnostic and the production target tier is industrial gateways.

---

## 3. Beyond RFID — Other Sensors and Peripherals

TagPulse's data model already separates **identity events** (tag reads → `tag_reads`) from **scalar telemetry** (sensor readings → `device_telemetry`) and **location** (lat/lon columns on `tag_reads` plus the future `…/location` topic). That same split absorbs non-RFID peripherals without schema churn.

### 3.1 Categories the platform should expect

| Category | Examples | Goes into | Notes |
|---|---|---|---|
| **Environmental** | Temperature, humidity, pressure, CO₂, light, noise | `device_telemetry` | Standalone or paired with tag reads (cold-chain) |
| **Motion / orientation** | IMU (accel + gyro + mag), tilt, vibration | `device_telemetry` (per-axis metrics) | Forklift impact, asset tamper |
| **Location** | GPS / GNSS, Wi-Fi RTT, UWB anchors, BLE beacons (RSSI fingerprinting) | `tag_reads.latitude/longitude` (mobile reader) or new `location_fixes` topic | UWB / BLE add a "presence at anchor" channel |
| **Vision** | USB / CSI camera, intelligent camera (Hailo, Coral) | New `vision_events` topic; metadata into `tag_reads.tag_data` when fused with a read | Camera frames don't go into TagPulse; only the *event* does |
| **Industrial I/O** | GPIO, dry-contact, 4–20 mA, 0–10 V via ADC; PLC over Modbus / OPC UA | `device_telemetry` (bridge metric per channel) | Door open, conveyor running, weight on scale |
| **Identification co-modalities** | Barcode (1D/2D), QR, OCR | `tag_reads` with `binding_kind='barcode'` (new value) or as auxiliary `tag_data.barcode` | When customer hardware is mixed |
| **Network / connectivity** | LoRaWAN, NB-IoT, satellite (Iridium / Swarm) | Transport choice, not a data category | Edge agent's transport layer; backend is unchanged |

### 3.2 Two integration patterns for non-RFID inputs

**Pattern A — Same edge agent, additional `submit_*` calls.**
The reader-host process already calls `agent.submit_tag_read(...)`. Add `submit_telemetry(metric_name, value, unit, metadata)` and `submit_location(lat, lon, accuracy_m, source)` for sensors physically wired to the same host. This is the Sprint 14 wiring; nothing new is required for environmental sensors, IMU, GPS, or GPIO.

**Pattern B — Sidecar / fan-in.**
A dedicated sensor node (BLE gateway, Modbus bridge, intelligent camera) publishes to its own MQTT topic under the same `device_id`. The edge agent process is just one of several producers. This works today because:

- Topic taxonomy is per-`device_id`, not per-process.
- Auth is per-`device_id` token, so a sidecar shares the device's token (or registers as its own device, which is cleaner).
- The wire contract applies to *every* publisher independently.

For a multi-modal node (RFID + camera + scale at one dock door), prefer "one logical device per physical mounting point" with multiple sub-processes publishing under the same `device_id`. The audit trail and metering stay intuitive.

### 3.3 Schema-side hooks already in place

Nothing in this section requires backend work right now — the points below are reminders that the building blocks already exist:

- `device_telemetry.metric_name` is free-form; `telemetry_models` (Sprint 2) constrains it per device type. Adding "imu_accel_x" or "scale_weight_kg" is a config change, not a schema change.
- `tag_reads.tag_data` JSONB absorbs co-modality auxiliaries (barcode, OCR text, image hash) without schema churn.
- The `…/events` topic ([edge-device-contract.md §3.2](../design/edge-device-contract.md)) is the place for "buffer drained", "GPS fix lost", "camera offline" — anything that is a state change, not a measurement.
- `telemetry_models` quarantine path (Sprint 14) means an unknown metric from a new peripheral is *visible* to the operator instead of silently dropped — they can promote it to a known metric in the UI.

### 3.4 What we explicitly avoid

- **Storing camera frames or audio in TagPulse.** TagPulse stores *events about* media (e.g., "person detected at dock 3 at T"), not the media itself. Object storage (S3/Blob) holds media; TagPulse holds the metadata + URL.
- **Hard-coding sensor types in the schema.** Every new sensor is a `metric_name` + a row in `telemetry_models`. No `temperature` column, no `weight_kg` column.
- **Per-vendor MQTT topics in the canonical taxonomy.** Vendor specifics belong inside `tag_data` / `metadata`, not in the topic tree.

---

## 4. When to Pick What — Quick Decision Guide

| Scenario | Recommended hardware | Notes |
|---|---|---|
| Lab / pilot / single warehouse | Pi 4/5 + USB or LLRP reader | Fastest to running; current `clients/pi/` reference |
| Forklift / vehicle-mounted reader | Industrial gateway with cellular (Eurotech / Moxa) | Wide temp, vibration, LTE/NB-IoT |
| Cold-chain pallet | Sensor-enabled tag (Magnus-S) + any reader | Schema covered by `tag_data` + `device_telemetry` mirror |
| Retail door / portal | Impinj R700 hosted app or Pi + R420/R700 | Reader-integrated wins on hardware count |
| Battery-powered sealed node (months) | nRF9160 + secure element | Requires C/Rust client; not Sprint 16 |
| Multi-modal dock door (RFID + camera + scale) | Industrial gateway + Pattern B sidecars | One logical `device_id` per dock door |
| Defense / regulated | Industrial gateway with discrete TPM 2.0 + mTLS | Phase-2 + Phase-3 from ADR-011 |

---

## 5. Further Reading

- **GS1 EPC Tag Data Standard** — authoritative reference for SGTIN/SSCC/GIAI/GRAI bit layouts.
- **EPCglobal Gen2v2 (ISO/IEC 18000-63)** — air-interface protocol; needed only if you implement a reader, not just consume one.
- **LLRP 1.1 spec** — reader integration protocol.
- **RAIN RFID Alliance** — industry hub; useful vendor lists.
- [docs/refs/IoT.md](IoT.md) — broader IoT architectural reference (transports, brokers, identity).
- [docs/design/rfid-tag-data-model.md](../design/rfid-tag-data-model.md) — how the above maps to TagPulse columns.
- [docs/design/edge-device-contract.md](../design/edge-device-contract.md) — the normative wire contract any device must satisfy.
