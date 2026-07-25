# Design: Device-token verification on HTTP ingest (I-K6D1)

**Sprint:** 78 · **Status:** proposed · **Date:** 2026-07-25
**Related:** [ADR-011 Device Identity Roadmap](../adr/011-device-identity-roadmap.md) (Phase 1),
[identity-device-provisioning.md §5](identity-device-provisioning.md),
[edge-device-contract.md](edge-device-contract.md)

## Summary

Per-device tokens (`tpd_…`) are **minted** (`generate_device_token`, stored hashed on
`devices.token_hash`/`token_prefix`) and **rotatable** (`POST /device-registry/{id}/rotate-token`),
but the HTTP auth dependency never verifies them, and no provisioning step ever delivers a
token to the device. Net effect: an approved handset cannot authenticate `POST /tag-reads`
as a per-device principal. Today the mobile client works around this with an out-of-band
tenant API key (`tp_`), which defeats per-device identity/revocation.

This sprint closes the loop with two changes: (1) verify `tpd_` tokens in `get_current_user`,
producing a device principal, and (2) mint + return the device token once at provision time.

## Problem (cited)

- `src/tagpulse/core/user_auth.py` `get_current_user` handles only JWT, `tp_` API keys, and
  `X-Tenant-ID`. A `tpd_` bearer does **not** start with `tp_` (3rd char is `d`), so it skips
  the API-key branch and is fed to `decode_jwt` → `InvalidTokenError` → 401.
- `src/tagpulse/api/routes/provisioning.py` `provision_device` creates a `pending` device but
  returns no token; only the admin-only `rotate-token` mints one — never handed to the device
  through the provisioning handshake.

## Decisions

1. **Single verification seam = `get_current_user`.** `get_current_tenant` (the `/tag-reads`
   and `/telemetry` gate) delegates to `get_current_user`, so wiring `tpd_` there fixes both
   tenant-scoped and role-gated paths from one point. The new branch is checked **before** the
   JWT/`tp_` branches (unambiguous: `tp_`/`tpd_` are mutually exclusive prefixes).

2. **Device principal = new role `"device"`, and it is confined to ingest.** The device
   `AuthenticatedUser` carries `user_id=None`, `role="device"`, and a new optional `device_id`.
   `get_current_tenant` guards **10 route modules** (queries, analytics, admin, tenant-config,
   tags, branding, telemetry reads, …) — far more than ingest — so a device principal must
   **not** flow through it. Therefore:
   - `get_current_tenant` **rejects** `role="device"` (403). Existing human principals are
     unaffected (devices never authenticated before, so no regression).
   - A new `get_ingest_auth` dependency accepts device **and** human principals and returns
     `IngestAuth(tenant, principal)`. Only the HTTP tag-read ingest routes (`POST /tag-reads`,
     `/tag-reads/batch`) use it. This is the least-privilege ingress for devices.
   - The legacy `POST /telemetry` and `/telemetry/readings/ingest` stay device-forbidden here;
     device telemetry ingest is **I-75YC's** scope.

3. **Issue the token at provision time, once.** `provision_device` mints the token and returns
   it in the response body to the caller that already holds the tenant provisioning key. The
   token is **inert until approval** — `get_current_user` requires `status="active"`. This is
   the only device-authenticated step that can safely deliver a raw secret (we store only the
   hash, so it cannot be re-read later — same copy-once contract as `rotate-token`). `approve`
   stays `204`; the device already holds its token and it activates on approval. An admin can
   still `rotate-token` to revoke/reissue.

4. **Non-active device with a valid token → `403 "Device not active"`** (authenticated but not
   permitted), distinct from a bad token → `401`. The lookup selects candidates by
   `token_prefix` **only**, verifies the full hash per candidate (multiple devices in a tenant
   share the 10-char prefix), and **then** checks status — never pre-filtering to active rows,
   so a valid-but-pending token yields the correct 403 rather than a misleading 401. The handset
   learns approval state from `GET /devices/provision/status`, not the ingest error.

5. **Device principals are bound to their own `device_id` and cannot backfill.** A device
   principal ingesting tag-reads may only submit rows whose payload `device_id` equals the
   authenticated `device_id` (single and every batch item) — else 403. This gives per-device
   attribution integrity, not just revocation. `backfill=true` (rule/analytics suppression,
   meant for admin replay) is rejected 403 for device principals. Multi-reader gateway relay
   (one principal, many downstream device_ids) is deliberately **out of scope** — that is the
   "approved SET of subjects" model owned by **I-75YC**.

### Rejected alternatives
- *Mint at approve, deliver via status-poll.* Requires storing the raw token at rest or a racy
  "mint on first active poll"; both worse than provision-time issue.
- *Give devices an existing role (viewer/editor).* Over-grants; a reader could read/write the
  console API. A dedicated `"device"` role is least-privilege.

## Changes

| Area | File | Change |
|---|---|---|
| Auth model | `core/user_auth.py` | `AuthenticatedUser.device_id` (default `None`); `tpd_` branch in `get_current_user` (prefix lookup → per-candidate hash verify → status check); stamp `device_id` on span |
| Ingest gate | `core/tenant_auth.py` | `get_current_tenant` rejects `role="device"` (403); new `get_ingest_auth → IngestAuth(tenant, principal)`; `enforce_device_ingest()` helper (device_id binding + backfill block) |
| Console guard | `core/user_auth.py` + `api/routes/ui_config.py` | `get_console_user` (rejects devices) applied to the four `ui_config` routes that depend on `get_current_user` directly (diff-stage review catch) |
| Ingest routes | `api/routes/ingestion.py` | `/tag-reads` + `/tag-reads/batch` switch to `get_ingest_auth`; call `enforce_device_ingest` |
| Provisioning | `api/routes/provisioning.py` | `provision_device` mints+returns token once; typed `ProvisionResponse` (additive: keeps `device_id`/`status`/`message`, adds `token`/`token_prefix`) |
| Tests | `tests/unit/` | `tpd_` auth matrix (active ok / bad hash 401 / pending 403 / decommissioned 403 / inactive tenant); device_id binding + backfill 403; `get_current_tenant` rejects device; provision returns token |
| Contract | `openapi.json` | regenerate (`make export-openapi`) |
| Docs | ADR-011, this doc, CHANGELOG, roadmap §78 | note HTTP verification + provision-time issue |

## Plan-stage rubber-duck — blockers resolved

1. *Device principals gain broad tenant API access via `get_current_tenant`.* → Decision 2:
   `get_current_tenant` now rejects `role="device"`; devices reach only `get_ingest_auth`-gated
   tag-read ingest.
2. *Authenticated identity not bound to payload `device_id`.* → Decision 5: device principals
   bound to their own `device_id` on single + batch ingest.
3. *Devices can bypass rules via `backfill=true`.* → Decision 5: `backfill` rejected 403 for
   device principals.

## Security notes
- Token hashing/lookup reuses the vetted API-key pipeline (SHA-256, prefix index, per-candidate
  hash compare). No plaintext at rest; copy-once reveal.
- Least privilege: `"device"` role reaches only tenant-scoped ingest.
- Revocation unchanged: `rotate-token` invalidates the prior hash immediately.

## Validation
- `make check` green (ruff + mypy --strict + pytest).
- New unit tests cover accept/reject matrix above.
- `openapi.json` regenerated; backend SHA recorded for the mobile client.

## Review attestations
- **Plan-stage rubber-duck:** ran (3 blockers raised, all folded into Decisions 2, 4, 5 above).
- **Diff-stage code-review:** ran — 1 medium finding (`GET /ui-config` newly reachable by device
  principals, since it depends on `get_current_user` directly). Fixed via `get_console_user`
  guard on all four `ui_config` routes; test added. No other blocking issues.
