# Design: Asset display_label + VIN binding lookup (I-P923)

**Sprint:** 82 · **Status:** proposed · **Date:** 2026-07-25
**Related:** TagPulse-Mobile `C-RYH7` (real-device HIL enrol/bind), `I-P923` backend ask,
[device-token-http-auth.md](device-token-http-auth.md) (I-K6D1 — auth context)

## Summary

TagPulse-Mobile mounts a handset in a vehicle and needs to Map-link a **scanned/keyed VIN** to
the vehicle **asset**, showing the **license plate** for operator confirmation. Two backend
gaps: (1) no public way to resolve a binding value (VIN) → asset; (2) no first-class asset field
for the plate. This sprint adds a **VIN→asset lookup endpoint** and a nullable **`display_label`**
column on assets. The VIN binding itself needs **no** schema change — `binding_kind='device'` is
already allowed and `resolve_asset_refs_by_values` is binding-kind-agnostic.

## Context (verified)
- `asset_tag_bindings.binding_kind` CHECK is `IN ('epc','tid','device')` (migration 018) and the
  `AssetTagBindingCreate` Literal already includes `'device'` — so a vehicle binds via the
  existing `POST /assets/{id}/bindings` with `binding_kind='device'`, `binding_value=<VIN>`.
- `AssetRepository.resolve_asset_refs_by_values` matches `binding_value.in_(...)` with **no**
  kind filter, `unbound_at IS NULL` — a VIN binding already resolves; it's just internal
  (tag-reads "Asset" column). No public lookup endpoint exists (`list_assets` has no binding
  filter; only get-by-id + list-bindings-for-known-asset).
- Device (`tpd_`) principals are rejected by `get_current_tenant` (I-K6D1) and confined to
  ingest. So the handset must call this **read** with its **tenant `tp_` key** (a normal tenant
  principal) — matching the mobile design where the handset holds an out-of-band `tp_` key.

## Decisions

1. **New nullable `assets.display_label VARCHAR(255)`** (migration 062). Additive + nullable
   (expand-safe; migrations run pre-rollout while old code is live, so no NOT NULL). Generic
   secondary human label — for a vehicle it carries the plate; reusable for any asset.
   Settable via `AssetCreate`/`AssetUpdate`, returned in `AssetResponse`. Follows the existing
   `external_ref` optional-string pattern (`None` = not provided on PATCH; MVE doesn't add an
   explicit "clear").

2. **New `GET /assets/by-binding?value=<str>`** → `AssetResponse` (404 if no active binding).
   Gated `require_role("admin","editor","viewer")` (mirrors `GET /assets/{id}`; the handset uses
   its `tp_` tenant key). Resolves the **active** binding (`unbound_at IS NULL`), kind-agnostic,
   to the full asset (incl. `display_label`). Tenant-scoped. If a value has multiple active
   bindings (shouldn't for a VIN), returns the first deterministically (ordered by `bound_at`).
   The handset keys the Map link on the returned `asset.id`; shows `asset.display_label` (plate).

3. **VIN uses a NEW `binding_kind='vin'` (not `'device'`).** `binding_kind='device'` is consumed
   by the telemetry-association SQL (`asset_location.py`, `consolidation_source.py`,
   `overlapping_zones.py`) as `tr.tag_id = binding_value` — storing a VIN there would falsely
   associate any read whose `tag_id` equals the VIN to the vehicle. A distinct `'vin'` kind is
   matched by **none** of those queries, so VIN bindings stay purely a lookup handle. Migration
   062 alters the `ck_asset_tag_bindings_kind` CHECK to `IN ('epc','tid','device','vin')` and the
   `AssetTagBindingCreate` Literal adds `'vin'`. **VIN canonicalization:** a `'vin'` binding value
   is canonicalized (`strip().upper()`) at bind time, and the lookup matches both the raw and
   canonical form so a differently-cased scan still resolves.

## Changes

| Area | File | Change |
|---|---|---|
| Migration | `migrations/versions/062_asset_display_label_vin_kind.py` | add nullable `assets.display_label`; alter `ck_asset_tag_bindings_kind` CHECK to add `'vin'`; symmetric downgrade |
| Model | `models/database.py` | `AssetModel.display_label` |
| Schemas | `models/schemas.py` | `display_label` on `AssetCreate`/`AssetUpdate`/`AssetResponse`; `'vin'` in `AssetTagBindingCreate.binding_kind` Literal |
| Repo | `repositories/timescaledb/assets.py` | persist `display_label` on create/update; new `get_by_binding_value(tenant_id, value) -> AssetResponse \| None` (matches raw + canonical, active only, tenant-scoped) |
| Service | `api/services/asset_service.py` | plumb `display_label`; canonicalize `'vin'` binding value on bind; `get_asset_by_binding_value` |
| Route | `api/routes/assets.py` | `GET /assets/by-binding` (declared before `/{asset_id}`) |
| Tests | `tests/unit/` | create/update/response carry display_label; `'vin'` bind canonicalizes; lookup resolves active binding (raw + cased) → asset / 404 / unbound excluded / tenant-scoped |
| Contract | `openapi.json` | regenerate |
| Docs | this doc, CHANGELOG, roadmap §82, data-models (assets), assets-and-zones | new field + endpoint + `'vin'` kind |

## Plan-stage rubber-duck — blocker resolved
1. *VIN cannot reuse `binding_kind='device'`* (device is a telemetry identity consumed by
   location/consolidation SQL as `tr.tag_id = binding_value`). → Decision 3: new isolated
   `'vin'` kind (CHECK + Literal expanded), with VIN canonicalization on bind + lookup.

## Route-ordering note
`GET /assets/by-binding` must be declared **before** `GET /assets/{asset_id}` or FastAPI would
match `by-binding` as an `asset_id` path param. Place the new route above the `/{asset_id}` route.

## Security notes
- Read is tenant-scoped (`require_role` + explicit `tenant_id` filter in the query) — a caller
  only resolves bindings within its own tenant.
- Migration is additive + nullable; no RLS change (assets RLS unchanged).
- No device-principal exposure: the lookup uses `require_role`, not `get_ingest_auth`.

## Validation
- `make check` green (via `python -m`); `alembic history` 061→062 head.
- New unit tests cover the field round-trip + the lookup matrix.
- `openapi.json` regenerated; backend SHA recorded for the mobile client.

## Review attestations
- **Plan-stage rubber-duck:** ran (1 blocker → folded in: VIN uses a new isolated `'vin'`
  binding kind, not `'device'`, + canonicalization).
- **Diff-stage code-review:** ran — no blocking issues (route ordering, migration round-trip
  symmetry, tenant isolation on the join, `'vin'` isolation from telemetry SQL, canonicalization
  consistency, PATCH clear-semantics, and auth all verified).
