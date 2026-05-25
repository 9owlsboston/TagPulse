# Contributing to TagPulse

## Getting Started

```bash
git clone https://github.com/9owlsboston/TagPulse.git
cd TagPulse
pip install -e ".[dev]"
git config core.hooksPath .githooks   # opt in to the pre-push guard on main
make check
```

Or with Docker:

```bash
docker compose up -d db mqtt
make run
```

> **New here?** Read [docs/guides/contributor-workflow.md](docs/guides/contributor-workflow.md)
> for the full picture: sprint model, multi-PR workflow, CHANGELOG conflict
> resolution, release process. The sections below are the quick reference.

## Branch Naming

- `feat/short-description` — new features
- `fix/short-description` — bug fixes
- `chore/short-description` — tooling, deps, docs

## Commit Format

```
type: short description

Optional longer body explaining why.
```

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`

## Before Submitting a PR

1. Run `make check` (lint + typecheck + test). Lint covers `src`, `tests`, and `clients/pi`.
2. Add tests for new/changed behavior
3. Update `CHANGELOG.md` under `## Unreleased`
4. Unit tests in `tests/unit/`, integration in `tests/integration/`
5. Mock external dependencies in unit tests

## Code Style

- Type hints on all functions
- Structured logging (`logging` module), never `print()`
- Business logic in service functions, not route handlers
- Pydantic models for all API schemas
- See `.github/copilot-instructions.md` for full conventions

## Cross-Repo Workflow

TagPulse ships as two repos (backend + `TagPulse-UI`). Sprint numbers are
shared; the backend `docs/roadmap.md` is the single source of truth.
Three work shapes (sprint / chore / in-flight follow-up) are routed
differently. See `.github/copilot-instructions.md` § Cross-Repo Workflow
for the full model, and use `scripts/start-sprint.sh --with-ui <NN> <topic>`
to kick off a paired backend + UI sprint.
