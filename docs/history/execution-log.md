# Execution log — TagPulse

Chronological record of **what was executed** against this repo — commands run,
changes made, and how they were verified. Distinct from `CHANGELOG.md` (which
records *content* changes for consumers); this log records **action** — especially
important because AI agents execute on our behalf.

Append newest-last. Preserve dates, commands, and verification notes; use
completed-state language (record what happened, not what to do).

---

<!-- Template (copy per entry):

### YYYY-MM-DD — <short title>

<what was done + why, in a sentence or two>. Verified: <how — command output,
test, diff, byte-identity, etc.>.
-->

### 2026-07-19 — Bootstrapped agentic-workflow surfaces (AGENTS.md + docs contract)

Ran the `dev-env-setup` bootstrap (`bootstrap-copilot-repo.sh` / `bootstrap-repo.sh`,
profile `xs`), then manually merged its `*.toolkit-new` outputs. Filled `AGENTS.md` §1–§3
and migrated the repo-specific contract (naming, process & artifacts, cross-repo workflow,
key docs) out of `.github/copilot-instructions.md` into new AGENTS §8–§11 — leaving
copilot-instructions thin and pointing at `AGENTS.md` as the single source of truth. Merged
`.editorconfig` (kept the Python-centric defaults, added `*.markdown` / PowerShell-CRLF /
`*.go` rules), added an agent-orientation block to `README.md`, and filled the seeded
`docs/current-state.md` (2026-07-19 snapshot). Discarded `CHANGELOG.md.toolkit-new` (the
existing changelog is richer and already Keep-a-Changelog conformant). Verified: no `TODO`
placeholders remain in `README.md` / `AGENTS.md` / `docs/current-state.md`; every cross-doc
link target resolves (`test -f` per link); `.editorconfig` and the seeded `.gitattributes`
agree on the PowerShell-CRLF rule.

### 2026-07-25 — Sprint 78: device-token HTTP auth + provision-time issuance (I-K6D1)

Closed a TagPulse-Mobile backend ask. `get_current_user` now verifies `tpd_` per-device
tokens (prefix lookup → per-candidate SHA-256 verify → device/tenant status gate) and yields
a least-privilege `device` principal (role `device`, `device_id`-bound); non-active device
with a valid token → 403, bad token → 401. `POST /devices/provision` mints + returns the token
once (hashed at rest, inert until admin approval). `get_current_tenant` now rejects device
principals; new `get_ingest_auth` gates only `POST /tag-reads(/batch)`, where devices are bound
to their own `device_id` and cannot `backfill`; `get_console_user` guard added to the four
`ui_config` routes (diff-stage review catch). Verified: `python -m ruff` clean, `python -m mypy
src` clean (144 files), `python -m pytest tests/unit` = 1814 passed / 1 skipped, `openapi.json`
regenerated. Plan-stage rubber-duck (3 blockers → folded in) + diff-stage code-review (1 medium
→ fixed) both ran. NOTE: `make check` on this box needs `python -m ...` — the `~/.local/bin`
pytest/mypy shebang is python3.13 which lacks the editable install (tagpulse lives in 3.11).

### 2026-07-25 — Sprint 79: gateway/device telemetry ingest (I-75YC)

Second TagPulse-Mobile backend ask. `POST /telemetry/readings/ingest` role gate widened
`admin,editor` → `admin,editor,device`; a `_enforce_device_telemetry` helper restricts device
principals to their OWN device subject (`subject_kind="device"` + `subject_id==device_id`, 403
otherwise) and clock-validates the whole batch via `ingestion.clock.check_clock_window` (400 if
any reading out of window) before any insert/publish; `source`→`external` and `device_id`→gateway
coerced (persisted row + published event). Admin/editor unchanged. Per-gateway approved-subject
grants deferred. Verified: `python -m ruff` clean, `python -m mypy src` clean (144 files),
`python -m pytest tests/unit` = 1820 passed / 1 skipped, `openapi.json` regenerated (endpoint
description). Plan-stage rubber-duck (2 blockers → self-subject-only + batch clock check) ran.

### 2026-07-25 — Sprint 80: external_locations subject generalization + device-self endpoint (I-9HQA)

Third TagPulse-Mobile backend ask. Migration 060 adds nullable `subject_kind`/`subject_id` to
`external_locations` (expand-phase safe — migrations run pre-rollout; backfills existing rows to
`subject_kind='asset'`), makes `asset_id` nullable, adds `ix_external_locations_by_subject`;
downgrade deletes `asset_id IS NULL` rows before restoring NOT NULL (data-lossy, documented).
Model/schema/repo generalized additively (generic `insert_for_subject`/`get_latest_for_subject`/
`list_for_subject`; asset methods delegate + keep `asset_id` so the asset UNION/index/RFID path
are untouched). New `POST /device-location` (require_role("device"), `device_id` guarded,
subject fixed to the token's device, `recorded_at` clock-validated) — emits NO event so the asset
`EXTERNAL_LOCATION_RECORDED` webhook contract (dispatcher subscribes to ALL topics) is unchanged.
Verified: `python -m ruff` clean, `python -m mypy src` clean (145 files), `python -m alembic
history` shows 059->060 head, `python -m pytest tests/unit` = 1826 passed / 1 skipped,
`openapi.json` regenerated. Plan-stage rubber-duck (4 blockers → nullable cols, device_id guard,
no device event, data-lossy downgrade) ran. Migration round-trip (make migration-check) runs in CI.

### 2026-07-25 — Sprint 81: per-gateway approved-subject-set grants (C-6S9H)

Completes the deferred relay-scoping from I-75YC/I-9HQA. Migration 061 adds a plain tenant-scoped
`gateway_subject_grants` table (soft-revoke via `revoked_at`, partial-unique index over active
rows, RLS `tenant_isolation_gateway_subject_grants`). New admin API `POST/GET/DELETE
/admin/gateways/{device_id}/subject-grants` (require_role admin, AuditLogger) validates the
gateway device + granted subject exist in-tenant (MVE kinds asset/device via their repos; other
kinds 422). Repo `TimescaleGatewaySubjectGrantRepository` filters every query by explicit
tenant_id (RLS is defense-in-depth). `_enforce_device_telemetry` now allows own device subject OR
an active grant (fetched once/request via get_gateway_grant_repo; device_id guarded to a local
UUID); fails closed. External-location relay for granted subjects deferred. Verified: `python -m
ruff` clean, `python -m mypy src` clean (147 files), `python -m alembic history` 060->061 head,
`python -m pytest tests/unit` = 1836 passed / 1 skipped, `openapi.json` regenerated. Plan-stage
rubber-duck (3 blockers → tenant-filter all methods, subject existence validation, device_id
narrowing) ran. Migration round-trip runs in CI (make migration-check).

### 2026-07-25 — Fix: inventory SGTIN auto-create epc_hex lookup (I-2J9R); I-EHQH already fixed

P0 pair from the backlog. I-2J9R (live bug): IngestionService._process_inventory_read looked up
the tag via get_by_epc(normalize_epc_hex(identity.epc)) — identity.epc is the decoded GS1 URI but
get_by_epc matches hex-keyed tags.epc_hex, so it never matched → every SGTIN auto-create blocked →
Stock Levels empty. Fixed to look up by identity.epc_hex (populated by _normalize for hex reads);
URI-only reads conservatively still block. 2 regression tests (match-by-hex proceeds + asserts the
hex was queried; unregistered hex blocks). I-EHQH (force-delete 500): verified ALREADY FIXED —
delete_stock_item gates on movement_count>0 → StockItemLedgerError → structured 409 before any
delete (ADR-031); only RESTRICT FK is stock_movements (parent is SET NULL); regression test
test_inventory_route_force_delete.py already asserts 409-not-500. Resolved in ledger; both stale
backlog.md entries drained. Verified: python -m ruff clean, python -m mypy src clean (147 files),
python -m pytest tests/unit = 1838 passed / 1 skipped, openapi unchanged.

### 2026-07-25 — Sprint 82: asset display_label + VIN binding lookup (I-P923, TagPulse-Mobile)

Serves TagPulse-Mobile C-RYH7. Migration 062 adds nullable assets.display_label (the plate) and
widens ck_asset_tag_bindings_kind to include 'vin' (downgrade drops 'vin' rows then narrows).
Model/schema/repo carry display_label (AssetCreate/Update/Response; update already clears via
model_dump(exclude_unset)). New binding_kind='vin' is isolated from 'device' — verified 3 SQL
paths (asset_location, consolidation_source, overlapping_zones) treat binding_kind='device' as
tr.tag_id=binding_value, so a VIN there would falsely associate reads; 'vin' matches none of
them. bind_tag canonicalizes 'vin' values (strip+upper); new
TimescaleAssetTagBindingRepository.get_by_binding_value matches raw+canonical, active only,
tenant-scoped, earliest bound_at. New GET /assets/by-binding (declared before /{asset_id} so it
isn't captured as a path param; require_role admin/editor/viewer — handset uses its tp_ key since
tpd_ device principals are console-forbidden per I-K6D1). Verified: python -m ruff clean, python
-m mypy src clean (147 files), python -m alembic history 061->062 head, python -m pytest
tests/unit = 1846 passed / 1 skipped, openapi.json regenerated. Plan-stage rubber-duck (1 blocker
-> 'vin' kind not 'device') ran. Migration round-trip runs in CI.

### 2026-07-25 — CI: gate the Alembic migration round-trip (C-EKF0 / roadmap E3)

Added a migration-check job to .github/workflows/ci.yml: a timescale/timescaledb:latest-pg16
service container + `make migration-check` (upgrade head -> downgrade -1 -> upgrade head via the
Sprint-19 TAGPULSE_INTEGRATION_DB_URL test) on every push/PR, blocking merge on a broken
downgrade. Previously the round-trip only ran manually, so migrations 059-062 shipped without
automated reversibility validation; the `-1` step tests the newest migration (always head in its
introducing PR). Driver: postgresql+asyncpg (alembic env.py builds an async engine); migration
001 CREATE EXTENSION timescaledb runs against the image. Could NOT validate locally (Docker
Desktop WSL integration off in this distro) — the CI job's first run on this PR IS the validation
(it exercises migration 062's downgrade). Roadmap E3 flipped to [done]. Verified: ci.yml parses
as valid YAML (3 jobs). CI-only change.

### 2026-07-25 — Test infra: live-DB integration harness (C-6RTX)

Stood up tests/integration/conftest.py: session-scoped `alembic upgrade head` + function-scoped
async `session` fixture on a fresh create_async_engine(poolclass=NullPool) built inside each
test's loop (avoids cross-event-loop asyncpg errors under asyncio_mode=auto), rolled back at
teardown; make_tenant/category/device/asset factory fixtures. Gated by TAGPULSE_INTEGRATION_DB_URL
(hermetic without it — verified 6 integration tests skip). New `make integration-test` +
`integration-test` CI job (own timescaledb service, itest db). Proof tests backfill Sprint 81
grant CRUD (partial-unique enforcement, soft-revoke+recreate, active_subject_set scoping — split
the duplicate-IntegrityError case into its own test so it can't poison the lifecycle tx) and
Sprint 82 get_by_binding_value (raw+lowercased scan, tenant isolation, unbound exclusion). Could
NOT run locally (Docker Desktop WSL integration off) — the integration-test CI job on the PR is
the validation. Plan-stage rubber-duck (2 blockers: NullPool per-test engine + split duplicate
test) ran. Verified: python -m ruff clean, python -m mypy src clean, python -m pytest tests/unit
= 1846 passed, ci.yml valid (4 jobs). Remaining fake-only paths (asset_q/facets/Transfers/
Reconciliation) logged as a backlog follow-up.

### 2026-07-25 — Sprint 83: /assets/by-binding returns matched binding_kind (I-WAPN)

Enriches I-P923. New AssetByBindingResponse(AssetResponse) adds binding_kind + stored
binding_value (flat, additive). Repo get_by_binding_value switched from select(AssetModel) +
scalars().first() to select(AssetModel, binding_kind, binding_value) + .first() (Row), builds the
subclass via asset.model_dump() + the matched kind/value. Route response_model + service return
type updated. Handset warns when a VIN resolves via a lookup-only 'vin' binding. Verified: python
-m ruff clean, python -m mypy src clean (147 files), python -m pytest tests/unit = 1846 passed,
integration tests skip without DB, openapi.json regenerated (+AssetByBindingResponse schema).
Plan-stage rubber-duck (no blockers) ran. Mobile side re-vendors openapi to pick up binding_kind
+ display_label (CONTRACT.md).

### 2026-07-25 — Fix: floor-position estimator hex-EPC binding fusion (I-KPT3)

TimescaleFloorPositionSource resolved reads to assets by the decoded EPC URI (tag_reads.epc)
only, so a tag bound by the hex form (binding_kind='epc' + hex binding_value, valid per ADR-033/
migration 057) never fused (reads silently dropped). Fix: RawRead carries epc_hex (SELECT adds
the column, default None so existing keyword constructions still work); the fuse candidate list
now includes both r.epc + r.epc_hex; build_floor_observations falls back to epc_to_asset.get(
r.epc_hex) when r.epc misses. Same class as the inventory-gate bug. Gated off
(position_estimator_enabled=false) so no live impact. Updated the module docstring (was 'not
epc_hex'). 1 regression test (asset bound only by hex → resolves via fallback; fails on old code).
Verified: python -m ruff clean, python -m mypy src clean, python -m pytest tests/unit = 1847
passed, openapi unchanged. Drained the backlog note.

### 2026-07-25 — Tests: backfill tag-reads query SQL onto the harness (C-XSD1)

Added make_binding + make_tag_read factories to tests/integration/conftest.py and
tests/integration/test_tag_reads_query_db.py: live-DB tests for the asset_q correlated EXISTS
(bound-asset-name filter, incl. tenant scoping + off-by-default) and GET /tag-reads/facets
(distinct scheme/antenna, sorted). These SQL paths were fake/contract-only (the in-memory fake
can't model the asset_tag_bindings->assets join). Verified: python -m ruff clean, 9 integration
tests skip hermetically without the DB env; the integration-test CI job validates on the PR.
Transfers/Reconciliation filters remain (backlog note updated). Tests only.

### 2026-07-25 — Fix: inventory simulator readers stay online on long runs (C-W7XM)

Reworked scripts/simulate_inventory._build_units so a long --duration no longer idles readers
past the dashboard's 5-min ONLINE_WINDOW (src/tagpulse/core/device_status.py). (1) Per-stage
dwell is capped at _MAX_STAGE_DWELL_S=240s via min(random.uniform(duration*0.10, 0.30), cap) —
the uniform draw is still consumed. (2) Once a unit reaches its final stage it gets resident
heartbeat re-reads every _HEARTBEAT_S=240s until `duration` (appended to its schedule, same
stage → location never flaps, no random draws). Also extracted the serial scheme into a shared
stock_unit_serial(product_idx, unit_idx) helper (single source of truth for external seeders).
Verified: standalone harness over --duration 90/600/1800 → worst consecutive read-gap ≤240s
(was up to 540s at 1800s) and every unit re-reads within one heartbeat of the run end; quarantine
path (coldchain scenario) also heartbeats to end; stock_unit_serial(0,0)=100000, (1,5)=200005.
python -m ruff format --check clean; the 7 pre-existing S311 (non-crypto random) warnings are
outside `make lint`'s scope (src tests clients/pi). scripts/ isn't type-checked/tested in CI.
Drained both 2026-06-13 SIM GAP backlog notes. Dev tooling only — no app/API/schema change.

### 2026-07-25 — Tests: backfill Transfers + Reconciliation wildcard filters (C-4PAD)

Added tests/integration/test_transfer_reconciliation_db.py (6 live-DB tests) + harness factories
make_user/make_tag/make_product/make_stock_item and a tag_known param on make_tag_read
(tests/integration/conftest.py). Covers the last fake/contract-only SQL paths from the Sprint 77
audit: TimescaleTagTransferRepository.list_for_tenant epc_q (ILIKE over epc_hex) + statuses
(status IN (...)) incl. compose + tenant scoping; and tag_reconciliation's three views'
q filter (query_registered_unread over tags.epc_hex, query_unregistered_reading over
tag_reads.tag_id with tag_known=False, query_bindings_on_retired over stock_items.binding_value
joined to terminal-status tags). Verified: python -m ruff check + format clean; wildcard_to_ilike
('E280AA*')=='E280AA%' confirms the * grammar; all 15 integration tests collect + skip
hermetically without TAGPULSE_INTEGRATION_DB_URL; the integration-test CI job validates against a
real TimescaleDB (Docker unavailable locally). mypy scope is `mypy src` so tests aren't gated.
Drained the C-XSD1 follow-up backlog note (no fake-only paths remain). Tests only.

### 2026-07-25 — Feat: gateway grant relay — device-location + more subject kinds (C-4Z66, Sprint 84)

Extended the C-6S9H grant seam (no migration). POST /device-location (device_location.py) now takes
an optional target subject via new DeviceLocationCreate(ExternalLocationCreate) (both-or-neither
model_validator); relay is own-or-granted (active_subject_set, keyed to the authenticated device_id,
403 on miss) mirroring _enforce_device_telemetry. Asset targets pass asset_id=subject_id (asset reads
filter asset_id, not subject_id — plan-stage blocker #1). Relay writes stamp a server-controlled
metadata.relayed_by_device_id via body.model_copy (blocker #3). gateway_grants.py _SUPPORTED_KINDS +=
lot/stock_item/zone with existence checks (TimescaleLot/StockItem/ZoneRepository.get). Fixed the
now-stale zone-422 unit test (zone is supported now) → widget-422 (schema). Plan-stage + diff-stage
rubber-duck both ran (3 plan blockers folded in; diff clean). Verified: python -m ruff clean, python
-m mypy src clean (147 files), python -m pytest tests/unit = 1861 passed/1 skipped, make export-openapi
regenerated (DeviceLocationCreate + /device-location body). Backend-only, no schema change.

### 2026-08-10 — docs-drift: reconcile 7 broken links (incl. an obsolete deploy runbook)

A cross-repo `hygiene` sweep reported 13 `docs-drift` findings. Six were `CHANGELOG.md`
links naming files as they were at their release — correct historical artifacts, now
exempted upstream (`dev-env-setup#152`). The other **7 were real**, and one of them was
hiding a genuinely wrong runbook:

| Finding | Diagnosis | Fix |
|---|---|---|
| `deploy/azure/README.md:137 → ../../scripts/azd-bootstrap-mqtt.sh` | **Obsolete section.** Sprint 23 (`0e996d2`) `git rm`'d that script and replaced Azure Files seeding with a custom Mosquitto image whose entrypoint materialises `mosquitto.passwd` from env vars. The README still told operators to run a 125-line script that no longer exists — plus a "manual fallback" for a storage share the Bicep no longer provisions. | Replaced the whole `## Bootstrap MQTT broker (one-time)` section with an accurate `## MQTT broker (no bootstrap step)`, pointing at `mosquitto.Dockerfile` / `mosquitto-entrypoint.sh` / `azd-mqtt-build.sh`, and recording that Sprint 23 removed the old path. |
| `docs/roadmap.md:1974 → adr/033-epc-dual-form-binding.md` | Plain rename. | Retargeted to `adr/033-epc-binding-resolves-uri-or-hex.md`. |
| `deploy/{aws,gcp}/ui/README.md:4 → ../README.md` | Forward-ref to a file never written — the line already said "(TODO: that README too)". | De-linked to plain code with "(no README there yet)". |
| `docs/design/admin-ui.md:373 → ~/.templates/…` | Home-relative, machine-specific — never resolvable from a checkout. | De-linked to inline code, marked "outside this repo". |
| `docs/roadmap.md:922,955 → refs/ui-crud-audit-sprint28.md` | Never produced; G2 was deferred to the UI repo. Line 955 also asserted as an acceptance criterion that it "exists". | De-linked at 922 (marked planned/never-produced); at 955 dropped the false claim so the criterion covers only the audit that does exist. |

The last one is the reason this is worth doing at all: the drift check surfaced a
**satisfied-looking acceptance criterion that could never have been met**.

**How verified.** `docs-drift` on this repo: 7 → **clean**. Each retarget checked to
resolve on disk; each de-link checked to be genuinely unresolvable (`git log --all` for the
removed script and the never-added audit doc).

**Review attestations.** Plan-stage / diff-stage: **waived** — `noncodefix` carve-out
(documentation only; no code, deps, CI, IaC, or behavioral config touched — the Bicep and
scripts referenced were already in their current state).

`current-state: not-affected`
