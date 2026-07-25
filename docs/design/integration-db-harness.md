# Design: Repo-level DB test harness (C-6RTX)

**Sprint:** chore · **Status:** proposed · **Date:** 2026-07-25
**Related:** C-EKF0 (migration-check CI job — proved a TimescaleDB service container works in CI),
`docs/backlog.md` (SQL paths only fake/contract-tested)

## Summary

Repo SQL paths (correlated `EXISTS` filters, `/tag-reads/facets` distinct, binding joins, the
Sprint-81 grant CRUD, the Sprint-82 `get_by_binding_value`) are only covered by **in-memory
fakes** that can't model real SQL (partial-unique indexes, joins, `.in_()`, canonical matching).
The only integration test today is the migration round-trip. This adds a **live-DB integration
harness** — an async session against a real TimescaleDB (schema via `alembic upgrade head`) with
per-test isolation and factory helpers — plus a CI job to run it, and seeds it with real coverage
for the two most recently-added fake-only paths (grants + binding lookup) to prove it.

## Decisions

1. **Real TimescaleDB, schema via `alembic upgrade head`.** The schema uses Postgres/Timescale
   features (JSONB, hypertables, partial-unique indexes, RLS, `CHECK`s) that aiosqlite can't
   build — so tests run against the same `timescale/timescaledb:latest-pg16` image the
   `migration-check` job already uses. A **session-scoped sync fixture** runs `alembic upgrade
   head` **once** (subprocess, `DATABASE_URL=TAGPULSE_INTEGRATION_DB_URL`).

2. **Function-scoped engine with `NullPool` (avoids cross-event-loop asyncpg errors).** With
   `asyncio_mode=auto`, each async test runs on its own event loop; a session-scoped pooled
   `AsyncEngine` would hand out asyncpg connections bound to an earlier loop → "Future attached to
   a different loop". So the `session` fixture is **function-scoped**: it builds a fresh
   `create_async_engine(url, poolclass=NullPool)` **within the test's loop**, opens an
   `AsyncSession`, yields, then **rolls back** and disposes. Cheap for a handful of tests; fully
   loop-safe. Flushed writes are visible in-test (repos call `flush()`, not `commit()`) but never
   persist.

3. **Per-test isolation:** rollback teardown + a fresh `tenant_id` per test (so even a committed
   prerequisite can't collide). Tests connect as the `tagpulse` table-owner → bypass RLS, matching
   the app's HTTP path (explicit `tenant_id` filters, not the GUC).

4. **Factory helpers** in `tests/integration/conftest.py`: `make_tenant`, `make_category`,
   `make_device`, `make_asset` — insert the minimal NOT-NULL columns and return the id, so a test
   builds the prerequisite graph in a few lines.

5. **Gated by `TAGPULSE_INTEGRATION_DB_URL`** (same skip as the round-trip), so `make test` / the
   unit `check` job stay hermetic. A new **`integration-test` CI job** (its **own** TimescaleDB
   service container — separate job = separate services, no cross-job DB sharing) runs
   `make integration-test` = `pytest tests/integration/` **excluding** the round-trip.
   **Required-check bootstrapping:** the job runs green on *this* PR first; only then is
   `integration-test` added to the `main-protection` required checks (the PR's own green run
   satisfies it), then merge — same order that worked for `migration-check`.

## Proof tests (real coverage, not just harness scaffolding)
- **Gateway subject grants (Sprint 81 deferred DB paths):**
  - *lifecycle test:* create → `get_active` → `revoke` (soft) → re-create the same pair now
    succeeds (the revoked row is excluded by the partial-unique `WHERE revoked_at IS NULL`) →
    `active_subject_set` returns the live pair. **No** constraint violation — one clean tx.
  - *duplicate test (separate):* create, then a second active create for the same
    `(tenant,gateway,kind,subject)` raises `IntegrityError` on `flush` (the partial-unique index).
    This is the test's **last** action — the aborted tx is discarded by the fixture rollback; it
    is deliberately split out so it can't poison the lifecycle test's transaction.
- **`get_by_binding_value` (Sprint 82):** bind a `vin` (stored canonical) → resolve by the raw
  **and** lower-cased scan → tenant isolation (another tenant's same value → `None`) → unbound
  binding excluded.

## Changes

| Area | File | Change |
|---|---|---|
| Harness | `tests/integration/conftest.py` (new) | session-scoped engine (alembic upgrade head) + function-scoped rollback session + factories; skip without `TAGPULSE_INTEGRATION_DB_URL` |
| Tests | `tests/integration/test_gateway_grants_db.py`, `test_asset_binding_lookup_db.py` (new) | grant CRUD + binding-lookup against the live DB |
| Make | `Makefile` | `integration-test` target |
| CI | `.github/workflows/ci.yml` | `integration-test` job (TimescaleDB service) + required-check |
| Docs | this doc, CHANGELOG, `AGENTS.md`/CONTRIBUTING pointer, `docs/backlog.md` (drain the harness item) | how to run integration tests locally |

## Non-goals
- Backfilling **every** fake-only path now (asset_q EXISTS, facets, Transfers/Reconciliation).
  The harness makes those cheap follow-ups; this MVE proves it with grants + binding lookup and
  leaves a backlog note for the rest.
- RLS-enforcement tests (app bypasses RLS via owner + explicit filters).

## Validation
- `make check` unaffected (unit-only, hermetic).
- The new `integration-test` CI job runs the harness + proof tests against TimescaleDB (this PR's
  own run is the validation — Docker Desktop WSL integration is off locally).

## Plan-stage rubber-duck — blockers resolved
1. *Session-scoped pooled engine crosses pytest event loops.* → Decision 2: function-scoped
   engine with `NullPool`, built inside each test's loop; session fixture only runs the migration.
2. *Duplicate-grant flow poisons the transaction.* → split into a lifecycle test (no violation)
   and a separate duplicate test whose IntegrityError is its last action (discarded by rollback).

## Review attestations
- **Plan-stage rubber-duck:** ran (2 blockers → NullPool per-test engine + split duplicate test).
- **Diff-stage code-review:** ran — 1 blocker (`make_category` defaulted `category_type="generic"`,
  which the DB CHECK rejects → both binding tests would fail on the live DB — exactly the class of
  bug the harness exists to catch). Fixed to `"object"` (an allowed value). No other issues.
