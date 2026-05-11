# GitHub Workflows Catalog

A single index for every workflow in `.github/workflows/`. Use this as the
operator-facing "what runs automatically and when" reference. For developer
workflow (branching, sprints, releases), see
[../guides/contributor-workflow.md](../guides/contributor-workflow.md).

> All scheduled workflows authenticate to Azure via the dev OIDC service
> principal (`AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID`
> repo secrets). The SP has Contributor on `tagpulse-dev-rg` and AcrPush on
> the dev ACR. See [azure-first-deploy.md](azure-first-deploy.md) for
> service-principal setup.

## Catalog

| Workflow | Trigger | Schedule (UTC) | Purpose | Auto-issue on failure |
|---|---|---|---|---|
| `ci.yml` | PR, push to main | — | `make lint` + `make typecheck` + `make test` on Python 3.12 | no |
| `docs-lint.yml` | PR touching `docs/**`, `*.md`, `CHANGELOG.md` | — | `markdownlint-cli2` + `lychee` link check | no |
| `build-and-push.yml` | push to main, `v*` tags | — | Build api/worker/migrations images → push to GHCR + ACR (SHA + `latest` + version tag) | no |
| `deploy-azure.yml` | push to main (after `build-and-push`), manual | — | `azd deploy` api + worker, run migrations job | no (deploy logs are inspected manually) |
| `dev-wake.yml` | cron, manual | Mon–Fri 13:00 | Start dev PG if Stopped, restart api revision, run `make doctor ENV=dev` (non-blocking) | **yes** (label: `ops`) |
| `dev-kv-cleanup.yml` | cron, manual | daily 00:00 | Purge all KV `ipRules`, set `publicNetworkAccess=Disabled`, verify | **yes** (label: `ops`) |
| `rotate-ui-token.yml` | cron, manual | quarterly (1st Jan/Apr/Jul/Oct, 09:00) | Rotate the SWA deploy token, push as secret to `TagPulse-UI` | **yes** |

## Schedule cadence at a glance

```
00:00 UTC daily        →  dev-kv-cleanup.yml
13:00 UTC weekdays     →  dev-wake.yml
09:00 UTC quarterly    →  rotate-ui-token.yml  (1st of Jan/Apr/Jul/Oct)
on every push          →  ci.yml, docs-lint.yml (path-filtered)
on push to main        →  build-and-push.yml → deploy-azure.yml
on v* tag              →  build-and-push.yml (version-tagged images)
```

## Manual triggers

All scheduled workflows expose `workflow_dispatch`. To run from the CLI:

```bash
gh workflow run dev-wake.yml
gh workflow run dev-kv-cleanup.yml
gh workflow run rotate-ui-token.yml
gh workflow run deploy-azure.yml          # redeploy current main

# watch the run start
gh run list --workflow=dev-wake.yml --limit 5
gh run watch
```

Or in the GitHub UI: **Actions** tab → pick the workflow → **Run workflow**.

## Triage a failed scheduled run

1. Open the auto-filed issue (label `ops`) — it links the failing run.
2. Or list recent runs:
   ```bash
   gh run list --workflow=<file>.yml --limit 5
   gh run view <RUN_ID> --log-failed
   ```
3. Re-run after fix:
   ```bash
   gh run rerun <RUN_ID> --failed
   # or trigger fresh
   gh workflow run <file>.yml
   ```
4. Cross-reference the related runbook:
   - `dev-wake.yml` failure → [db-failover-and-restore.md](db-failover-and-restore.md) §A
   - `dev-kv-cleanup.yml` failure → [secret-rotation.md](secret-rotation.md)
   - `deploy-azure.yml` failure → [azd-survival-guide.md](azd-survival-guide.md)
   - `rotate-ui-token.yml` failure → [secret-rotation.md](secret-rotation.md) §UI

## Adding a new workflow

When introducing a workflow:

1. Add a YAML header comment that explains *what* and *why* (see
   `dev-wake.yml` for the pattern).
2. Add a row to the catalog table above.
3. If it's scheduled, add it to the cadence block.
4. If it auto-files issues, add the failing-run triage entry.
5. Update `CHANGELOG.md` under `## Unreleased`.
