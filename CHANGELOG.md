# Changelog

All notable changes to TagPulse will be documented in this file.

## Unreleased

### Added
- **Edge device reference client (`clients/pi/`):** Python package shipped to Raspberry Pi developers that enforces the on-the-wire device contract — dedup window, ENTER/EXIT state machine, batched publish, SQLite WAL ring buffer (restart-safe, size + age bounded), full-jitter exponential reconnect backoff, UTC timestamp validation, MQTT LWT, periodic heartbeat. Includes runnable example and 16 unit/integration tests.
- Design document: [docs/design/asset-tracking-gap-analysis.md](docs/design/asset-tracking-gap-analysis.md) — end-to-end gap audit against the home-grown Pi asset-tracking goal (location, sensor telemetry, asset/zone model, device identity, MQTT topic taxonomy).
- Reference document: [docs/azure-iot-asset-tracking.md](docs/azure-iot-asset-tracking.md) — Azure-equivalent architecture for asset tracking.
- **UI Authentication (Sprint 13):** Two-mode login page — API Key (email + key → full role-based access) and Tenant ID (backward-compat viewer access)
- `POST /auth/login` endpoint — exchanges email + API key for a 1-hour JWT access token
- JWT authentication in `get_current_user` middleware (JWT → API key → X-Tenant-ID priority)
- `JWT_SECRET` and `JWT_EXPIRY_SECONDS` configuration settings
- Login rate limiting (5 attempts/minute per IP) on `POST /auth/login`
- `RoleGuard` component and `useCanPerform()` hook for role-based UI rendering
- Role guards on all mutation actions: device decommission (admin), create/edit rules (editor+), delete rules (admin), create integrations (editor+), delete integrations (admin), create telemetry models (editor+), delete telemetry models (admin), acknowledge alerts (editor+)
- Usage menu item hidden for non-admin users in sidebar
- User profile display in header (name, role badge, tenant name)
- JWT token expiry handling — auto-logout on expired/revoked tokens
- API client sends `Authorization: Bearer <JWT>` when logged in via API key
- 15 unit tests for JWT creation/decode, login schemas, rate limiting, and API key verification
- `PyJWT>=2.8` dependency added to pyproject.toml
- `/auth` and `/users` nginx proxy routes for Docker deployment
- Design document: [docs/design/ui-authentication.md](docs/design/ui-authentication.md)

### Changed
- Auth context expanded: `user`, `role`, `accessToken`, `isAuthenticated`, `loginWithApiKey()`, `loginWithTenantId()`
- Login page redesigned with Ant Design Tabs (API Key tab default, Tenant ID tab secondary)
- Sidebar menu items filtered by role (Usage visible to admin only)
- API client upgraded: JWT Bearer token takes priority over X-Tenant-ID header

### Fixed
- Navigation highlight: "Telemetry Models" no longer incorrectly highlights "Telemetry" (longest-prefix-match fix)

---

## Previous Unreleased

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
