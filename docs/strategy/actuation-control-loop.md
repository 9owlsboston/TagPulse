# Strategy: actuation & the control loop — monitoring → automation

**Date:** 2026-07-26
**Status:** exploration
**Related:** [ADR-005 (embedded rules engine)](../adr/005-embedded-rules-engine.md), [ADR-006 (webhook integration layer)](../adr/006-webhook-integration-layer.md), [ADR-002 (MQTT connectivity)](../adr/002-mqtt-device-connectivity.md), [ADR-011 (device identity roadmap)](../adr/011-device-identity-roadmap.md), [home-automation.md](home-automation.md)

---

## Summary

Today TagPulse is a **monitoring** platform: sense → rule → *notify* (webhook / email /
internal queue). **Actuation** closes the loop — sense → decide → **act**, sending a command
back down to a device. That single addition reframes the product from "dashboards & alerts" to
an **automation platform**, and it cuts across every wedge (home valves/thermostats, industrial
setpoints, fleet lock/immobilize). This note maps what already exists to build on, the hard
part (downlink to an outbound-only edge), the safety model, and why it warrants its own ADR.

## What already exists to build on

- **The rules engine already has an "action" abstraction** — webhook, email, internal
  notification ([ADR-005](../adr/005-embedded-rules-engine.md)). A **"device command" action**
  is a new action type, not a new engine.
- **MQTT is inherently bidirectional** ([ADR-002](../adr/002-mqtt-device-connectivity.md)) — a
  `devices/{id}/commands` downlink topic mirrors the existing `.../tag-reads` uplink.
- **Device identity is in place** — rotatable tokens (shipped) and mTLS
  ([ADR-011](../adr/011-device-identity-roadmap.md)) authenticate *who* may receive a command.
- **Audit + grants** — `audit_logs` and the per-gateway subject-grant model give the spine for
  "who commanded what, and were they allowed to."

## The hard part: downlink to an outbound-only edge

The mobile/edge gateway is deliberately **outbound-only** (durable outbox → batched HTTPS POST;
no inbound socket) — great for residential/NAT security, but a command has nowhere to land.
Options, by gateway type:

| Pattern | Fits | Trade-off |
|---|---|---|
| **MQTT downlink** (device subscribes) | fixed readers, `clients/pi` hubs | needs a live broker session; not the outbox model |
| **Command queue + device poll** | outbound-only mobile gateway | simple, secure; latency = poll interval |
| **SSE / WebSocket downlink** | always-connected gateways | keeps a socket open; battery/cost on mobile |
| **Cloud-to-device via provider** (e.g. Azure IoT Hub C2D) | Azure-native fleets | offloads the channel; new dependency |

Recommendation: **command queue + poll** for the mobile/edge lineage (preserves the
outbound-only invariant), **MQTT downlink** for fixed/hub devices — one command API, two
delivery adapters.

## Safety model (the part that makes this real)

Actuation has **physical consequences**, so the control plane needs more than the alert path:

- **Desired-vs-reported state** (device-shadow / digital-twin pattern) — commands set a *desired*
  state; the device reports *reported* state; the platform reconciles. Avoids fire-and-forget.
- **Idempotency + command IDs** — a retried "close valve" must not double-actuate.
- **TTL / staleness guard** — don't actuate on stale telemetry or deliver an expired command.
- **Acknowledgement / receipts** — every command has a lifecycle (queued → delivered → applied →
  failed), surfaced in the UI and audit log.
- **Authorization** — extend the subject-grant model: commanding a subject is a stronger right
  than reading it.
- **Safety interlocks** — per-tenant guardrails (rate limits, allowed command whitelist, manual
  confirmation for high-risk actions like fleet immobilize).

## Verticals it unlocks

- **Home / property** — auto-shutoff valve on leak, thermostat setback on freeze risk (the
  insurance loss-prevention story becomes *active*, not just alerting).
- **Industrial / facilities** — setpoint changes, actuator control, load shedding.
- **Fleet** — remote lock / immobilize / geofence-triggered actions (**high-liability** — gate
  behind strong confirmation).

## AI angle

Closing the loop is what makes AI *act*, not just advise: **closed-loop optimization** (RL/MPC
for HVAC or demand response), **autonomous remediation** (model predicts excursion → command
issued → outcome fed back), and a genuine **data-flywheel** where actions and their outcomes
become training signal.

## Risks

- **Safety-critical liability** — a wrong/late command can cause physical harm or damage; this
  is a different risk class than a missed alert.
- **Security = physical** — command injection now has real-world effect; the downlink auth +
  signing bar is higher than uplink.
- **Reconciliation complexity** — desired/reported state, retries, partial failure, offline
  devices — this is the genuinely hard engineering.
- **Scope creep** — it touches rules, ingestion, device registry, identity, UI, and the edge
  clients (3+ components) → **design doc + ADR first** per AGENTS.md §9 before any code.
