# Strategy: phased synthesis — sequencing the exploration

**Date:** 2026-07-26
**Status:** exploration — capstone / index of the strategy set
**Related:** all notes in this folder ([README](README.md))

---

## Summary

This note sequences the nine strategy explorations into a **dependency-ordered path** so they
read as one plan, not nine ideas. The spine: **decide the no-regret moves → win the STR-noise
beachhead → expand into insurance-leak on the same installed base → compound with an AI/data
platform.** It also maps each move to what it would **seed in the real process** — a
[`docs/roadmap.md`](../roadmap.md) entry, an [ADR](../adr/README.md), or a
[`docs/design/`](../design/) doc — since this folder's job is to *feed* the roadmap, not replace
it. Nothing here is committed until it crosses that line.

## Critical path (and what runs in parallel)

```
                 ┌─────────────────── no-regret, independent ───────────────────┐
                 │  MCP operator/rule copilot   ·   broker HA + mTLS hardening   │
                 └──────────────────────────────────────────────────────────────┘
HARDWARE POSTURE ─▶ STR-noise ─▶ installed ─▶ ┬─ insurance-leak ─▶ actuation ─▶ data
   (decision)       beachhead     base + platform│   (channel)       (ADR-gated)   monetization
                       │                         └─ multifamily (fast-follow)      + federated ML
                       └─ 1 ML analytics module  ·  edge-AI pre-filter (once gateways deployed)
```

The **hardware-posture decision gates everything** on the home path; the **MCP copilot and
broker hardening are independent** and worth doing regardless of which bets land.

## Horizon 0 — decide & de-risk (weeks, no-regret)

| Move | Draws from | Seeds |
|---|---|---|
| **Force the hardware posture** — off-the-shelf-via-`clients/pi` (durable) + partner (first reference) | [beachhead](beachhead-str-vs-insurance.md), [competitive](competitive-positioning.md) | an **ADR** (hardware/sourcing strategy) |
| **Ship the MCP operator + rule-authoring copilot** over `openapi.json` | [ai-landscape](ai-landscape.md) §3 | a roadmap entry + design doc |
| **Buy one primary market report** for the chosen vertical | [market data](home-automation-market.md) caveats | GTM validation, not code |
| **Commit the beachhead motion: STR-noise entry** | [beachhead](beachhead-str-vs-insurance.md) | roadmap direction |

Rationale: the copilot is demoable in days with no data science; the hardware decision unblocks
the whole home path; both are true regardless of vertical outcome.

## Horizon 1 — win the STR-noise beachhead (the wedge)

| Move | Draws from | Seeds |
|---|---|---|
| **STR fleet product** on the existing platform — per-unit subjects, property-manager tenants, threshold/absence rules, fleet dashboard | [home-automation](home-automation.md), [beachhead](beachhead-str-vs-insurance.md) | roadmap sprint(s) |
| **`clients/pi` MQTT-bridge driver** (subscribe to HA/zigbee2mqtt, relay with tenant+subject mapping) | [home-automation](home-automation.md), [sensor-wedges](sensor-wedges.md) | design doc + roadmap |
| **Land 1 reference customer** via partner hardware | [beachhead](beachhead-str-vs-insurance.md) | GTM |
| **One ML analytics module** (learned anomaly on the telemetry) through the existing module→rules→integration path | [ai-landscape](ai-landscape.md) §1, [edge-ai](edge-ai-architecture.md) | roadmap + design doc |

Goal: revenue + a reference deployment + proof the substrate is ML-ready — the assets the next
horizon's channel motion needs.

## Horizon 2 — expand & build the moat (insurance-leak)

| Move | Draws from | Seeds |
|---|---|---|
| **Add leak/freeze/smoke metrics** to the same multi-sensor unit + rules | [beachhead](beachhead-str-vs-insurance.md), [home-automation](home-automation.md) | roadmap |
| **Insurer channel motion** — the reference base opens the door | [beachhead](beachhead-str-vs-insurance.md), [market data](home-automation-market.md) | GTM/partnership |
| **Actuation / control loop** — auto-shutoff valve = prevention, the real moat | [actuation](actuation-control-loop.md) | **ADR + design doc** (safety-critical, 3+ components) |
| **Edge-AI pre-filter / vision** once gateways are deployed at scale | [edge-ai](edge-ai-architecture.md) | ADR (signed model delivery) + design doc |
| **Multifamily fast-follow** — SmartRent-shaped buyer, same dashboard | [beachhead](beachhead-str-vs-insurance.md) | roadmap |

Gate: actuation is **safety-critical and ADR-gated** — it does not start until the STR base and
platform maturity justify it.

## Horizon 3 — compound (AI/data platform)

| Move | Draws from | Seeds |
|---|---|---|
| **Privacy-preserving cross-tenant benchmarks** (min-cohort, consent, separate audited path) | [data-monetization](data-monetization.md) | ADR (governed aggregation) + design doc |
| **Federated models / edge-AI flywheel** | [data-monetization](data-monetization.md), [edge-ai](edge-ai-architecture.md) | roadmap |
| **Vector store + RAG** over telemetry + runbooks | [ai-landscape](ai-landscape.md) §4 | design doc |

Gate: monetization needs **tenant density + a mature trust posture** first — premature and it
poisons adoption.

## Foundations (parallel, enabling — from current-state open gaps)

- **Managed HA MQTT broker + persistence** and **TLS-only + mTLS** ([current-state](../current-state.md)
  open gaps, [ADR-012](../adr/012-mtls-for-mqtt.md)) — prerequisites for scaling edge trust and
  actuation. These are already on the product's own future-state list; the home/edge push raises
  their priority.

## No-regret moves (do regardless of which bets land)

1. **MCP operator/rule copilot** — fast, useful, vertical-agnostic.
2. **One ML analytics module** — proves the substrate, reusable across every wedge.
3. **Broker HA + mTLS hardening** — already needed; unblocks edge/actuation.
4. **Force the hardware-posture decision** — cheap to decide, expensive to defer.

## How this feeds the real roadmap

Per the [folder convention](README.md), these horizons are **exploration outputs**, not schedule.
When a move is picked up, add the [`docs/roadmap.md`](../roadmap.md) entry (and the ADR/design doc
it seeds) on a sprint/kickoff branch, and link it back to the note here. This capstone
deliberately does **not** edit `roadmap.md` — it stays in the strategy tier until a human commits
a bet.
