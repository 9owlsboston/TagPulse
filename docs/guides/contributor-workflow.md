# Contributor Workflow

How we develop, review, and ship TagPulse. Read this **once** when you join,
re-read the [Releases](#releases) section before cutting a release.

For the *operator* view of what runs in CI/CD, see
[../runbooks/github-workflows.md](../runbooks/github-workflows.md).
For coding conventions, see [../../CONTRIBUTING.md](../../CONTRIBUTING.md).

## TL;DR

- **One PR = one logical change.** Small, mergeable, reviewable on a phone.
- **Branch off `main`, never off another feature branch.** No stacked PRs.
- **Update `CHANGELOG.md` under `## Unreleased` in every PR.**
- **Run `make check` before pushing.** CI runs the same.
- **Sprints are planning units, not branches.** Each sprint produces *many*
  small PRs to `main`.

## Branch naming

| Prefix | Use for |
|---|---|
| `feat/<slug>` | new user-visible behaviour |
| `fix/<slug>` | bug fixes |
| `chore/<slug>` | tooling, deps, refactors with no behaviour change |
| `docs/<slug>` | docs-only |
| `sprint-NN/<topic>` | sprint kickoff branch (rare — see below) |

The `sprint-NN/...` form is reserved for the **first** PR of a sprint that
sets up scope / scaffolding. Day-to-day sprint work goes on regular
`feat/`/`fix/` branches off `main`.

## Sprints: planning unit, not a branch

A "sprint" is a numbered chunk of scope tracked in
[../roadmap.md](../roadmap.md) (e.g. Sprint 28 — Operational Excellence).
A sprint typically ships **5–15 small PRs** over 1–2 weeks, each independently
reviewable and mergeable to `main`. We do **not** keep a long-lived sprint
branch.

### Starting a sprint

Use the helper:

```bash
scripts/start-sprint.sh 29 my-topic "feat(sprint-29): my topic"
```

It enforces: clean tree, `sprint-NN/<topic>` branch name, draft PR with the
standard checklist. Use it for the **kickoff PR only** — once scope is
agreed, do the actual work in normal small PRs.

### During the sprint

For each piece of work:

```bash
git checkout main && git pull --ff-only
git checkout -b feat/<small-thing>
# ...code, test...
make check
git commit -m "feat: short description"
git push -u origin HEAD
gh pr create --fill
```

### CHANGELOG conflicts

When multiple in-flight PRs all touch `## Unreleased`, the second-merging PR
needs a rebase. Pattern:

```bash
git fetch origin
git rebase origin/main
# resolve CHANGELOG.md by keeping BOTH bullet lists
git add CHANGELOG.md
git rebase --continue
git push --force-with-lease
```

Long-term we plan to switch to **towncrier** (per-PR fragment files in
`changelog.d/` collapsed at release time) — this eliminates the conflict
class entirely. Tracked in roadmap as a candidate for a future sprint.

## Pull requests

Required before review:

- [ ] `make check` clean (lint + typecheck + test)
- [ ] New/changed behaviour has tests (`tests/unit/` or `tests/integration/`)
- [ ] `CHANGELOG.md` updated under `## Unreleased`
- [ ] If scope creeps beyond the PR title — split it

Required before merge:

- [ ] At least one review approval
- [ ] All CI checks green (`ci`, `docs-lint` if docs touched)
- [ ] Branch up-to-date with `main` (rebase, don't merge)

We **squash-merge** to keep `main` linear. Squash commit message = PR title.

## Releases

### Current model: continuous deployment per commit

Every merge to `main`:

1. `build-and-push.yml` builds api/worker/migrations images → tags with
   `<sha>` and `latest` → pushes to GHCR + ACR.
2. `deploy-azure.yml` runs `azd deploy` against **dev** with the new images
   and runs the migrations job.

There is no manual "release" step today. `main` *is* the release. Rollback =
revert the offending commit (which produces a new green deploy).

### Cutting a release (when we adopt versioning)

We do not yet cut versioned releases. The mechanism is in place
(`build-and-push.yml` reacts to `v*` tags) but we haven't activated it.
When we do, the flow will be:

1. Make sure `main` is green and `## Unreleased` in `CHANGELOG.md` is the
   release notes you want.
2. Rename the `## Unreleased` section to `## vX.Y.Z — YYYY-MM-DD` and
   re-add an empty `## Unreleased` above it. Open a release-prep PR.
3. After merge, tag from main:
   ```bash
   git checkout main && git pull --ff-only
   git tag -a vX.Y.Z -m "Release vX.Y.Z"
   git push origin vX.Y.Z
   ```
4. `build-and-push.yml` produces version-tagged images on the tag push.
5. Create a GitHub Release pointing at the tag, paste the changelog
   section as the release body:
   ```bash
   gh release create vX.Y.Z --notes-file <(awk '/^## v'"X.Y.Z"'/,/^## /' CHANGELOG.md | head -n -1)
   ```
6. Promote to staging / prod by deploying the version-tagged image (env
   variables `IMAGE_TAG=vX.Y.Z`, then `make deploy ENV=staging` →
   `ENV=prod`).

### Versioning scheme (future)

SemVer:

- **MAJOR** — breaking API or DB schema change requiring an out-of-band
  migration (e.g. a manual ALTER, multi-step cutover documented in a
  runbook).
- **MINOR** — backward-compatible additions: new endpoints, new optional
  fields, new device types.
- **PATCH** — backward-compatible fixes: bug fixes, perf, dep bumps.

Pre-1.0 we'll use `0.MINOR.PATCH` — any minor bump may break.

### Hotfixes (future)

For an urgent fix when `main` has unreleased work that can't ship:

```bash
git checkout -b hotfix/v1.2.4 v1.2.3
# fix + test + changelog
git push -u origin HEAD
gh pr create --base main      # PR back into main first
# after merge:
git tag -a v1.2.4 -m "Hotfix v1.2.4"
git push origin v1.2.4
```

We have not exercised this path yet — when it's needed we'll harden it
into a runbook.

## Cross-references

- Coding conventions: [../../CONTRIBUTING.md](../../CONTRIBUTING.md)
- Active scope / sprint log: [../roadmap.md](../roadmap.md)
- Architecture decisions: [../adr/README.md](../adr/README.md)
- What runs in CI/CD: [../runbooks/github-workflows.md](../runbooks/github-workflows.md)
- First-time deploy: [../runbooks/azure-first-deploy.md](../runbooks/azure-first-deploy.md)
