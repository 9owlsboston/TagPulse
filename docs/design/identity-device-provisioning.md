# Design Document: Identity & Device Provisioning (Sprint 12)

**Date:** 2026-04-26
**Status:** proposed
**Related:** [IoT Central Gap Analysis](iot-central-gap-analysis.md) (G1, G2, G4)

---

## 1. Problem Statement

TagPulse currently authenticates at the tenant level only (`X-Tenant-ID` header). Three gaps:

1. **No individual users** — Cannot identify who made a change. No accountability.
2. **No API key security** — Raw tenant UUID in header, no hashing, no expiration, no revocation.
3. **No device provisioning** — Devices registered manually via API. No self-registration flow.

---

## 2. User & Role Management (G1)

### Data Model

```
users
-----
id              UUID PK
tenant_id       UUID FK → tenants.id (NOT NULL, indexed)
email           VARCHAR(255) NOT NULL
name            VARCHAR(255) NOT NULL
role            VARCHAR(50) NOT NULL DEFAULT 'viewer'
status          VARCHAR(20) NOT NULL DEFAULT 'active'
api_key_hash    VARCHAR(255) NULL     -- bcrypt hash
api_key_prefix  VARCHAR(10) NULL      -- first 8 chars for identification
created_at      TIMESTAMPTZ NOT NULL
last_login      TIMESTAMPTZ NULL
```

### Roles

| Role | Devices | Rules | Integrations | Analytics | Usage/Billing | Users |
|------|---------|-------|-------------|-----------|---------------|-------|
| `admin` | CRUD | CRUD | CRUD | Read | Read | CRUD |
| `editor` | CRUD | CRUD | CRUD | Read | — | — |
| `viewer` | Read | Read | Read | Read | — | — |

### API Endpoints

```
POST   /users                    — create user (admin only)
GET    /users                    — list users (admin only)
GET    /users/{id}               — get user
PATCH  /users/{id}               — update role/status (admin only)
DELETE /users/{id}               — deactivate user (admin only)
POST   /users/{id}/api-key       — generate API key (returns key once, stores hash)
DELETE /users/{id}/api-key       — revoke API key
```

All endpoints tenant-scoped.

---

## 3. API Key Authentication (G2)

### Key Format

```
tp_{tenant_slug}_{32_random_alphanumeric}
```

Example: `tp_acme-corp_a8f3k2m9x4b7n1p5q6r8s0t2u3v4w5y7`

### Auth Flow

```
1. Client sends: Authorization: Bearer tp_acme-corp_a8f3k2m9x4...
2. Server extracts prefix: tp_acme-corp_a8f3k2m9
3. Looks up user by api_key_prefix + tenant slug
4. Verifies full key against api_key_hash (bcrypt)
5. Returns Tenant + User + Role
```

### Changes to Auth Dependency

```python
async def get_current_user(
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Security(api_key_header),
    session: AsyncSession = Depends(get_session),
) -> User:
    # Try Bearer token first (API key)
    if authorization and authorization.startswith("Bearer "):
        return await _auth_api_key(authorization[7:], session)
    # Fall back to X-Tenant-ID header (backward compat, viewer role)
    if x_tenant_id:
        return await _auth_tenant_header(x_tenant_id, session)
    raise HTTPException(401, "Authentication required")
```

### Backward Compatibility

- `X-Tenant-ID` header continues to work → creates a virtual viewer-role user
- API key auth is the preferred path for new integrations
- Existing MQTT subscriber uses tenant_id from topic (no change)

---

## 4. Role Enforcement

### FastAPI Dependency

```python
def require_role(*roles: str):
    async def check(user: User = Depends(get_current_user)):
        if user.role not in roles:
            raise HTTPException(403, "Insufficient permissions")
        return user
    return check

# Usage in routes:
@router.post("/rules")
async def create_rule(
    body: RuleCreate,
    user: User = Depends(require_role("admin", "editor")),
    session: AsyncSession = Depends(get_session),
) -> RuleResponse:
    ...
```

### Route Permissions

| Route | Required Role |
|-------|--------------|
| `GET` (any read) | viewer, editor, admin |
| `POST /device-registry` | editor, admin |
| `POST /rules`, `PATCH /rules` | editor, admin |
| `POST /integrations` | editor, admin |
| `DELETE` (any) | admin |
| `GET /admin/usage` | admin |
| `GET /admin/audit-logs` | admin |
| `POST /users`, `PATCH /users` | admin |

---

## 5. Device Provisioning (G4)

### Self-Registration Flow

```
1. Device knows: tenant provisioning key + its own device_type
2. Device sends: POST /devices/provision
   Body: { "name": "Reader-42", "device_type": "rfid_reader" }
   Header: X-Provisioning-Key: pk_acme-corp_...
3. Server validates provisioning key → creates device with status="pending"
4. Admin approves: POST /device-registry/{id}/approve
5. Device polls: GET /devices/provision/status?device_name=Reader-42
   → Returns "pending" or "active"
6. Once active, device connects via MQTT with its device_id
```

### Provisioning Key

One provisioning key per tenant, stored in `tenants` table:

```sql
ALTER TABLE tenants ADD COLUMN provisioning_key_hash VARCHAR(255) NULL;
ALTER TABLE tenants ADD COLUMN provisioning_key_prefix VARCHAR(10) NULL;
```

Key format: `pk_{tenant_slug}_{32_random}`

### API Endpoints

```
POST  /devices/provision           — self-register (provisioning key auth)
GET   /devices/provision/status    — check registration status (provisioning key auth)
POST  /device-registry/{id}/approve  — approve pending device (admin only)
POST  /device-registry/{id}/reject   — reject pending device (admin only)
```

### Device Status Flow

```
provision → pending → approve → active
                   → reject  → rejected
```

---

## 6. Data Model Summary

### Migration

```sql
-- Users table
CREATE TABLE users (
    id UUID PK,
    tenant_id UUID FK → tenants.id NOT NULL,
    email VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'viewer',
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    api_key_hash VARCHAR(255) NULL,
    api_key_prefix VARCHAR(10) NULL,
    created_at TIMESTAMPTZ NOT NULL,
    last_login TIMESTAMPTZ NULL,
    UNIQUE(tenant_id, email)
);

-- Provisioning key on tenants
ALTER TABLE tenants ADD COLUMN provisioning_key_hash VARCHAR(255) NULL;
ALTER TABLE tenants ADD COLUMN provisioning_key_prefix VARCHAR(10) NULL;

-- RLS
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_users ON users
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid);
```

---

## 7. Security Considerations

- API keys stored as **bcrypt hashes** — never plaintext
- Key returned **once** on generation — cannot be retrieved later
- Key prefix stored for lookup efficiency (avoid scanning all hashes)
- Provisioning keys are tenant-scoped — one per tenant
- Rate limiting on `/devices/provision` to prevent brute force (10 req/min)
- Audit log records user creation, role changes, key generation/revocation

---

## 8. Dependencies

```
bcrypt>=4.0     — API key hashing
```

---

## 9. Project Structure

```
src/tagpulse/
  core/
    user_auth.py         # New auth dependency (replaces tenant_auth.py)
    roles.py             # require_role() dependency
  api/routes/
    users.py             # User CRUD + API key management
    provisioning.py      # Device self-registration
  models/
    user_schemas.py      # UserCreate, UserResponse, etc.
migrations/versions/
    014_users.py         # users table + tenant provisioning key
```

---

## 10. Implementation Plan

| Step | Effort | Description |
|------|--------|-------------|
| 1. Users table + migration | Small | DB model, migration, RLS |
| 2. User CRUD API | Medium | Routes, service, schemas |
| 3. API key auth | Medium | bcrypt hashing, auth dependency, backward compat |
| 4. Role enforcement | Small | require_role() dependency, wire into routes |
| 5. Device provisioning | Medium | Provisioning endpoint, approval flow, status polling |
| 6. Audit integration | Small | Log user mutations to audit_logs |
| 7. Tests | Medium | Auth flow, role enforcement, provisioning flow |

---

## 11. Testing Strategy

- Unit tests: bcrypt key verification, role enforcement logic
- Unit tests: provisioning flow (provision → pending → approve → active)
- Unit tests: backward compatibility (X-Tenant-ID still works)
- No E2E browser tests (API-only features)

---

## 12. Open Questions

- Should API keys have expiration? Recommendation: No for v1 — revocation is sufficient.
- Should provisioning use X.509 certificates instead of pre-shared keys? Recommendation: Pre-shared keys for v1. X.509 is enterprise-tier.
- Should we migrate existing X-Tenant-ID auth to require API keys? Recommendation: Keep both — X-Tenant-ID for backward compat, API keys for new integrations. Deprecate X-Tenant-ID in a future release.
