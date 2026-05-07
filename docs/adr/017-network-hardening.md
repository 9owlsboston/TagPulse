# ADR-017: Network Hardening — VNet Integration + Private Endpoints

- Status: Proposed (Sprint 23, May 2026)
- Supersedes: none
- Related: [ADR-016](016-multi-cloud-deployment-strategy.md) (Azure-first IaC), [docs/roadmap.md Sprint 23](../roadmap.md), [docs/runbooks/azure-first-deploy.md](../runbooks/azure-first-deploy.md), [deploy/azure/README.md](../../deploy/azure/README.md)

## Context

Sprint 22 Phase C landed `azd up` against Azure. First-deploy in the
target subscription surfaced corporate Azure Policy blockers that
ADR-016's "v1 ships with public network access; harden post-launch"
plan didn't account for:

1. **`Storage account: allowSharedKeyAccess` is policy-enforced as `Modify`-mode** on the subscription. The Bicep sets it `true`, the policy silently flips it back to `false`, and the Mosquitto ACI fails to mount its Azure Files config + data shares with `CannotAccessStorageAccount … 403`. There is no in-IaC override. ACI mounts Azure Files via SMB using the storage account *key* — managed-identity mount is not supported. Result: **Sprint 22 Phase C is not deployable in this subscription as designed.**
2. **Key Vault network-access policies are `Audit`-only today but flagged for promotion to `Deny` at midnight tonight.** The platform team's enforcement schedule means we have <24h before `publicNetworkAccess=Enabled` becomes a hard block. After the cutover, the deploy principal can't seed secrets, the Container Apps can't read them at startup, and `azd provision` fails at the workload deployment step.
3. **Postgres has the same pattern queued behind KV** — same enforcement schedule, slightly later cutover.

Sprint 22's "hardening backlog" listed Postgres private endpoint, EMQX
HA, Front Door + WAF, and passwordless Postgres as deferred-by-design.
The KV/storage policy enforcement compresses items 1 and 2 of that
list into an unavoidable pre-launch sprint, ahead of EMQX and Front
Door.

Three forces shape the response:

- **Time pressure** (midnight): a same-day mitigation that doesn't require VNet recreate is mandatory; the proper fix can land within the sprint.
- **No regression for environments without the policy**: the existing local-dev path and any subscription that doesn't enforce these policies must keep working unchanged.
- **Avoid a half-step into private networking that we'll redo for EMQX**: the EMQX cutover (Sprint 24 candidate) needs the same VNet, so getting the network topology right now costs nothing extra later.

## Decision

### Phase A — Same-day mitigation: drop the storage dependency entirely

The only resource currently affected by an actively-enforced (`Modify`-
mode) policy is the Mosquitto storage account. KV is still `Audit`-mode
until tonight. The fastest unblock is therefore to **eliminate the
storage account from the architecture**, not work around the policy:

1. **Custom Mosquitto image** (`docker/mosquitto.Dockerfile`) `FROM eclipse-mosquitto:2`. COPYs `mosquitto.conf` into `/mosquitto/config/`. Entrypoint generates `mosquitto.passwd` at boot from `MOSQUITTO_USERNAME` / `MOSQUITTO_PASSWORD` env vars (Mosquitto's documented bootstrap pattern).
2. **`mqtt` becomes a fourth `azd` service** in `azure.yaml` so `azd deploy mqtt` builds + pushes to ACR alongside api/worker/migrations. Image: `${ACR}/tagpulse-mqtt:${tag}`.
3. **`mqtt.bicep` drops both `Microsoft.Storage/*` resources, both `volumes`, both `volumeMounts`.** ACI pulls the image from ACR via the existing UAMI. KV `mqtt-password` continues to feed the ACI as a `secureValue` env var (no client-side change).

**Loss of broker retained-message persistence across container restarts.**
Mosquitto's `/mosquitto/data` was where retained messages and persistent
client subscriptions lived. After Phase A, those don't survive an ACI
restart. This is acceptable because:

- Devices republish state on reconnect (the existing edge contract has this).
- Sprint 24's EMQX cutover replaces the broker with a managed/HA service that has its own persistence story. Phase A is intentionally a 1–2 sprint bridge, not a permanent design.
- v1 has no MQTT consumer that depends on retained messages — the api/worker subscribe at startup with `cleanSession=False` and the broker hands them whatever's in flight.

### Phase B — VNet integration + private endpoints

The proper fix for KV (and Postgres, and ACR) is the topology Microsoft
documents and the policy explicitly recommends:

- **One VNet per env** (`tpdev-vnet`, `tpstaging-vnet`, `tpprod-vnet`), `10.10.0.0/16`. Three subnets:
  - `aca-infra` — `10.10.0.0/23`, delegated to `Microsoft.App/environments`. NSG with default-deny + ACA service-tag allow.
  - `pe` — `10.10.2.0/27`, no delegation. Private endpoints land here.
  - `mgmt` — `10.10.3.0/27`, reserved for Bastion / future jumpbox. Empty in dev.
- **Container Apps environment becomes VNet-integrated** (`vnetConfiguration.infrastructureSubnetId = aca-infra.id`, `internal=false`). External (api) ingress stays public — this sprint's scope is *egress* hardening, not making the api private. ACA env is **immutable on this property**, so existing Sprint 22 envs require `azd down --purge` + redeploy.
- **Three private endpoints** in the `pe` subnet, one each for KV, Postgres, ACR. Each PE creates a Private DNS Zone (`privatelink.vaultcore.azure.net`, `privatelink.postgres.database.azure.com`, `privatelink.azurecr.io`) linked to the VNet so app pods resolve to `10.10.x.x` automatically.
- **Public network access disabled** on KV (`publicNetworkAccess=Disabled`) and Postgres (`publicNetworkAccess=Disabled`, drop the `0.0.0.0/0` firewall rule). ACR keeps public ON during Sprint 23 because GitHub Actions runners need to push images during CI; ACR firewall allow-list will be added in Sprint 24 once a self-hosted runner or a GHA service-tag policy is in place.

### Phase B is feature-flagged

Two new bicep params control whether Phase B applies:

- `enableVnetIntegration: bool = false`
- `disablePublicNetworkAccess: bool = false`

The flags are independent so an env can opt into VNet integration first
and flip the public-access kill switch later (the path we're taking for
dev). Both default `false` to preserve Sprint 22 behaviour for
subscriptions without the policy. CI sets them via env vars
(`AZURE_ENABLE_VNET=true` etc.) read by `main.bicepparam`.

### Dev / break-glass access

When `disablePublicNetworkAccess=true`, `az keyvault secret show` from a
laptop fails. Three documented options:

| Option | Cost | When |
|---|---|---|
| (a) Azure Bastion in `mgmt` subnet | ~$140/mo | `production` (always-on access, audit logs) |
| (b) Ad-hoc dev container app with public ingress + `az` CLI | ~$0 (ephemeral) | break-glass in any env |
| (c) Toggle the public-access flag temporarily for a deploy | $0 | `dev` only |

**Decision:** dev uses (c), production uses (a), staging starts on (c)
and graduates to (a) when the first paying customer is onboarded. (b)
is reserved for incident-response.

### Non-goals for Sprint 23

- **API ingress stays public.** Front Door + WAF goes in front of it in a later sprint. Making the api `internal=true` requires Front Door anyway, so they ship together.
- **EMQX HA broker** stays deferred. Phase A's custom Mosquitto image is a deliberate bridge; the storage savings + image-baked config are net-positive even if EMQX ships in Sprint 24.
- **Passwordless Postgres** stays deferred. The KV-mediated password works inside the VNet; Entra-ID-only access can come once the broader platform requires it.
- **ACR firewall** stays open in Sprint 23 (private endpoint is added but `publicNetworkAccess` stays Enabled). Closing it requires a CI-runner change that's out of scope.

## Consequences

### Positive

- Compatible with the corporate "no public KV / Postgres" policy without manual policy exemptions.
- Sprint 22's `azd up` flow remains the user-facing API; Phase B is a flag flip and a redeploy, not a workflow change.
- Same VNet supports Sprint 24 EMQX (managed broker subnet) and the eventual Front Door / WAF cutover with no rework.
- Removes the ACI Azure Files mount, which has been a recurring source of provision-time races (`CannotAccessStorageAccount: 403` was the documented "retry once" failure mode in [docs/runbooks/azure-first-deploy.md](../runbooks/azure-first-deploy.md)). Phase A makes that runbook entry obsolete.

### Negative

- ACA env recreate is destructive. Existing dev resource group must be torn down before Phase B applies. `azd down --purge --force` is the only path; the migration runbook (`docs/runbooks/sprint-23-network-cutover.md`) walks through it.
- Mosquitto loses retained-message persistence (Phase A trade). Mitigated by edge-contract republish + the EMQX timeline.
- Bastion adds ~$140/mo per production env. Acceptable; Bastion is the only audited break-glass path for fully-private resources.
- Three new Private DNS Zones per env. Each is free, but they accumulate — the cleanup runbook in Sprint 23 D2 explicitly lists them.
- Bicep complexity increases (one new module, two new bool params, branching in three existing modules). Mitigated by feature-flagging — the `false`/`false` path is a no-op vs Sprint 22.

### Out of scope (intentional)

- AWS / GCP equivalents (VPC + interconnect + PrivateLink endpoints). Phase F skeletons in Sprint 22 already documented the per-cloud mapping; concrete IaC waits until those skeletons graduate (Sprint 25+ at the earliest).
- A general "all egress through a NAT + firewall" policy. Container Apps egress filtering is on the post-launch hardening backlog; Sprint 23 only addresses *ingress to KV/Postgres/ACR from the apps*, not the reverse.

## Alternatives considered

1. **Request a per-resource policy exemption from the platform team.** Rejected: lead time uncertain (>1 week typical), doesn't address the strategic need to be private-by-default for production.
2. **Migrate Mosquitto to ACI with Azure Files using Entra ID auth (no shared key).** Not supported by ACI today. (Container Apps supports this for AKS-equivalent volume mounts via CSI driver; ACI does not.)
3. **Move the broker into the Container Apps environment as a managed container instead of ACI.** ACA doesn't support exposing TCP 1883/8883 to public clients (no L4 ingress); EMQX HA is the natural answer here, queued for Sprint 24.
4. **Skip Phase A and only do Phase B.** Rejected: Phase B is multi-day work, the storage policy is enforced *now*, and Phase A is hours of work that's net-positive even after EMQX cutover (smaller image, no Files mount, no bootstrap script).
