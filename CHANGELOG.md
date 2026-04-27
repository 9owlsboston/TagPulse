# Changelog

All notable changes to TagPulse will be documented in this file.

## Unreleased

### Added
- Audit logging now records `user_id` to attribute who made each change (migration 015)
- Role-based permission matrix: admin (full), editor (create/update), viewer (read-only) on device, rule, integration, telemetry model, and admin routes
- `count_alerts_since` repository method for computing device error rates
- Unit tests for user routes, provisioning schemas, and admin ops (test_user_routes.py, test_provisioning.py, test_admin_ops.py)
- Remote IoT testing guide: ngrok tunneling for HTTP API and MQTT broker (quickstart)
- Corporate proxy (WSL) troubleshooting section in quickstart guide

### Changed
- Device health `error_rate` now computed from alert-to-read ratio instead of hardcoded 0.0
- Routes migrated from `get_current_tenant` to `require_role()` for proper access control

### Fixed
- Docker Compose: added `api` network alias to `app` service so the UI nginx proxy can resolve `http://api:8000`
- TagPulse-UI Dockerfile: created nginx cache directories with correct ownership for non-root operation
- TagPulse-UI nginx.conf: set `pid /tmp/nginx.pid` to allow non-root nginx process
- Migration 001: composite primary key `(id, timestamp)` on `tag_reads` for TimescaleDB 2.26+ hypertable compatibility
- Migration 006: composite primary key `(id, triggered_at)` on `alerts` for TimescaleDB 2.26+ hypertable compatibility

### Added (prior)
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
