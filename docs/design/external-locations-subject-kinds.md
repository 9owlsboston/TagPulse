# Design: Generalize external_locations to all subject_kinds + device-self endpoint (I-9HQA)

**Sprint:** 80 · **Status:** proposed · **Date:** 2026-07-25
**Related:** migration 019 (`external_locations`),
[device-token-http-auth.md](device-token-http-auth.md) (I-K6D1),
[gateway-telemetry-ingest.md](gateway-telemetry-ingest.md) (I-75YC),
[mobile-carriers-and-manifests.md](mobile-carriers-and-manifests.md)

## Summary

`external_locations` (migration 019) is **asset-scoped**: `asset_id NOT NULL`, endpoints are
`POST/GET /assets/{id}/external-position(s)`, and it feeds the asset current-location UNION.
The mobile edge gateway needs to stamp a position on **non-asset** subjects (starting with its
own `device`). This sprint generalizes the storage to `(subject_kind, subject_id)` **additively**
(asset rows keep working, RFID/tag path untouched) and adds a **device-self** location endpoint
(mirrors I-75YC: a device may stamp only its own `device` location, clock-validated).

## Decisions

1. **Additive `(subject_kind, subject_id)` columns, kept NULLABLE (expand-phase safe);
   `asset_id` retained but nullable.** Migration 060: add `subject_kind VARCHAR(16)` +
   `subject_id UUID` **nullable**, **backfill** (`subject_kind='asset', subject_id=asset_id`) for
   every existing row, and **drop `asset_id`'s NOT NULL**. The columns stay nullable — migrations
   run **pre-rollout** (`deploy/common/migrations-job.yaml`) while old API code is still live, so a
   `NOT NULL` column the old code doesn't populate would break every legacy insert. New code always
   sets the subject fields; a later contract migration can add `NOT NULL` once all writers do.
   Asset rows keep `asset_id` (=`subject_id`) so the existing asset queries (`asset_location.py`
   raw SQL `WHERE asset_id=…`, the current-location UNION) and `ix_external_locations_by_asset`
   are **unaffected**. New index `ix_external_locations_by_subject (tenant_id, subject_kind,
   subject_id, recorded_at DESC)`.

2. **Repository stays backward compatible.** The `insert` shim delegates to
   `insert_for_subject` (sets `subject_kind='asset'`, keeps `asset_id`). The **read** shims
   `get_latest_for_asset`/`list_for_asset` keep filtering on **`asset_id`** (not the subject
   columns) so any expand-window row written by pre-060 code — `asset_id` set but `subject_id`
   still NULL — stays visible (the one-shot backfill only touches rows existing at migration
   time). New generic `insert_for_subject`/`get_latest_for_subject`/`list_for_subject` serve
   non-asset subjects (rows with `asset_id=NULL`).

3. **`ExternalLocationResponse` gains `subject_kind` + `subject_id`; `asset_id` becomes
   `UUID | None`.** `ExternalLocationCreate` is unchanged (position fields only — the subject is
   fixed by the route/principal, never client-chosen for the device-self endpoint).

4. **Device-self endpoint `POST /device-location`** (new small router), gated
   `require_role("device")`. It **guards `principal.device_id is not None`** (403 otherwise —
   also narrows the `UUID | None` type for mypy), then stamps `subject_kind='device'`,
   `subject_id=device_id`, `tenant_id=principal.tenant_id`; **clock-validates `recorded_at`**
   (`check_clock_window`, 400 if out of window). A device can stamp only *its own* location.
   `require_role("device")` excludes admin/editor/viewer (correct — they have no device identity).

5. **No event from the device-self endpoint; the asset event is untouched.** The webhook
   dispatcher subscribes to **every** `Topic` (`main.py`: `for topic in Topic: subscribe(...)`),
   so `EXTERNAL_LOCATION_RECORDED` **does** have a live consumer — generalizing its payload (or
   emitting a device row without `asset_id` under the asset topic) risks breaking webhook
   consumers. Therefore the asset producer (`asset_service.record_external_position`) and its
   event payload are left **exactly as-is**, and the device-self endpoint emits **no** event in
   this MVE (persist + return only). A dedicated subject-scoped topic can be added later when a
   consumer needs it.

6. **Migration downgrade is data-lossy by design and documented.** `downgrade` drops the new
   index + columns and restores `asset_id NOT NULL`; because non-asset rows have `asset_id=NULL`
   (and `subject_id` can't be copied into `asset_id` — the asset FK expects a real asset), the
   downgrade **`DELETE`s rows where `asset_id IS NULL`** first. No-op on the empty CI round-trip
   DB; on real data it drops device/other-subject positions (acceptable for a rollback).

7. **Arbitration := `accuracy_meters`.** No priority field exists; when multiple external sources
   report for one subject, the smaller `accuracy_meters` wins. Recorded as convention + exposed on
   the generic read; wiring it into multi-source fusion is a consumer concern (no consumer yet).

## Non-goals
- No generic *admin/editor* "record external position for any subject" endpoint (option C) — the
  gateway relay for **other** subjects is deferred (chore C-6S9H).
- No change to the asset current-location UNION or the RFID/tag path.

## Changes

| Area | File | Change |
|---|---|---|
| Migration | `migrations/versions/060_external_locations_subject.py` | add nullable `subject_kind`/`subject_id` (+backfill), `asset_id` nullable, new subject index; downgrade deletes `asset_id IS NULL` rows then restores NOT NULL |
| Model | `models/database.py` | `ExternalLocationModel`: nullable `subject_kind`, `subject_id`; `asset_id` nullable |
| Schemas | `models/schemas.py` | `ExternalLocationResponse`: `subject_kind: str\|None`, `subject_id: UUID\|None`, `asset_id: UUID\|None` |
| Repo | `repositories/timescaledb/external_locations.py` | generic `insert_for_subject`/`get_latest_for_subject`/`list_for_subject`; asset methods delegate (set subject_kind='asset'); `_to_response` carries subject fields |
| Endpoint | `api/routes/device_location.py` (new) + `main.py` | `POST /device-location` (device-only, `device_id` guarded, self-stamped, clock-validated, **no event**) |
| Dep | `api/dependencies.py` | `get_external_location_repo` provider |
| Tests | `tests/unit/` | repo subject round-trip; device-self endpoint (own device ok / clock 400 / non-device role 403 / missing device_id 403); asset path still works; schema shape |
| Contract | `openapi.json` | regenerate |
| Docs | this doc, CHANGELOG, roadmap §80 | generalization + endpoint |

## Plan-stage rubber-duck — blockers resolved
1. *NOT NULL breaks pre-rollout migration.* → Decision 1: subject columns stay **nullable**
   (expand phase); new writers set them, old writers still succeed.
2. *Downgrade fails on non-asset rows.* → Decision 6: downgrade `DELETE`s `asset_id IS NULL`
   rows before restoring `asset_id NOT NULL` (documented data loss).
3. *Event contract break (webhook subscribes to all topics).* → Decision 5: asset event
   untouched; device-self endpoint emits no event.
4. *`device_id` is `UUID | None`, not narrowed by `require_role`.* → Decision 4: endpoint guards
   `device_id is not None` (403) before use.

## Security notes
- Device-self endpoint is device-only and self-stamped — a device cannot write another subject's
  location; `recorded_at` clock-validated (no far-future/stale poisoning). Tenant-scoped.
- Migration is additive + backward compatible; asset RLS policy unchanged (still `tenant_id`).

## Validation
- `make check` green (via `python -m`).
- `alembic history` loads the chain (060 head); migration reviewed for symmetric downgrade
  (round-trip runs in CI `make migration-check` against TimescaleDB).
- New unit tests cover repo generalization + the device-self endpoint matrix.
- `openapi.json` regenerated.

## Review attestations
- **Plan-stage rubber-duck:** ran (4 blockers → folded into Decisions 1, 4, 5, 6:
  nullable columns, device_id guard, no device event, data-lossy documented downgrade).
- **Diff-stage code-review:** ran — 1 medium finding (asset read shims delegated to the
  `subject_id` filter, hiding expand-window rows with NULL `subject_id`). Fixed: the
  `get_latest_for_asset`/`list_for_asset` shims filter on `asset_id`. No other issues.
