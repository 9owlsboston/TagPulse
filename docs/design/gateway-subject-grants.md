# Design: Per-gateway approved-subject-set grants (C-6S9H)

**Sprint:** 81 · **Status:** proposed · **Date:** 2026-07-25
**Related:** [gateway-telemetry-ingest.md](gateway-telemetry-ingest.md) (I-75YC),
[external-locations-subject-kinds.md](external-locations-subject-kinds.md) (I-9HQA),
[device-token-http-auth.md](device-token-http-auth.md) (I-K6D1)

## Summary

I-75YC restricted a device/gateway principal on `POST /telemetry/readings/ingest` to its **own**
device subject; relaying telemetry for *other* subjects was deferred (this chore). This sprint
adds a **per-gateway approved-subject-set grant** model: an admin authorizes a specific gateway
device to relay telemetry for a specific set of `(subject_kind, subject_id)` pairs, and the
telemetry-ingest guard then allows the gateway's own device subject **plus** its granted subjects.

## Scope (MVE)

- **In:** `gateway_subject_grants` table + repo; admin create/list/revoke endpoints; enforcement
  in the telemetry-ingest path (`_enforce_device_telemetry`) — own device subject *or* an active
  grant.
- **Deferred (fast-follow):** relaying **external location** for granted subjects via
  `POST /device-location` (its request is self-only today; extending it to a target subject is a
  separate contract change). Logged as a follow-up. The MVE covers the primary relay path
  (telemetry), which already carries `(subject_kind, subject_id)` per reading.
- **Grant subject kinds (MVE): `asset` and `device` only.** These have readily-available in-tenant
  existence checks (assets repo / devices repo), so a grant is validated against a **real**
  subject at creation (blocker fix — a grant can't authorize telemetry for a bogus UUID that could
  add orphan rows or trip broad `telemetry.threshold` rules). `lot`/`stock_item`/`zone` grants are
  rejected `422` in the MVE and enabled in a follow-up once their existence checks are wired.
- **Deferred:** grant caching (request-scoped lookup is correct; the existing
  `core/telemetry_caches.py` TTL cache is for tenant opt-ins, not per-gateway authz).

## Decisions

1. **Table `gateway_subject_grants`** (regular tenant-scoped table, **not** a hypertable —
   mirrors an association table like `asset_tag_bindings`). Migration **061**:
   - `id UUID PK`, `tenant_id UUID FK tenants.id NOT NULL`,
     `gateway_device_id UUID FK devices.id ON DELETE CASCADE NOT NULL`,
     `subject_kind VARCHAR(16) NOT NULL`, `subject_id UUID NOT NULL`,
     `granted_by UUID NULL` (the admin user), `granted_at TIMESTAMPTZ NOT NULL DEFAULT now()`,
     `revoked_at TIMESTAMPTZ NULL`.
   - **Soft revoke:** partial UNIQUE index `(tenant_id, gateway_device_id, subject_kind,
     subject_id) WHERE revoked_at IS NULL` — one active grant per pair, history preserved.
   - Lookup index `(tenant_id, gateway_device_id) WHERE revoked_at IS NULL`.
   - RLS `tenant_isolation_gateway_subject_grants USING (tenant_id = current_setting(
     'app.current_tenant_id')::uuid)` — same policy shape as migrations 019/051/058/059.

2. **Repository** `TimescaleGatewaySubjectGrantRepository` — **every method takes `tenant_id`
   and filters on it in the SQL** (HTTP requests may not set the RLS GUC, so explicit tenant
   scoping is mandatory, not just RLS): `create(tenant_id, gateway_device_id, …)`,
   `list_for_gateway(tenant_id, gateway_device_id)` (active only),
   `revoke(tenant_id, gateway_device_id, subject_kind, subject_id)` (idempotent), and
   `active_subject_set(tenant_id, gateway_device_id) -> set[tuple[str, UUID]]` for enforcement.

3. **Admin management API** (new router `gateway_grants.py`, prefix `/admin`, `require_role("admin")`,
   audit-logged via `AuditLogger`):
   - `POST /admin/gateways/{device_id}/subject-grants` `{subject_kind, subject_id}` → 201 grant.
     Verifies the **gateway device exists** in-tenant (404). **Validates the granted subject exists**
     in-tenant — `asset` via the assets repo, `device` via the devices repo (`404` if missing);
     `lot`/`stock_item`/`zone` → `422` (not supported in the MVE). Rejects `subject_kind="device"` +
     `subject_id==device_id` (redundant — always allowed) `422`. `409` if an active grant already
     exists for the pair.
   - `GET /admin/gateways/{device_id}/subject-grants` → active grants.
   - `DELETE /admin/gateways/{device_id}/subject-grants/{subject_kind}/{subject_id}` → 204 revoke
     (404 if no active grant). Audit actions `gateway_subject_grant.created`/`.revoked`.

4. **Enforcement** — `_enforce_device_telemetry` (telemetry.py) gains the gateway's active grant
   set. The route **guards `user.device_id is not None` → local `UUID`** (fail-closed + mypy
   narrowing) and fetches the set **once per request** (only when `user.role=="device"`) via a new
   `get_gateway_grant_repo` dependency, keyed **only** by that authenticated `device_id` (never a
   request-supplied id). A device reading is allowed iff `subject == ("device", device_id)` **or**
   `(subject_kind, subject_id)` is in the active grant set — else 403. Clock validation +
   `source`/`device_id` coercion (I-75YC) unchanged; for a **granted non-device subject**,
   `device_id` is still stamped to the gateway (relay provenance) and `source` still coerced to
   `"external"`.

## Plan-stage rubber-duck — blockers resolved
1. *Tenant filtering underspecified for list/revoke.* → Decision 2: every repo method takes +
   filters `tenant_id` in SQL (RLS is defense-in-depth only).
2. *Non-existent grants aren't inert (orphan telemetry / broad-rule alerts).* → MVE grant kinds
   limited to `asset`/`device` with **in-tenant existence validation at grant creation**;
   other kinds 422.
3. *`device_id` (`UUID | None`) not narrowed by `require_role`.* → Decision 4: guard `None` → local
   `UUID`; gateway id for the grant lookup is always the authenticated `device_id`, never from the
   request.

## Changes

| Area | File | Change |
|---|---|---|
| Migration | `migrations/versions/061_gateway_subject_grants.py` | table + partial-unique + lookup index + RLS; symmetric downgrade |
| Model | `models/database.py` | `GatewaySubjectGrantModel` |
| Schemas | `models/schemas.py` | `GatewaySubjectGrantCreate`, `GatewaySubjectGrantResponse` |
| Repo | `repositories/timescaledb/gateway_subject_grants.py` (new) | create/list/revoke/active_subject_set |
| Admin API | `api/routes/gateway_grants.py` (new) + `main.py` | create/list/revoke, audit |
| Dep | `api/dependencies.py` | `get_gateway_grant_repo` |
| Enforcement | `api/routes/telemetry.py` | fetch grant set for device principals; `_enforce_device_telemetry` allows own device **or** granted subject |
| Tests | `tests/unit/` | repo (create/dup-409/revoke/active-set); admin routes (create/list/revoke/role/404); enforcement (own ok / granted ok / ungranted 403 / revoked 403); migration parses |
| Contract | `openapi.json` | regenerate |
| Docs | this doc, CHANGELOG, roadmap §81, data-models §gateway_subject_grants | new table + endpoints |

## Security notes
- Grants are tenant-scoped (RLS + explicit `tenant_id` filter) and gateway-scoped; a device can
  never self-grant (management is `require_role("admin")`).
- Enforcement fails closed: no grant → 403; a revoked grant leaves no active row → 403.
- Own-device subject remains unconditional (unchanged I-75YC behavior).
- Grant lookup is per-request (no stale-cache authorization).

## Validation
- `make check` green (via `python -m`); `alembic history` shows 060→061 head.
- New unit tests cover repo + admin routes + the enforcement matrix.
- `openapi.json` regenerated.

## Review attestations
- **Plan-stage rubber-duck:** ran (3 blockers → folded in: tenant-filter every repo method,
  in-tenant subject existence validation at grant creation with MVE kinds asset/device, device_id
  narrowing).
- **Diff-stage code-review:** ran — no blocking issues (migration round-trip symmetric,
  enforcement all-or-nothing + fail-closed, tenant isolation on every repo method, coercion
  consistent, partial-unique re-grant verified).
