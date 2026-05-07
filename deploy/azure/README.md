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

From the repo root:

```sh
azd env new tagpulse-prod
azd env set AZURE_LOCATION southcentralus
azd env set AZURE_SUBSCRIPTION_ID <sub-id>
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

`.github/workflows/deploy-azure.yml` runs on push of a `v*` tag (and on
manual dispatch). It:

1. Federates to Azure via OIDC (no secrets stored in GitHub — uses the `production` environment + a GitHub-issued token).
2. Pulls the images already published by `build-and-push.yml` (B3) — no rebuild.
3. Tags them in ACR (`docker pull ghcr.io/.../tagpulse-{component}:<sha>` → `docker tag` → `docker push <acr>.azurecr.io/...`).
4. Runs the `tagpulse-migrations` Container Apps Job and waits for completion.
5. Updates `tagpulse-api` and `tagpulse-worker` to the new image tag.

Required GitHub repo secrets / variables (set under **Settings → Environments → production**):

| Variable / secret | Value |
|---|---|
| `AZURE_CLIENT_ID` | App registration client ID with federated credential bound to this repo |
| `AZURE_TENANT_ID` | Azure AD tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Target subscription |
| `AZURE_RESOURCE_GROUP` | `tagpulse-rg` (or your override) |
| `AZURE_ACR_NAME` | ACR name (without `.azurecr.io`) |

## Hardening backlog (deferred, by design)

These intentionally do **not** ship in v1 to keep the cost floor and complexity low. Each is a self-contained follow-on:

- **Postgres private endpoint + VNet** — replace the `0.0.0.0` firewall with a VNet + private endpoint; ACA env subnet-injected.
- **Front Door + WAF** — TLS termination + WAF rules + custom domain.
- **EMQX (or AKS-hosted Mosquitto cluster)** — HA MQTT broker; the ACI broker is single-node.
- **Postgres passwordless auth via Entra ID** — drop the admin-password Key Vault secret entirely.
- **Geo-redundant Postgres backup + cross-region failover** — set `geoRedundantBackup: 'Enabled'` and add the failover module.
- **mTLS broker rollout** (Sprint 17c) — landed via the cert columns from Sprint 17b but broker-side enforcement is its own sprint.
