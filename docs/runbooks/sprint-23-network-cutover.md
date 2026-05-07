# Sprint 23 Network Cutover Runbook

> **Sprint:** 23 / Network Hardening — Phase B
> **Audience:** on-call engineer running the cutover for an existing TagPulse env (dev/staging/prod) provisioned under Sprint 22.
> **Reading time:** ~5 min. **Total cutover wall-clock:** ~25 min (most spent waiting on `azd provision`).

## Why this is a cutover, not a migration

Container Apps `vnetConfiguration.infrastructureSubnetId` is **immutable post-create**. You cannot toggle VNet integration on an existing env in place — Azure rejects the PATCH. The supported path is `azd down --purge --force` followed by `azd provision` against the new flag values, which recreates the ACA managed env (and everything that lives inside it: api, worker, migrations job).

Outage envelope: ~10–15 min of full data-plane downtime per env. Schedule accordingly. Production should be cut over during a maintenance window; dev/staging can be cut over ad-hoc.

## Prerequisites

- [ ] You are on `main` (or the branch that contains the Sprint 23 Bicep modules + `disablePublicNetworkAccess`-aware images).
- [ ] `az login` is current; `azd auth login` is current.
- [ ] You have run `scripts/azd-preflight.sh` and `Microsoft.Network` shows `registered`. (The preflight registers it automatically when `AZURE_ENABLE_VNET=true`.)
- [ ] Postgres backup taken — `az postgres flexible-server backup create` or rely on the daily geo-backup. (Migrations replay on the new instance.)
- [ ] Key Vault has `enablePurgeProtection=true` (staging/prod) **OR** you've timed the cutover so the 7-day soft-delete window is acceptable. The KV contents (jwt secret, postgres admin password, mqtt password) are reseeded by Bicep from `azd env get-values`, so soft-delete is OK either way; the concern is name reuse.
- [ ] Coordinated with anyone who deploys via `gh workflow run deploy-azure.yml` — they should pause until the cutover completes, otherwise the smoke step (Phase C2) will fire mid-recreation and report a false negative.

## Steps

### 1. Set the feature flags

```bash
azd env select tagpulse-prod   # or tagpulse-dev / tagpulse-staging
azd env set AZURE_ENABLE_VNET true
azd env set AZURE_DISABLE_PUBLIC_NETWORK_ACCESS true
```

Both flags must be `true` for the full hardening posture. You **can** set only `AZURE_ENABLE_VNET=true` to get the VNet without yet closing public access — useful for staging-only smoke tests of the VNet plumbing before flipping the firewall.

> **Safety guard.** Setting `AZURE_DISABLE_PUBLIC_NETWORK_ACCESS=true` *without* `AZURE_ENABLE_VNET=true` would brick the env (firewall closes but no PE replaces it). The Bicep coerces this combination to "effective false" and surfaces the actual decision via the `disablePublicNetworkAccessEffective` deployment output. Verify post-provision: `az deployment sub show --name $AZURE_ENV_NAME --query 'properties.outputs.disablePublicNetworkAccessEffective.value'`.

### 2. Tear down the existing env

```bash
azd down --purge --force
```

`--purge` removes the soft-deleted KV (so the next provision can reuse the name). `--force` skips the confirmation prompt.

This deletes the resource group entirely. Wait for it to finish (~5 min).

### 3. Reprovision with VNet + private endpoints

```bash
azd provision
```

Watch for:
- `network` module deploys first (VNet + 3 subnets + 2 NSGs).
- `acr` module deploys with `sku.name=Premium` (visible in the deployment timeline; ~$11/mo cost increase per env).
- `kv` deploys with `publicNetworkAccess=Disabled` and `networkAcls.defaultAction=Deny`.
- `postgres` deploys with `network.publicNetworkAccess=Disabled` and **no** `AllowAllAzureIPs` firewall rule.
- `kvPrivateEndpoint`, `postgresPrivateEndpoint`, `acrPrivateEndpoint` deploy after their target resources.
- `acaEnv` deploys with `vnetConfiguration.infrastructureSubnetId` set to the `aca-infra` subnet ID.

Total time ~15 min. If anything fails midway, see [Common failures](#common-failures) below.

### 4. Push images + run migrations

```bash
azd deploy
```

The `postdeploy` hook runs the migrations job, then the Sprint 23 Phase C1 network reachability smoke (`scripts/azd-network-check.sh`). The smoke fails fast if anything is mis-wired.

### 5. Verify

From the GHA / your workstation (outside the VNet):

```bash
# KV REST should return 403 Forbidden (publicNetworkAccess=Disabled).
az keyvault secret list --vault-name "$(azd env get-value AZURE_KEYVAULT_NAME)" --maxresults 1
# Expected: ERROR ... Public network access is disabled and request is not from a trusted service.

# Postgres should refuse the connection (no firewall rule).
psql "host=$(azd env get-value AZURE_POSTGRES_FQDN) user=tagpulse_admin dbname=tagpulse sslmode=require" -c 'select 1' </dev/null
# Expected: connection refused / timeout (firewall blocks).
```

From inside the api container app:

```bash
RG=$(azd env get-value AZURE_RESOURCE_GROUP)
az containerapp exec --name tagpulse-api --resource-group "$RG" \
  --command "python -c 'import socket; print(socket.gethostbyname(\"$(azd env get-value AZURE_KEYVAULT_NAME).vault.azure.net\"))'"
# Expected: 10.10.2.x (the PE address, not a public IP).
```

Confirm `/health/ready` is green: `curl https://$(azd env get-value AZURE_API_FQDN)/health/ready`.

### 6. Break-glass rollback (if anything is wrong)

To revert to the Sprint 22 posture (public KV/Postgres, no VNet) without code changes:

```bash
azd env set AZURE_ENABLE_VNET false
azd env set AZURE_DISABLE_PUBLIC_NETWORK_ACCESS false
azd down --purge --force
azd provision && azd deploy
```

`AZURE_DISABLE_PUBLIC_NETWORK_ACCESS=false` alone (with VNet still on) is a valid intermediate state if you only need to temporarily reopen public access while keeping the VNet for diagnostics.

## Common failures

| Symptom | Cause | Fix |
|---|---|---|
| `azd provision` fails on `network` module: `subnet must be delegated to Microsoft.App/environments` | Existing VNet leftover from a prior run | `azd down --purge --force` first, then re-provision |
| ACR PE deploy fails: `Operation 'CreatePrivateEndpoint' is not supported on a resource of SKU 'Basic'` | `disablePublicNetworkAccess=true` was set but the Bicep wasn't re-evaluated | Confirm `acr.bicep` has `enablePrivateEndpoint: disablePublicNetworkAccess` wiring; rerun `az bicep build` and re-provision |
| `azd deploy` migrations job hangs in `Pending` forever | Migrations image still resolving via public ACR but the GHA runner can't reach it... or the ACA env can't pull from ACR via PE because ACR is also `publicNetworkAccess=Enabled` (correct per ADR-017) and the env is using PE for resolution | Check `az containerapp env show` — ACA env should pull via ACR PE in the `pe` subnet (10.10.2.x); if it's resolving the public ACR FQDN, the private DNS zone link is broken |
| `azd-network-check.sh` reports KV resolves to a public IP from inside | Private DNS zone `privatelink.vaultcore.azure.net` not linked to VNet, or the PE's DNS group config didn't auto-register | Inspect with `az network private-dns zone list -g $RG` and `az network private-endpoint dns-zone-group list -g $RG --endpoint-name <kv-pe>` |
| `mosquitto` ACI container can't pull `tagpulse-mqtt` after cutover | ACI lives outside the VNet and ACR is now Premium with PE — but public access is still on (ADR-017), so the public ACR endpoint should still work via UAMI | Confirm `acrLoginServer` output is the `*.azurecr.io` FQDN, not a privatelink one. Check the UAMI's `AcrPull` role assignment is still in place after the recreate. |

## After the cutover

- [ ] Update `docs/runbooks/azure-first-deploy.md` — the new-env walkthrough should reference these flags.
- [ ] Tag the env in `docs/roadmap.md` Sprint 23 acceptance section as `[shipped on YYYY-MM-DD]`.
- [ ] If the cutover surfaces any deviations from this runbook, edit this file in the same PR that fixes them — keep the runbook honest.
