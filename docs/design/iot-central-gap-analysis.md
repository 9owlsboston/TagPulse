# Design Document: IoT Central Gap Analysis — Capabilities to Remediate

**Date:** 2026-04-25
**Status:** accepted
**Reference:** [Azure IoT Central Architecture](https://learn.microsoft.com/en-us/azure/iot-central/core/concepts-architecture)

---

## 1. Purpose

This document maps Azure IoT Central's architecture against TagPulse's current implementation (Sprints 1–8) to identify capability gaps that need to be remediated for production readiness.

---

## 2. Gap Summary

| # | Capability | IoT Central | TagPulse Status | Priority | Target Sprint |
|---|-----------|-------------|----------------|----------|---------------|
| G1 | User & role management | Users, roles, permissions | ❌ Missing | **P1** | New Sprint (pre-9) |
| G2 | API key management | API tokens with scopes | ❌ X-Tenant-ID only, no key validation | **P1** | New Sprint (pre-9) |
| G3 | Audit logging | Track config changes by user | ❌ Missing | **P1** | Sprint 10 |
| G4 | Device provisioning | Self-registration, approval flow | ❌ Missing | **P1** | New Sprint |
| G5 | Data Explorer UI | Ad-hoc query builder | ❌ API exists, no UI | **P1** | Sprint 9 |
| G6 | Telemetry model management UI | Manage device templates in UI | ❌ API exists, no UI | **P1** | Sprint 9 |
| G7 | Overview dashboard with KPI tiles | KPI, LKV, summary tiles | ❌ Missing | **P1** | Sprint 9 |
| G8 | Cloud-to-device commands | Send commands to devices | ❌ Missing | **P2** | Backlog |
| G9 | Bulk device operations (jobs) | Batch update, batch export | ❌ Missing | **P2** | Backlog |
| G10 | Edge gateway support | Disconnected devices, local aggregation | ❌ Missing | **P2** | Backlog |
| G11 | Data export transformations | Modify payload shape before delivery | ❌ Missing | **P2** | Backlog |
| G12 | Scheduled exports | Periodic CSV/JSON to storage | ❌ Sprint 8 [planned] | **P2** | Sprint 8 continuation |
| G13 | Customizable dashboards | Drag-and-drop tile layout | ❌ Fixed layout | **P2** | SPA migration |
| G14 | Device type-specific views | Per-template custom views | ❌ Same view for all | **P2** | Sprint 9+ |

---

## 3. P1 Gaps — Detailed Analysis

### G1: User & Role Management

**IoT Central:** Multiple users per application, custom roles with granular permissions (device CRUD, rule management, data export, dashboard edit, etc.), org-scoped access.

**TagPulse today:** Tenant-level auth only via `X-Tenant-ID` header. No concept of individual users within a tenant. No roles. No permissions.

**Impact:** Cannot restrict access within a tenant. All API keys have full access. No accountability for who made a change.

**Remediation:**

```
users
-----
id              UUID PK
tenant_id       UUID FK → tenants.id
email           VARCHAR(255) NOT NULL UNIQUE
name            VARCHAR(255) NOT NULL
role            VARCHAR(50) NOT NULL    -- admin | editor | viewer
api_key_hash    VARCHAR(255) NULL       -- bcrypt hash of API key
status          VARCHAR(20) NOT NULL DEFAULT 'active'
created_at      TIMESTAMPTZ NOT NULL
last_login      TIMESTAMPTZ NULL

Roles:
- admin:  full CRUD on all resources, manage users, view billing
- editor: CRUD on devices, rules, integrations; no user management
- viewer: read-only access to all data; no mutations
```

**API endpoints:**
- `POST /users` — create user (admin only)
- `GET /users` — list users (admin only)
- `PATCH /users/{id}` — update role/status (admin only)
- `DELETE /users/{id}` — deactivate user (admin only)
- `POST /users/{id}/api-key` — generate API key (returns key once)

**Auth flow change:** Replace `X-Tenant-ID` header with `Authorization: Bearer <api-key>`. Look up user → get tenant_id + role. Enforce role permissions in route dependencies.

**Effort:** 1 sprint (medium)

---

### G2: API Key Management

**IoT Central:** Scoped API tokens with expiration, revocation.

**TagPulse today:** Raw tenant UUID in header. No key hashing. No expiration. No revocation.

**Remediation:** Bundled with G1 (users table has `api_key_hash`). API key = `tp_{tenant_slug}_{random_32_chars}`. Stored as bcrypt hash. Verified on each request.

**Effort:** Included in G1

---

### G3: Audit Logging

**IoT Central:** Tracks who created/updated/deleted entities, exportable.

**TagPulse today:** Application logs changes but no structured audit trail in DB.

**Remediation:**

```
audit_logs
----------
id              UUID PK
tenant_id       UUID FK → tenants.id
user_id         UUID FK → users.id NULL  -- NULL for system actions
action          VARCHAR(20) NOT NULL     -- created | updated | deleted
resource_type   VARCHAR(50) NOT NULL     -- device | rule | integration | ...
resource_id     UUID NOT NULL
changes         JSONB NULL               -- before/after for updates
created_at      TIMESTAMPTZ NOT NULL (indexed)
```

**Implementation:** Middleware or decorator on service methods that logs mutations. Query via `GET /admin/audit-logs`.

**Effort:** Small (1-2 days within Sprint 10)

---

### G4: Device Provisioning

**IoT Central:** Device Provisioning Service — devices self-register, get approved, assigned to templates.

**TagPulse today:** Devices are manually registered via `POST /device-registry`. No self-registration. No approval flow.

**Remediation:**

1. **Provisioning endpoint:** `POST /devices/provision` — device sends credentials, gets registered with `status=pending`
2. **Approval flow:** Admin approves via `POST /device-registry/{id}/approve` → sets `status=active`
3. **Pre-shared key auth:** Device includes a tenant-scoped provisioning key in the request
4. **Auto-assign template:** Match device_type to telemetry model definitions

**Effort:** 1 sprint (medium) — needs design doc first

---

### G5: Data Explorer UI

**IoT Central:** Interactive query builder, pin results to dashboards.

**TagPulse today:** `GET /tag-reads` API with filters exists. No UI.

**Remediation:** Add `/ui/query` page to Sprint 9:
- Device picker dropdown
- Tag ID text filter
- Date range picker (start/end)
- Signal strength range slider
- Query button → results table
- Chart.js line chart of results over time

**Effort:** Small (part of Sprint 9 UI)

---

### G6: Telemetry Model Management UI

**IoT Central:** Device templates define telemetry, properties, commands. Managed in UI.

**TagPulse today:** `POST /telemetry-models` API exists. No UI.

**Remediation:** Add `/ui/telemetry-models` page to Sprint 9:
- List all telemetry models
- Create form: device_type + metrics (name, unit, min, max)
- Delete button

**Effort:** Small (part of Sprint 9 UI)

---

### G7: Overview Dashboard with KPI Tiles

**IoT Central:** KPI tiles, LKV tiles, device count, event counts.

**TagPulse today:** No dashboard overview page designed.

**Remediation:** Add KPI summary to `/ui/` landing page:
- Total devices (active/decommissioned)
- Tag reads today
- Active alerts (open count)
- Anomalies detected (from analytics)
- Device health summary (online/offline counts)

Each tile uses HTMX `hx-get` with `hx-trigger="every 30s"` for periodic refresh.

**Effort:** Small (part of Sprint 9 UI)

---

## 4. P2 Gaps — Deferred

| Gap | Why Deferred | When to Revisit |
|-----|-------------|-----------------|
| G8: Cloud-to-device commands | Needs MQTT publish infrastructure + command queue | After production hardening |
| G9: Bulk operations / jobs | Low demand at current scale | When fleet > 100 devices |
| G10: Edge gateway | Significant new component, needs design doc | When disconnected device scenarios arise |
| G11: Export transformations | Enrichments cover 80% of the need | When customers request payload reshaping |
| G12: Scheduled exports | croniter dependency, background scheduler | Sprint 8 continuation |
| G13: Customizable dashboards | Requires SPA migration (React) | When HTMX limitations become blocking |
| G14: Device type-specific views | Per-template UI customization | After telemetry model UI is built |

---

## 5. What TagPulse Does Better Than IoT Central

| Advantage | Detail |
|-----------|--------|
| **Built-in multi-tenancy** | IoT Central is single-tenant per app; TagPulse has row-level isolation + RLS + usage metering |
| **Usage metering + quotas** | IoT Central doesn't expose per-tenant consumption; TagPulse meters 12 dimensions |
| **Extensible analytics** | IoT Central's analytics are fixed; TagPulse has pluggable `AnalyticsModule` framework |
| **Self-hosted** | IoT Central is Azure-only; TagPulse runs on any Docker host |
| **Event filters + enrichments** | Already implemented on integrations, matching IoT Central's data export capabilities |
| **Open API-first** | All features accessible via REST API before UI exists |

---

## 6. Remediation Roadmap

```
Current state (Sprints 1-8 complete)
    │
    ├── Sprint 8.5 (new): User & role management + API key auth (G1, G2)
    │
    ├── Sprint 9: Admin UI + Data Explorer + Telemetry Models UI + KPI dashboard (G5, G6, G7)
    │
    ├── Sprint 10: Production hardening + audit logging (G3)
    │
    ├── Sprint 11: Observability
    │
    ├── Sprint 12 (new): Device provisioning flow (G4)
    │
    └── Backlog: Commands (G8), Jobs (G9), Edge (G10), Transforms (G11), Exports (G12)
```

---

## 7. Open Questions

- Should user/role management (G1) be a prerequisite for Sprint 9 UI, or can the UI launch with tenant-level auth only?
  - **Recommendation:** Launch UI with tenant auth (v1). Add user auth as Sprint 8.5 before production GA.
- Should we adopt a standard like DTDL v2 for telemetry models, or keep our simpler MetricDefinition format?
  - **Recommendation:** Keep simple format for v1. DTDL adds complexity with no current consumer requiring it.
- Should device provisioning (G4) use pre-shared keys or X.509 certificates?
  - **Recommendation:** Pre-shared keys for v1. X.509 is enterprise-tier (database-per-tenant path).
