# ADR-008: Multi-Tenancy Strategy

**Status:** proposed
**Date:** 2026-04-25

## Context

TagPulse is currently single-tenant — one database, no tenant concept in the data model or API layer. To serve multiple organizations (Company X, Y, Z) from a shared platform we need:

1. **Data isolation** — each tenant sees only its own devices, tag reads, rules, and alerts.
2. **Chargeback / usage metering** — track resource consumption per tenant for billing.
3. **Data residency** — some tenants may require data to stay within a specific geographic region.

These requirements range from table-stakes SaaS (isolation, metering) to enterprise-grade (data residency). The strategy must support the simple case cheaply while leaving a path to stronger isolation when demanded.

## Decision

Adopt a **hybrid multi-tenancy model**:

### Tier 1 — Row-Level Isolation (default)

- Add a `tenant_id UUID NOT NULL` column to every table (`devices`, `tag_reads`, `rules`, `alerts`, `integrations`).
- Add `tenant_id` as a **space-partitioning dimension** on TimescaleDB hypertables (`tag_reads`, `alerts`) for query performance.
- Create a `tenants` table holding tenant metadata (name, plan, region preference, status).
- Enforce scoping via a FastAPI dependency (`get_current_tenant`) that extracts `tenant_id` from a JWT claim or API key and injects it into all service-layer calls.
- Service functions always filter by `tenant_id`; no query may omit the filter except super-admin endpoints.
- Row-Level Security (RLS) policies on PostgreSQL as a defense-in-depth layer.

### Tier 2 — Database-Per-Tenant (opt-in for data residency)

- Tenants requiring data residency get a **dedicated TimescaleDB instance** deployed in the required region.
- A `tenant_routing` table in the control-plane database maps `tenant_id → database_url + region`.
- The FastAPI dependency resolves the correct async database session per request based on the routing table.
- Application code remains unchanged — the tenant-scoped session is injected via `Depends()`.

#### Routing mechanism (per [storage-strategy.md §6 Q2](../design/storage-strategy.md))

Routing is implemented as a **hybrid middleware-default model with mixed-tier capability built in from v1**:

- **Single seam:** `db_session_var: ContextVar[AsyncSession]` in `tagpulse.core.context`. Repositories never see a tenant argument; they read the contextvar.
- **Pool registry:** built once at startup from `config/database.yaml`, mapping `db_pool_key → async_sessionmaker`. v1 ships with a single `shared_default` entry; additional pools are added per sovereign-tenant onboarding.
- **Tenants table** carries `db_pool_key VARCHAR(64) NOT NULL DEFAULT 'shared_default'`. Most tenants share the default pool with RLS isolation; tenants with sovereignty / residency contracts get their own key pointing at a dedicated cluster in the required region.
- **Per-request path:** middleware resolves the tenant from the JWT/API key, looks up `tenants.db_pool_key`, fetches a session from the matching pool, sets `app.current_tenant_id` for shared-pool tenants (RLS), binds the contextvar, runs the request, resets on exit.
- **Background / admin path:** `async with tenant_context(tenant_id):` async context manager binds the contextvar manually for non-request code (rules engine, scheduled jobs, scripts).
- **Cross-tenant operations** go through a dedicated `AdminRepository` that takes an explicit `tenant_id` and is gated by an admin role at the route layer — making cross-tenant queries visible in code review.
- **Mixed-tier example:** Tenant A (regulated, sovereign) has `db_pool_key='acme_eu_west'` → dedicated cluster in West Europe. Tenants B and C (smaller, flexible) have `db_pool_key='shared_default'` → shared cluster in East US, isolated from each other by RLS. All three are served by the same code; the middleware routes per request.
- **Promotion path** (shared → sovereign): provision new cluster, register pool key, run migrations against it, `pg_dump` filtered by `tenant_id` from shared pool, restore to new cluster, update `tenants.db_pool_key` in a brief read-only window, delete migrated rows from shared pool. **No application code change.**
- **Migrations** run against every registered pool at deploy time; schema is identical across pools.

### Usage Metering & Chargeback

#### Billable Dimensions

Every resource and operation attributable to a tenant is tracked as a named **dimension**:

| Dimension | Unit | Source |
|-----------|------|--------|
| `ingestion` | events | Ingestion layer — count of tag reads written per tenant |
| `api_read` | requests | API middleware — query/telemetry endpoint calls |
| `api_write` | requests | API middleware — CRUD mutation calls |
| `active_devices` | devices | Daily snapshot — `COUNT(*)` of active devices per tenant |
| `mqtt_connections` | connections | MQTT broker plugin/proxy — peak concurrent connections |
| `rule_evaluations` | evaluations | Rules engine — evaluations performed per tenant per event |
| `alerts_fired` | events | Rules engine — alerts created per tenant |
| `webhook_deliveries` | requests | Integration layer — outbound HTTP calls per tenant |
| `sse_connections` | connections | Integration layer — concurrent SSE streams per tenant |
| `export_volume` | bytes | Integration layer — payload size per scheduled export |
| `storage` | bytes | Background job — TimescaleDB chunk metadata per tenant |
| `eventbus_events` | events | EventBus — internal events published + consumed per tenant |

#### Metering Architecture

Each component calls `meter.record(tenant_id, dimension, count)` inline. The `UsageMeter` service:

1. **Buffers in memory** — `dict[(tenant_id, dimension)] → int` accumulator.
2. **Flushes every 60 seconds** — single batch `INSERT ... ON CONFLICT DO UPDATE SET quantity = quantity + excluded.quantity` into `tenant_usage_detail`.
3. **Checks quotas inline** — `meter.check_quota(tenant_id, dimension)` reads from buffer + DB, returns `allowed | throttled | rejected`.
4. **Emits metrics** — exposes `tenant_usage_recorded` counter and `tenant_quota_exceeded` counter for observability.

```
                          +---------------------------+
  Ingestion ---count--+   |                           |
  API middleware ------+-->  UsageMeter (in-process)   |
  Rules engine --------+  |  * Buffers in memory      |
  Integration layer ---+  |  * Flushes every 60s      |
  EventBus ------------+  |  * Checks quotas inline   |
                          +-------------+-------------+
                                        | flush
                                        v
                              tenant_usage_detail
                              (TimescaleDB)
                                        |
                                        v
                              /admin/usage API
                              /admin/billing/export
```

#### Quotas & Rate Limiting

Tenants are assigned quotas per dimension based on their plan. Enforcement happens inline at the point of metering.

| Plan | ingestion/day | api_read/day | api_write/day | devices | rules | webhook_deliveries/day |
|------|--------------|-------------|--------------|---------|-------|----------------------|
| Free | 10,000 | 1,000 | 500 | 5 | 3 | 100 |
| Standard | 1,000,000 | 50,000 | 10,000 | 100 | 50 | 10,000 |
| Enterprise | Custom | Custom | Custom | Custom | Custom | Custom |

Enforcement behavior per `action_on_exceed`:
- **`throttle`** — add artificial delay (exponential back-off) to slow the tenant down.
- **`reject`** — return HTTP 429 or drop MQTT message. Hard stop.
- **`alert_only`** — allow the operation but fire an internal alert for platform operators.

#### Billing API

- `GET /admin/usage?tenant_id=X&start=2026-04-01&end=2026-04-30` — JSON usage report per dimension per day.
- `GET /admin/usage/summary?tenant_id=X&period=monthly` — aggregated totals for a billing period.
- `GET /admin/billing/export?format=csv&period=2026-04` — CSV export for import into billing systems (Stripe, Zuora, etc.).
- Webhook on quota breach for real-time billing alerts to tenant admins and platform operators.

### Tenant-Scoped Authentication

- Each tenant has one or more API keys and/or OAuth client credentials.
- JWT tokens carry `tenant_id` and `role` claims.
- Roles: `tenant_admin`, `tenant_user`, `platform_admin` (cross-tenant).

## Data Model Changes

```
tenants
-------
id              UUID PK
name            TEXT NOT NULL
slug            TEXT UNIQUE NOT NULL
plan            TEXT NOT NULL DEFAULT 'standard'  -- free | standard | enterprise
region          TEXT          -- e.g. 'us-east-1', NULL = default region
database_url    TEXT          -- NULL = shared DB, set = dedicated DB
status          TEXT NOT NULL DEFAULT 'active'
created_at      TIMESTAMPTZ NOT NULL DEFAULT now()

-- Every existing table gains:
ALTER TABLE devices       ADD COLUMN tenant_id UUID NOT NULL REFERENCES tenants(id);
ALTER TABLE tag_reads     ADD COLUMN tenant_id UUID NOT NULL REFERENCES tenants(id);
ALTER TABLE rules         ADD COLUMN tenant_id UUID NOT NULL REFERENCES tenants(id);
ALTER TABLE alerts        ADD COLUMN tenant_id UUID NOT NULL REFERENCES tenants(id);
ALTER TABLE integrations  ADD COLUMN tenant_id UUID NOT NULL REFERENCES tenants(id);

tenant_usage_detail
-------------------
tenant_id       UUID NOT NULL REFERENCES tenants(id)
usage_date      DATE NOT NULL
dimension       TEXT NOT NULL   -- 'ingestion', 'api_read', 'api_write', 'rule_evaluations', etc.
quantity        BIGINT NOT NULL DEFAULT 0
unit            TEXT NOT NULL   -- 'events', 'requests', 'bytes', 'connections', 'evaluations', 'devices'
PRIMARY KEY (tenant_id, usage_date, dimension)

-- Example rows for one tenant on one day:
-- ('tenant-x', '2026-04-25', 'ingestion',          145832, 'events')
-- ('tenant-x', '2026-04-25', 'api_read',              892, 'requests')
-- ('tenant-x', '2026-04-25', 'api_write',              47, 'requests')
-- ('tenant-x', '2026-04-25', 'rule_evaluations',   145832, 'evaluations')
-- ('tenant-x', '2026-04-25', 'alerts_fired',           23, 'events')
-- ('tenant-x', '2026-04-25', 'webhook_deliveries',     46, 'requests')
-- ('tenant-x', '2026-04-25', 'storage',        524288000, 'bytes')
-- ('tenant-x', '2026-04-25', 'active_devices',         38, 'devices')
-- ('tenant-x', '2026-04-25', 'mqtt_connections',        38, 'connections')

tenant_quotas
-------------
tenant_id           UUID NOT NULL REFERENCES tenants(id)
dimension           TEXT NOT NULL   -- matches dimension in tenant_usage_detail
max_quantity        BIGINT NOT NULL
period              TEXT NOT NULL DEFAULT 'daily'  -- daily | monthly
action_on_exceed    TEXT NOT NULL DEFAULT 'throttle'  -- throttle | reject | alert_only
PRIMARY KEY (tenant_id, dimension)
```

## MQTT Topic Convention

Update topic structure to include tenant context:

```
tenants/{tenant_id}/devices/{device_id}/tag-reads
tenants/{tenant_id}/devices/{device_id}/status
```

MQTT broker ACLs restrict each tenant's credentials to its own topic prefix.

## Consequences

- **Good:** Row-level isolation is low-cost, no infra duplication, works from day one.
- **Good:** Database-per-tenant path satisfies data residency without changing app code.
- **Good:** Usage metering with per-dimension granularity enables pay-per-use billing and detailed chargeback from the start.
- **Good:** Quota enforcement protects the platform from noisy-neighbor problems.
- **Good:** RLS provides defense-in-depth against accidental cross-tenant data leakage.
- **Bad:** Every query must include `tenant_id` — missed filters cause data leakage. Mitigated by centralized service-layer enforcement and RLS.
- **Bad:** Database-per-tenant adds operational complexity (connection management, migrations across instances). Mitigated by keeping it opt-in for enterprise tenants only.
- **Bad:** MQTT topic restructuring is a breaking change if devices are already deployed. Mitigated by doing this before GA.

## Alternatives Considered

- **Schema-per-tenant:** Each tenant gets a PostgreSQL schema. Moderate isolation, but schema migrations become O(N) operations and TimescaleDB hypertable partitioning doesn't align well with schema boundaries. Rejected as worst of both worlds.
- **Separate deployments per tenant:** Full stack isolation. Maximum safety but extremely high operational cost. Rejected for the general case; database-per-tenant captures most of the benefit at lower cost.
