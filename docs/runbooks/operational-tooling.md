# Runbook: Operational Tooling Job

> **Sprint 26 deliverable.** Lets operators run any script in [`scripts/`](../../scripts/)
> against a deployed env without poking holes in the private Postgres
> firewall, and without shipping a separate "tools" image.
>
> **Pairs with:** [docs/runbooks/azure-first-deploy.md § Phase 3c — Operational scripts](azure-first-deploy.md).
> Read that section first to understand which scripts are live-safe.

## How it works

1. The api Docker image's `base` stage now includes `scripts/` (Sprint 26 A1).
2. A Container Apps Job (`<env>-tools`) reuses that image and runs in-VNet
   with the workload's UAMI — same Postgres, KV, ACR, and Application
   Insights wiring as the api/worker (Sprint 26 B1 + B2).
3. `scripts/azd-job.sh` (C1) overrides the job's `command` + `args` at start
   time, polls for completion, then tails Log Analytics for the execution's
   stdout.

```
operator → azd-job.sh ─┐
                       │ az containerapp job update / start
                       ▼
              tools-job (single replica)
                       │ runs python scripts/<name>.py …
                       │ stdout → ContainerAppConsoleLogs_CL
                       ▼
              Log Analytics (90-day retention)
                       │ az monitor log-analytics query
                       ▼
              operator's terminal
```

## Prerequisites

- `az login` with Contributor on the target resource group.
- `azd env select <env>` resolves to the env you intend to target.
- The `tools-job` exists in that env. It's provisioned automatically by
  `azd up` once Sprint 26 B2 has landed; verify with:
  ```bash
  az containerapp job show -n "$(azd env get-value toolsJobName)" \
    -g "$(azd env get-value AZURE_RESOURCE_GROUP)" \
    --query '{status:properties.provisioningState, image:properties.template.containers[0].image}'
  ```

## Worked examples

### 1. Seed the demo tenant (closes the Sprint 25 dev gap)

```bash
scripts/azd-job.sh dev smoke_setup.py -- \
  --full --with-roles --with-subject-telemetry --regenerate-key
```

Expected: ~3 minutes wall-clock. Fresh admin/editor/viewer keys land in
Key Vault as `tagpulse-test-corp-{admin,editor,viewer}-key` (Sprint 26 D3
makes that the default inside the job — no plaintext on stdout). Pull the
admin key into your shell:

```bash
KV=$(azd env get-value keyVaultName)
export TAGPULSE_API_KEY=$(az keyvault secret show \
  --vault-name "$KV" --name tagpulse-test-corp-admin-key \
  --query value -o tsv)
```

Verify end-to-end: the SPA's **Tenant ID** login tab should now succeed
with `11111111-1111-1111-1111-111111111111`.

### 2. Rotate the demo admin's API key

```bash
scripts/azd-job.sh dev smoke_setup.py -- --regenerate-key
```

Idempotent. The previous version of `tagpulse-test-corp-admin-key` is
preserved as a non-current version of the KV secret (KV soft-delete is
on for non-dev envs; dev relies on the secret-version history).

### 3. Push fake telemetry into a deployed env for a load smoke

```bash
scripts/azd-job.sh dev simulate_devices.py -- \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  --devices 5 --interval 2 --duration 60 --with-gps
```

`--duration` is **not optional** when running against staging/prod — the
default behavior is to loop forever, which would happily saturate the api
until the operator notices.

### 3b. Stage the inventory "Boston DC" scenario in a deployed env

```bash
scripts/azd-job.sh dev simulate_inventory.py -- \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  --duration 240
```

Idempotently provisions a `Boston DC` site, 4 reader-bound zones
(Receiving Dock / Cold Storage / Pick Floor / Shipping Dock), 4 distinct
products with distinct lot codes per product (one near-expiry Milk lot to
fire `stock.expiring_within`), and a finite pool of stock units with
stable SGTIN-96 serials. Each unit advances Receiving → Cold Storage →
(Pick Floor → Shipping) on a randomised timeline, populating
`stock_movements` and per-zone counts on **Stock Levels**. Re-runs reuse
existing rows (idempotent by name / SKU / lot_code / serial).

Full scenario walk-through, options, and UI/API verification:
[docs/quickstart.md §6c — Inventory Tracking Smoke Test](../quickstart.md#6c-inventory-tracking-smoke-test).

Requires **Inventory tracking** enabled on the target tenant (Tracking
Modes UI, or `PATCH /tenant/config`). `TAGPULSE_API_KEY` is auto-injected
from Key Vault by the tools job — no `--api-key` flag needed.

### 4. Re-tail logs after a terminal disconnect

```bash
scripts/azd-job.sh dev smoke_setup.py --update-only
```

Skips the `az containerapp job update / start` and just re-queries Log
Analytics for the most recent execution. Safe to repeat if the LA
ingestion lag (~30s) leaves the first call empty.

## What NOT to run via the tools-job

| Script | Why not |
|---|---|
| `load_test.py` | Default target is `localhost:8000`. If pointed at the deployed api FQDN, it hits the api's *own* egress and triggers autoscale + cost. Run from a laptop or a separate load-test VM instead. |
| `start-sprint.sh`, `azd-bootstrap.sh`, `azd-cicd-setup.sh` | Operator-side workflow tooling. They mutate the local `azd` env and `gh` repo settings — running them inside a job has no useful effect and may corrupt state. |
| Anything destructive (e.g. a future `reset_tenant.py`) | Gated on a `--i-know-what-im-doing` flag in the wrapper. Sprint 27+ will likely add an allow-list of script names. |

The wrapper does not enforce these today — the contract is currently
documented + reviewer-enforced. If we hit one near-miss, we'll add an
allow-list.

## Stale image gotcha

The job runs the **deployed** image. Local edits to `scripts/<name>.py`
are invisible to the job until the next `azd deploy`. The wrapper refuses
to start by default if the working tree is dirty or has unpushed commits;
override with `--allow-stale` if you intentionally want to run the
deployed version against a not-yet-pushed branch.

```
error: 2 local commit(s) not yet pushed to origin/sprint-26/foo.
       Push + redeploy first, or pass --allow-stale.
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `azd env $ENV is missing toolsJobName` | `azd up` hasn't run since B2 landed | `azd up` (or `azd provision` if image is unchanged). |
| Execution status `Failed`, no logs visible | LA ingestion lag (~30s) | Re-run with `--update-only` after 30s. |
| `Forbidden` on KV write | UAMI missing `Key Vault Secrets Officer` | Re-run `azd provision`; identity.bicep grants the role at deploy time (Sprint 26 D3 ride-along on B1). |
| `connection refused` to Postgres | env still points at public-only Postgres | Confirm the job is running in the env's VNet: `az containerapp job show -n <job> -g <rg> --query 'properties.template.containers[0].env'`. |
| Logs show `ImportError: scripts.smoke_setup` | image pre-dates Sprint 26 A1 | Redeploy: `azd deploy api`. |

## See also

- Phase 3c contract: [azure-first-deploy.md § Phase 3c](azure-first-deploy.md)
- KV-push behavior: [scripts/smoke_setup.py § `--key-vault-name`](../../scripts/smoke_setup.py)
- Roadmap entry: [docs/roadmap.md § Sprint 26](../roadmap.md)
