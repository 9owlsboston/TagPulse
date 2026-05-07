# Azure First-Deploy Checklist

End-to-end checklist for standing TagPulse up in a fresh Azure environment.
Works for `dev`, `staging`, or `prod` — each runs independently with its own
resource group, Container Registry, Key Vault, and CD identity.

> Companion docs: [deploy/azure/README.md](../../deploy/azure/README.md) for
> module/SKU details, [docs/adr/016-cloud-readiness.md](../adr/016-cloud-readiness.md)
> for the design decisions, [CONTRIBUTING.md](../../CONTRIBUTING.md) for sprint workflow.

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
  - [ ] Final output shows `SERVICE_API_URI = https://tagpulse-api.<random>.<region>.azurecontainerapps.io`
  - [ ] _Self-healing note:_ the `preprovision` hook auto-recovers any soft-deleted Key Vault matching this env's prefix, so re-running `azd up` after a teardown does **not** require purging or renaming. See [`scripts/azd-kv-recover.sh`](../../scripts/azd-kv-recover.sh).
- [ ] **MQTT broker bootstrap** (one-time after first `azd up` only)
  - [ ] `scripts/azd-bootstrap-mqtt.sh <env>` exits 0 (uploads `mosquitto.conf` + `mosquitto.passwd` to the `mosquitto-config` Azure Files share, restarts the ACI)
  - [ ] `az container logs --name tagpulse-mqtt --resource-group <rg>` shows `mosquitto version 2.x.x running`

---

## Phase 3 — Post-deploy smoke tests

- [ ] `curl "$(azd env get-value SERVICE_API_URI)/health/live"` → `{"status":"alive"}`
- [ ] `curl "$(azd env get-value SERVICE_API_URI)/health/ready" | jq` →
  - [ ] `status: "ready"`
  - [ ] `checks.db == "ok"`
  - [ ] `checks.migrations.match == true`
  - [ ] `checks.mqtt == "ok"` (worker has connected)
  - [ ] `config.environment` matches `TAGPULSE_ENVIRONMENT` from your `.env.<env>`
  - [ ] `config.strict_migration_check == true` for staging/prod
- [ ] `TAGPULSE_API_URL=$(azd env get-value SERVICE_API_URI) python scripts/smoke_setup.py --full` exits 0
- [ ] App Insights receiving traces:
  - [ ] Open the App Insights resource → **Transaction search** → confirm `GET /health/ready` spans appear within 2 minutes of the smoke test
- [ ] Worker is processing:
  - [ ] `az containerapp logs show --name tagpulse-worker --resource-group <rg> --follow` shows `MQTT subscriber connected` and no exception spam

---

## Phase 4 — Wire up CI/CD (one-time, per environment)

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
| Mosquitto ACI fails: `CannotAccessStorageAccount … 403` (during first `azd up`) | ACI mounted Azure Files before storage-key propagation finished | Re-run `azd provision` — the second pass succeeds with no other changes |
| `/health/ready` returns `migrations.match=false` | Migrations job didn't run, or ran older image | Re-run job: `az containerapp job start --name tagpulse-migrations -g <rg>` |
| `/health/ready` shows `checks.mqtt=="error"` | Mosquitto bootstrap not done; password mismatch | Re-do Phase 2 MQTT bootstrap; confirm `AZURE_MQTT_PASSWORD` matches the file uploaded to Azure Files |
| App refuses to start: `jwt_secret missing` and `environment != "dev"` | Strict-mode validator (Phase A1) tripped | Confirm `AZURE_JWT_SECRET` was set; re-run `azd-env-load.sh` then `azd provision` |
| App refuses to start: `CORS allow_origins contains "*"` | Strict-mode validator (Phase A2) tripped | Set `CORS_ALLOW_ORIGINS=https://app.example.com` and redeploy |
| GHA deploy fails with `AADSTS70021: No matching federated identity record found` | Federated-credential subject mismatch | Confirm the credential's subject is exactly `repo:9owlsboston/TagPulse:environment:<env>` (case-sensitive) |
| GHA deploy fails on `Verify all three images exist in ACR` | Tag wasn't pushed by `build-and-push.yml` | Check the build workflow ran on this commit; for manual dispatch pass an explicit `image_tag` |
| Postgres connection from ACA fails: `no pg_hba.conf entry` | Firewall rule missing | The Bicep adds a `0.0.0.0` rule; confirm with `az postgres flexible-server firewall-rule list`. (Replace with private endpoint in hardening sprint.) |
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
