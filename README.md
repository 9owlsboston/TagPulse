# TagPulse

IoT platform for RFID tag readers and sensor data. Ingests device telemetry, manages device registry, and runs pluggable analytics modules tailored to application needs.

## Status

- **Current sprint:** 53 — Workflow tooling & cross-sprint catch-up (see [docs/roadmap.md §sprint-53](docs/roadmap.md#sprint-53--workflow-tooling--cross-sprint-catch-up-pr-72)).
- **Shipped through Sprint 53:** Azure deploy via `azd up` (Sprint 22+), Static Web App frontend (Sprint 24), VNet integration + private endpoints (Sprint 23), tenant-scoped KV (Sprint 26), subject-scoped telemetry (Sprints 18–21), SLOs + alerts + on-call runbooks (Sprint 28), edge wire format v2 + presence reconciler ([ADR-025](docs/adr/025-edge-wire-format-v2.md), Sprints 46/47), tag registry backend + UI ([ADR-028](docs/adr/028-tags-as-first-class-entity.md), Sprints 50/51), `GET /bulk-operations` (Sprint 52), v2 test clients + MQTT TLS sidecar + `sensor_data → telemetry_readings` bridge fix (Sprint 53 F/H/I).
- **Operators:** start at [docs/operator-quickstart.md](docs/operator-quickstart.md). On-call → [docs/runbooks/incident-template.md](docs/runbooks/incident-template.md). Edge / Pi smoke + canary recipes → [clients/pi/README.md](clients/pi/README.md).
- **Developers on a laptop:** [docs/quickstart.md](docs/quickstart.md).

## Quick Start (developer laptop)

```bash
# Install dependencies
pip install -e ".[dev]"

# Run quality gates
make check

# Start the development server
make run
```

## Architecture

- **Device registry & config** — register, configure, and monitor IoT device fleet.
- **Dual ingestion** — MQTT (on `:1883`; `:8883` TLS opt-in via Sprint 28 C6) and HTTP endpoints for device telemetry.
- **TimescaleDB on Azure PG Flex (PG15)** — time-series storage for tag reads + relational storage for device registry.
- **Rules & alerts** — user-defined rules evaluated against telemetry, with webhook/email alert routing.
- **Plugin analytics** — analytics modules as internal Python packages.
- **Integration layer** — outbound webhooks, SSE streaming, scheduled data exports.
- **Admin UI** — React SPA on Azure Static Web Apps (shipped Sprint 24).
- **Observability** — OpenTelemetry → Application Insights, SLO-aligned metric alerts (Sprint 28 D2), KQL workbook (Sprint 28 D3).

See [docs/architecture.md](docs/architecture.md) for the full system overview and [docs/azure-architecture.md](docs/azure-architecture.md) for the Azure-specific layout.

## Deployment

- **Azure (first-class target).** Step-by-step checklist: [docs/runbooks/azure-first-deploy.md](docs/runbooks/azure-first-deploy.md). Module/SKU reference: [deploy/azure/README.md](deploy/azure/README.md). Design rationale: [ADR-016](docs/adr/016-multi-cloud-deployment-strategy.md).
- **Frontend (Azure Static Web App).** SPA shipping path: [docs/runbooks/ui-first-deploy.md](docs/runbooks/ui-first-deploy.md). UI repo: [9owlsboston/TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI). Design rationale: [ADR-018](docs/adr/018-frontend-cloud-deployment.md).
- **Provider-agnostic Helm chart** (k8s portability target): [deploy/common/helm/tagpulse/README.md](deploy/common/helm/tagpulse/README.md).
- **Operator runbooks** (token rotation, deploy, etc.): [docs/runbooks/](docs/runbooks/README.md).

### Deployment topology (CI/CD vs local azd)

There are three distinct paths that can change what's running in Azure. Knowing which one fired is essential when correlating an incident with a workflow run (see [issue #17](https://github.com/9owlsboston/TagPulse/issues/17)).

| Path | Trigger | What it does | Touches ACA revisions? |
| --- | --- | --- | --- |
| [`build-and-push.yml`](.github/workflows/build-and-push.yml) | every push to `main`, `v*` tag, PR | Builds `tagpulse-{api,worker,migrations}` images, pushes to GHCR (always) and ACR (on `main`/tag). Smokes the tools image. Attests provenance. | **No.** Registry only. ACA pulls images at deploy time, not on push. |
| [`deploy-azure.yml`](.github/workflows/deploy-azure.yml) | `v*` tag push (→ `production`), or manual `workflow_dispatch` (→ `dev`/`staging`/`production`) | Reuses an already-pushed image tag, runs the migrations job, then updates the ACA app to the new revision. This is the **only CI path that creates revisions.** | **Yes.** Revision name pattern: `<app>--<sprint-or-tag>-<sha>`. |
| Local `azd deploy` (operator laptop) | manual, ad hoc | `azd` builds locally and updates the ACA app directly. | **Yes**, but the revision name is `<app>--azd-<unix_ts>` (e.g. `tpdev-api--azd-1778312957`) — that prefix uniquely identifies a laptop deploy. |

**Audit rules of thumb**
- `azd-<digits>` revision suffix → someone ran `azd deploy` locally; correlate with operator activity, not a workflow run.
- `build-and-push` finishing alone never changes what's live. If a new revision appeared right after it, it came from one of the other two paths.
- `dev` (`tagpulse-dev-rg`) is a free-fire zone for local `azd deploy`. `staging` and `production` should only ever change via `deploy-azure.yml` so the audit trail is in GitHub Actions.

## Project Structure

```
src/
  tagpulse/
    api/          # FastAPI routes
    ingestion/    # MQTT + HTTP ingestion endpoints
    models/       # Database models (SQLAlchemy + TimescaleDB)
    rules/        # Rules engine + alert routing
    analytics/    # Pluggable analytics modules
    integrations/ # Webhooks, SSE, scheduled exports
    core/         # Config, dependencies, shared utilities
tests/
  unit/           # Fast, isolated tests
  integration/    # Cross-component tests
docs/             # Architecture, ADRs, runbooks
```
