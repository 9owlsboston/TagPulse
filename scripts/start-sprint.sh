#!/usr/bin/env bash
# Start a new sprint: branch off main + open a draft PR.
#
# Usage:  scripts/start-sprint.sh [--carry] [--with-ui] <NN> <topic-slug> ["PR title"]
# Example: scripts/start-sprint.sh 23 anomaly-detection
#          scripts/start-sprint.sh 23 anomaly-detection "feat(sprint-23): anomaly detection"
#          scripts/start-sprint.sh --carry 23 anomaly-detection
#          scripts/start-sprint.sh --with-ui 54 data-ops-menu
#
# Default mode: requires a clean tree. The new sprint-NN/<topic> branch is
# the canonical place for sprint planning artifacts (ADRs, design docs,
# roadmap updates) — make them the first commits on the branch and they
# ride in the draft kickoff PR.
#
# --carry mode: if you started planning on main before remembering to
# branch, --carry stashes the in-flight changes, creates the branch, then
# pops the stash so the WIP comes along. Tracked + untracked files are
# carried; ignored files are not.
#
# --with-ui mode: after the backend branch + draft PR exist, also create
# a matching sprint-NN/<topic> branch + draft PR in TagPulse-UI at
# $TAGPULSE_UI_PATH (default ~/ws/TagPulse-UI) and cross-link the two
# PRs. Use for sprints that need parallel backend + UI work; skip when
# the sprint is backend-only. Requires gh authenticated for both repos.

set -euo pipefail

CARRY=0
WITH_UI=0
while [[ "${1:-}" == --* ]]; do
    case "$1" in
        --carry)    CARRY=1; shift ;;
        --with-ui)  WITH_UI=1; shift ;;
        *)
            echo "Unknown flag: $1" >&2
            echo "Usage: $0 [--carry] [--with-ui] <NN> <topic-slug> [\"PR title\"]" >&2
            exit 1
            ;;
    esac
done

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 [--carry] [--with-ui] <NN> <topic-slug> [\"PR title\"]" >&2
    exit 1
fi

NN="$1"
topic="$2"
title="${3:-feat(sprint-${NN}): ${topic//-/ }}"
branch="sprint-${NN}/${topic}"

current_branch="$(git rev-parse --abbrev-ref HEAD)"
dirty=0
if [[ -n $(git status --porcelain) ]]; then
    dirty=1
fi

if (( dirty )) && (( ! CARRY )); then
    echo "Working tree not clean. Commit, stash, or re-run with --carry to bring WIP onto the new branch." >&2
    exit 1
fi

stash_ref=""
if (( CARRY )) && (( dirty )); then
    if [[ "$current_branch" != "main" ]]; then
        echo "--carry only supports stashing from main (currently on $current_branch)." >&2
        exit 1
    fi
    echo "==> Stashing in-flight planning artifacts (will pop onto $branch)"
    git stash push --include-untracked -m "start-sprint carry: ${branch}"
    stash_ref="stash@{0}"
fi

# Opt the clone into the tracked pre-push guard (blocks direct pushes to main).
# Idempotent: a no-op if already set.
if [[ "$(git config --get core.hooksPath || true)" != ".githooks" ]]; then
    echo "==> Setting core.hooksPath = .githooks (pre-push guard for main)"
    git config core.hooksPath .githooks
fi

echo "==> Updating main"
git checkout main
git pull --ff-only

echo "==> Creating branch $branch"
git checkout -b "$branch"

# Bump the docs/roadmap.md current-sprint badge so the rest of the org
# always knows which sprint is live. Idempotent — re-running on a branch
# that already bumped the badge is a no-op. README.md just points at this
# badge, so this single edit keeps both docs in sync.
roadmap="docs/roadmap.md"
if [[ -f "$roadmap" ]] && grep -q "current-sprint:start" "$roadmap"; then
    readable_topic="${topic//-/ }"
    new_badge="**Current sprint:** ${NN} — ${readable_topic} · branch \`${branch}\` (full scope lands in §sprint-${NN} during the sprint)."
    awk -v new="$new_badge" '
        /<!-- current-sprint:start -->/ { print; print new; in_block=1; next }
        /<!-- current-sprint:end -->/   { in_block=0 }
        !in_block { print }
    ' "$roadmap" > "${roadmap}.tmp" && mv "${roadmap}.tmp" "$roadmap"
    if ! git diff --quiet -- "$roadmap"; then
        echo "==> Bumped current-sprint badge in $roadmap"
        git add "$roadmap"
    fi
fi

if [[ -n "$stash_ref" ]]; then
    echo "==> Popping carried changes onto $branch"
    git stash pop
    echo "==> Committing carried planning artifacts"
    git add -A
    git commit -m "chore(sprint-${NN}): start branch with planning artifacts"
else
    # Empty commit so the PR has something to show
    git commit --allow-empty -m "chore(sprint-${NN}): start branch"
fi

echo "==> Pushing branch + creating draft PR"
git push -u origin "$branch"

backend_pr_body="Sprint ${NN} workstream. See \`docs/roadmap.md\` for scope.

## Cross-repo plan
_Fill in even when the answer is 'backend only' — explicit beats implicit._

- Backend: _TBD_
- UI: _TBD_
- OpenAPI: _change expected? regenerate \`openapi.json\` in this PR if yes_
- Merge order: _backend first when contract changes; otherwise independent_

## Checklist
- [ ] Implementation complete
- [ ] Tests added / updated
- [ ] \`make check\` clean
- [ ] CHANGELOG updated under \`## Unreleased\`
- [ ] Roadmap status updated in \`docs/roadmap.md\`
- [ ] \`openapi.json\` regenerated (if API surface changed)"

backend_pr_url=$(gh pr create --draft --base main --head "$branch" \
    --title "$title" \
    --body "$backend_pr_body")
echo "==> Backend PR: $backend_pr_url"

if (( WITH_UI )); then
    ui_path="${TAGPULSE_UI_PATH:-$HOME/ws/TagPulse-UI}"
    if [[ ! -d "$ui_path/.git" ]]; then
        echo "==> --with-ui requested but $ui_path is not a git repo; skipping" >&2
        echo "    set TAGPULSE_UI_PATH or clone TagPulse-UI to ~/ws/TagPulse-UI" >&2
    else
        ui_title="${title/feat(sprint-${NN})/feat(sprint-${NN})}"  # passthrough; user may rename
        echo "==> Creating matching UI branch at $ui_path"
        (
            cd "$ui_path"
            if [[ -n $(git status --porcelain) ]]; then
                echo "    UI working tree not clean; aborting UI branch creation" >&2
                exit 1
            fi
            git checkout main
            git pull --ff-only
            git checkout -b "$branch"
            git commit --allow-empty -m "chore(sprint-${NN}): start UI branch"
            git push -u origin "$branch"
            ui_pr_url=$(gh pr create --draft --base main --head "$branch" \
                --title "$ui_title" \
                --body "Sprint ${NN} UI workstream. Paired with backend PR: $backend_pr_url

## Cross-repo plan
See backend PR for the canonical plan: $backend_pr_url

## Checklist
- [ ] Implementation complete
- [ ] Tests added / updated
- [ ] \`npm run check\` clean
- [ ] CHANGELOG updated under \`## Unreleased\`
- [ ] \`openapi.json\` regenerated against backend PR's contract (record backend SHA below)
- [ ] Backend SHA \`openapi.json\` was generated against: _\`<sha>\`_")
            echo "    UI PR: $ui_pr_url"
        ) || echo "==> UI branch creation failed; backend branch is still good" >&2
    fi
fi

echo ""
echo "Done. You're now on $branch with a draft PR."
