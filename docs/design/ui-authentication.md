# Design Document: UI Authentication & Session Management (Sprint 13)

**Date:** 2026-04-26
**Status:** proposed
**Related:** [Identity & Device Provisioning](identity-device-provisioning.md) (Sprint 12), [Admin UI](admin-ui.md) (Sprint 9)

---

## 1. Problem Statement

The UI currently authenticates via a bare tenant UUID (`X-Tenant-ID` header), which grants **viewer-only** access to every user. Three gaps:

1. **No user identity in the UI** — all UI sessions are anonymous viewers. Admins and editors must use `curl` with API keys for write operations.
2. **No session management** — the tenant ID is stored in localStorage indefinitely with no expiry.
3. **No role-aware UI** — all users see identical UI regardless of role. Buttons for create/edit/delete are visible but return 403 errors for viewers.

---

## 2. Goals

- Users log in with **email + API key** and get full role-based access in the UI.
- Backward-compatible: tenant-ID-only login still works (viewer access).
- Sessions expire and can be revoked.
- UI adapts to the user's role — hide actions the user cannot perform.

---

## 3. Authentication Flow

### 3.1 Login Page

Replace the current single-field tenant gate with a two-mode login:

```
┌─────────────────────────────────────┐
│            TagPulse                 │
│                                     │
│  [Tab: API Key]  [Tab: Tenant ID]   │
│                                     │
│  ┌─ API Key Login ────────────────┐ │
│  │ Email:    [________________]   │ │
│  │ API Key:  [________________]   │ │
│  │                                │ │
│  │         [Sign In]              │ │
│  └────────────────────────────────┘ │
│                                     │
│  ┌─ Tenant ID (read-only) ───────┐ │
│  │ Tenant ID: [_______________]   │ │
│  │                                │ │
│  │         [Continue]             │ │
│  └────────────────────────────────┘ │
└─────────────────────────────────────┘
```

- **API Key tab** (default): email + API key → full role-based session.
- **Tenant ID tab**: existing flow → viewer-only session.

### 3.2 Backend: Session Token Endpoint

New endpoint to exchange an API key for a short-lived JWT:

```
POST /auth/login
Content-Type: application/json

{
  "email": "admin@example.com",
  "api_key": "tp_test-corp_50df825898d762502abc8b9fc40fd3ce"
}
```

Response (200):

```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 3600,
  "user": {
    "id": "e91e853d-...",
    "email": "admin@example.com",
    "name": "Admin",
    "role": "admin",
    "tenant_id": "11111111-...",
    "tenant_name": "Test Corp"
  }
}
```

Errors:
- `401` — invalid email or API key
- `403` — user is deactivated

### 3.3 JWT Token Design

| Field | Value |
|-------|-------|
| `sub` | user UUID |
| `tid` | tenant UUID |
| `role` | `admin` / `editor` / `viewer` |
| `exp` | issued_at + 1 hour |
| `iss` | `tagpulse` |

Signing: HS256 with server-side secret (`JWT_SECRET` env var).

No refresh tokens in v1 — when the token expires, the user logs in again.

### 3.4 Backend: Auth Middleware Update

Update `get_current_user` to accept three methods (priority order):

1. **Bearer JWT** — decode token, extract user ID + role + tenant ID.
2. **Bearer API key** — existing `tp_` prefix lookup (unchanged).
3. **X-Tenant-ID header** — existing backward-compat viewer fallback (unchanged).

```python
async def get_current_user(request: Request) -> AuthenticatedUser:
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if token.startswith("tp_"):
            return await _authenticate_api_key(token, session)
        else:
            return _authenticate_jwt(token)

    tenant_id = request.headers.get("X-Tenant-ID")
    if tenant_id:
        return await _authenticate_tenant_header(tenant_id, session)

    raise HTTPException(status_code=401, detail="Authentication required")
```

---

## 4. Frontend Changes

### 4.1 Auth Context

Expand the existing `useAuth()` context:

```typescript
interface AuthState {
  // Current state
  tenantId: string | null;

  // New fields
  user: User | null;              // null for tenant-ID-only sessions
  role: 'admin' | 'editor' | 'viewer';
  accessToken: string | null;     // JWT token
  isAuthenticated: boolean;       // true if logged in (either mode)

  // Methods
  loginWithApiKey: (email: string, apiKey: string) => Promise<void>;
  loginWithTenantId: (tenantId: string) => void;  // existing flow
  logout: () => void;
}
```

### 4.2 API Client Update

Inject the JWT (or fall back to `X-Tenant-ID`) in all API requests:

```typescript
function getAuthHeaders(): Record<string, string> {
  const { accessToken, tenantId } = useAuth();
  if (accessToken) {
    return { Authorization: `Bearer ${accessToken}` };
  }
  return { 'X-Tenant-ID': tenantId ?? '' };
}
```

### 4.3 Token Storage & Expiry

- Store JWT in `sessionStorage` (cleared on tab close).
- Store tenant ID in `localStorage` (persists across sessions, existing behavior).
- On app load, check JWT expiry — if expired, clear and redirect to login.

### 4.4 Role-Aware UI

Use a `<RoleGuard>` component and a `useCanPerform()` hook:

```typescript
// Hide elements the user cannot access
<RoleGuard roles={['admin', 'editor']}>
  <Button onClick={handleCreate}>Register Device</Button>
</RoleGuard>

// Programmatic check
const canEdit = useCanPerform('editor');
```

**Visibility rules:**

| Element | viewer | editor | admin |
|---------|--------|--------|-------|
| Dashboard widgets | yes | yes | yes |
| Device list / detail | yes | yes | yes |
| Register / edit device | hidden | yes | yes |
| Decommission device | hidden | hidden | yes |
| Create / edit rule | hidden | yes | yes |
| Delete rule | hidden | hidden | yes |
| Create integration | hidden | yes | yes |
| Delete integration | hidden | hidden | yes |
| Create telemetry model | hidden | yes | yes |
| Usage dashboard | hidden | hidden | yes |
| User management | hidden | hidden | yes |

### 4.5 User Profile in Header

Replace the plain "Tenant: {id}" text with:

```
Logged in as: Admin (admin@example.com) · admin · Test Corp   [Logout]
```

For tenant-ID-only sessions:

```
Tenant: 11111111-... · viewer (read-only)   [Logout]
```

---

## 5. Data Model Changes

No new tables required. Add one column to `users`:

```sql
ALTER TABLE users ADD COLUMN last_login TIMESTAMPTZ NULL;
```

Updated on each successful `POST /auth/login`.

---

## 6. Configuration

New environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `JWT_SECRET` | (required) | HMAC signing key for JWTs |
| `JWT_EXPIRY_SECONDS` | `3600` | Token lifetime (1 hour) |

For local dev, add to `docker-compose.yml`:

```yaml
environment:
  JWT_SECRET: dev-secret-change-in-production
```

---

## 7. Migration Path

1. **Phase 1 (this sprint):** Add login page + JWT endpoint + role-aware UI. Tenant-ID login remains as secondary tab.
2. **Phase 2 (future):** Add password-based auth if needed (email + password, bcrypt hash). Requires `password_hash` column on `users` table.

---

## 8. Security Considerations

- JWT secret must be strong and unique per environment. Reject startup if unset in production.
- API keys are never stored in the browser — only the resulting JWT.
- `sessionStorage` for JWT prevents persistence across tabs (acceptable for v1).
- Rate-limit `POST /auth/login` to prevent brute-force (5 attempts per minute per IP).
- HTTPS required in production — JWTs in cookies must have `Secure` + `SameSite=Strict` flags.

---

## 9. Task Breakdown

| # | Task | Scope | Estimate |
|---|------|-------|----------|
| 1 | `POST /auth/login` endpoint (validate API key, issue JWT) | Backend | S |
| 2 | JWT decode in `get_current_user` middleware | Backend | S |
| 3 | `JWT_SECRET` config + startup validation | Backend | XS |
| 4 | Login page with API Key / Tenant ID tabs | Frontend | M |
| 5 | Auth context expansion (user, role, token) | Frontend | S |
| 6 | API client — inject JWT in headers | Frontend | S |
| 7 | `<RoleGuard>` component + `useCanPerform()` hook | Frontend | S |
| 8 | Apply role guards to all create/edit/delete actions | Frontend | M |
| 9 | Header — user profile display | Frontend | XS |
| 10 | Token expiry handling + redirect | Frontend | S |
| 11 | Rate limiting on login endpoint | Backend | S |
| 12 | Tests — backend auth + frontend role guards | Both | M |
| 13 | User management page — list, create, edit, deactivate users (admin only) | Frontend | L |
| 14 | API key management — generate/revoke from user detail | Frontend | M |
| 15 | Register Device button on device list page | Frontend | XS |
| 16 | `get_current_tenant` delegation to `get_current_user` for JWT compat | Backend | S |

---

## 10. Admin CRUD — User Management UI

### 10.1 Pages & Routes

| Route | Page | Description |
|-------|------|-------------|
| `/admin/users` | `UserList` | Table of all users in tenant (admin only) |
| `/admin/users/new` | `UserCreate` | Form to create a new user |
| `/admin/users/:id` | `UserDetail` | View/edit user, manage API key |

### 10.2 User List Page

**Table columns:** Name, Email, Role (tag), Status (tag), API Key (prefix or "—"), Created, Actions.

**Actions per row:**
- **Edit** — navigate to user detail
- **Deactivate/Reactivate** — toggle user status

**Top bar:** Title "Users" + "Create User" button (admin only).

### 10.3 User Create Form

**Fields:**
- **Email** — required, validated format
- **Name** — required
- **Role** — select: admin, editor, viewer (default: viewer)

**On submit:** `POST /users` → redirect to user list with success message.

### 10.4 User Detail Page

**Sections:**

1. **User Info** — editable name, role selector, status badge.
   - **Save** button → `PATCH /users/{id}`.
   - **Deactivate** / **Reactivate** button → `PATCH /users/{id}` with status change.

2. **API Key Management** — card showing:
   - Current key prefix (e.g., `tp_test-c...`) or "No key generated".
   - **Generate API Key** button → `POST /users/{id}/api-key` → show key once in modal with copy button.
   - **Revoke API Key** button (if key exists) → `DELETE /users/{id}/api-key` with confirmation.

### 10.5 Device Registration Button

Add a "Register Device" button to the device list page header, linking to `/devices/register`. Guarded by `RoleGuard` for editor+ roles.
