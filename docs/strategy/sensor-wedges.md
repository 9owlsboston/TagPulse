# Strategy: sensor wedges for the gateway — market scan

**Date:** 2026-07-26
**Status:** exploration
**Related:** [ai-landscape.md](ai-landscape.md), [home-automation.md](home-automation.md), [TagPulse-Mobile](https://github.com/9owlsboston/TagPulse-Mobile)

---

## Summary

The mobile MVE picked a **phone → OBD-II dongle** because that sensor maxes a specific
accessibility rubric. The important architectural point is that
[TagPulse-Mobile](https://github.com/9owlsboston/TagPulse-Mobile) already generalizes it:
`:obdii` is just **one `GatewayDriver`**, and `:gateway-core` (durable outbox + relay +
`Observation` model) is sensor-agnostic. **Expanding wedges = writing another driver, not
rebuilding.** This note scores the sensor market against the OBD-II rubric and ranks the next
wedges. Hardware prices and market sizes are `unverified` (directional).

## Why OBD-II won (the wedge rubric)

Score every candidate sensor on five things — OBD-II maxes all:

1. **Cheap** — ~$10–20 ELM327 BLE dongle, no CapEx.
2. **Standardized protocol** — OBD-II PIDs / ELM327 AT commands; one codec reads every car.
3. **Huge install base** — every car since ~2001; zero greenfield.
4. **Rich, valuable data** — engine, fault codes, VIN → clear monetizable signal.
5. **Fits the gateway seam** — BLE → phone → cellular; maps onto subject-scoped telemetry +
   rules + geofencing with **zero backend change**.

**Consequence:** each sensor class feeds a different AI model class → the gateway becomes a
**multi-modal edge-AI collector**.

## Tier 1 — near-drop-in (cheap BLE, clean substrate fit, named verticals)

| Wedge | Hardware | Protocol | TagPulse fit | Vertical | AI |
|---|---|---|---|---|---|
| **Cold-chain temp/humidity** | BLE loggers $10–30 | BLE GATT/adv | `telemetry_readings` + threshold rules already ship | Pharma, food, reefer | Excursion **prediction** before spoilage |
| **BLE asset beacons / RTLS** | iBeacon/Eddystone $2–5 | BLE adv | maps to assets/zones/geofencing (no UHF) | Warehouse, tools, yard | Dwell/congestion, loss anomaly |
| **TPMS / trailer sensors** | BLE $30–80/set | BLE | extends OBD fleet wedge | Fleet, trailers | Blowout risk, door tamper |

Cold-chain is the strongest single adjacency: the vertical is already named, the telemetry
model + threshold/absence rules already ship, and phone-as-gateway grabs BLE temp beacons in
transit while a fixed gateway covers a warehouse.

## Tier 2 — the phone *is* the sensor (zero extra hardware, lowest friction)

| Wedge | Sensor | Business value | AI |
|---|---|---|---|
| **Camera as universal reader** | phone camera (+ ~$200 thermal clip) | reads **analog gauges/meters** on legacy gear (large retrofit market), nameplate OCR, damage inspection | CV gauge-reading, defect detection — extends the ML Kit barcode/VIN work |
| **Acoustic condition monitoring** | phone microphone | bearing/pump faults, air/gas **leak** detection | acoustic anomaly / classification |
| **GPS + IMU telematics** | phone GPS + accel | driver-behavior / UBI **without a dongle** | harsh-event scoring |

These are the purest expression of the MVE thesis: the OBD dongle was cheap; the phone's own
camera/mic/IMU are **free** and already in the field tech's hand.

## Tier 3 — cheap MEMS / industrial (needs mounting, higher unit value)

- **Vibration sensors** (BLE, $20–100) → predictive maintenance / RUL on rotating equipment.
- **Current clamps / BLE energy meters** → submetering, ESG/energy.
- **Air quality (CO₂ / PM2.5, $30–80)** → indoor-air-quality & building-health / ESG.
- **Ultrasonic / soil-moisture / LoRa** → agriculture, facilities.

## Tier 4 — people-centric (high value, but regulated → later)

- **Lone-worker / man-down + gas badges (H₂S, CO)** → industrial worker safety.
- **Remote patient monitoring** (BLE BP, glucose, SpO₂) → high value but HIPAA / MDR regulated;
  treat as a separate compliance track, not a quick wedge.

## Recommended next wedges (in order)

1. **BLE cold-chain temp/humidity** — highest business potential × lowest build cost; reuses
   telemetry + rules verbatim; new `:cold-chain` driver only.
2. **Camera-as-gauge-reader** — no hardware, large legacy-industrial retrofit TAM, flagship
   edge-AI demo (on-device CV).
3. **Vibration → predictive maintenance** — pairs with the OBD PdM model to make "predictive
   maintenance" a cross-sensor product.

## Selection criteria for *any* future sensor

Prefer sensors that are (a) BLE or phone-native so they ride the existing gateway backhaul,
(b) standardized enough to write one codec, (c) mappable to an existing `subject_kind` + metric
so **no backend change** is needed, and (d) tied to a vertical where a model turns raw
telemetry into a decision. Avoid custom gateways, proprietary undocumented protocols, or
anything needing regulatory approval before first revenue.
