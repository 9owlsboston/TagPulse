# Backlog

Lightweight scratch list for **in-flight ideas** you don't want to lose
but won't pull into the active sprint. See
`.github/copilot-instructions.md` § Cross-Repo Workflow for the model.

## How to use this file

- Add a line whenever you notice something mid-work that's out of scope.
- Don't edit existing sprints/PRs to absorb the idea.
- Drain this file during sprint planning: each item either
  - gets promoted to `docs/roadmap.md` (becomes a future sprint), or
  - gets a `chore/<topic>` branch (small standalone PR), or
  - gets deleted (was a fleeting thought).

Format per entry: `- [YYYY-MM-DD] <one-line description> [tag]`
Tags: `[backend]`, `[ui]`, `[docs]`, `[ops]`, `[idea]`.

## Open items

- [2026-05-25] Normalize `reads-per-hour` sparkline `v` to reads/hr (currently bucket-volume, ~6× headline number with default `bucket_hours=6`); or rename the tile-id semantics. PR #79 follow-up. [backend]
- [2026-05-25] Eliminate double `get_summary()` per Dashboard load — `/sparklines` re-runs the 13-query summary that the UI already fetched via `/summary`. Either accept current values from client or drop flat tiles from `/sparklines`. PR #79 follow-up. [backend]

<!-- Add new items above this line. Oldest at bottom; remove when drained. -->
