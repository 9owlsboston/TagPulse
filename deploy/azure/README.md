# Azure Deployment

This directory ships TagPulse on **Azure Container Apps (ACA)**, with the
container images pulled from **Azure Container Registry (ACR)** and secrets
sourced from **Azure Key Vault** via a user-assigned managed identity.

## What gets deployed

| Resource | SKU / Tier | Purpose |
|---|---|---|
| Resource group | — | Container for everything below |
| Container Registry (ACR) | Basic (~$5/mo) | Holds `tagpulse-{api,worker,migrations}` images |
| Key Vault | Standard (~$0/mo) | `jwt-secret`, `postgres-admin-password`, `mqtt-broker-password` |
| User-assigned managed identity | — | ACR pull + KV read for ACA apps + job |
| Postgres Flexible Server | `Standard_B1ms`, 32 GiB (~$15/mo) | TimescaleDB extension, public access + firewall (replace with private endpoint in hardening sprint) |
| ACI (Mosquitto) | 0.5 vCPU / 1 GiB (~$15/mo) | Single-node MQTT broker for v1 |
| Storage account | Standard_LRS | Persistent volume for Mosquitto data + config |
| Log Analytics workspace | PerGB2018 | Stdout/stderr from ACA |
| App Insights | — | OTel destination for traces + metrics from the api/worker |
| Container Apps environment | Consumption profile | Shared compute env |
| ACA: api | 0.5 vCPU / 1 GiB, 1–3 replicas | HTTP ingress on port 8000 |
| ACA: worker | 0.5 vCPU / 1 GiB, 1 replica | No ingress; runs `RuleEvaluator`, `DwellWorker`, `inventory_rule_worker`, MQTT subscriber |
| ACA: migrations | 0.5 vCPU / 1 GiB | Manual-trigger Job: `alembic upgrade head` |
| Static Web App | Free | Hosts the `TagPulse-UI` SPA |

**Estimated monthly cost (idle):** ~$40–50.

## Prerequisites

```sh
# Tools
brew install azure-cli azd            # macOS
curl -fsSL https://aka.ms/install-azd.sh | bash   # Linux

# Sign in
az login
az account set --subscription <subscription-id>
azd auth login
```

## First deploy (`azd up`)

TagPulse supports multiple Azure environments side-by-side (`dev`,
`staging`, `prod`). Each maps to:

| File | Purpose | Committed? |
|---|---|---|
| `deploy/azure/.env.<env>.example` | Variable contract + safe defaults for that env | ✅ |
| `deploy/azure/.env.<env>` | Real values (sub id + 3 secrets) | ❌ gitignored |
| azd env `tagpulse-<env>` | azd's per-env state (managed by azd, in `.azure/`) | ❌ gitignored |

The `TAGPULSE_ENVIRONMENT` value flows through Bicep → ACA → app `Settings.environment`,
so `dev` keeps the strict-mode validators relaxed while `staging` / `production` enforce
them (no dev-secret fallbacks, no CORS `*`, `strict_migration_check` forced True).

### One-time bootstrap (per environment)

```sh
# Generates 3 strong secrets, prompts for sub id + region,
# writes deploy/azure/.env.<env> (mode 600), creates azd env tagpulse-<env>.
scripts/azd-bootstrap.sh dev          # or staging | prod
```

### Deploy

```sh
scripts/azd-env-load.sh dev           # push .env.dev → azd env (also selects it)
azd up                                # provision + build + push + migrate + deploy
```

### Switching between environments

```sh
azd env select tagpulse-staging       # azd-side switch
scripts/azd-env-load.sh staging       # re-sync values (idempotent)
azd deploy                            # deploy app code only (no infra change)
```

### Rotating a secret in place

```sh
$EDITOR deploy/azure/.env.prod        # change AZURE_JWT_SECRET, etc.
scripts/azd-env-load.sh prod          # push the new value
azd provision                         # re-runs Bicep so KV is reseeded
# Then bounce the apps so they re-fetch the secret:
az containerapp revision restart --name tagpulse-api --resource-group tagpulse-prod-rg
az containerapp revision restart --name tagpulse-worker --resource-group tagpulse-prod-rg
```

### CI / inline form (no .env file)

```sh
azd env new tagpulse-prod
azd env set AZURE_LOCATION southcentralus
azd env set AZURE_SUBSCRIPTION_ID <sub-id>
azd env set TAGPULSE_ENVIRONMENT production
azd env set AZURE_POSTGRES_ADMIN_PASSWORD "$(openssl rand -base64 32)"
azd env set AZURE_JWT_SECRET "$(openssl rand -hex 32)"
azd env set AZURE_MQTT_PASSWORD "$(openssl rand -base64 24)"
azd up
```

`azd up` runs four phases:

1. **Provision** — `deploy/azure/bicep/main.bicep` creates the resource group + all resources above.
2. **Build & push** — Dockerfile targets `api`, `worker`, `migrations` are built and pushed to ACR.
3. **Postdeploy hook** — runs the `tagpulse-migrations` Container Apps Job to completion before user traffic reaches the new api revision (~30s for an empty schema, longer for additive migrations).
4. **Update apps** — api + worker container apps roll out to the new image tag (zero-downtime for api; recreate for worker so MQTT never double-subscribes).

The api FQDN is printed at the end:

```
URL  https://tagpulse-api.<random>.southcentralus.azurecontainerapps.io
```

## Bootstrap MQTT broker (one-time)

ACI cannot inject files into the Mosquitto config volume on first boot. Seed the
config + password file once:

```sh
RG=tagpulse-rg
SA=$(az deployment sub show --name tagpulse-prod \
  --query 'properties.outputs.mqttStorageAccountName.value' -o tsv)
KEY=$(az storage account keys list -g "$RG" -n "$SA" --query '[0].value' -o tsv)

# Generate password file using mosquitto_passwd from the eclipse-mosquitto image
docker run --rm -v "$(pwd)":/work eclipse-mosquitto:2 \
  mosquitto_passwd -b -c /work/mosquitto.passwd tagpulse "$AZURE_MQTT_PASSWORD"

cat > mosquitto.conf <<'EOF'
listener 1883
allow_anonymous false
password_file /mosquitto/config/mosquitto.passwd
persistence true
persistence_location /mosquitto/data/
EOF

az storage file upload --account-name "$SA" --account-key "$KEY" \
  --share-name mosquitto-config --source mosquitto.conf
az storage file upload --account-name "$SA" --account-key "$KEY" \
  --share-name mosquitto-config --source mosquitto.passwd

az container restart --name tagpulse-mqtt --resource-group "$RG"
```

## Smoke test

```sh
API_URL=$(azd env get-value SERVICE_API_URI)
curl "$API_URL/health/ready" | jq
```

Expect `{"status":"ready", "checks": {"db": "ok", "migrations": {"current": "...", "head": "...", "match": true}, ...}}`.

End-to-end:

```sh
TAGPULSE_API_URL="$API_URL" python scripts/smoke_setup.py --full
```

## CD (GitHub Actions)

`.github/workflows/deploy-azure.yml` runs:

- automatically on push of a `v*` tag → deploys to the **`production`** GitHub Environment;
- on `workflow_dispatch` with an `environment` input → `dev` | `staging` | `production`.

It federates to Azure via OIDC (no long-lived secrets), verifies the three
images already exist in ACR at the target tag, runs the `tagpulse-migrations`
Container Apps Job to completion, then `az containerapp update`s api +
worker and smoke-tests `/health/ready`.

### One-time setup (per environment)

For each of `dev`, `staging`, `production`:

**1. Create the GitHub Environment** (Settings → Environments → New environment).
Add required reviewers on `production` only.

**2. Create an Entra app registration + federated credential** scoped to that environment:

```sh
ENV=dev                                    # or staging | production
APP_NAME="tagpulse-deploy-${ENV}"
REPO=9owlsboston/TagPulse

# Create the app + service principal
APP_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)
az ad sp create --id "$APP_ID"

# Federated credential: GitHub-issued tokens for this Environment can assume the SP
az ad app federated-credential create --id "$APP_ID" --parameters "{
  \"name\": \"github-${ENV}\",
  \"issuer\": \"https://token.actions.githubusercontent.com\",
  \"subject\": \"repo:${REPO}:environment:${ENV}\",
  \"audiences\": [\"api://AzureADTokenExchange\"]
}"

# Grant Contributor on the env's resource group + AcrPush on its ACR
SUB=$(az account show --query id -o tsv)
RG=tagpulse-${ENV/prod/prod}-rg            # tagpulse-dev-rg / tagpulse-staging-rg / tagpulse-prod-rg
ACR=$(az acr list -g "$RG" --query '[0].name' -o tsv)

az role assignment create --assignee "$APP_ID" --role Contributor \
  --scope "/subscriptions/${SUB}/resourceGroups/${RG}"
az role assignment create --assignee "$APP_ID" --role AcrPush \
  --scope "/subscriptions/${SUB}/resourceGroups/${RG}/providers/Microsoft.ContainerRegistry/registries/${ACR}"

echo "AZURE_CLIENT_ID=$APP_ID"
echo "AZURE_TENANT_ID=$(az account show --query tenantId -o tsv)"
echo "AZURE_SUBSCRIPTION_ID=$SUB"
```

**3. Set five Environment-scoped variables** (Settings → Environments → `<env>` → Variables):

| Variable | Value |
|---|---|
| `AZURE_CLIENT_ID` | App registration appId from step 2 |
| `AZURE_TENANT_ID` | Tenant ID from step 2 |
| `AZURE_SUBSCRIPTION_ID` | Target subscription ID |
| `AZURE_RESOURCE_GROUP` | `tagpulse-dev-rg` / `tagpulse-staging-rg` / `tagpulse-prod-rg` |
| `AZURE_ACR_NAME` | ACR name (without `.azurecr.io`) |

These are GitHub *variables*, not secrets — none of them are sensitive on
their own; the federated credential is what gates token issuance to the
right repo + environment.

### Triggering a deploy

```sh
# Production via tag push (auto-runs on tag, gated by production reviewers):
git tag v0.22.0 && git push --tags

# Manual dispatch to any environment:
gh workflow run deploy-azure.yml \
  -f environment=staging \
  -f image_tag=sha-abc123      # optional, defaults to the tag/SHA
```

The `production` environment can additionally be configured to allow
deploys only from `main` and `v*` tag refs (Settings → Environments →
production → Deployment branches).

### Rollback

Re-run `deploy-azure.yml` with the previous tag — every container-app
revision is immutable so traffic flips back atomically.


## Hardening backlog (deferred, by design)

These intentionally do **not** ship in v1 to keep the cost floor and complexity low. Each is a self-contained follow-on:

- **Postgres private endpoint + VNet** — replace the `0.0.0.0` firewall with a VNet + private endpoint; ACA env subnet-injected.
- **Front Door + WAF** — TLS termination + WAF rules + custom domain.
- **EMQX (or AKS-hosted Mosquitto cluster)** — HA MQTT broker; the ACI broker is single-node.
- **Postgres passwordless auth via Entra ID** — drop the admin-password Key Vault secret entirely.
- **Geo-redundant Postgres backup + cross-region failover** — set `geoRedundantBackup: 'Enabled'` and add the failover module.
- **mTLS broker rollout** (Sprint 17c) — landed via the cert columns from Sprint 17b but broker-side enforcement is its own sprint.
