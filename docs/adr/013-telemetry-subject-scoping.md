# ADR-013: Subject-scoped telemetry — schema rename + back-compat view

- Status: superseded by [ADR-014](014-telemetry-multi-subject-ingest.md) + [ADR-015](015-telemetry-rules-and-deprecation.md); back-compat view + legacy hypertable dropped in Sprint 21 ([migration 032](../../migrations/versions/032_drop_legacy_device_telemetry.py))
- Date: 2026-05-05
- Supersedes: none
- Related: [ADR-003 TimescaleDB storage](003-timescaledb-storage.md), [ADR-014 Multi-subject telemetry ingest](014-telemetry-multi-subject-ingest.md), [ADR-015 Telemetry rules + sunset](015-telemetry-rules-and-deprecation.md), [docs/design/subject-scoped-telemetry.md](../design/subject-scoped-telemetry.md), [docs/design/telemetry-and-location.md](../design/telemetry-and-location.md)

> **Update (May 2026):** the original Sprint 20 deprecation sunset (drop the back-compat view, the legacy hypertable, and `TimescaleTelemetryRepository`) **deferred to Sprint 21** — see [ADR-015 §6](015-telemetry-rules-and-deprecation.md). The trigger is the slowest tenant's `telemetry_retention_days` cycling past the Sprint 18 cutover. The Sprint 20 references below describe the original plan; substitute "Sprint 21 (gated)" wherever this ADR mentions Sprint 20 dropping things.

> **Update (Sprint 21 — May 2026):** sunset shipped. `TimescaleTelemetryRepository` and `DeviceTelemetryModel` are removed; the device-shaped surface lives on `TimescaleTelemetryReadingsRepository` alongside the subject-aware surface. The `device_telemetry` view + `telemetry_readings_legacy_device` hypertable + `tenant_isolation_device_telemetry` RLS policy + `ix_device_telemetry_lookup` index are dropped by [migration 032](../../migrations/versions/032_drop_legacy_device_telemetry.py). The `GET /telemetry-models/{device_type}` 301 redirect becomes `410 Gone` with a migration hint.

## Context

Sprint 14 (ADR-003) shipped `device_telemetry`, a TimescaleDB hypertable keyed on `(tenant_id, device_id, timestamp, metric_name)`. That schema assumes telemetry is *about* the device that reported it — fine for reader CPU temperature or radio noise floor, useless for cold-chain milk: a temperature logger riding a pallet reports a reading whose semantically interesting subject is the **lot** (or the **asset** wrapping the pallet), not the gateway that uplinked it.

Sprint 15 added `subject_kind`/`subject_id` to `subject_current_zone` for the same reason on the location side. Sprint 18 brings telemetry in line: every reading carries an explicit subject so cold-chain queries (and the rules/alerts that watch them) target the lot directly.

Two design constraints made the change non-trivial:

1. **Existing Sprint 14 callers must keep working.** The telemetry ingest service, its tests, Grafana dashboards, and any operator psql session that selects from `device_telemetry` cannot be expected to change in lockstep.
2. **The hypertable already holds production data** in deployed environments. We can't drop and recreate; we have to migrate in-place.

## Decision

**Three-part schema change** (migration `030_subject_scoped_telemetry.py`):

1. **Create `telemetry_readings`** — a new hypertable with the columns Sprint 14 had plus `subject_kind VARCHAR(32)`, `subject_id UUID NOT NULL`, `source VARCHAR(20)`, and a nullable `device_id` (still populated for `source='device'` rows). Indexed on `(tenant_id, subject_kind, subject_id, metric_name, timestamp DESC)` for the hot path and on `(tenant_id, device_id, …)` partially for the legacy "all readings from this reader" query. RLS policy mirrors `device_telemetry`.

2. **Rename, don't drop.** `device_telemetry` is renamed to `telemetry_readings_legacy_device` and back-filled into `telemetry_readings` with `subject_kind='device'`, `subject_id=device_id`, `source='device'`. The renamed table keeps its data, indexes, and policies attached by OID; cosmetic name mismatches are acceptable for a read-only legacy table that will be dropped in Sprint 20.

3. **Recreate `device_telemetry` as a SQL view** over `telemetry_readings WHERE subject_kind='device'`. The view is read-only by design — Grafana dashboards, ad-hoc psql queries, and any other Sprint 14-era consumer keeps working without code changes. Application writes go through the new repository, never through the view.

**Repository layer split** in `tagpulse.repositories.timescaledb.telemetry`:

- `TimescaleTelemetryRepository` (Sprint 14) keeps its public contract (`insert_reading` / `query` / `quarantine` / `list_quarantine`) but internally writes to and reads from `telemetry_readings` with `subject_kind='device'` filter. Marked `@deprecated:: Sprint 18` in the docstring.
- `TimescaleTelemetryReadingsRepository` (new) is the subject-aware surface used by Sprint 19 multi-subject ingest and the Sprint 20 rules engine.

`telemetry_models` and `telemetry_quarantine` get matching `subject_kind` columns (defaulting to `'device'` and `NULL` respectively for back-compat).

## Why not just drop and re-create?

Considered. Rejected because:

- TimescaleDB hypertables hold compressed chunks; dropping and back-filling means re-compressing, which is slow and costs disk during the migration window.
- We'd lose the option to roll back without restoring from backup. With the rename approach, downgrade copies any post-upgrade `subject_kind='device'` rows back into the legacy table and renames it back — a clean round-trip CI exercises.
- External consumers (Grafana, Datadog forwarders, operator scripts) can't be enumerated and updated atomically with the migration. The view bridges the deprecation window.

## Why a SQL view, not a SQLAlchemy facade?

A view gives every consumer back-compat — including the 80% that don't go through SQLAlchemy. The cost is that the view is read-only (Postgres won't auto-INSERT into a view whose rows would violate the underlying NOT NULL constraints on `subject_id` / `source`). That's fine: writes happen exclusively in the application, which we control; reads happen everywhere, which we don't.

## Why keep `DeviceTelemetryModel`?

It maps to the view, so SELECTs through the legacy SQLAlchemy model still work. Any Sprint 14 service that hadn't been refactored yet keeps reading. We mark it `@deprecated:: Sprint 18` so new code converges on the new model.

## Trade-offs accepted

- **Two repository classes for a deprecation window.** Sprint 20 will drop `TimescaleTelemetryRepository` and the back-compat view together; until then both surfaces are maintained.
- **Back-fill is non-incremental.** The migration copies every legacy row in one transaction. Acceptable at current data volumes (tens of millions of rows in the largest tenant); if we cross a size threshold before Sprint 20, we'll switch the back-fill to a chunked batch script invoked outside the migration.
- **`telemetry_quarantine.subject_kind` is nullable** so back-fill doesn't have to invent values. Sprint 19 ingest will populate it on every new row; Sprint 20 will tighten the column to NOT NULL after the legacy NULLs age out.

## Rollout

- Sprint 18 (this ADR): schema + repo split + back-compat view, **no behaviour change**.
- Sprint 19: multi-subject ingest writes additional `subject_kind='asset' / 'lot' / …` rows; HTTP/MQTT contracts gain optional subject hints; new `/telemetry-readings` API.
- Sprint 20: rules engine and UI consume subject-scoped telemetry; `device_telemetry` view + `TimescaleTelemetryRepository` + `telemetry_readings_legacy_device` table all dropped after a one-release deprecation notice.

## Verification

- Every Sprint 14 telemetry test under `tests/unit/test_telemetry_*` passes unmodified — see Sprint 18 acceptance criteria in `docs/design/subject-scoped-telemetry.md` §11.
- `make check` round-trips migration 030 (`alembic upgrade head` → `alembic downgrade -1` → `alembic upgrade head`) on a populated TimescaleDB instance.
- A read-through test asserts that legacy `SELECT * FROM device_telemetry` returns the same row count after upgrade as before.
