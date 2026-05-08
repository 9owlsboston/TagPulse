# Azure First-Deploy Checklist

End-to-end checklist for standing TagPulse up in a fresh Azure environment.
Works for `dev`, `staging`, or `prod` — each runs independently with its own
resource group, Container Registry, Key Vault, and CD identity.

> Companion docs: [deploy/azure/README.md](../../deploy/azure/README.md) for
> module/SKU details, [docs/adr/016-cloud-readiness.md](../adr/016-cloud-readiness.md)
> for the design decisions, [CONTRIBUTING.md](../../CONTRIBUTING.md) for sprint workflow.

---

## Deployment paths — what triggers what

There are **three distinct flows** that put code on Azure. They share the
same images but have different triggers and run different scripts. Knowing
which flow you're on tells you which artifacts get rebuilt and which hooks
fire.

### 1. Continuous integration — every push to `main`

```
git push origin main
        │
        ▼
.github/workflows/build-and-push.yml
   matrix: [api, worker, migrations]
   • docker build --target <component>
   • docker push ghcr.io/9owlsboston/tagpulse-<c>:<sha>
   • docker push <acr>.azurecr.io/tagpulse-<c>:<sha>   (if AZURE_ACR_NAME var is set)
        │
        ▼
   Images sit in ACR. Nothing on Azure changes yet.
```

### 2. Production deploy — `v*` tag push or manual dispatch

```
git tag v1.2.3 && git push --tags          (or: Actions UI → "Run workflow")
        │
        ▼
.github/workflows/deploy-azure.yml
   • OIDC login to Azure (no PATs)
   • production environment → manual approval gate
   • verify api/worker/migrations images exist in ACR @ tag
   • az containerapp job start <env>-migrations
   • az containerapp update --image …
   • smoke: GET https://<api>/health/ready
        │
        ▼
   API + worker rolled to new revision on Container Apps.
   ⚠ DOES NOT call `azd`. The local hooks (pg-ensure,
   network-check) are NEVER invoked here.
```

### 3. Operator deploy — `azd up` / `azd deploy` from your laptop

```
azd up   |   azd deploy
        │
        ▼
azd lifecycle (phases 4 + 7 are the local hooks)
   1. preprovision hook
   2. terraform/bicep deploy   (provision)
   3. postprovision hook
        └─ exports envs (postgresFqdn, keyVaultName, …)
   4. predeploy hook
        └─ scripts/azd-pg-ensure-running.sh
   5. docker build + push (api/worker/migrations) to ACR
   6. az containerapp update
   7. postdeploy hook
        ├─ run migrations job + poll
        └─ scripts/azd-network-check.sh
```

### Summary

| Flow | Triggered by | Builds images? | Updates Azure? | Runs `azd` hooks? |
|------|------|------|------|------|
| 1. build-and-push | push to `main` | yes | no | no |
| 2. deploy-azure | `v*` tag / manual | no (reuses ACR) | yes | no |
| 3. local `azd up` / `azd deploy` | operator | yes (local) | yes | **yes** |

Scripts under `scripts/azd-*.sh` only run in Flow 3 — they live on the
operator's workstation and are read from the working tree at hook time.
A merge to `main` makes them available to anyone who pulls; it does not
deploy them anywhere.

---

## Phase 0 — Prerequisites (one-time, per workstation)

> **Shortcut:** run [`scripts/azd-preflight.sh`](../../scripts/azd-preflight.sh)
> to check every item below in one shot. It exits non-zero on any blocking
> failure and prints exact `az provider register …` / `az login` fix
> commands. Re-run after fixing each issue until it passes.

- [ ] **Azure CLI ≥ 2.60** — `az version`
- [ ] **azd ≥ 1.10** — `azd version`
- [ ] **Docker** running locally (azd builds images via Docker)
- [ ] **gh CLI** signed in to `9owlsboston` org (for setting Environment vars)
- [ ] `az login` and `azd auth login` complete; `az account show` returns the right tenant
- [ ] Subscription has the **`Microsoft.App`**, **`Microsoft.ContainerRegistry`**, **`Microsoft.DBforPostgreSQL`**, **`Microsoft.OperationalInsights`**, **`Microsoft.Insights`**, **`Microsoft.KeyVault`**, **`Microsoft.ContainerInstance`**, and **`Microsoft.Web`** resource providers registered (`az provider list --query "[?registrationState=='Registered'].namespace" -o tsv`)
- [ ] You have **Owner** or **Contributor + User Access Administrator** on the target subscription (RBAC role assignments require it)
- [ ] `scripts/azd-preflight.sh` exits 0

---

## Phase 1 — Bootstrap the local env file (per environment)

For each new environment (`dev` / `staging` / `prod`), run **once**:

- [ ] `scripts/azd-bootstrap.sh <env>` completed without errors
  - Confirm subscription id is correct
  - Confirm region (default = `southcentralus`)
  - Generated `deploy/azure/.env.<env>` exists at mode 600 (`ls -l`)
  - azd env `tagpulse-<env>` was created (`azd env list`)
- [ ] Inspect generated values; do **not** commit the file (`git status` should not list it)
- [ ] If overriding any defaults (region, RG, name prefix), edit the `.env.<env>` file before continuing

---

## Phase 2 — First `azd up` (per environment)

- [ ] `scripts/azd-env-load.sh <env>` ran clean (selects azd env + pushes vars)
- [ ] `azd env get-values | grep -E 'AZURE_(SUBSCRIPTION_ID|LOCATION|RESOURCE_GROUP|NAME_PREFIX)|TAGPULSE_ENVIRONMENT'` matches what you expect
- [ ] `azd up` completes (~10–15 min on a cold sub):
  - [ ] Provision phase prints `✓ Done` for resource group + workload module
  - [ ] Build phase pushes `tagpulse-{api,worker,migrations}:azd-deploy-…` to ACR
  - [ ] Postdeploy hook reports `Migrations execution: …  Succeeded`
  - [ ] Final output shows `apiFqdn = tpdev-api.<random>.<region>.azurecontainerapps.io` (azd auto-promotes this Bicep output into env values; `https://$(azd env get-value apiFqdn)` is the canonical api URL going forward — `SERVICE_API_URI` is printed at deploy time but not persisted by azd for the `containerapp` host)
  - [ ] _Self-healing note:_ the `preprovision` hook auto-recovers any soft-deleted Key Vault matching this env's prefix, so re-running `azd up` after a teardown does **not** require purging or renaming. See [`scripts/azd-kv-recover.sh`](../../scripts/azd-kv-recover.sh).
  - [ ] _Sprint 23 note:_ the broker config + password are now baked into the [`tagpulse-mqtt`](../../docker/mosquitto.Dockerfile) image (built into ACR by [`scripts/azd-mqtt-build.sh`](../../scripts/azd-mqtt-build.sh)). **No post-`azd up` MQTT bootstrap step is required.** First `azd up` runs the broker on a placeholder image; second `azd up` (after the build has populated `tagpulse-mqtt:<tag>` in ACR) provisions the ACI on the real image.
  - [ ] _Subscription with corporate `allowSharedKeyAccess` policy?_ Sprint 23 Phase A is mandatory — Sprint 22's Azure Files volume mount cannot satisfy a `Modify`-mode policy and the broker will fail with `CannotAccessStorageAccount`.

---

## Phase 3 — Post-deploy smoke tests

> The api URL is `https://$(azd env get-value apiFqdn)`. Export it once for the rest of the phase: `API=https://$(azd env get-value apiFqdn)`.

- [ ] `curl "$API/health/live"` →
      ```
      {"status":"alive","version":"<git-sha>","build_time":"<iso8601>"}
      ```
      Sprint 25 A1: `version` + `build_time` come from the Dockerfile build args (`BUILD_VERSION`, `BUILD_TIME`); a value of `dev`/`unknown` means the deployed image was built outside the GHA pipeline.
- [ ] `curl -I "$API/health/live" | grep -i cache-control` → `Cache-Control: no-store` (Sprint 25 A1; lets the SPA's startup gate poll without the SWA edge memoizing).
- [ ] `curl "$API/health/ready" | jq` →
  - [ ] `status: "ready"`
  - [ ] `checks.db == "ok"`
  - [ ] `checks.migrations.match == true`
  - [ ] `checks.mqtt == "ok"` (worker has connected)
  - [ ] `config.environment` matches `TAGPULSE_ENVIRONMENT` from your `.env.<env>`
  - [ ] `config.strict_migration_check == true` for staging/prod
  - [ ] `config.cors.allow_origins` contains the SWA hostname (see CORS step below; will be empty until Sprint 24 wiring lands)
- [ ] `TAGPULSE_API_URL="$API" python scripts/smoke_setup.py --full` exits 0
- [ ] App Insights receiving traces:
  - [ ] Open the App Insights resource → **Transaction search** → confirm `GET /health/ready` spans appear within 2 minutes of the smoke test
- [ ] Worker is processing:
  - [ ] `az containerapp logs show --name tagpulse-worker --resource-group <rg> --follow` shows `MQTT subscriber connected` and no exception spam

### 3a — Add the SWA hostname to CORS (Sprint 24 A4)

Sprint 22 C-1 provisions the Static Web App but leaves `CORS_ALLOW_ORIGINS`
in `.env.<env>` set to whatever you bootstrapped with — typically just
the api FQDN itself plus `http://localhost:5173`. The deployed SPA will
load fine but every fetch from `https://<swa-host>` will be blocked by
the strict-mode CORS validator (see [Sprint 22 A2](../../src/tagpulse/core/config.py)).

Order matters: do this *before* deploying the UI bundle, otherwise the
first browser session sees a wall of CORS errors.

```bash
SWA_HOST=$(azd env get-value staticWebAppHostname)
API_HOST=$(azd env get-value apiFqdn)

# Append, don't overwrite — keep localhost for dev iteration.
CURRENT=$(grep -E '^CORS_ORIGINS=' deploy/azure/.env.<env> | cut -d= -f2-)
sed -i.bak "s|^CORS_ORIGINS=.*|CORS_ORIGINS=${CURRENT},https://${SWA_HOST}|" \
    deploy/azure/.env.<env>

scripts/azd-env-load.sh <env>
azd provision     # pushes the new origin list into the api revision
```

Verify: `curl "https://$(azd env get-value apiFqdn)/health/ready" | jq '.config.cors.allow_origins'`
should now include `https://<swa-host>`.

### 3b — SPA-vs-api consistency smoke (Sprint 25 A4)

After the SWA deploys (UI repo, Sprint 24) and CORS is wired (3a), confirm
the SPA was built against the right api. The most insidious post-deploy
failure mode is a SPA shipped against a *stale* `VITE_API_BASE_URL` — e.g.
the backend env was recreated and the api FQDN changed but the GH
Environment variable in the UI repo wasn't refreshed.

```bash
SWA="https://$(azd env get-value staticWebAppHostname)"
API="https://$(azd env get-value apiFqdn)"

# 1. SPA loads + advertises the api it was built against.
curl -fsS "$SWA/" -o /tmp/index.html
# Hash of the main asset changes on every UI deploy; useful for cache-bust audit.
grep -oE '/assets/main-[^"]+\.js' /tmp/index.html | head -1

# 2. The api the SPA points at is healthy.
curl -fsS "$API/health/ready" | jq '.status, .checks.database.status, .checks.mqtt.status'
# Expect: "healthy" / "up" / "up".

# 3. The SPA hostname is in the api's CORS allow-list (round-trip 3a).
curl -fsS "$API/health/ready" | jq -e ".config.cors.allow_origins | index(\"$SWA\") != null"
```

If step 3 fails, you shipped CORS without the SWA host or you're staring at
the wrong `apiFqdn` — re-run 3a against the *correct* azd environment.

### 3c — CSP violation triage (Sprint 25 A4)

Once the UI ships the report-only CSP header (Sprint 25 B5, follow-up to
backend A3), the SPA quietly POSTs `application/csp-report` /
`application/reports+json` envelopes to `${API}/security/csp-report` whenever
a browser blocks a resource the policy doesn't allow. The endpoint:

- emits a structured WARN log with `(blocked_uri, document_uri,
  violated_directive, source_file, line_number, column_number, user_agent)`,
- increments the Prometheus counter
  `tagpulse_csp_violations_total{directive="<name>"}`,
- is per-IP rate-limited at 10 reports/minute (DoS protection against a
  noisy browser extension).

Routine triage (weekly during the report-only burn-in window):

```kql
// Log Analytics
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == "tpdev-api"
| where Log_s contains "csp.violation"
| extend body = parse_json(Log_s)
| summarize count() by tostring(body.violated_directive), tostring(body.blocked_uri)
| order by count_ desc
| take 50
```

Treat any `directive == 'script-src'` or `'connect-src'` violation as
high-priority — that's the policy actually blocking SPA functionality.
`img-src`/`font-src`/`style-src` violations are usually third-party
extensions or mis-cached SPA assets and can be batched. Document any
expected origin and add it to `staticwebapp.config.json` (UI repo) before
flipping report-only → enforced (Sprint 26+).

---

## Phase 3c — Operational scripts (Sprint 26)

Anything in [`scripts/`](../../scripts/) that talks to a deployed environment
is **designed to run from one of two places**:

| Caller | When | Auth |
|---|---|---|
| Operator's laptop | Local dev against `make run`, or one-off cloud queries when public Postgres firewalling allows | `az login` + `TAGPULSE_API_URL` + `TAGPULSE_API_KEY` exported |
| **Tools-job in Container Apps** ([`scripts/azd-job.sh`](../../scripts/azd-job.sh), Sprint 26 C1) | Anything that needs in-VNet Postgres access, or that runs against staging/prod | Job's user-assigned managed identity; the wrapper resolves env vars from `azd env get-values` |

### Env-var contract every "live-safe" script must satisfy

| Var | Required? | What it points at |
|---|---|---|
| `TAGPULSE_API_URL` | yes | `https://$(azd env get-value apiFqdn)` (or `http://localhost:8000` for laptop dev) |
| `TAGPULSE_API_KEY` | yes (after first run) | Admin API key for the target tenant. First-run smoke seeds it; subsequent runs reuse it. |
| `DATABASE_URL` *or* `TAGPULSE_SMOKE_DB_URL` | yes for scripts that do raw SQL (`smoke_setup.py`) | Direct Postgres URL. The tools-job receives this via the same secret as `tpdev-migrations`. |
| `TAGPULSE_SMOKE_KEY_VAULT_NAME` | optional | When set, `smoke_setup.py` pushes plaintext keys to KV instead of stdout (Sprint 26 D3). The tools-job sets this by default. |

### Live-safe vs local-only

| Script | Live-safe? | Notes |
|---|---|---|
| `smoke_setup.py` | ✅ yes | Idempotent. Re-runs require `$TAGPULSE_API_KEY` already exported (or `--regenerate-key`). With `--key-vault-name` no plaintext leaves the job. |
| `simulate_devices.py` | ✅ yes (low volume) | Useful for end-to-end smoke after `azd up`. Throttle with `--interval`; **do not** run against `staging`/`prod` without `--duration` set. |
| `simulate_assets.py`, `simulate_inventory.py` | ✅ yes (one-shot) | Idempotent fixture provisioning. Safe to re-run. |
| `benchmark_pg_metrics.py` | ✅ yes (read-only) | Connects directly to Postgres; in-VNet path via the tools-job is the only way against private clusters. |
| `load_test.py` | ❌ **local only** | Saturates `localhost:8000` by default; running it via the tools-job would hit the api's *own* egress and could trigger autoscale + cost. |
| `start-sprint.sh`, `azd-*.sh` | ❌ **local only** | Operator-side workflow tooling. Never invoke via the job. |

The job's image is the api image — when [`Dockerfile`](../../Dockerfile)'s
`base` stage runs `COPY scripts/ scripts/` (Sprint 26 A1), every live-safe
script above is available at `/app/scripts/<name>.py`. Adding a new
live-safe script is a one-line PR after honoring the env-var contract.

### 3d — Seed the demo tenant + verify Tenant ID login (Sprint 26 D1)

Closes the Sprint 25 follow-up gap that left the deployed SPA's **Tenant ID**
login flow with no working tenant out of the box. Run this **once** after the
first `azd up` of any new env; idempotent on re-run.

```bash
# 1. Seed Test Corp (tenant_id 11111111-1111-1111-1111-111111111111),
#    create admin/editor/viewer roles, push a few subject-telemetry rows,
#    and rotate a fresh admin key into Key Vault.
scripts/azd-job.sh dev smoke_setup.py -- \
  --full --with-roles --with-subject-telemetry --regenerate-key

# 2. Pull the freshly-rotated admin key from KV (the job's --key-vault-name
#    default means the plaintext never hit Log Analytics).
KV=$(azd env get-value keyVaultName)
export TAGPULSE_API_KEY=$(az keyvault secret show \
  --vault-name "$KV" --name tagpulse-test-corp-admin-key \
  --query value -o tsv)

# 3. Verify the api sees the tenant.
API=$(azd env get-value apiFqdn)
curl -fsS "https://$API/tenant/config" \
  -H "X-Tenant-Id: 11111111-1111-1111-1111-111111111111" \
  -H "Authorization: Bearer $TAGPULSE_API_KEY" \
  | jq -r '.name'
# Expected: "Test Corp"

# 4. Verify the SPA's Tenant ID login flow works.
#    Open https://$(azd env get-value staticWebAppHostname) → Login → "Tenant ID" tab →
#    paste 11111111-1111-1111-1111-111111111111 + the admin key from step 2.
#    Should land on the dashboard with the seeded subject-telemetry visible.
```

If step 3 returns `404` or step 4 lands on "tenant not found", re-check the
`smoke_setup.py` execution status:

```bash
JOB=$(azd env get-value toolsJobName)
RG=$(azd env get-value AZURE_RESOURCE_GROUP)
az containerapp job execution list -n "$JOB" -g "$RG" \
  --query '[0].{status:properties.status, start:properties.startTime, end:properties.endTime}' -o table
```

Common failures: `Forbidden` writing to KV (the workload UAMI didn't pick up
the Sprint 26 B1 ride-along Key Vault Secrets Officer assignment — re-run
`azd provision`); `connection refused` to Postgres (the env's tools-job is
provisioned in the wrong VNet — confirm `properties.template.containers[0].env`
on the job has the same `POSTGRES_FQDN` as `tpdev-migrations`).

---

## Phase 4 — Wire up CI/CD (one-time, per environment)

> **Shortcut:** run [`scripts/azd-cicd-setup.sh <env>`](../../scripts/azd-cicd-setup.sh)
> after `azd up` succeeds. It is idempotent and performs every step in this
> phase (GitHub Environment, Entra app + federated credential, RBAC, the
> 5 Environment variables). Then run
> [`scripts/azd-cicd-verify.sh <env>`](../../scripts/azd-cicd-verify.sh)
> to confirm. The manual checklist below documents what those scripts do
> for audit / drift purposes.

- [ ] `scripts/azd-cicd-setup.sh <env>` exits 0
- [ ] `scripts/azd-cicd-verify.sh <env>` exits 0
- [ ] **GitHub Environment created** (Settings → Environments → New) named exactly `dev` / `staging` / `production`
  - [ ] `production`: required reviewer added; deployment branches restricted to `main` + `v*` tags
- [ ] **Entra app registration + federated credential created** per environment (see [deploy/azure/README.md § One-time setup](../../deploy/azure/README.md#one-time-setup-per-environment) for the `az ad app …` snippet)
  - [ ] `az ad app federated-credential list --id <appId>` shows subject `repo:9owlsboston/TagPulse:environment:<env>`
- [ ] **Role assignments verified**:
  - [ ] `Contributor` on the env's resource group
  - [ ] `AcrPush` on the env's ACR
  - [ ] `az role assignment list --assignee <appId> -o table` reflects both
- [ ] **GitHub Environment variables set** (5 variables, no secrets):
  - [ ] `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `AZURE_ACR_NAME`
- [ ] **Test dispatch** — `gh workflow run deploy-azure.yml -f environment=<env> -f image_tag=<existing-tag>` succeeds end-to-end (no token errors, image verification passes, smoke test green)

---

## Phase 5 — Production cutover gates (production only)

Before flipping DNS / opening to real users, confirm:

- [ ] Postgres backup verified — `az postgres flexible-server backup list` shows at least one automated backup
- [ ] Key Vault soft-delete + purge-protection enabled (defaults are off-by-policy in some tenants — `az keyvault show` → `properties.enableSoftDelete && enablePurgeProtection`)
- [ ] App Insights sampling reviewed (the Bicep default is 100%; reduce if cost is a concern)
- [ ] Log Analytics retention set to your compliance window (default is 30 days)
- [ ] Alerts configured against the four key signals: HTTP 5xx rate, DB connection failures, MQTT disconnect duration, migrations-job failures
- [ ] **Hardening backlog reviewed** — see [deploy/azure/README.md § Hardening backlog](../../deploy/azure/README.md#hardening-backlog-deferred-by-design). At minimum decide whether each item is *deferred* or *blocking* for your launch:
  - [ ] Postgres private endpoint
  - [ ] Front Door + WAF
  - [ ] EMQX HA broker (replaces single-node ACI Mosquitto)
  - [ ] Passwordless Postgres via Entra ID
  - [ ] Geo-redundant Postgres backup
- [ ] First-deploy tag pushed: `git tag v0.22.0 && git push --tags` triggers `deploy-azure.yml` → `production` Environment → reviewer approves → migrations job runs → api/worker rolled → smoke test passes

---

## Common failures (top 10)

| Symptom | Likely cause | Fix |
|---|---|---|
| `azd up` fails at provision: `Authorization failed` | Missing role on subscription | Get Owner or Contributor + UAA, retry |
| `azd up` fails: `VaultAlreadyExists … recently deleted but not purged` | Previous teardown left a soft-deleted KV; the `preprovision` hook couldn't recover it | Run `scripts/azd-kv-recover.sh <env>` manually. If it reports a permission error, grant `Key Vault Contributor` at subscription scope (see error message), then retry `azd up`. **No need to tear down working resources.** |
| Provision succeeds but ACA fails to start: `ImagePullBackOff` | UAMI missing `AcrPull` on ACR | `az role assignment create --assignee <uami-principal> --role AcrPull --scope <acr-id>` |
| Mosquitto ACI fails to start: `mosquitto-entrypoint: MOSQUITTO_USERNAME and MOSQUITTO_PASSWORD must be set` | KV `mqtt-broker-password` secret never populated, or UAMI missing `Key Vault Secrets User` on KV | Confirm `AZURE_MQTT_PASSWORD` is in `deploy/azure/.env.<env>`; run `scripts/azd-env-load.sh <env>`; rerun `azd provision` so KV reseeds. |
| `/health/ready` returns `migrations.match=false` | Migrations job didn't run, or ran older image | Re-run job: `az containerapp job start --name tagpulse-migrations -g <rg>` |
| `/health/ready` shows `checks.mqtt=="error"` | Broker still on placeholder image (`aci-helloworld`) — first `azd up` provisions the ACI before `tagpulse-mqtt` is in ACR | Run `azd up` a second time. The preprovision hook builds + pushes the image, image-check flips placeholders off, the ACI re-provisions on the real broker. |
| App refuses to start: `jwt_secret missing` and `environment != "dev"` | Strict-mode validator (Phase A1) tripped | Confirm `AZURE_JWT_SECRET` was set; re-run `azd-env-load.sh` then `azd provision` |
| App refuses to start: `CORS allow_origins contains "*"` | Strict-mode validator (Phase A2) tripped | Set `CORS_ALLOW_ORIGINS=https://app.example.com` and redeploy |
| GHA deploy fails with `AADSTS70021: No matching federated identity record found` | Federated-credential subject mismatch | Confirm the credential's subject is exactly `repo:9owlsboston/TagPulse:environment:<env>` (case-sensitive) |
| GHA deploy fails on `Verify all three images exist in ACR` | Tag wasn't pushed by `build-and-push.yml` | Check the build workflow ran on this commit; for manual dispatch pass an explicit `image_tag` |
| Postgres connection from ACA fails: `no pg_hba.conf entry` | Firewall rule missing | The Bicep adds a `0.0.0.0` rule; confirm with `az postgres flexible-server firewall-rule list`. (Replace with private endpoint in hardening sprint.) |
| **Backend appears completely down** — `/health/ready` 5xx or curl times out, replica `ready: false`, logs show `asyncpg.exceptions.ConnectionDoesNotExistError` then `TimeoutError` on `asyncpg.connect` | **Flexible Server is `Stopped`.** Burstable-tier dev servers auto-stop after 7 days of inactivity (and are often stopped manually to save cost). The api keeps its replica running but every DB call fails, `/health/ready` flips unhealthy, ACA blocks ingress. | `az postgres flexible-server show -n <pg> -g <rg> --query state` — if `Stopped`, run `scripts/azd-pg-ensure-running.sh` (or `az postgres flexible-server start …`), then `az containerapp revision restart -n <api> -g <rg> --revision <latest>` to drain the stale connection pool. The script is also wired as the `azd` `predeploy` hook so `azd deploy` self-heals automatically. |
| `azd env select` complains the env doesn't exist | Cleanup happened or different machine | Re-run `scripts/azd-bootstrap.sh <env>` (it detects and offers to recreate) |

---

## Decommissioning an environment

```sh
azd env select tagpulse-<env>
azd down --purge --force        # deletes all resources + KV soft-deleted entries
rm deploy/azure/.env.<env>
```

The `--purge` flag is essential for Key Vault (the name is reserved otherwise
for 7 days). Re-bootstrap from `.env.<env>.example` when you're ready to
recreate.
