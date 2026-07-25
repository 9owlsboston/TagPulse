# Design: Gateway/device telemetry ingest (I-75YC)

**Sprint:** 79 · **Status:** proposed · **Date:** 2026-07-25
**Related:** [device-token-http-auth.md](device-token-http-auth.md) (I-K6D1, prerequisite),
[ADR-014 Multi-Subject Telemetry Ingest](../adr/014-telemetry-multi-subject-ingest.md),
[subject-scoped-telemetry.md](subject-scoped-telemetry.md)

## Summary

`POST /telemetry/readings/ingest` is gated by `require_role("admin","editor")`, so a
gateway/device principal (the `tpd_` principal added in I-K6D1) cannot relay pre-resolved
subject telemetry — the mobile edge client falls back to an out-of-band tenant API key. This
sprint authorizes **device principals** on that one endpoint, **tenant-scoped**, with
attribution guards. It deliberately does **not** build a per-gateway approved-subject-set
allowlist (greenfield, no existing model to mirror) — that is a possible future follow-up.

## Decision (MVE — scope agreed with maintainer: device **self** only)

1. **Allow device principals on `POST /telemetry/readings/ingest`.** Gate becomes
   `require_role("admin","editor","device")`. All other telemetry endpoints keep
   `get_current_tenant`, which already rejects devices (I-K6D1), so this is surgical — only
   this endpoint opens to devices.

2. **Device principals are restricted to their own device subject.** Every reading from a
   device principal MUST have `subject_kind == "device"` **and** `subject_id ==
   principal.device_id` — else 403. A device may report only *its own* telemetry (health,
   position, battery, …); it may **not** write telemetry attributed to arbitrary
   assets/lots/stock_items/zones. This is stricter than a fixed reader (which can only emit
   tag-reads that the server fans out to *resolved bindings*, never arbitrary subject writes),
   so devices gain no new blast radius. Relaying telemetry for *other* subjects (the "approved
   SET") is deferred to a future per-gateway subject-grant model.

3. **Device batches are clock-validated.** Each reading's `timestamp` must fall inside the
   contract clock window (`check_clock_window`: ≥ now−24h, ≤ now+5min) — the **whole batch is
   rejected 400** if any reading is out of window, *before* any insert or event publish. This
   mirrors the tag-read ingest path and stops a (potentially compromised) device from poisoning
   "latest" telemetry or firing `telemetry.threshold` alerts with far-future/stale rows. The
   admin/editor path keeps its existing no-clock-check behavior (trusted backfill of historical
   telemetry is a legitimate admin use).

4. **Provenance coercion for device principals:** `source` forced to `"external"` and
   `device_id` stamped to `principal.device_id` (in both the persisted row and the published
   `TELEMETRY_RECORDED` event, kept consistent). Humans are unaffected.

## Plan-stage rubber-duck — blockers resolved
1. *Every active device becomes a tenant-wide telemetry writer.* → Decision 2: devices are
   restricted to `subject_kind="device"` + own `device_id`; no arbitrary subject writes.
2. *Device readings bypass clock/backfill protections.* → Decision 3: whole-batch
   `check_clock_window` prevalidation for device principals before any insert/publish.

## Why not the allowlist / broader relay now
The ask said "an approved SET of downstream subjects," but there is **no existing per-subject
authz** to mirror, and (per rubber-duck) even fixed readers cannot write arbitrary subject
telemetry — they emit tag-reads resolved to *approved bindings*. A grant table + management API
is a materially larger feature. The MVE unblocks the gateway reporting **its own** telemetry
safely; broader relay lands with a future subject-grant model.

## Changes

| Area | File | Change |
|---|---|---|
| Ingest gate + enforce | `api/routes/telemetry.py` | `ingest_telemetry_readings` role gate `admin,editor` → `admin,editor,device`; module helper `_enforce_device_telemetry` (self-subject + whole-batch `check_clock_window`) run before the loop; per-reading `source`/`device_id` coercion for device principals |
| Tests | `tests/unit/` | device own-device reading ok (source/device_id coerced); foreign device_id 403; non-device subject_kind 403; out-of-window batch 400; human unaffected; device not 403 on the endpoint |
| Contract | `openapi.json` | regenerate |
| Docs | ADR-014 note, this doc, CHANGELOG, roadmap §79 | endpoint no longer strictly admin-only |

## Security notes
- Opens exactly one endpoint to devices; every other telemetry route stays device-forbidden
  via `get_current_tenant`.
- Device principals cannot spoof another device's identity (self-binding) or forge provenance
  (`source`/`device_id` coerced).
- Tenant isolation unchanged (writes scoped to `principal.tenant_id`).

## Validation
- `make check` green (via `python -m` on this box — see repo test-tooling note).
- New unit tests cover the accept/coerce/reject matrix.
- `openapi.json` regenerated; backend SHA recorded for the mobile client.

## Review attestations
- **Plan-stage rubber-duck:** ran (2 blockers → folded into Decisions 2 & 3: self-subject-only
  + whole-batch clock validation for device principals).
- **Diff-stage code-review:** ran — no blocking issues found (all-or-nothing ordering, UUID
  comparison, coercion consistency across insert+event, per-route gate, clock polarity, no
  device escape all verified).
