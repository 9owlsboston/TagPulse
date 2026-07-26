# Design: Gateway grant relay — device-location + more subject kinds (C-4Z66)

**Sprint:** 84 · **Status:** proposed · **Date:** 2026-07-25
**Related:** [gateway-subject-grants.md](gateway-subject-grants.md) (C-6S9H, the MVE this extends),
[gateway-telemetry-ingest.md](gateway-telemetry-ingest.md) (I-75YC),
[external-locations-subject-kinds.md](external-locations-subject-kinds.md) (I-9HQA)

## Summary

C-6S9H shipped the per-gateway approved-subject-set grant model, but with two deliberate MVE
cuts. This sprint closes both **fast-follows**, reusing the existing grant seam — **no schema
change, no migration**:

1. **External-location relay.** `POST /device-location` is self-only today (subject fixed to the
   calling device). Extend it to accept an **optional target `(subject_kind, subject_id)`**, gated
   by the **same** `active_subject_set` grant check the telemetry path already uses. A gateway can
   then relay a non-RFID position for a subject it has been granted — the location-side twin of the
   telemetry relay.
2. **Grant subject kinds.** Enable `lot` / `stock_item` / `zone` grants (rejected `422` in the MVE)
   by wiring their in-tenant existence checks into `_assert_subject_exists`. `asset`/`device`
   unchanged.

## Scope

- **In:**
  - `DeviceLocationCreate` request schema (subclass of `ExternalLocationCreate` + optional target
    subject); `POST /device-location` grant-gated relay.
  - `gateway_grants.py` `_SUPPORTED_KINDS` += `lot`/`stock_item`/`zone`, each with an existence
    check (`TimescaleLotRepository`/`TimescaleStockItemRepository`/`TimescaleZoneRepository.get`).
- **Out / unchanged:**
  - `ExternalLocationCreate` and the asset external-position path (`POST /assets/{id}/…`) — the
    subclass keeps the asset path's body identical.
  - The `gateway_subject_grants` table & `GatewaySubjectGrantCreate` schema — its `subject_kind`
    Literal already lists all five kinds and the column has **no CHECK** (migration 061), so
    enabling the kinds is purely route-level.
  - Telemetry ingest enforcement — already kind-agnostic (`(subject_kind, subject_id) in
    grant_set`), so lot/stock_item/zone grants transparently authorize telemetry relay too, which
    is the intended generalization.

## Decisions

1. **Request schema — `DeviceLocationCreate(ExternalLocationCreate)`** adds
   `subject_kind: Literal[...] | None = None` and `subject_id: UUID | None = None`. A model
   validator enforces **both-or-neither** (a lone `subject_kind` or `subject_id` → `422`). Both
   `None` = self (unchanged behavior). Subclassing (not editing `ExternalLocationCreate`) keeps the
   asset external-position body byte-identical.

2. **Enforcement mirrors `_enforce_device_telemetry` exactly.** A device principal may stamp a
   location for a subject iff `own_device OR granted`, where `own_device = (subject_kind=="device"
   AND subject_id==device_id)` and `granted = (subject_kind, subject_id) in
   active_subject_set(tenant_id, device_id)`. Else `403`. The grant set is fetched **once per
   request**, keyed **only** by the authenticated `device_id` (never a request-supplied id). Non-
   device roles keep the existing `403` (endpoint is `require_role("device")`).

3. **Self resolution.** When no target is supplied, the subject stays `('device', device_id)` — the
   exact current path. When the target *is* `('device', device_id)`, it is the same self case
   (`own_device` true), so it is allowed without a grant (consistent with telemetry).

4. **`asset` relay must populate `asset_id` (plan-stage blocker #1).** Every asset-location read
   (`get_latest_for_asset`, `list_for_asset`, and the `asset_location.py` current-location fusion)
   filters `external_locations.asset_id = :asset_id`, **not** `subject_id`. So a relayed asset
   position written via the generic `insert_for_subject` with `asset_id=NULL` would be **invisible**
   to every asset API/view. Therefore, when the resolved `subject_kind == "asset"`, the route
   passes `asset_id=subject_id` — mirroring the existing asset shim (`insert()` →
   `insert_for_subject(..., asset_id=asset_id)`). Non-asset kinds leave `asset_id=NULL` (correct).

5. **Grants are general subject-relay authority, not telemetry-only (plan-stage blocker #2).** This
   is the **explicitly deferred** intent of C-6S9H, whose Scope says: *"Deferred (fast-follow):
   relaying external location for granted subjects via `POST /device-location`."* A grant authorizes
   a gateway to relay **for a subject**; the relay channel (telemetry vs location) is not part of the
   grant's scope. This sprint realizes that deferred capability — it is **not** an accidental
   privilege escalation, and no per-channel grant scoping is introduced (a real YAGNI unless a
   customer needs channel isolation). Recorded here as the explicit contract decision the plan-stage
   review asked for.

6. **Relayed writes stamp server-controlled provenance (plan-stage blocker #3).** `external_locations`
   has no relaying-device *column*, and `source` is legitimately caller-supplied (the sensor
   modality, e.g. `"gps"`). To keep relay writes attributable, the route injects
   `metadata.relayed_by_device_id = str(<authenticated device_id>)` on **relay** writes (target ≠
   self), **overwriting** any client-supplied key of that name (server truth, spoof-proof). Self
   writes are unchanged (the subject *is* the device). A dedicated `relayed_by_device_id` column is a
   larger migration, deferred to the backlog.

7. **No target-existence re-check at location time.** The **grant** already validated the subject
   exists in-tenant at creation (Decision 8). Telemetry relay likewise doesn't re-check. The grant
   is the authority; a stale grant after a subject delete yields a harmless generic row
   (`external_locations.subject_id` has no FK). Documented limitation, not a regression.

8. **Existence checks for the new kinds** use the canonical repos, matching the `asset`/`device`
   pattern: `lot` → `TimescaleLotRepository.get`, `stock_item` → `TimescaleStockItemRepository.get`,
   `zone` → `TimescaleZoneRepository.get`; `404 "Subject <kind> not found"` when absent.

## Changes

| Area | File | Change |
|---|---|---|
| Schema | `models/schemas.py` | new `DeviceLocationCreate(ExternalLocationCreate)` + both-or-neither validator |
| Relay | `api/routes/device_location.py` | accept `DeviceLocationCreate`; add `get_gateway_grant_repo` dep; own-or-granted enforcement; `asset_id=subject_id` for asset targets; stamp `relayed_by_device_id`; `insert_for_subject` with the resolved target |
| Grant kinds | `api/routes/gateway_grants.py` | `_SUPPORTED_KINDS` += `lot`/`stock_item`/`zone`; `_assert_subject_exists` branches (lot/stock_item/zone repos) |
| Tests | `tests/unit/test_device_location.py` | relay: granted-ok / ungranted-403 / explicit-self-ok / both-or-neither-422 / clock-400 still holds |
| Tests | `tests/unit/test_gateway_subject_grants.py` (or route test) | grant create accepts lot/stock_item/zone when the subject exists; 404 when absent |
| Contract | `openapi.json` | regenerate (`make export-openapi`) — new `DeviceLocationCreate` body |
| Docs | this doc, CHANGELOG, roadmap §84, execution-log | new relay behavior + enabled kinds |

## Security notes

- Relay authority is **admin-granted only** and gateway-scoped; a device can never self-grant.
- Fail-closed: no grant → `403`; a revoked grant leaves no active row → `403`; own-device stays
  unconditional (unchanged).
- The grant lookup is keyed to the authenticated `device_id` — a device cannot relay for a subject
  by spoofing another gateway's id.
- Grant set is fetched per-request (no stale-cache authorization).

## Validation

- `make check` green (via `python -m` — pytest/mypy/ruff), targeted at `test_device_location.py` +
  the grants route test.
- New unit tests cover the relay enforcement matrix + the three new grant kinds (exists / 404).
- `openapi.json` regenerated; `alembic history` unchanged (no migration).

## Plan-stage rubber-duck — blockers resolved

Ran (rubber-duck agent). Three blockers, all folded into the plan:
1. *Relayed `asset` locations invisible (asset reads filter `asset_id`, not `subject_id`).* →
   Decision 4: pass `asset_id=subject_id` when `subject_kind=="asset"`.
2. *Grants retroactively privilege-expanded (telemetry → also location).* → Decision 5: this is the
   **documented deferred intent** of C-6S9H; recorded as the explicit "grant = general subject-relay
   authority" contract decision. No per-channel scoping (YAGNI).
3. *Relayed writes have no trustworthy provenance.* → Decision 6: server-stamp
   `metadata.relayed_by_device_id` (spoof-proof, overwrites client key). Dedicated column deferred.

## Review attestations

- **Plan-stage rubber-duck:** ran — 3 blockers resolved (see above).
- **Diff-stage rubber-duck:** ran — no blocking issues (asset_id populated, own-or-granted enforced,
  provenance server-stamped + preserved by `model_copy`, location authz matches telemetry authz,
  security cases covered). 28 targeted tests + full suite (1861) green, mypy-strict clean.
