# azd 101 — Survival Guide

A pragmatic reference for using the **Azure Developer CLI (`azd`)** in this repo.
Companion to [azure-first-deploy.md](azure-first-deploy.md), which is the
step-by-step checklist for standing up a fresh environment.

---

## What `azd` is

The Azure Developer CLI (`azd`) ties together **infrastructure-as-code**
(Bicep/Terraform), **container/image builds**, **deployment**, and
**environment configuration** into a small set of commands. It reads
[azure.yaml](../../azure.yaml) at the repo root to know what services exist,
where their code lives, and which IaC templates to use.

---

## Mental model

An **azd project** is the unit defined by a single `azure.yaml`. Most repos
have one `azure.yaml` at the root, so in practice "the repo" and "the azd
project" are the same thing — that's the case for TagPulse. (A monorepo
could host several azd projects in subdirectories; each has its own
`azure.yaml`, infra, services, and envs.)

Every azd project has three things:

1. **Infra** — `infra/` or in this repo `deploy/azure/bicep/` — provisions cloud resources.
2. **Services** — listed in `azure.yaml`; each maps to a folder + a target host (Container Apps, App Service, Functions, AKS, etc.).
3. **Environment** — a named bag of config + secrets stored locally under `.azure/<env-name>/` and tracked by `azd env`. Switch envs with `azd env select`.

---

## Core lifecycle commands

| Command | What it does |
|---|---|
| `azd init` | Scaffold or detect a project; create the first env. |
| `azd env new / select / list / set / get-value / get-values` | Manage env-scoped vars (`AZURE_LOCATION`, `AZURE_RESOURCE_GROUP`, custom secrets). |
| **`azd provision`** | Run the IaC template. Creates/updates Azure resources. **Does NOT build or push images.** |
| `azd package` | Build container images / zip artifacts locally. Doesn't push. |
| `azd deploy [service]` | Push artifacts (e.g. `docker push` to ACR) and roll the target service (e.g. update a Container App revision). Requires provision to have already run. |
| **`azd up`** | Convenience: runs `provision` then `deploy` for all services. |
| `azd down [--purge] [--force]` | Tear down everything provisioned. `--purge` also purges soft-deleted Key Vault / App Config / Cognitive Services. |
| `azd monitor` | Open App Insights / Log Analytics in the browser. |
| `azd pipeline config` | Set up a GitHub Actions / Azure DevOps pipeline with federated credentials. |
| `azd show` | Print the deployed services + their URIs for the active env. |
| `azd auth login` | Browser login (separate from `az login`). |

---

## What `azd provision` actually does

1. Loads the active env (`.azure/<env>/.env`) and exports its values as process env vars.
2. Resolves the IaC template from `azure.yaml` (`infra:` block — in this repo: `deploy/azure/bicep/main.bicep` + `main.bicepparam`).
3. Runs **preprovision hooks** (this repo: `scripts/azd-kv-recover.sh`).
4. Calls **`az deployment sub create`** (or `group create` depending on `infra.scope`) using the bicepparam file. Bicep `readEnvironmentVariable(...)` calls pull from the env vars from step 1.
5. Streams the ARM deployment progress (lines like `(✓) Done: Container Registry: …`).
6. Captures **template outputs** and writes them back into the azd env (e.g. `acrLoginServer` → `AZURE_ACR_LOGIN_SERVER`). This is how `azd deploy` later knows where to push images.
7. Runs **postprovision hooks** (this repo: derives `AZURE_ACR_LOGIN_SERVER` and `AZURE_IMAGE_TAG`).

If any step fails, no outputs get written, and `azd deploy` later complains
*"could not determine container registry endpoint"*.

---

## `azd up` vs `provision` + `deploy`

`azd up` ≈ `azd provision && azd deploy && (postdeploy hook)`.

Split them when:

- Iterating on **app code only** — just `azd deploy api`.
- Iterating on **Bicep** — `azd provision` tests infra changes without rebuilding images.
- You want to run the migrations job manually between provision and deploy.

---

## Hooks (azure.yaml)

```yaml
hooks:
  preprovision:    # before az deployment ... (e.g. KV soft-delete recovery)
  postprovision:   # after outputs are captured (e.g. derive ACR login server)
  predeploy:       # before pushing images
  postdeploy:      # after services rolled (this repo runs the migrations job here)
```

Each hook can use `shell: sh|pwsh|bash` and runs in the repo root with the
azd env vars exported. Non-zero exit aborts the operation.

In this repo (see [azure.yaml](../../azure.yaml)):

- **preprovision** — `scripts/azd-kv-recover.sh` recovers/purges/sidesteps soft-deleted Key Vaults so the next provision succeeds.
- **postprovision** — captures `AZURE_ACR_LOGIN_SERVER` + `AZURE_IMAGE_TAG`.
- **postdeploy** — starts the `migrations` Container App Job and waits for completion.

---

## Diagnostics cheat sheet

```sh
azd env get-values                  # all env vars azd will inject
azd show                            # current services + endpoints
azd provision --preview             # what-if (no changes applied)
azd deploy <service> --debug        # verbose output
azd config list                     # CLI-level config (auth method, etc.)

# Find the underlying ARM deployment azd just created:
az deployment sub list \
  --query "[?starts_with(name,'$(azd env get-value AZURE_ENV_NAME)')].{name:name,state:properties.provisioningState,ts:properties.timestamp}" \
  -o table

# Drill into a failed deployment:
az deployment sub show -n <name> --query "properties.error" -o json
az deployment operation group list -g <rg> -n <name> \
  --query "[?properties.provisioningState=='Failed'].{res:properties.targetResource.resourceName,msg:properties.statusMessage}" \
  -o json

# After deploy, hit the API:
API="https://$(azd env get-value apiFqdn)"
curl "$API/health/live"
curl "$API/health/ready" | jq
```

---

## Where state lives

- `.azure/<env>/.env` — env-scoped vars (gitignored).
- `.azure/<env>/config.json` — service → resource-ID mapping that `azd deploy` uses to find the right Container App.
- `azure.yaml` — committed; the project descriptor.
- The cloud — actual resources, plus ARM deployment history under the subscription.

---

## TagPulse-specific notes

- Infra is **subscription-scoped** (`main.bicep` creates the resource group itself), so `azd provision` calls `az deployment sub create`.
- Three services (`api`, `worker`, `migrations`) all map to the same ACR. `azd deploy` builds Docker images via your local Docker, tags them with `AZURE_IMAGE_TAG`, pushes to ACR, then updates each Container App's image reference.
- The **postdeploy hook** starts the `migrations` Container App Job and waits for it before declaring success.
- Use `scripts/azd-bootstrap.sh <env>` to scaffold a new env file and `scripts/azd-env-load.sh <env>` to push its values into the active azd env before `azd up`.
- Secrets-in-env caveat: `main.bicepparam` reads `AZURE_POSTGRES_ADMIN_PASSWORD`, `AZURE_JWT_SECRET`, `AZURE_MQTT_PASSWORD` from environment variables. They must be present in the azd env (or process env) before `azd provision`.

---

## Common gotchas

| Symptom | Cause | Fix |
|---|---|---|
| `azd deploy` says "could not determine container registry endpoint" | `azd provision` didn't finish (no outputs written) | Re-run `azd provision`, fix the underlying error first |
| Provision fails on Key Vault `VaultAlreadyExists` | Prior teardown left a soft-deleted KV (names are global + reserved 7–90 days) | The preprovision hook handles this; manual fix: `scripts/azd-kv-recover.sh <env>`. If purge protection is on and the region differs, the hook auto-bumps `AZURE_KV_NAME_SUFFIX`. |
| Provision fails: Mosquitto ACI `403` accessing storage *or* `azurerm` reverts `allowSharedKeyAccess` to `false` silently | Corporate Azure Policy in `Modify` mode on Storage accounts | Sprint 23 Phase A is mandatory for these subscriptions — the custom Mosquitto image (`docker/mosquitto.Dockerfile`) drops the Azure Files dependency entirely. Set `AZURE_USE_IMAGE_PLACEHOLDERS=true` for the first provision and re-run; the `azd-mqtt-build.sh` hook builds the image into ACR on the second pass |
| `azd env get-value apiFqdn` is empty | API Container App wasn't created (provision failed earlier) | Inspect resource list + ARM deployment errors. Note: `SERVICE_API_URI` is printed by `azd deploy` at the end of a run but not persisted into env values for the `containerapp` host — use `apiFqdn` (the Bicep output) for scripts instead |
| `KeyVault: Forbidden` from a container app at startup, or `az keyvault secret show` from your laptop returns `Forbidden` | Sprint 23 Phase B is on (`AZURE_DISABLE_PUBLIC_NETWORK_ACCESS=true`) — public KV access is intentionally closed | From inside the api app: `az containerapp exec --name tagpulse-api --resource-group $RG --command "python -c 'import socket; print(socket.gethostbyname(\"<kv>.vault.azure.net\"))'"`. Expect `10.10.x.x`. If you get a public IP, the private DNS zone link is broken — `az network private-dns zone list -g $RG`. From your laptop: this is by design; use the Sprint 23 `mgmt` subnet Bastion (production) or temporarily flip `AZURE_DISABLE_PUBLIC_NETWORK_ACCESS=false` + `azd provision` (dev break-glass) |
| `azd provision` fails on `network` module: "subnet must be delegated to Microsoft.App/environments" | Existing VNet from a prior partial provision | `azd down --purge --force`, then re-provision (ACA env's `vnetConfiguration` is immutable post-create — see [sprint-23-network-cutover.md](sprint-23-network-cutover.md)) |
| `disablePublicNetworkAccessEffective` deployment output reports `false` after you set both Sprint 23 flags | Safety guard tripped: `AZURE_DISABLE_PUBLIC_NETWORK_ACCESS=true` requires `AZURE_ENABLE_VNET=true` (otherwise the firewalls would close with no PE replacement) | Set `AZURE_ENABLE_VNET=true` and re-provision. Verify: `az deployment sub show --name $AZURE_ENV_NAME --query 'properties.outputs.disablePublicNetworkAccessEffective.value'` |
| Hook fails with `set: Illegal option -o pipefail` | `shell: sh` invoked a `bash` script via `sh ./script.sh`, overriding the shebang | Call the script directly: `./script.sh` |
| Hook exits 141 (SIGPIPE) under `set -o pipefail` | `tr </dev/urandom \| head -c N` — `head` closes early, `tr` killed by SIGPIPE | Use `head -c 64 /dev/urandom \| tr ...` instead |

---

## Further reading

- Microsoft docs: <https://learn.microsoft.com/azure/developer/azure-developer-cli/>
- This repo: [azure-first-deploy.md](azure-first-deploy.md), [deploy/azure/README.md](../../deploy/azure/README.md), [azure.yaml](../../azure.yaml).
