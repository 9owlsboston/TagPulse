# Changelog

All notable changes to TagPulse will be documented in this file.

## Unreleased

### Added
- Core ingestion pipeline: MQTT subscriber + HTTP push endpoint (Sprint 1)
- Tag read Pydantic schemas with validation (Sprint 1)
- TimescaleDB schema with hypertable for tag reads (Sprint 1)
- Alembic migrations with async support (Sprint 1)
- EventBus: capacity-limited async pub/sub with overflow policies (Sprint 1)
- Device registry: CRUD API for readers at `/device-registry` (Sprint 2)
- Device configuration profiles: metadata + configuration JSONB (Sprint 2)
- Device status tracking: connection state, firmware version, last seen (Sprint 2)
- MQTT status topic handling: `devices/{device_id}/status` (Sprint 2)
- Telemetry model definitions: per-device-type metric schemas (Sprint 2)
- Tag read query API with filters and pagination (Sprint 3)
- Aggregations: reads per hour, unique tags per time window (Sprint 3)
- Live telemetry API: recent reads per device (Sprint 3)
- Device health API: connectivity, last-seen, reads/hour (Sprint 3)
- Docker Compose: app + TimescaleDB + Mosquitto for local dev (Sprint 4)
- Dockerfile: multi-stage build with non-root user (Sprint 4)
- GitHub Actions CI: lint + typecheck + test (Sprint 4)
- Structured JSON logging with request ID correlation (Sprint 4)
- CONTRIBUTING.md with branch naming and PR expectations (Sprint 4)
