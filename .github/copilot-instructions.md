# Project: TagPulse

## Overview
TagPulse is an IoT platform that provides device registration/configuration, data ingestion endpoints (MQTT + HTTP), telemetry modeling and monitoring, user-defined rules and alerts, pluggable analytics modules, and data integration/export to external systems — all managed through APIs and an admin UI. First device type: RFID readers sending tag read events with metadata (tag ID, reader ID, timestamp, signal strength, optional sensor data).

## Tech Stack
- Language: Python 3.12
- Framework: FastAPI (async)
- Database: TimescaleDB (PostgreSQL extension)
- MQTT Broker: EMQX or Mosquitto (external)
- Testing: pytest + pytest-asyncio
- Linting: ruff
- Type checking: mypy (strict mode)

## Code Conventions
- Use type hints on all functions (public and private)
- Use Pydantic models for all API request/response schemas and MQTT message parsing
- Use structured logging (Python logging module with JSON formatter), never print()
- Prefer async/await for all I/O operations
- Use dependency injection via FastAPI's Depends() for database sessions and config

## Testing Expectations
- Every PR must include tests for new/changed behavior
- Unit tests go in `tests/unit/`, integration in `tests/integration/`
- Use fixtures for shared test setup, no global state
- Mock external dependencies (MQTT broker, database) in unit tests
- Run `make test` to validate

## Naming
- Files: snake_case
- Classes: PascalCase
- Functions/variables: snake_case
- API routes: kebab-case (`/tag-reads`, `/device-registry`)
- MQTT topics: slash-separated (`devices/{device_id}/tag-reads`)

## Do NOT
- Do not add dependencies without updating pyproject.toml
- Do not use wildcard imports
- Do not commit .env files or secrets
- Do not catch bare exceptions — catch specific exception types
- Do not put business logic in API route handlers — delegate to service functions
- Do not import from `tests/` in `src/`

## Process & Artifacts
- **Starting a new sprint:** run `scripts/start-sprint.sh <NN> <topic-slug> ["PR title"]` from a clean `main`. This is the canonical workflow — it enforces branch naming (`sprint-NN/topic-slug`) and creates the draft PR with the standard checklist. Do not branch + open PRs manually.
- **Planning artifacts (ADRs, design docs, roadmap edits) belong on the sprint kickoff branch, not on `main`.** Create the branch first, then add planning commits to it. If you already started planning on `main`, use `scripts/start-sprint.sh --carry <NN> <topic-slug>` — it stashes the WIP, branches, pops, and commits.
- Before starting work, check `docs/roadmap.md` to confirm the task is in-scope
- Every PR must update `CHANGELOG.md` under an `## Unreleased` section
- When making a non-obvious technical decision, create an ADR in `docs/adr/`
- When changing system boundaries or adding a service, update `docs/architecture.md`
- When a change touches 3+ components, write a design doc in `docs/design/` first
- Follow `CONTRIBUTING.md` for branch naming, commit format, and PR expectations
- Run `make check` before marking work complete

## Key Docs
- Architecture: docs/architecture.md
- ADRs: docs/adr/README.md
- IoT Reference: IoT.md

## Cross-Repo Workflow

TagPulse ships as **two repos**: this backend (`TagPulse`) and the React SPA
(`TagPulse-UI` at `$TAGPULSE_UI_PATH`, default `~/ws/TagPulse-UI`). The
backend owns the product roadmap; the UI consumes the OpenAPI contract.
Both repos share sprint numbers but ship independent PRs.

### One product, two deliverables, one roadmap
- `docs/roadmap.md` (this repo) is the **single source of truth** for
  planning. UI-only items are listed here too, tagged `[UI]`.
- `TagPulse-UI` has no roadmap. It has its own `CHANGELOG.md` for release
  notes and its own `.github/copilot-instructions.md` mirroring this
  section.
- Sprint numbers are **shared**. "Sprint 54" means the same theme in both
  repos. Each participating repo gets a `sprint-NN/<topic>` branch.

### Three work shapes — route each differently
| Shape | What | Branch |
|---|---|---|
| **Sprint** | Themed multi-day effort tracked in `docs/roadmap.md` | `sprint-NN/<topic>` (use `scripts/start-sprint.sh`) |
| **Chore** | Standalone tooling/cleanup, ≤ ~half day, no roadmap impact | `chore/<topic>` (manual branch) |
| **In-flight follow-up** | Mid-sprint discovery that the other repo is missing a piece | `sprint-NN/<topic>-<repo>-followup` (small focused PR) |

The in-flight follow-up shape is the key. When mid-sprint you discover the
other repo needs a change, **don't derail the active branch**: commit/stash
what you have, switch repos, ship a small focused follow-up PR with the
same sprint number, then resume.

### OpenAPI is the contract handoff
- Any backend change that touches the API regenerates `openapi.json` as
  part of the same PR.
- UI PRs that consume new API record the backend commit SHA the
  `openapi.json` was regenerated against in the PR description.
- Merge order when both repos are involved: **backend first** (so the
  contract is live), UI rebases onto the updated `openapi.json`.

### Sprint kickoff — declare cross-repo plan upfront
`scripts/start-sprint.sh` injects a `## Cross-repo plan` section into the
draft PR body. Fill it in even when the answer is "backend only" or
"UI TBD pending backend exploration" — explicit beats implicit.

Use `scripts/start-sprint.sh --with-ui <NN> <topic>` to also create the
matching `sprint-NN/<topic>` branch + draft PR in `$TAGPULSE_UI_PATH` and
cross-link the two PRs. Requires `gh` authenticated against both repos.

### Backlog
`docs/backlog.md` is the lightweight scratch list for in-flight ideas you
don't want to lose but won't pull into the active sprint. Drain it during
sprint planning: promote items to `docs/roadmap.md` or delete.

### Chores are not sprints
Chores branch directly off `main` as `chore/<topic>`. No sprint number, no
roadmap entry, no kickoff script. Open a normal PR with a CHANGELOG entry
and call it done.
