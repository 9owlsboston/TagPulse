#!/usr/bin/env bash
# Ship a sprint: flip its docs/roadmap.md status to **shipped** (committed INTO
# the PR being merged), then squash-merge the sprint PR(s).
#
# This is the merge-time bookend to scripts/start-sprint.sh. It exists so that
# "mark the sprint shipped" is **never a separate post-merge chore** — the
# status flip rides into the same squash merge that ships the work, at the only
# correct moment (when you actually merge), done deliberately by the person
# shipping. No bot, no direct push to main, no staleness window.
#
# Usage:  scripts/ship-sprint.sh [--with-ui] [--yes]
#
#   Run from the **backend** repo while checked out on the sprint branch
#   (`sprint-NN/<topic>`). The sprint number + topic are read from the branch.
#
#   --with-ui : also squash-merge the matching UI PR (the `sprint-NN/<topic>`
#               branch in $TAGPULSE_UI_PATH, default ~/ws/TagPulse-UI). The UI
#               repo has no roadmap, so only the merge happens there.
#   --yes,-y  : skip the confirmation prompt (non-interactive).
#
# What it does on the current sprint branch:
#   1. Flips the Sprint NN entry in docs/roadmap.md:
#        - `> **Status (DATE, in progress).**`  →  `… , shipped).**`
#        - appends ` (shipped)` to the `## Sprint NN — …` header when it has no
#          status suffix yet (nuanced statuses like `(shipped — gated off)` are
#          left untouched — only the literal `in progress` token is rewritten)
#        - resets the current-sprint badge to a "shipped; between sprints" state
#   2. Commits the flip on the branch + pushes, so it is part of the squash.
#   3. Squash-merges the backend PR (and the UI PR with --with-ui), deleting the
#      merged branches. Uses `--auto` so GitHub merges once the flip commit's
#      checks pass; if the repo has no required checks it merges immediately.
#
# Requires: `gh` authenticated for both repos. For --auto to merge unattended,
# enable "Allow auto-merge" in the repo settings; otherwise the flip is still
# committed into the PR and you can complete the merge from the GitHub UI.
set -euo pipefail

WITH_UI=0
ASSUME_YES=0
while [[ "${1:-}" == --* || "${1:-}" == -y ]]; do
    case "$1" in
        --with-ui) WITH_UI=1; shift ;;
        --yes|-y)  ASSUME_YES=1; shift ;;
        *)
            echo "Unknown flag: $1" >&2
            echo "Usage: $0 [--with-ui] [--yes]" >&2
            exit 1
            ;;
    esac
done

branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ ! "$branch" =~ ^sprint-([0-9]+)/(.+)$ ]]; then
    echo "Not on a sprint-NN/<topic> branch (currently on '$branch')." >&2
    echo "Checkout the sprint branch in the backend repo first." >&2
    exit 1
fi
NN="${BASH_REMATCH[1]}"
topic="${BASH_REMATCH[2]}"
readable_topic="${topic//-/ }"

if [[ -n $(git status --porcelain) ]]; then
    echo "Working tree not clean. Commit or stash the sprint work before shipping." >&2
    exit 1
fi

roadmap="docs/roadmap.md"
if [[ ! -f "$roadmap" ]]; then
    echo "No $roadmap found — run from the backend repo root." >&2
    exit 1
fi
if ! grep -qE "^## Sprint ${NN} " "$roadmap"; then
    echo "No '## Sprint ${NN} ' entry in $roadmap — nothing to flip." >&2
    exit 1
fi

# Best-effort PR number for the badge label (don't fail shipping if absent).
pr_num="$(gh pr view --json number --jq .number 2>/dev/null || true)"
badge="**Current sprint:** ${NN} — ${readable_topic} · **shipped**"
[[ -n "$pr_num" ]] && badge="${badge} (PR #${pr_num})"
badge="${badge}; between sprints."

# Single awk pass: reset the badge, append (shipped) to the Sprint NN header,
# and flip that section's first Status line from `in progress` to `shipped`.
awk -v nn="$NN" -v badge="$badge" '
    /<!-- current-sprint:start -->/ { print; print badge; in_badge=1; next }
    /<!-- current-sprint:end -->/   { in_badge=0; print; next }
    in_badge { next }   # drop the old badge line(s) between the markers
    /^## Sprint / {
        if ($0 ~ ("^## Sprint " nn " ")) {
            in_sec = 1; status_done = 0
            if (index($0, "(shipped") == 0) { $0 = $0 " (shipped)" }
        } else {
            in_sec = 0
        }
        print; next
    }
    in_sec && !status_done && /^> \*\*Status \(/ {
        sub(/, in progress\)/, ", shipped)")
        status_done = 1
        print; next
    }
    { print }
' "$roadmap" > "${roadmap}.tmp" && mv "${roadmap}.tmp" "$roadmap"

if git diff --quiet -- "$roadmap"; then
    echo "==> Sprint ${NN} status already shipped in $roadmap (no change)."
else
    echo "==> Flipped Sprint ${NN} → shipped in $roadmap:"
    git --no-pager diff -- "$roadmap" | grep -E '^[+-]' | grep -vE '^[+-]{3}' | sed 's/^/    /'
fi

echo ""
echo "About to ship Sprint ${NN} (${readable_topic}):"
echo "  • commit the roadmap flip onto ${branch} and push"
echo "  • squash-merge ${branch} (backend) and delete it"
(( WITH_UI )) && echo "  • squash-merge the matching UI PR (${branch}) and delete it"
if (( ! ASSUME_YES )); then
    read -rp "Proceed? [y/N] " ans
    [[ "$ans" == [yY]* ]] || { echo "Aborted."; git checkout -- "$roadmap" 2>/dev/null || true; exit 1; }
fi

if ! git diff --quiet -- "$roadmap"; then
    git add "$roadmap"
    git commit -m "docs(sprint-${NN}): mark shipped"
fi
git push

echo "==> Squash-merging backend PR for ${branch}"
gh pr merge "$branch" --squash --delete-branch --auto

if (( WITH_UI )); then
    ui_path="${TAGPULSE_UI_PATH:-$HOME/ws/TagPulse-UI}"
    if [[ ! -d "$ui_path/.git" ]]; then
        echo "==> --with-ui requested but $ui_path is not a git repo; skipping UI merge." >&2
    else
        echo "==> Squash-merging UI PR for ${branch} at ${ui_path}"
        ( cd "$ui_path" && gh pr merge "$branch" --squash --delete-branch --auto ) \
            || echo "==> UI merge could not be enabled; complete it from the GitHub UI." >&2
    fi
fi

echo ""
echo "Done. Sprint ${NN} flipped to shipped and merge enabled."
echo "If a PR doesn't merge automatically, enable 'Allow auto-merge' in repo settings"
echo "or merge it from the GitHub UI — the shipped flip is already committed in the PR."
