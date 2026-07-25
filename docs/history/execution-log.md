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
