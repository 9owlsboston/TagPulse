# AI-First Development Project Template

A stamping document for creating new projects that co-develop with AI agents and scale uniformly over time.

---

## How to Use This Template

### Starting a New Project (30 minutes)

**Step 1 — Create the repo**

```bash
mkdir my-project && cd my-project
git init
```

**Step 2 — Discovery: let the agent help you decide**

Most projects start with an idea, not a tech stack. Use this prompt to work through the decisions with the agent:

```
I want to build [describe what you want — the problem, users, rough behavior].

Help me decide:
1. What language and framework fit this best? Consider: team familiarity,
   ecosystem maturity, deployment target, and performance needs.
2. What data store (if any)? Consider: data shape, query patterns, scale.
3. What's the simplest architecture that works for v1?

For each recommendation, explain the trade-off vs the runner-up.
Keep it to 3-5 decisions max.
```

The agent will propose a stack with rationale. You discuss, push back, refine. Once you've agreed on the key decisions, tell the agent:

```
Write these decisions as ADR drafts in docs/adr/ so we don't lose the rationale.
Use the format: title, status (accepted), context, decision, consequences.
```

This creates your first ADRs *before* any code exists — the "why" is captured while it's still fresh.

**Step 3 — Bootstrap via agent prompt**

Now that you know your stack, give the agent the scaffold prompt (customize the bracketed values):

```
Bootstrap this project using the AI-First Development Project Template.

Project: [project name]
Description: [one sentence — what it does, who it's for]
Language: [Python 3.12 / Node 20 / Go 1.22]
Framework: [FastAPI / Express / Gin / none]
Database: [PostgreSQL / none]

Create the Phase 0 skeleton:
1. .gitignore (language-appropriate)
2. README.md (project name + description)
3. .github/copilot-instructions.md (fill in the skeleton from §4 with the details above)
4. .editorconfig (spaces, 2-indent, lf, trim trailing)
5. [pyproject.toml / package.json / go.mod] with linter + test runner deps
6. src/ and tests/unit/ directories with placeholder files
7. .vscode/tasks.json with lint, typecheck, test, format tasks pointing at make targets
8. Makefile with lint, typecheck, test, format, check targets wired for [language]
9. .env.example with placeholder vars
10. .vscode/extensions.json with recommended extensions for [language]

For copilot-instructions.md, include:
- Overview paragraph
- Tech stack
- Code conventions (3-5 rules appropriate for [language/framework])
- Testing expectations
- Naming conventions
- "Do NOT" guardrails (3-5 rules)
- Process & Artifacts section (changelog, ADR, architecture doc, roadmap, design doc triggers)
- Key docs links
```

The agent will scaffold all files in one pass. Review the output, adjust `copilot-instructions.md` to match your preferences, then commit:

```bash
git add -A && git commit -m "chore: bootstrap project skeleton"
```

**Step 4 — Verify quality gates work**

```bash
make check
```

If anything fails, tell the agent: "fix the make check failures". It will read the error output and correct the wiring.

**Step 5 — Start coding with your agent**

You now have a Phase 0 project. Begin the Inner Loop (§10):

```
You: "Create a health check endpoint at GET /health that returns { status: ok }"
Agent: reads copilot-instructions.md → proposes plan → writes code + test → runs make check
You: review diff → approve → commit
```

### Growing the Project

Don't add artifacts preemptively. Use the triggers from §3 and §9:

| When this happens... | Do this |
| --- | --- |
| First PR or first collaborator | Add `CONTRIBUTING.md`, `.github/workflows/ci.yml`, `CHANGELOG.md` (→ Phase 1) |
| Second contributor joins | Add `docs/architecture.md` |
| First non-obvious technical choice | Create `docs/adr/001-{slug}.md` + `docs/adr/README.md` index |
| Same task explained to agent twice | Create `.github/prompts/{verb}-{noun}.prompt.md` |
| Area needs special conventions | Create `.github/instructions/{scope}.instructions.md` |
| Multiple work streams / planning cycles | Create `docs/roadmap.md` |
| First production deployment | Add `docs/runbooks/`, `Dockerfile`, `tests/e2e/` (→ Phase 3) |

### Maintaining Over Time

Follow the cadence in §13:
- **Every sprint end:** update changelog, mark roadmap items done, review copilot-instructions
- **Every quarter:** archive old changelog/roadmap entries, prune prompts, verify architecture doc
- **Every major change:** write ADR if non-obvious, design doc if 3+ components affected

### Quick Reference

| I need to... | Go to section |
| --- | --- |
| See the full repo layout | §1 |
| Know what to create first | §2 |
| Know when to add an artifact | §3, §9 |
| Write copilot-instructions.md | §4 |
| Name files consistently | §5 |
| Set up CI quality gates | §6 |
| Wire my language/framework | §7 |
| Understand agent file roles | §8 |
| Follow the dev workflow | §10 |
| Onboard a new teammate | §11 |
| Keep docs from bloating | §12, §14 |
| Schedule doc maintenance | §13 |

---

## 1. Repository Layout

```
repo/
  src/                          # product code
  tests/                        # test suites
    unit/                       #   fast, isolated tests
    integration/                #   cross-component / service tests
    e2e/                        #   end-to-end / acceptance tests

  docs/                         # human + agent-readable docs
    architecture.md             #   system design overview
    adr/                        #   architecture decision records
      README.md                 #   ADR index (one-line summary + status each)
    runbooks/                   #   operational runbooks
    design/                     #   design docs / RFCs for significant changes
      archive/                  #   archived design docs
    roadmap.md                  #   planned work (current + next cycle only)
    roadmap/                    #   archived roadmap cycles
    changelog/                  #   archived changelog years

  CHANGELOG.md                  # release history (last 3-5 releases only)
  CONTRIBUTING.md               # contribution rules (agents follow these too)

  # ── Agent configuration ─────────────────────────────────────────
  .github/
    copilot-instructions.md     # always-on repo rules (THE most important file)
    instructions/               # path/file-scoped rules (applied by glob match)
      *.instructions.md
    prompts/                    # reusable /slash-command prompts
      *.prompt.md
    agents/                     # custom agent personas & tool allowlists
      *.agent.md
    copilot-setup-steps.yml     # Copilot Coding Agent environment bootstrap
    workflows/                  # CI/CD pipelines

  AGENTS.md                     # agent index — lists available agents + purposes

  # ── VS Code workspace wiring ────────────────────────────────────
  .vscode/
    settings.json               # workspace settings (minimal + agent-related)
    tasks.json                  # build/test/lint tasks agents can invoke
    launch.json                 # debug configs
    extensions.json             # recommended extensions
    mcp.json                    # MCP tool wiring for this workspace

  # ── Dev environment ─────────────────────────────────────────────
  .devcontainer/                # reproducible dev env (high ROI for agents)
    devcontainer.json           #   pins runtime, tools, extensions, env vars

  # ── Root config ─────────────────────────────────────────────────
  pyproject.toml                # or package.json — deps, scripts, tool config
  Makefile                      # or justfile — standard task runner
  Dockerfile                    # container build (if applicable)
  .env.example                  # documents required env vars (no secrets)
  .editorconfig                 # editor-agnostic formatting rules
  .gitignore
  README.md
```

---

## 2. Bootstrap Sequence (Day 0 Checklist)

Create files in this order. Each step builds on the last.

```
Step  File / Dir                          Why first
────  ──────────────────────────────────  ──────────────────────────────────────
 1    git init + .gitignore               Version control exists
 2    README.md                           Humans know what this project is
 3    .github/copilot-instructions.md     Agent knows the rules before writing code
 4    .editorconfig                       Formatting is consistent from line one
 5    pyproject.toml or package.json      Deps + scripts have a home
 6    src/ + tests/unit/                  Code and tests have a place
 7    .vscode/tasks.json                  Agent can run build/test/lint
 8    Makefile or justfile                Standard task runner wraps tool commands
 9    .env.example                        Config shape is documented
10    .devcontainer/devcontainer.json     Reproducible environment (optional but high ROI)
```

Everything else is added on demand as the project grows (see Phase Model below).

---

## 3. Phase Model (Minimum Viable → Mature)

Not every file is needed on day 1. Add artifacts when they become necessary.

### Phase 0 — Skeleton (day 0)

Bootstrap sequence above. One human, one agent. No process overhead.

**Files:** `README.md`, `.github/copilot-instructions.md`, `.editorconfig`, `pyproject.toml` / `package.json`, `src/`, `tests/unit/`, `.vscode/tasks.json`, `Makefile`, `.env.example`, `.gitignore`

### Phase 1 — First Feature (week 1–2)

Working code exists. Need basic quality gates and contribution norms.

**Add:**
- `CONTRIBUTING.md` — commit message format, branch naming, PR expectations
- `.github/workflows/ci.yml` — lint + test on every push/PR
- `CHANGELOG.md` — start tracking changes from first release
- `.vscode/launch.json` — debug configs
- `.vscode/extensions.json` — recommended extensions

**Trigger:** First PR or first collaborator.

### Phase 2 — Team Scale (month 1–3)

Multiple contributors (human or agent). Need coordination artifacts.

**Add:**
- `docs/architecture.md` — system overview so agents don't have to infer structure
- `docs/adr/` + `README.md` index — when the first non-obvious technical choice is made
- `.github/instructions/` — scoped rules for areas with specific conventions
- `.github/prompts/` — when you explain the same task pattern twice
- `AGENTS.md` — when you create the first custom agent
- `tests/integration/` — cross-component tests
- `.devcontainer/devcontainer.json` — when "works on my machine" becomes a problem

**Trigger:** Second contributor, or first architectural decision that needs rationale.

### Phase 3 — Mature (quarter 2+)

Production traffic, operational concerns, multiple work streams.

**Add:**
- `docs/design/` — design docs / RFCs for significant changes
- `docs/runbooks/` — operational playbooks
- `docs/roadmap.md` — planned work across cycles
- `.github/agents/` — specialized agent personas (security reviewer, docs updater)
- `.github/copilot-setup-steps.yml` — headless agent environment bootstrap
- `.vscode/mcp.json` — MCP tool wiring
- `tests/e2e/` — end-to-end acceptance tests
- `Dockerfile` — containerized deployment

**Trigger:** Production deployment, or when operational incidents need documented response procedures.

---

## 4. `copilot-instructions.md` Skeleton

This is the single highest-leverage file. Keep it under ~100 lines. Link to `docs/` for details.

```markdown
# Project: {project-name}

## Overview
{One paragraph: what this project does, who it's for, key constraints.}

## Tech Stack
- Language: {e.g., Python 3.12}
- Framework: {e.g., FastAPI}
- Database: {e.g., PostgreSQL}
- Testing: {e.g., pytest}

## Code Conventions
- {e.g., Use type hints on all public functions}
- {e.g., Prefer composition over inheritance}
- {e.g., Use structured logging (structlog), never print()}

## Testing Expectations
- Every PR must include tests for new/changed behavior
- Unit tests go in `tests/unit/`, integration in `tests/integration/`
- Use fixtures for shared test setup, no global state
- Run `make test` to validate

## Naming
- Files: snake_case
- Classes: PascalCase
- Functions/variables: snake_case
- API routes: kebab-case

## Do NOT
- {e.g., Do not add dependencies without updating pyproject.toml}
- {e.g., Do not use wildcard imports}
- {e.g., Do not commit .env files or secrets}
- {e.g., Do not catch bare exceptions}

## Process & Artifacts
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
- Contributing: CONTRIBUTING.md
```

---

## 5. Naming Conventions

Consistent naming across projects makes agents (and humans) find things instantly.

| Artifact | Pattern | Examples |
| --- | --- | --- |
| Scoped instructions | `{scope}.instructions.md` | `api.instructions.md`, `database.instructions.md` |
| Prompts | `{verb}-{noun}.prompt.md` | `add-endpoint.prompt.md`, `write-migration.prompt.md` |
| Agents | `{role}.agent.md` | `security-reviewer.agent.md`, `test-writer.agent.md` |
| ADRs | `{NNN}-{slug}.md` | `001-use-postgres.md`, `012-switch-to-nats.md` |
| Design docs | `{feature-slug}.md` | `auth-redesign.md`, `caching-strategy.md` |
| Workflows | `{trigger}.yml` | `ci.yml`, `release.yml`, `deploy.yml` |
| Runbooks | `{scenario}.md` | `database-failover.md`, `rollback-release.md` |

---

## 6. Quality Gates

Minimum gates that must pass before merge. Wire these in `tasks.json` and CI.

| Gate | Tool (examples) | When |
| --- | --- | --- |
| **Lint** | ruff, eslint, golangci-lint | Every push |
| **Typecheck** | mypy, tsc, go vet | Every push |
| **Unit tests** | pytest, jest, go test | Every push |
| **Integration tests** | pytest, jest | Every PR |
| **Security scan** | bandit, npm audit, trivy | Every PR |
| **Format check** | ruff format, prettier | Every push |

### `tasks.json` Minimum Tasks

Every project should wire at least these four tasks so agents can self-validate:

```json
{
  "version": "2.0.0",
  "tasks": [
    { "label": "lint",      "type": "shell", "command": "make lint" },
    { "label": "typecheck", "type": "shell", "command": "make typecheck" },
    { "label": "test",      "type": "shell", "command": "make test" },
    { "label": "format",    "type": "shell", "command": "make format" }
  ]
}
```

### `Makefile` Minimum Targets

```makefile
.PHONY: lint typecheck test format check

lint:        ## Run linter
typecheck:   ## Run type checker
test:        ## Run unit tests
format:      ## Auto-format code
check: lint typecheck test  ## Run all gates
```

Fill in the commands for your language/framework (see Language Adaptation below).

---

## 7. Language / Framework Adaptation

The layout is language-agnostic. Here's how to wire it for common ecosystems.

### Python

| File | Content |
| --- | --- |
| `pyproject.toml` | deps, scripts, ruff/mypy/pytest config |
| `Makefile` | `lint: ruff check .` / `typecheck: mypy src` / `test: pytest` / `format: ruff format .` |
| `.devcontainer` | `python:3.12`, install `ruff`, `mypy`, `pytest` |

### Node.js / TypeScript

| File | Content |
| --- | --- |
| `package.json` | deps, scripts (`lint`, `test`, `typecheck`, `format`) |
| `Makefile` | `lint: npm run lint` / `typecheck: npx tsc --noEmit` / `test: npm test` / `format: npx prettier --write .` |
| `.devcontainer` | `node:20`, install project deps |

### Go

| File | Content |
| --- | --- |
| `go.mod` | deps |
| `Makefile` | `lint: golangci-lint run` / `typecheck: go vet ./...` / `test: go test ./...` / `format: gofmt -w .` |
| `.devcontainer` | `go:1.22`, install `golangci-lint` |

---

## 8. Key Files for Agent Behavior

| File | Scope | Purpose |
| --- | --- | --- |
| `.github/copilot-instructions.md` | Always on | Repo-wide rules: conventions, style, testing, guardrails |
| `.github/instructions/*.instructions.md` | File/path glob | Scoped rules applied when agent touches matching files |
| `.github/prompts/*.prompt.md` | On demand | Reusable workflows invoked via `/prompt-name` |
| `.github/agents/*.agent.md` | On demand | Custom agent personas with tool restrictions |
| `AGENTS.md` | Always on | Index of available agents and their capabilities |
| `.vscode/tasks.json` | Workspace | Tasks agents can run (build, test, lint, format) |
| `.vscode/mcp.json` | Workspace | MCP server connections available to agents |
| `.github/copilot-setup-steps.yml` | CI | Environment setup for Copilot Coding Agent (headless) |

### Notes

- **Skills** live at the user level (`~/.agents/skills/`), not in-repo. They sync across workspaces and survive repo switches. Extension-managed skills (e.g., Azure) auto-install via their extension.
- **Git hooks** (`.git/hooks/`) are separate from agent lifecycle — don't confuse the two.
- **`.env.example`** should list every env var the project needs with placeholder values. Agents use this to understand configuration without accessing secrets.

---

## 9. Project Artifacts & Triggers

Artifacts are created on demand, not up front. Each has a trigger — the moment it becomes necessary.

| Artifact | Location | Trigger | Agent value |
| --- | --- | --- | --- |
| **Architecture overview** | `docs/architecture.md` | Second contributor or second service | System-level context: components, boundaries, data flow |
| **ADRs** | `docs/adr/` | First non-obvious technical choice | Records *why* — agents won't re-propose rejected approaches |
| **Design docs / RFCs** | `docs/design/` | Change touching 3+ components or requiring team buy-in | Agent reads before architectural changes |
| **Roadmap** | `docs/roadmap.md` | Multiple work streams or planning cycles | Agent knows what's in-scope vs out-of-scope |
| **Changelog** | `CHANGELOG.md` | First release | Agent references for regression awareness and PR descriptions |
| **Contributing guide** | `CONTRIBUTING.md` | First external contributor or first PR | Codifies PR expectations, commit format, branch naming |
| **Runbooks** | `docs/runbooks/` | First production deployment | Operational playbooks for incident response |
| **Scoped instructions** | `.github/instructions/` | File/area needing conventions beyond repo-wide rules | Area-specific rules (e.g., "API handlers use structured logging") |
| **Prompts** | `.github/prompts/` | Same task pattern explained twice | Codified recipe agents reuse |
| **Custom agents** | `.github/agents/` | Need constrained persona (reviewer, test writer) | Focused tool access and behavior |

---

## 10. Development Workflow

The core loop: **you steer, the agent executes.**

### Inner Loop (Daily Coding)

```
1. INTENT       → Describe what you want (natural language, issue link, or /prompt)
2. PLAN         → Agent proposes a plan (files to touch, approach, trade-offs)
3. REVIEW PLAN  → You approve, refine, or redirect
4. EXECUTE      → Agent writes code, tests, and config
5. VALIDATE     → Agent runs tests/lint via tasks.json, you review diffs
6. COMMIT       → You (or agent) commits with a clear message
```

How repo files feed each step:

| Step | Agent reads | Why |
| --- | --- | --- |
| Understanding context | `copilot-instructions.md`, `AGENTS.md`, `docs/` | Knows the rules before writing a line |
| Scoped edits | `instructions/*.instructions.md` | Applies file-specific conventions |
| Repeatable tasks | `prompts/*.prompt.md` | `/add-endpoint`, `/write-migration` — codified recipes |
| Running checks | `tasks.json` | `build`, `test`, `lint`, `typecheck` — agent self-validates |
| External tools | `mcp.json` | Database queries, cloud lookups, custom tooling |
| Specialized work | `agents/*.agent.md` | Constrained personas: security reviewer, test writer |

### Outer Loop (Feature Lifecycle)

```
1. ISSUE / SPEC       → Write or link a clear issue / spec
2. BRANCH             → Agent creates feature branch
3. INNER LOOP         → (plan → code → test → validate) × N
4. PR                 → Agent drafts PR with description
5. CI                 → copilot-setup-steps.yml bootstraps → workflows run
6. REVIEW             → Human reviews, agent addresses comments
7. MERGE              → Human merges
```

### Practical Tips

- **Front-load `copilot-instructions.md`** — every minute here saves hours correcting agent output
- **Use prompts for repeatable work** — if you explain the same thing twice, make it a `.prompt.md`
- **Let agents run tests** — put `test`, `lint`, `typecheck` in `tasks.json`. The agent will self-correct when tests fail
- **Small, reviewable PRs** — agents work best with focused scope. One issue = one PR
- **Treat agent output like junior dev output** — always review diffs. Trust but verify

---

## 11. Team Onboarding Path

When a new human or agent joins the project, they read these files in this order:

```
1. README.md                           → What is this project?
2. .github/copilot-instructions.md     → What are the rules?
3. CONTRIBUTING.md                     → How do I contribute?
4. docs/architecture.md               → How is it built?
5. docs/adr/README.md                 → Why were key choices made?
6. CHANGELOG.md                        → What changed recently?
7. docs/roadmap.md                     → What's planned next?
8. AGENTS.md                           → What agents are available?
```

For agents, steps 1–3 are loaded automatically. Steps 4–8 are accessed on demand when the agent needs deeper context.

---

## 12. Scaling Project Docs

As the project grows, both agents and humans lose signal in large documents. The pattern: **thin index at the top, detail in dated files below, archive aggressively.**

### Changelog

Keep only the **last 3–5 releases** in `CHANGELOG.md`. Move older entries to per-year archives.

```
CHANGELOG.md              ← last 3-5 releases only
docs/changelog/
  2026.md                 ← full 2026 archive
  2025.md
```

### Roadmap

Show only **current cycle + next cycle** in `docs/roadmap.md`. Use status markers: `[done]`, `[in-progress]`, `[planned]`, `[cut]`. At cycle boundaries, move completed/cut items to dated archives. Keep it under ~30–50 lines.

```
docs/roadmap.md           ← current + next cycle only
docs/roadmap/
  2026-Q1.md              ← archived cycle
  2025-Q4.md
```

### ADRs

ADRs are immutable — they accumulate. Add an **index file** with a one-line summary per ADR and its status (`accepted`, `superseded by ADR-NNN`, `deprecated`). Never delete superseded ADRs — mark them and link to the replacement.

```
docs/adr/
  README.md               ← index: "ADR-007: Use Kafka [superseded by ADR-012]"
  001-use-mqtt.md
  012-switch-to-nats.md
```

### Design Docs

Use a lifecycle status on the first line: `Status: draft | active | implemented | archived`. Move `archived` docs to `docs/design/archive/`. Only `active` and `draft` docs stay in the main directory.

```
docs/design/
  feature-x.md            ← Status: active
  archive/
    feature-old.md        ← Status: archived
```

### General Rules

| Rule | Why |
| --- | --- |
| **Index files at every directory** | Agents search breadth-first — a `README.md` with one-liners is cheaper than scanning 40 files |
| **Date-partition archives** | `YYYY.md` or `YYYY-QN.md` keeps old content reachable but out of default context |
| **Status markers on everything** | `[active]`, `[archived]`, `[superseded]` — agents and humans both filter on this |
| **Keep active docs under ~200 lines** | Beyond that, split. 200 lines fits comfortably in agent context |
| **`copilot-instructions.md` stays lean** | Always loaded. Keep under ~100 lines. Link to `docs/` for details |

---

## 13. Maintenance Cadence

Tie doc hygiene to your existing rhythm. No separate process — just checkpoints.

### Every Sprint / Iteration End

- [ ] Update `CHANGELOG.md` with completed work
- [ ] Mark shipped roadmap items as `[done]`
- [ ] Review `copilot-instructions.md` — still accurate?

### Every Quarter / Cycle Boundary

- [ ] Archive completed/cut roadmap items to `docs/roadmap/YYYY-QN.md`
- [ ] Rotate `CHANGELOG.md` — move older entries to `docs/changelog/YYYY.md`
- [ ] Move `implemented` design docs to `docs/design/archive/`
- [ ] Update `docs/adr/README.md` index with any new/superseded ADRs
- [ ] Review and prune `.github/prompts/` — remove unused prompts
- [ ] Verify `docs/architecture.md` reflects current system

### On Every Major Change

- [ ] Write an ADR if the decision is non-obvious or reversible-but-costly
- [ ] Write a design doc if the change touches 3+ components
- [ ] Update `docs/architecture.md` if system boundaries change

---

## 14. The Pyramid Model

The repo is a pyramid. Agents and humans navigate top-down. Top layers are small and always relevant. Bottom layers are large and accessed only when needed.

```
        ┌─────────────────────┐
        │ copilot-instructions│  ← always loaded, ~100 lines
        │ README.md           │
        ├─────────────────────┤
        │ Index files         │  ← read on demand, small
        │ (AGENTS.md,         │
        │  adr/README.md,     │
        │  CHANGELOG.md top)  │
        ├─────────────────────┤
        │ Full docs           │  ← read rarely, can be long
        │ (design docs, ADRs, │
        │  archived changelogs│
        │  old roadmap cycles)│
        └─────────────────────┘
```
