# Strategy: home-automation sensor wedge

**Date:** 2026-07-26
**Status:** exploration
**Related:** [sensor-wedges.md](sensor-wedges.md), [ADR-002 (MQTT connectivity)](../adr/002-mqtt-device-connectivity.md), [ADR-008 (multi-tenancy)](../adr/008-multi-tenancy-strategy.md), [edge-device-contract.md](../design/edge-device-contract.md)

---

## Summary

Home-automation sensors move the gateway from the **moving cellular phone** to a **stationary
WiFi/Ethernet hub** — but that is a smaller leap than it looks, and the data is far wider. Two
existing facts make TagPulse an unusually good fit: the `clients/pi` **Pi-gateway lineage**
(Sprints 46–48) already is a stationary hub, and the backend **ingests MQTT natively** — the
protocol the entire smart-home ecosystem already speaks. The verdict: **strong as a B2B
fleet-of-homes play (insurance / property-management lead), weak as a consumer play.** Market
sizes below are `unverified`.

## The gateway shifts phone → hub — but the lineage already exists

1. **`clients/pi` already exists** (Sprint 46/47 "Pi-gateway producer + reader-to-edge
   contract", lint-gated Sprint 48). The home hub **is** that lineage — a mains-powered SBC on
   home WiFi — not a new gateway. The outbound-only outbox/relay pattern is *ideal* for
   residential NAT: no port-forwarding, no inbound exposure.
2. **TagPulse ingests MQTT natively** (`ingestion/mqtt_subscriber`, [ADR-002](../adr/002-mqtt-device-connectivity.md)).
   The smart-home ecosystem (Home Assistant, zigbee2mqtt, ESPHome, Tasmota, Shelly) already
   **normalizes hundreds of device types into MQTT** — so the "wide range of sensors" is
   unified *upstream*, and the hub can be a thin MQTT-bridge-with-tenant-mapping. The
   lowest-code driver yet.

**Standardization bet = Matter/Thread.** Just as OBD-II PIDs gave one codec for every car,
**Matter clusters** give one data model across vendors. Bet the durable codec on Matter; lean
on HA/zigbee2mqtt normalization today so you never reimplement Zigbee/Z-Wave stacks.

## The "wide range" (why this wedge is data-rich)

| Class | Examples | TagPulse mapping |
|---|---|---|
| Environmental | temp, humidity, CO₂/VOC/PM2.5, radon, lux, pressure | `telemetry_readings` + threshold rules |
| Presence/occupancy | PIR motion, mmWave presence, contact, BLE presence | zones/geofencing + presence model |
| Safety | smoke, CO, water-leak, freeze, gas | absence/threshold rules → alerts |
| Energy | metering plugs, whole-home CT, water/gas pulse | telemetry + usage metering |
| Security | cameras, doorbell, glass-break, vibration | edge vision (ties to camera wedge) |
| Access/actuation | smart locks, garage, valves, thermostats, blinds | **NEW: bidirectional — read *and* command** |

## Business potential — go B2B fleets-of-homes, NOT consumer DIY

> Market sizing for each vertical below is now backed by sourced (secondary) research in
> the data appendix: **[home-automation-market.md](home-automation-market.md)**.

Consumer smart home (Home Assistant, SmartThings, Hubitat) is crowded, low-margin, and
self-hosted — those users won't buy SaaS. The money is in **B2B fleets of homes/units**, which
is exactly TagPulse's multi-tenant + assets/zones + rules + metering shape:

1. **Property/home insurance (loss prevention)** — water-leak/freeze/fire detection prevents
   claims; insurers subsidize sensors. **Direct parallel to the OBD → UBI auto-insurance
   wedge.** Highest B2B potential.
2. **Property management / multi-family / short-term rental** — noise, occupancy, energy, leak,
   HVAC across many units on one fleet dashboard. A 100-unit landlord = one tenant, 100
   subjects — native fit.
3. **Energy / demand response / VPP / ESG** — aggregated home energy, grid services.
4. **Assisted living / aging-in-place** — motion+door+bed fusion → activity-of-daily-living
   wellness anomaly. High value, some regulation.
5. **Light-commercial facilities** — same sensors, offices/retail/restaurants.

## New architectural dimension: actuation (closing the loop)

Home automation is **read *and* write** — close the valve on a leak, set back a thermostat.
TagPulse today is ingest → rules → *outbound webhook/email*. Adding a **"device command" rule
action** (rule fires → command back down through the hub to the device) turns TagPulse from a
**monitoring** platform into an **automation** platform. Bigger build (command channel,
idempotency, safety interlocks) and would warrant its own [ADR](../adr/README.md), but it is a
real product-category upgrade.

## AI angle

- **Occupancy/energy behavioral models** — occupancy prediction, HVAC optimization, "home when
  they shouldn't be" (security) vs "no activity" (elder-care).
- **NILM (energy disaggregation)** — identify appliances from whole-home power (classic ML).
- **Predictive safety** — pipe-freeze risk from temp trend, not just reactive alarms.
- **Cross-home fleet benchmarking** — a home vs its cohort.
- **LLM** — "why was my energy bill high this month?" conversational over home telemetry.

## Honest risks

- **Don't fight consumer DIY** — win in B2B fleets or not at all.
- **You now own software in someone's home** — support, OTA updates, security surface, offline
  handling (outbox mitigates). Heavier ops than a phone app.
- **Privacy is heavier** — occupancy reveals when people are home; GDPR/consumer-privacy,
  data-residency, consent matter more than industrial telemetry.
- **Matter is still maturing** — lean on HA/zigbee2mqtt normalization now; adopt Matter clusters
  as they stabilize.

## Rubric score vs OBD-II

Cheaper *sensors* ($5–30) but adds a $35–100 hub · **better protocol match (MQTT-native +
Matter)** · massive/growing install base · far wider data · gateway = existing `clients/pi`
lineage. Trade-offs: stationary (more ops), crowded consumer space (must go B2B), heavier
privacy. **Cleanest first build:** a `clients/pi` **MQTT-bridge driver** that subscribes to a
home's Home-Assistant / zigbee2mqtt broker and relays to TagPulse with tenant + subject
mapping — near-zero backend change, reuses the native MQTT path.
