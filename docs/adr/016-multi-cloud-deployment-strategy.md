# ADR-016: Multi-Cloud Deployment Strategy

- Status: Accepted (Sprint 22 Phase A–C, May 2026)
- Supersedes: none
- Related: [ADR-008](008-multi-tenancy-strategy.md) (tenant routing + sovereign-tenant promotion shape), [ADR-009](009-containerization-local-dev.md) (container baseline), [ADR-002](002-mqtt-device-connectivity.md) (broker target), [ADR-012](012-mtls-for-mqtt.md) (broker auth roadmap), [docs/roadmap.md Sprint 22](../roadmap.md)

## Context

Through Sprint 21 TagPulse runs only on `docker-compose up`. The Sprint 22
cloud-readiness review surfaced 12 must-fix gaps before a first cloud
deploy: dev defaults in `Settings`, no global rate-limit middleware, no
IaC, migrations not in the deploy pipeline, no `/health/ready` split,
etc. Two product constraints frame the IaC structure:

1. **First cloud target is Azure.** No reason to invest in an
   abstraction layer that has only one concrete implementation.
2. **Second and third cloud targets must not be a rewrite.** Customer
   conversations have raised AWS and (less urgently) GCP. The data
   layer in particular must be portable — moving a tenant between
   clouds should be an operational drill, not an engineering project.

A third force is the existing Sprint 13b multi-tenant routing
(`db_pool_key`, `PoolRegistry`, `tenant_context()`) plus the ADR-008
Tier-2 sovereign-tenant promotion design (`pg_dump --where=tenant_id …`
+ one row update on `tenants.db_pool_key`). That mechanism is the
natural substrate for cross-cloud migration too — the same export
shape that promotes a tenant to a sovereign Postgres instance also
promotes it to a *different cloud's* Postgres instance.

## Decision

### 1. Three-layer deployment topology

```
┌────────────────────────────────────────────────────────────────┐
│ Layer 3 — Per-cloud IaC (Bicep / Terraform / Pulumi)           │
│   deploy/azure/bicep/    ← v1 (Sprint 22)                      │
│   deploy/aws/terraform/  ← skeleton only (Sprint 23+)          │
│   deploy/gcp/terraform/  ← skeleton only (Sprint 23+)          │
│   Provisions: compute platform, managed Postgres, secret       │
│   store, registry, observability, broker, edge / WAF.          │
├────────────────────────────────────────────────────────────────┤
│ Layer 2 — Portable workload spec                               │
│   deploy/common/helm/tagpulse/                                 │
│   Helm chart deploying api + worker + migrations-job +         │
│   ServiceMonitor + PDB. Canonical "what runs where" spec.      │
│   Used directly on AWS/GCP later; on Azure the Bicep modules   │
│   render Container Apps natively but match the chart's shape.  │
├────────────────────────────────────────────────────────────────┤
│ Layer 1 — Portable data layer                                  │
│   deploy/portable/data-migration/{export,import}_tenant.py     │
│   pg_dump --where="tenant_id='…'" per tenant-scoped table,     │
│   FK-ordered, manifest-checksummed, archive uploaded via       │
│   tagpulse.storage.BlobStore (Azure Blob / S3 / GCS / FS).     │
└────────────────────────────────────────────────────────────────┘
```

Each layer has one canonical implementation per cloud. Layer 1 and
Layer 2 are cloud-shaped but cloud-agnostic; Layer 3 is unapologetically
per-provider.

### 2. Bicep on Azure, Terraform elsewhere

Bicep is the best-of-breed Azure IaC and integrates cleanly with `azd`,
which is what the [azure-prepare](https://learn.microsoft.com/azure/)
deployment workflow expects. A cloud-agnostic Terraform-everywhere
approach was rejected because:

- Terraform's Azure provider lags Bicep on new resource types
  (Container Apps Jobs, Front Door Standard, Azure DB for PostgreSQL
  Flexible Server features).
- `azd up` orchestration over Bicep is materially better DX than the
  Terraform equivalent, and Sprint 22's "stand up cloud from clean
  subscription in one command" acceptance criterion depends on it.
- The cost of two IaC dialects is low — there is no shared logic
  between provisioning Azure Container Apps and AWS ECS Fargate that
  would benefit from a common templating layer.

Pulumi was considered and rejected: the per-language SDK gain over
Terraform is not worth the team-onboarding cost when the IaC surface
is small and provisioned rarely.

### 3. Helm chart as the portable workload spec

The Helm chart in `deploy/common/helm/tagpulse/` is the **canonical**
description of what runs in production:

- Three workloads: `api` (FastAPI), `worker` (MQTT subscriber + dwell +
  inventory rule worker), `migrations` (one-shot job).
- Standard k8s primitives: `Deployment`, `Job`, `Service`,
  `ServiceMonitor` (Prometheus Operator), `PodDisruptionBudget`.
- All knobs in `values.yaml`; per-environment overlays
  (`values-azure.yaml`, eventually `values-aws.yaml`).

On Azure the Bicep modules render Container Apps directly (not k8s),
but the Container Apps shape mirrors the chart 1:1 — same image
references, same env-var contract, same scale rules. Anyone reading
the Helm chart will recognize what's running in Azure.

The chart **must** deploy cleanly against `kind` locally (Sprint 22
acceptance criterion) so it is exercised on every PR that touches
`deploy/common/helm/`, even though k8s is not the v1 production target.

### 4. Data portability as a first-class deliverable

Sprint 22 ships:

- `deploy/portable/data-migration/export_tenant.py` — wraps `pg_dump`
  per tenant in FK-dependency order; emits a `.tar.zst` archive plus a
  JSON manifest with `(table, row_count, checksum, source_schema_version)`.
- `deploy/portable/data-migration/import_tenant.py` — validates
  manifest against target `alembic_version head`; optionally remaps
  `tenant_id` (UUID collision avoidance); restores in dependency order;
  asserts row-count parity against the manifest before committing.
- `tagpulse.storage.BlobStore` protocol + Azure Blob / S3 / GCS / FS
  implementations. Selected at runtime via `STORAGE_BACKEND` env var.

Critical constraint: the export shape **must** be the same one ADR-008
Tier 2 uses for sovereign-tenant promotion. A single `pg_dump --where`
contract serves three use cases:

- Promote tenant from shared pool to sovereign instance (ADR-008).
- Migrate tenant from one cloud to another (Sprint 22).
- Scheduled CSV exports (Sprint 8 backlog) — same `BlobStore`
  abstraction; different table / format, same upload path.

Building a separate per-use-case data-export pipeline was rejected as
gratuitous duplication.

### 5. Rate-limiting: in-process token bucket, Redis later

Phase A4 ships an in-process token bucket keyed on
`(tenant_id, route_class)`. Single-replica APIs see exact limits;
multi-replica APIs see drift proportional to replica count. We accept
this trade-off for v1 because:

- The first paying tenant will run a single-replica API tier.
- Redis adds an operational dependency (HA, failover, secret rotation)
  that Sprint 22 doesn't otherwise need.
- Migrating from in-process to Redis-backed (`slowapi` or `limits` lib)
  is a `RateLimiter` protocol swap; no route-handler changes.

Revisit when first multi-replica deployment goes live or first tenant
reports rate-limit drift.

### 6. MQTT broker stays parameterized; EMQX cutover deferred

The Bicep `mqtt` module accepts a parameter:

- `broker = 'mosquitto-aci'` — single-node Mosquitto on Azure Container
  Instances, no HA, ~$15/mo. Sufficient for v1, dev, and demos.
- `broker = 'emqx-cloud'` — connects to an externally-provisioned EMQX
  Cloud subscription via Key Vault-stored creds. HA, ~$50+/mo.

The EMQX cutover (and its mTLS broker pairing per Sprint 17c) is its
own ADR. Sprint 22 makes it a **module parameter** so tenants on Azure
can opt in without an architecture change; it does not commit to EMQX
as the production default.

### 7. Strict-mode startup checks

In `environment in {staging, production}`, the API and worker refuse
to start unless:

- `jwt_secret` is set and not `dev-secret-change-in-production`.
- `database_url` is set and password ≠ `secret`.
- CORS `allow_origins` does not contain `*`.
- `alembic_version == head` (gated by `STRICT_MIGRATION_CHECK`,
  default `True` in non-dev).

Dev workflow (`make run`, `scripts/smoke_setup.py`) is unaffected
because `environment` defaults to `dev`.

## Consequences

### Good

- Cloud-target choice becomes a `deploy/<cloud>/` directory, not a
  refactor.
- Tenant export → import is the same script regardless of source and
  target cloud; the cross-cloud DR runbook is a workflow doc, not an
  engineering project.
- The Helm chart doubles as living documentation of what runs in
  production, even on Azure where it isn't the runtime.
- Sprint 22's 12 cloud-readiness gaps are all closed without coupling
  any of them to a specific cloud.

### Bad

- Two IaC dialects in-tree once Sprint 23 lands AWS Terraform. We
  accept this — there is no realistic shared layer.
- The Helm chart needs maintenance even when the v1 production target
  doesn't use it. Mitigated by `kind`-deploy CI smoke check.
- In-process rate limiter is technically incorrect for multi-replica
  deployments. Mitigated by deferring multi-replica until Redis is
  brought in.

### Migration path

- Sprint 22 lands all of Phase A immediately (no cloud account needed).
- Phase B–E land in order against an Azure dev subscription.
- Phase F (AWS/GCP skeletons) lands as documentation-only PRs.
- Sprint 23+ implements one of `deploy/aws/terraform/` or
  `deploy/gcp/terraform/` against a real customer requirement.

## Alternatives considered

- **Cloud-agnostic Terraform everywhere** — rejected (§2). Provider
  lag and `azd` DX were the deciding factors.
- **Crossplane / Open Application Model** — rejected. Pulls k8s into
  the Azure path even though Container Apps is the v1 runtime.
- **Vendor-managed multi-cloud platform (e.g., Render, Railway)** —
  rejected. Dependency on a single vendor's view of multi-cloud, and
  none of them offer Azure DB for PostgreSQL Flexible Server with the
  TimescaleDB extension.
- **Custom data-export DSL** (instead of `pg_dump --where`) — rejected.
  Reuses ADR-008's already-designed mechanism; new DSL would need
  parity testing against `pg_dump` for every Alembic revision.
- **Skipping the Helm chart** (Bicep-only for v1) — rejected. The
  chart is the only thing AWS/GCP can adopt without re-deriving the
  workload shape from Bicep.
- **GHCR as the only image registry** — rejected during Phase C
  implementation. The original B3 design pushed only to GHCR and let
  ACA pull cross-registry via service principal. Switched to **dual
  push (GHCR + ACR)** because (a) GHCR cross-region pulls into Azure
  add 30–60s to ACA cold starts and (b) ACR Basic at ~$5/mo is
  cheaper than the GHCR private storage charge for our image volume.
  GHCR remains the dev-pull target; ACR is the production pull source
  referenced by the Bicep modules. See
  [.github/workflows/build-and-push.yml](../../.github/workflows/build-and-push.yml).

## Phase C implementation notes (Sprint 22)

The decision shape above held; concrete realization deltas:

- Bicep modules live under [deploy/azure/bicep/modules/](../../deploy/azure/bicep/modules/) — one per resource family. Orchestrator is [workload.bicep](../../deploy/azure/bicep/workload.bicep), entrypoint is [main.bicep](../../deploy/azure/bicep/main.bicep) (subscription-scope, creates RG + workload).
- **Identity model**: single user-assigned managed identity (UAMI) granted `AcrPull` on ACR + `Key Vault Secrets User` on KV. Both api/worker container apps and the migrations job assume the same UAMI. Avoids the chicken-and-egg of system identity AcrPull grants.
- **Postgres**: Flexible Server `Standard_B1ms` Burstable, public access + `AllowAllAzureIPs` firewall for v1. TimescaleDB enabled via `azure.extensions` allow-list + `shared_preload_libraries = timescaledb`. Private endpoint deferred to hardening backlog (see [deploy/azure/README.md](../../deploy/azure/README.md)).
- **MQTT**: single-node Mosquitto on ACI with Azure Files-backed config + data shares. Broker config + password file must be seeded once post-deployment (`az storage file upload`); ACI cannot inject volume contents on first boot.
- **OTel**: app code (`src/tagpulse/core/telemetry.py`) soft-imports `azure.monitor.opentelemetry.configure_azure_monitor` when `APPLICATIONINSIGHTS_CONNECTION_STRING` is set, taking precedence over the OTLP exporter path. Installed via `pip install ".[azure]"` in the Dockerfile build stage; non-Azure deployments are unaffected.
- **CD**: `azd up` for first deploy + ad-hoc; [.github/workflows/deploy-azure.yml](../../.github/workflows/deploy-azure.yml) on `v*` tag push for subsequent rollouts. Both run the migrations job to completion before updating api/worker revisions. OIDC federation only — no PATs.
