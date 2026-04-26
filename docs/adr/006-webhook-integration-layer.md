# ADR-006: Webhook-First Integration Layer

**Status:** accepted
**Date:** 2026-04-25

## Context

TagPulse must export data and events to external systems (ERP, dashboards, third-party analytics, customer applications). We need to decide on the integration pattern: push vs pull, real-time vs batch, and how integration targets are configured.

## Decision

Use a **webhook-first integration layer** with three complementary channels:

1. **Outbound webhooks** — push events (alerts, telemetry batches, device status changes) to configured HTTP endpoints on triggers.
2. **Streaming endpoint** — Server-Sent Events (SSE) feed for real-time consumers that want to pull a live stream.
3. **Scheduled exports** — periodic batch exports (CSV/JSON) to configured destinations (object storage URL or email).

All integration targets are managed via a CRUD API with configuration stored in TimescaleDB.

## Consequences

- **Good:** Webhooks are universally supported — any system with an HTTP endpoint can integrate.
- **Good:** SSE is simpler than WebSocket for unidirectional streaming and works through proxies/firewalls.
- **Good:** Scheduled exports cover the "weekly report" use case without requiring the consumer to build polling logic.
- **Good:** Integration configuration is a first-class API resource — manageable via UI or programmatically.
- **Bad:** Webhook delivery needs retry logic, dead-letter handling, and delivery status tracking — non-trivial to build reliably.
- **Bad:** SSE is unidirectional. If bidirectional real-time is needed later (e.g., remote device control), WebSocket will need to be added.

## Alternatives Considered

- **Message queue (Kafka, RabbitMQ) as integration bus:** More robust for high-volume consumers but requires consumers to run queue clients. Overkill for v1 where most integrations are HTTP-based.
- **GraphQL subscriptions:** Good developer experience but smaller ecosystem adoption for IoT integrations vs. webhooks.
