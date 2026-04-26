# ADR-005: Embedded Rules & Alerts Engine

**Status:** accepted
**Date:** 2026-04-25

## Context

TagPulse needs user-defined rules that evaluate conditions against incoming telemetry and trigger alerts. Examples: "alert when tag X not seen for 10 minutes", "alert when read rate drops below threshold", "alert when signal strength exceeds range". Alerts must be routed to external systems (webhook, email).

We need to decide whether to build a rules engine internally, use an existing open-source engine, or delegate to an external service.

## Decision

Build an **embedded rules engine** within the TagPulse monolith. Rules are stored in TimescaleDB, evaluated in-process against the telemetry stream, and alert delivery is handled by an async task worker.

Rule model:
- **Condition types:** threshold (>, <, ==), absence (not seen for duration), rate change (% change over window)
- **Actions:** webhook call, email notification, internal event queue
- **Scope:** per-device, per-device-group, or global

## Consequences

- **Good:** No external dependency for a core platform feature. Rules evaluate with low latency (in-process, same data path as ingestion).
- **Good:** Rule configuration is a standard CRUD API — consistent with the rest of the platform.
- **Good:** Absence detection requires stateful tracking (timers per device/tag) — easier to manage in-process than across services.
- **Bad:** Complex rule logic (CEP, multi-event correlation) will eventually need a dedicated engine. Accept this limitation for v1.
- **Bad:** CPU-heavy rule evaluation could contend with ingestion. Mitigated by evaluating rules in the background worker, not inline.

## Alternatives Considered

- **External rules engine (e.g., Drools, Node-RED):** More powerful but adds operational complexity and a new technology to the stack.
- **Stream processor rules (e.g., Flink, Kafka Streams):** Best for complex event processing but massive overkill for v1 rule needs. Revisit if rule complexity grows.
