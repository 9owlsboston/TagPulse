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
