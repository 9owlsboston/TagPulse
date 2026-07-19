# AGENTS.md — TagPulse

Repo-specific operating contract for any AI coding agent (Copilot CLI, VS Code,
Claude, etc.) working here. This file is the **cross-tool source of truth** —
the sibling of `.github/copilot-instructions.md` (which stays thin and points
here).

> **Not here:** the SDLC (explore → plan → implement → verify → ship → **close-out**,
> the `planner`/`implementer`/`verifier` personas + the `explorer`/`rubber-duck`
> review capabilities, conventional commits, branching) lives in the **global**
> `~/.copilot/copilot-instructions.md` (SoT: `ai-tooling-config`) — this file does
> **not** repeat it. The full model + diagram is in
> [`dev-env-setup` `docs/guides/sdlc.md`](https://github.com/9owlsboston/dev-env-setup/blob/main/docs/guides/sdlc.md).
> Keep this file to what is *unique to this repo*.

## 1. What this repo is

TagPulse is the **backend** of a two-repo IoT product (the React SPA lives in
[9owlsboston/TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI)). It provides device
registration/config, dual telemetry ingestion (**MQTT** on `:1883`, `:8883` TLS opt-in, and
**HTTP**), TimescaleDB-backed time-series storage + monitoring, a user-defined rules/alerts
engine, pluggable analytics modules, and outbound integration/export — behind FastAPI and an
admin UI. First device type: RFID readers emitting tag-read events. **Category:** async
Python service (app).

## 2. Hard rules (repo-specific)

- **Python 3.12 + FastAPI (async).** Prefer `async`/`await` for all I/O; inject DB
  sessions and config via FastAPI `Depends()`.
- **Type hints on every function** (public *and* private) — `mypy` runs in **strict** mode.
- **Pydantic models** for all API request/response schemas *and* MQTT message parsing.
- **Structured logging only** (stdlib `logging` + JSON formatter) — never `print()`.
- **No business logic in route handlers** — delegate to service functions.
- **Catch specific exceptions**, never a bare `except`.
- **No wildcard imports.** **Never import from `tests/` in `src/`.**
- **Pin deps in `pyproject.toml`** — don't add a dependency without updating it.
- **Never commit `.env` files or secrets.**

## 3. Run / test

```bash
pip install -e ".[dev]"    # install with dev extras
make check                 # lint + typecheck + test — the full gate; run before "done"
make test                  # unit tests only (pytest + pytest-asyncio)
make lint                  # ruff style + format check   (make format to auto-fix)
make typecheck             # mypy --strict
make run                   # start the dev server
make export-openapi        # regenerate openapi.json after ANY API change
```

Every PR includes tests for new/changed behavior — `tests/unit/` (fast, isolated) and
`tests/integration/` (cross-component). Use fixtures for shared setup (no global state);
mock the MQTT broker + database in unit tests.

## 4. Where to write (docs map)

Pick the destination by the **kind** of content, not the topic:

| Kind of content | Goes in |
|---|---|
| How to run / use this repo | `README.md` |
| Rules for agents working here | `AGENTS.md` (this file) |
| Dated "where we are now" snapshot (current → future → gaps) | `docs/current-state.md` |
| **What commands actually ran / how verified** (action trail) | `docs/history/execution-log.md` |
| Durable working memory (issues/chores/decisions/**routines**/memories) | the **agent ledger** (`repo:<name>` scope; promote to `execution-log.md` when it earns a commit, or to `/kb` when generalizable) |
<!-- Optional: declare cross-repo PROJECT membership so `ledger recall` / `profile`
     union open items + facts across EVERY repo that declares the SAME (lowercase)
     project name. Copy the line below, DROP the `-example` suffix so it goes live,
     put it on its own line, and set your name (the `-example` form is inert): -->
<!-- ledger-project: tagpulse -->
<!-- For the cross-repo union to work, TagPulse-UI must declare the SAME `tagpulse`
     project line (add it there if absent) — then ledger recall/profile union open
     items + facts across both repos of this two-repo product. -->
<!-- Uncomment the rows the repo has grown into (profile s+ / grow):
| Architecture, proposals, decisions — the *why* (Diátaxis *explanation* / ADRs) | `docs/design/` |
| How-to workflows and walkthroughs (Diátaxis *how-to* / *tutorial*) | `docs/guides/` |
| Stable technical reference — facts that don't expire (Diátaxis *reference*) | `docs/reference/` |
| Consumer-facing change log (content) | `CHANGELOG.md` |
-->

## 5. Drift-rules

Facts that **must stay true** in this repo. `docs-drift` flags any doc/code hit
against a bad-substring below. Use a **live/actionable pattern** (a command or
import used *as if current*), NOT a bare noun — nouns appear in explanatory prose
and history and would just create noise. Add a row whenever a live path moves/renames.

```drift-rules
# <live-pattern>       →   <why it's wrong / what's correct now>
# (example) python old/path/x.py  →   moved to new/path (invoke via $X); <when/why>
```

## 6. Doc-lifecycle (pre / post — agent-enforced)

- **Pre** (session start): read this file + the relevant plan/design doc; run
  `docs-drift` before changing code.
- **Agent memory (ledger):** at session start **recall** the ledger; **apply a
  relevant routine before planning**; **log a routine after a notable success**
  (a routine is a distilled how-to with the five fields
  `goal:/applies-when:/preconditions:/steps:/pitfalls:`). The exact commands (and
  the OS-specific `python3`/`python` invocation) live in the usage guide — see the
  engine + how-to pointer below.
- **During**: update `docs/history/execution-log.md` *as part of* the change
  (what ran, how verified) — not after; keep any plan status honest. Docs change
  *with* code: if a change alters behavior, config, CLI, API, or deployment, the
  closest doc changes in the same change.
- **Post** (session/PR end): **close out the change** — squash-merge, then ff
  `main` (primary worktree) → remove the worktree → delete the local branch (`-D`,
  since a squash-merged branch isn't an ancestor of `main`) → run `docs-drift` →
  update refs on any move/rename (and the drift-rules above) → note residuals. If
  the change moved the current state, reconcile `docs/current-state.md` and bump
  its snapshot date as the **last step**.
- **Wrap review (current-state rubber-duck):** at the end of any change that
  touched product code or config, read `git diff` + `docs/current-state.md` +
  `README.md` + any touched topic docs, then explicitly report one of
  `current-state: updated / not-affected / needs-human-decision`. A read-only
  judgment pass — not a script.
- **Rubber-duck termination:** rubber-duck loops on **blocking** findings only,
  then terminates by **acceptance** (plan-stage) or the stage-3 `verifier` gate
  (diff-stage); round cap 2–3, open blockers at the cap → Open Questions, never
  dropped. Full rule:
  [`dev-env-setup` `docs/guides/sdlc.md`](https://github.com/9owlsboston/dev-env-setup/blob/main/docs/guides/sdlc.md).
- **Rubber-duck enforcement:** rubber-duck is **required** (not optional) at
  plan-stage and diff-stage for code/config changes — record a **ran-or-waived**
  attestation in the design doc's `## Review attestations` (PR body mirrors the
  diff-stage line). Carve-outs (`noncodefix`/`spike`/`release`) are exempt **unless**
  the change touches deps/CI/IaC/security/behavioral config. Full rule:
  [`dev-env-setup` `docs/guides/sdlc.md`](https://github.com/9owlsboston/dev-env-setup/blob/main/docs/guides/sdlc.md).

Full lifecycle spec: global `~/.copilot/copilot-instructions.md`.

**Agent-memory engine + how-to.** The ledger is
[`ledger.py`](https://github.com/9owlsboston/kb-tools/blob/main/ledger.py) in
`kb-tools` — **not** executable and **not** on PATH. Set `KB` for your shell, then
call the interpreter on the script:
- **POSIX:** `KB=~/ws/kb-tools` → `python3 "$KB/ledger.py" <verb>`
- **PowerShell (Windows):** `$KB = "$env:USERPROFILE\ws\kb-tools"` → `python "$KB\ledger.py" <verb>`

How-to:
[agent-memory-usage.md](https://github.com/9owlsboston/dev-env-setup/blob/main/docs/guides/agent-memory-usage.md).
Design:
[agent-memory-ledger.md](https://github.com/9owlsboston/dev-env-setup/blob/main/docs/design/agent-memory-ledger.md).

## 7. Documentation output style

When writing or editing any doc, follow this output contract so a human can trust
and skim it (full rationale: the ecosystem's *AI documentation output contract*
design doc).

**Structure**

- **Summary first.** Open with a plain-English summary a non-author grasps in one
  read: *what this is, who it's for, when to use it.* For non-trivial topics, lead
  the summary with a high-level **contextual diagram** (source in `docs/diagrams/`,
  Mermaid/drawio/excalidraw) — the diagram *is* the summary.
- **Why before how.** State purpose/value before implementation detail.
- **One doc, one intent.** Route by Diátaxis (tutorial / how-to / reference /
  explanation) and the where-to-write map (§4); don't mix intents. *(Exception:
  `docs/current-state.md` is a deliberate rollup/index.)*
- **Link, don't duplicate.** Point to the source-of-truth doc instead of copying
  it; on conflict, the linked topic doc wins.

**Prose discipline (the anti-machine rules)**

- **Don't restate code.** If the code/signature already says it, link to it —
  don't narrate it.
- **No filler, no narration of the obvious.** Cut "In this section we will…" and
  ceremony.
- **Cite or flag.** Every non-obvious behavioral claim must trace to code, a test,
  an ADR, or a linked source — otherwise mark it **`unverified`**.
- **Mark assumptions explicitly.** Never present an assumption as a fact.
- **Length discipline (soft).** Summaries stay short (a few sentences / ≤ ~8
  lines); depth goes in the detail sections below.

## 8. Naming conventions

- Files: `snake_case` · Classes: `PascalCase` · functions/variables: `snake_case`.
- API routes: kebab-case (`/tag-reads`, `/device-registry`).
- MQTT topics: slash-separated (`devices/{device_id}/tag-reads`).

## 9. Process & artifacts

- **New sprint:** run `scripts/start-sprint.sh <NN> <topic-slug> ["PR title"]` from a clean
  `main` — it enforces the `sprint-NN/<topic-slug>` branch name and opens the draft PR with
  the standard checklist. Don't branch + open PRs by hand. Planning artifacts (ADRs, design
  docs, roadmap edits) go on the kickoff branch, not `main` (use `--carry` if you already
  started planning on `main`).
- **Chore** (standalone tooling/cleanup, no roadmap impact): branch off `main` as
  `chore/<topic>` — normal PR + CHANGELOG entry, no sprint number, no kickoff script.
- Check `docs/roadmap.md` (the single planning source of truth) before starting.
- Every PR updates `CHANGELOG.md` under `## Unreleased`.
- Non-obvious technical decision → ADR in `docs/adr/`. New service / boundary change →
  update `docs/architecture.md`. Change touching **3+ components** → design doc in
  `docs/design/` first.
- Follow `CONTRIBUTING.md` for branch naming, commit format, and PR expectations. Run
  `make check` before marking work complete.

## 10. Cross-repo workflow (TagPulse + TagPulse-UI)

- Two repos, **one roadmap**: `docs/roadmap.md` here is the single source of truth; UI-only
  items live here too, tagged `[UI]`. Sprint numbers are **shared** across both repos.
- **OpenAPI is the contract handoff.** Any API-touching change regenerates `openapi.json`
  in the same PR (`make export-openapi`). UI PRs that consume new API **record the backend
  commit SHA** the `openapi.json` was regenerated against, in the PR description. When both
  repos change, merge **backend first**, then the UI rebases onto the updated `openapi.json`.
- **Declare the cross-repo plan upfront.** `scripts/start-sprint.sh` injects a
  `## Cross-repo plan` section into the draft PR body — fill it in even when the answer is
  "backend only" or "UI TBD pending backend exploration". Explicit beats implicit.
- Mid-sprint discovery that the *other* repo needs a change → don't derail the active
  branch; ship a small focused `sprint-NN/<topic>-<repo>-followup` PR.
- `scripts/start-sprint.sh --with-ui <NN> <topic>` also creates the matching UI branch +
  draft PR and cross-links them. `docs/backlog.md` is the scratch list — drain it at
  sprint planning.

## 11. Key docs

- Architecture: `docs/architecture.md` · Azure layout: `docs/azure-architecture.md`
- ADRs: `docs/adr/README.md` · Runbooks: `docs/runbooks/README.md`
- Operator quickstart: `docs/operator-quickstart.md` · IoT reference: `IoT.md`
