# Strategy: competitive positioning

**Date:** 2026-07-26
**Status:** exploration
**Related:** [ai-landscape.md](ai-landscape.md), [sensor-wedges.md](sensor-wedges.md), [home-automation.md](home-automation.md)

---

## Summary

TagPulse plays in a crowded IoT market, but most incumbents sit in **different layers** — and
that layering is the opportunity. TagPulse is an **application-layer, multi-tenant telemetry +
rules + geofencing + analytics platform** with two things few others pair: a **sensor-agnostic
edge-gateway driver model** (cheap/accessible sensors, [sensor-wedges.md](sensor-wedges.md)) and
an **AI-ready substrate** ([ai-landscape.md](ai-landscape.md)). The defensible move is **not**
to out-breadth Samsara or out-plumb the hyperscalers, but to win **vertical application depth on
cheap sensors, with an AI-native analytics/agentic layer**. Vendor descriptions below are
sourced from public positioning (2025) and are directional.

## The landscape, by layer

| Layer | Players | What they sell | TagPulse relationship |
|---|---|---|---|
| **Connectivity / hardware modules** | Particle, Blues, Golioth | cellular modules + device-cloud, firmware-to-cloud, OTA | **complementary** — TagPulse can *sit on top of* their backhaul, not compete on silicon |
| **Device reliability / observability** | Memfault | crash/diagnostics, OTA, device health for OEMs (engineering buyer) | **adjacent** — they watch *device health*, TagPulse watches *the business signal* |
| **Full-stack connected operations** | Samsara | hardware + SaaS fleet/ops, AI dash-cams, enterprise, $100k+ ARR | **avoid head-on** — capital-heavy, vertical, enterprise sales motion |
| **IoT application enablement (PaaS)** | Losant, ThingsBoard, Datacake, AWS/Azure IoT | low-code dashboards, rules, device mgmt; DIY building blocks | **closest competitors** — but they're horizontal toolkits; TagPulse is a *vertical application* |
| **RFID / asset-tracking specialists** | various | tag-read capture, RTLS | **origin niche** — TagPulse is generalizing beyond it |

## Where TagPulse actually sits

Not silicon (Particle/Blues), not device-health (Memfault), not a capital-heavy full-stack
(Samsara), not a bare toolkit (ThingsBoard/Losant/hyperscaler IoT). TagPulse is a
**multi-tenant, opinionated application** — telemetry + subject-scoped model + rules/alerts +
geofencing + analytics + integration — that a "fleet-of-X" operator uses **without assembling
it themselves**, fed by **cheap sensors via the driver model** and an **AI/agentic layer**.

## The defensible wedge

Two failure modes to avoid: competing on **breadth** (Samsara has more hardware + a sales army)
and competing on **plumbing** (hyperscaler IoT is undifferentiated and cheap). The wedge is the
**squeeze in between**:

- **Deeper than a DIY toolkit** (ThingsBoard/Losant) — ships the vertical logic, not just
  building blocks.
- **Lighter/cheaper than Samsara** — phone/BLE/`clients/pi` sensors, no hardware CapEx, SaaS
  self-serve.
- **AI-native, not AI-bolted-on** — the analytics-module + edge-gateway seams make ML and an
  agentic operator copilot first-class, not a roadmap afterthought.
- **Multi-tenant "fleet-of-X" SaaS shape** — a landlord with 100 units or an operator with 500
  reefers is one tenant with many subjects; the data model already fits.

Land in **one vertical** (cold-chain, property-insurance loss-prevention, or fleet-UBI) where
*full-stack + AI + cheap-sensor* beats both the DIY toolkits (too much assembly) and the
heavyweights (too much cost).

## Honest gaps vs the field

- **No hardware, no scale, no vertical GTM yet** — incumbents have funding, ecosystems, and
  reference customers; TagPulse has none of that today.
- **Operational maturity gaps** — the MQTT broker HA/persistence story is still open (see
  [current-state.md](../current-state.md)); Samsara/Memfault ship reliability as a product.
- **Differentiation must be software depth + AI + a specific vertical** — "another horizontal IoT
  platform" loses to both ends of the market.
- **`unverified`** — competitor capabilities and market claims here are from public 2025
  positioning, not primary research; validate before using in GTM material.
