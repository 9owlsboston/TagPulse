# Operator Runbooks

Step-by-step procedures for running TagPulse in production. Each runbook is
keyed to the design / ADR that introduced it. **Reading order for a new
on-call engineer:** [operator-quickstart](../operator-quickstart.md) →
[incident-template](incident-template.md) → the failure-mode runbook
referenced from the incident template.

> **Lint:** all runbooks are checked by `.github/workflows/docs-lint.yml`
> (Sprint 28 H5) — `markdownlint-cli2` + `lychee` link check on every PR
> that touches `docs/**.md`, top-level `*.md`, or `CHANGELOG.md`.

## First-time setup

| Runbook | Introduced | Source |
|---|---|---|
| [azure-first-deploy.md](azure-first-deploy.md) | Sprint 22 Phase C | [deploy/azure/README.md](../../deploy/azure/README.md), [ADR-016](../adr/016-multi-cloud-deployment-strategy.md) |
| [ui-first-deploy.md](ui-first-deploy.md) | Sprint 24 Phase C | [docs/design/frontend-deployment.md](../design/frontend-deployment.md), [ADR-018](../adr/018-frontend-cloud-deployment.md) |

## Day-to-day ops

| Runbook | Introduced | What it covers |
|---|---|---|
| [operational-tooling.md](operational-tooling.md) | Sprint 26 | The `make doctor` / `make smoke` / `scripts/azd-*` loop. |
| [secret-rotation.md](secret-rotation.md) | Sprint 27 D2 (extended Sprint 28 C6) | KV rotation for JWT, MQTT user/pass, MQTT TLS material (Sprint 28 C6), PG admin, UI deploy token, CI SP. |
| [azd-survival-guide.md](azd-survival-guide.md) | Sprint 22 Phase C | `azd` state recovery, env value resolution, image-existence checks. |
| [github-workflows.md](github-workflows.md) | Post Sprint 28 | Catalog of every `.github/workflows/*.yml`: trigger, schedule, purpose, manual-run, failure triage. |

## Incident response

| Runbook | Introduced | When to open |
|---|---|---|
| [incident-template.md](incident-template.md) | **Sprint 28 E1** | Always. First doc opened during a SEV-1/2. |
| [db-failover-and-restore.md](db-failover-and-restore.md) | **Sprint 28 E2** | Postgres server unhealthy, PG-side data corruption, or connection-pool exhaustion. |
| [dead-letter-triage.md](dead-letter-triage.md) | **Sprint 28 E3** | Dead-letter burst alert fires or the workbook's "dead-letters by source" panel trends up. |
| [mqtt-outage.md](mqtt-outage.md) | **Sprint 28 C4** | MQTT subscriber stalled alert, broker unreachable, or worker can't auth. Pairs with `scripts/mqtt_canary.py` (C2) and `scripts/azd-mqtt-restart.sh` (C5). |

## Migrations & cutovers

| Runbook | Introduced | Status |
|---|---|---|
| [sprint-23-network-cutover.md](sprint-23-network-cutover.md) | Sprint 23 Phase B | Historical — references the VNet / private endpoint cutover. Keep for posterity / re-do in a clean env. |
| [geofence-postgis-trigger.md](geofence-postgis-trigger.md) | Sprint 17a | Stable. PostGIS trigger DDL + idempotency notes. |
| [subject-scoped-telemetry.md](subject-scoped-telemetry.md) | Sprint 20 | Cutover from device-scoped to subject-scoped telemetry; idempotent dual-write window. |
| [device-token-rotation.md](device-token-rotation.md) | Sprint 16 | Bulk-rotate device JWTs; see also ADR-011 for the long-term mTLS plan (ADR-012 partially implemented in Sprint 28 C6). |

## Cross-references

- Architecture surface map: [docs/architecture.md](../architecture.md)
- Azure-specific layout: [docs/azure-architecture.md](../azure-architecture.md)
- SLO definitions + burn-rate math: [docs/observability/slos.md](../observability/slos.md)
- Alert + KQL queries: [ops/azure-monitor/README.md](../../ops/azure-monitor/README.md)
- ADR index: [docs/adr/README.md](../adr/README.md)

