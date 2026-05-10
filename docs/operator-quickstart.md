# Operator Quickstart

One page. If you can't find what you need here in 30 seconds, the answer is
in the linked runbook — not in this file.

## What is running where

Each Azure environment (`dev`, `staging`, `production`) has the same shape.
All resource names follow the pattern `tp${env}-*`:

```text
+--------------------------+      +---------------------------+
|  Static Web App          |      |  Azure Container Apps     |
|  tp${env}-ui             |      |  env: tp${env}-aca-env    |
|  (React SPA, public)     |----->|                           |
+--------------------------+      |  - tp${env}-api  (HTTP)   |
                                  |  - tp${env}-worker (MQTT) |
                                  |  - migrations / tools job |
                                  +-----------+---------------+
                                              |
            +---------------------------------+----------------------+
            |                  |                       |             |
            v                  v                       v             v
   +-----------------+ +---------------+ +-----------------+ +-------------+
   |  Mosquitto ACI  | |  PG Flex (15) | |  Key Vault      | |  ACR        |
   |  tp${env}-mqtt  | |  tp${env}-pg  | |  tp${env}-kv-*  | |  tp${env}acr|
   |  :1883 / :8883* | |  Timescale    | |  jwt/mqtt/pg/   | |  images     |
   +-----------------+ +---------------+ |  tls secrets    | +-------------+
                                         +-----------------+
                                                  |
                                          +---------------+
                                          | Log Analytics |
                                          | App Insights  |
                                          | tp${env}-logs |
                                          +---------------+
```

`*` port 8883 (TLS) is provisioned only when `AZURE_MQTT_TLS_ENABLED=true`
in the azd env. See [secret-rotation.md §mqtt-tls](runbooks/secret-rotation.md#mqtt-tls-ca--mqtt-tls-cert--mqtt-tls-key-sprint-28-c6).

## How to log in

1. Pull the dev tenant's admin API key from KV:

   ```bash
   scripts/azd-kv-get.sh dev tagpulse-test-corp-admin-key
   ```

2. Hit the API directly:

   ```bash
   API=$(azd env get-value AZURE_API_URL --cwd .)
   curl -H "Authorization: Bearer $KEY" "$API/api/v1/tenant/config"
   ```

3. Or browse the SPA:

   ```bash
   azd env get-value SERVICE_UI_URI --cwd .
   ```

   Sign in via the dev tenant's seeded user (`docs/runbooks/azure-first-deploy.md`
   §"Seed users").

## How to do the 5 most common things

All commands take `ENV=dev|staging|production`. Targets defined in `Makefile`.

| Task | Command | Runbook |
|---|---|---|
| **Check env health** | `make doctor ENV=dev` | [operational-tooling.md](runbooks/operational-tooling.md) |
| **Deploy a code change** | `azd up` (or `azd deploy api`) | [azure-first-deploy.md §subsequent-deploys](runbooks/azure-first-deploy.md) |
| **Rotate the test-corp API key** | `make rotate-key ENV=dev TENANT=test-corp` | [secret-rotation.md §tagpulse-test-corp-admin-key](runbooks/secret-rotation.md#tagpulse-test-corp-admin-key) |
| **Simulate MQTT ingestion** | `scripts/azd-job.sh dev mqtt_canary.py` | [mqtt-outage.md §canary](runbooks/mqtt-outage.md) |
| **Retry a dead-letter event** | `scripts/azd-job.sh dev replay_dead_letter.py -- --id <UUID>` | [dead-letter-triage.md](runbooks/dead-letter-triage.md) |
| **Restart Mosquitto** | `scripts/azd-mqtt-restart.sh dev` | [mqtt-outage.md §restart-broker](runbooks/mqtt-outage.md) |
| **Tail logs** | `make logs ENV=dev SERVICE=api SINCE=15m` | [operational-tooling.md](runbooks/operational-tooling.md) |

(Yes, that's seven. Counting is hard.)

## When something is on fire

1. Open [`incident-template.md`](runbooks/incident-template.md). Copy
   the first-5-minutes block into a fresh incident doc; assign IC + comms
   + scribe.
2. Run `make doctor ENV=<env>` and paste the output. This is the fastest
   way to know "is the platform broken, or is just this one thing broken".
3. Match the alert / symptom to a failure-mode runbook:
   - MQTT silent / subscriber stalled → [mqtt-outage.md](runbooks/mqtt-outage.md)
   - DB unreachable / corruption → [db-failover-and-restore.md](runbooks/db-failover-and-restore.md)
   - Dead-letter burst → [dead-letter-triage.md](runbooks/dead-letter-triage.md)
   - API 5xx burn / availability alert → [incident-template.md §triage](runbooks/incident-template.md) → check Application Insights `requests/failed`
4. After the incident, fill the timeline + RCA section. Open a ticket for
   every "actions" row.

## Where the alerts are wired

Sprint 28 D2 ships four Azure Monitor metric alerts, default-off. To enable
on an env:

```bash
azd env set AZURE_DEPLOY_ALERTS true
azd env set AZURE_ALERT_EMAIL oncall@example.com
azd up
```

Alerts (with the runbook each one points at):

| Alert | Severity | Points at |
|---|---|---|
| `tp${env}-alert-mqtt-stalled` | SEV1 | [mqtt-outage.md](runbooks/mqtt-outage.md) |
| `tp${env}-alert-availability-fast-burn` | SEV1 | [incident-template.md](runbooks/incident-template.md) |
| `tp${env}-alert-api-p95-latency` | SEV2 | [incident-template.md §triage](runbooks/incident-template.md) |
| `tp${env}-alert-dead-letter-burst` | SEV1 | [dead-letter-triage.md](runbooks/dead-letter-triage.md) |

See [observability/slos.md](observability/slos.md) for the burn-rate math
and error-budget freeze policy.

## SSH / shell into things

Container Apps don't give you a shell. Use the `tools` job for one-shot
Python commands:

```bash
scripts/azd-job.sh dev <script.py> [-- script-args]
```

For Postgres, port-forward via the api app's managed identity:

```bash
PG_HOST=$(azd env get-value AZURE_POSTGRES_FQDN --cwd .)
# psql via private DNS — requires running from inside the VNet
# (a one-shot tools-job is the easiest path):
scripts/azd-job.sh dev psql_probe.py
```

For Mosquitto, log into the ACI:

```bash
az container exec -g tp${env}-rg -n tp${env}-mqtt --exec-command /bin/sh
```

## Pointers

- Full architecture: [architecture.md](architecture.md)
- Azure-specific layout: [azure-architecture.md](azure-architecture.md)
- Developer / laptop on-ramp: [quickstart.md](quickstart.md)
- All runbooks: [runbooks/README.md](runbooks/README.md)
- SLOs: [observability/slos.md](observability/slos.md)
