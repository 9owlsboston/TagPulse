# Azure Monitor: TagPulse observability assets

**Sprint introduced:** 28 (D3)

This directory holds the KQL queries and workbook JSON for the
Azure Monitor / Log Analytics workspace defined in
`deploy/azure/bicep/modules/monitoring.bicep`. Companion docs:

- [SLO catalog](../../docs/observability/slos.md) — the four metrics
  the workbook fronts.
- [MQTT outage runbook](../../docs/runbooks/mqtt-outage.md) — uses
  these KQL snippets directly during triage.

## Contents

| File                              | Purpose                                                                  |
| --------------------------------- | ------------------------------------------------------------------------ |
| [`kql/api-availability.kql`](kql/api-availability.kql)        | Numerator/denominator for SLO #1 + p95 latency for SLO #2.               |
| [`kql/ingestion-freshness.kql`](kql/ingestion-freshness.kql) | Plots `tagpulse_mqtt_subscriber_last_message_age_seconds` per env.       |
| [`kql/dead-letter-by-source.kql`](kql/dead-letter-by-source.kql) | Pivots dead_letter_events by `source` (Sprint 28 C3).                    |
| [`kql/tenant-error-pivot.kql`](kql/tenant-error-pivot.kql)   | 5xx by tenant_id (uses Sprint 28 D5 span attribute).                     |
| [`workbook.json`](workbook.json) | Importable workbook composing the four KQL files into one SLO dashboard. |

## How to deploy

The workbook itself isn't auto-provisioned (Bicep workbook resources
are noisy and version poorly). To install:

```bash
az monitor app-insights workbook create \
  --resource-group "$(azd env get-value AZURE_RESOURCE_GROUP)" \
  --name tagpulse-slos \
  --display-name "TagPulse SLOs" \
  --serialized-data @ops/azure-monitor/workbook.json \
  --location "$(azd env get-value AZURE_LOCATION)" \
  --category workbook
```

Re-run after editing `workbook.json` (idempotent — same `--name`
overwrites). `make doctor` does NOT verify workbook presence (it's
operator-tooling, not a service-health gate).

## Iterating on a query

1. Edit the `.kql` file.
2. Paste into the Log Analytics query editor for the
   `tagpulse-${env}-logs` workspace.
3. Confirm the time-range, save back to the file with the working
   version.
4. Update `workbook.json` (the queries are inlined as strings).
