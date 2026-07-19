# Copilot instructions — TagPulse

> The cross-tool source of truth is **`AGENTS.md`** at the repo
> root. This file is Copilot-specific and intentionally thin.

Read `AGENTS.md` first — it is the **cross-tool repo contract** (repo-specific
hard rules, where-to-write map, drift-rules, doc-lifecycle). The SDLC itself is
**not** repeated there: the 5-stage flow (Plan → Implement → Verify → Ship →
**close-out**), carve-outs (`noncodefix`, `spike`, `release`), commit-message
format, and change-logging convention live in the **global**
`~/.copilot/copilot-instructions.md` (SoT: `ai-tooling-config`), with the full
model + diagram in
[`dev-env-setup` `docs/guides/sdlc.md`](https://github.com/9owlsboston/dev-env-setup/blob/main/docs/guides/sdlc.md).

## When in doubt, switch personas

- Researching → `explorer`
- Planning a change → `planner`
- Writing code → `implementer`
- Auditing a PR → `verifier`
- A typo / doc-only fix → `noncodefix`
- Cutting a release → `release`

Personas live in `~/.copilot/agents/` (personal) or `.github/agents/` (repo).

## Repo-specific rules

The repo contract lives in [`AGENTS.md`](../AGENTS.md) — hard rules (§2), run/test (§3),
naming (§8), process & artifacts (§9), and the TagPulse ↔ TagPulse-UI cross-repo workflow
(§10). Add new repo-wide rules **there**, not here, so there is a single source of truth.
