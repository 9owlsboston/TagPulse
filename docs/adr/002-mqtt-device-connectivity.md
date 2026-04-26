# ADR-002: Use MQTT for Device Connectivity

**Status:** accepted
**Date:** 2026-04-25

## Context

RFID tag readers and associated sensors need to send data to the platform reliably. These devices may operate in environments with intermittent connectivity. We need a lightweight, bidirectional protocol that supports publish/subscribe patterns.

## Decision

Use **MQTT** (via an external broker such as EMQX or Mosquitto) as the primary device-to-cloud protocol.

## Consequences

- **Good:** MQTT is the de facto IoT standard. Most RFID readers and IoT gateways support it natively.
- **Good:** Low overhead, persistent connections, QoS levels for delivery guarantees, built-in last-will for device health monitoring.
- **Good:** Pub/sub model enables adding analytics consumers without changing device firmware.
- **Bad:** Requires running/managing an MQTT broker (EMQX, Mosquitto, or managed service).
- **Bad:** Not directly accessible from web browsers (need WebSocket bridge for dashboards).

## Alternatives Considered

- **HTTP/REST:** Simpler, no broker needed. But no persistent connections, no push, higher overhead per message. Poor fit for high-frequency tag reads.
- **AMQP:** More feature-rich than MQTT but heavier. Less native support on constrained IoT devices.
