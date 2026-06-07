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
| **Check env health** | `make doctor ENV=dev` | [operational-tooling.md §doctor-cheat-sheet](runbooks/operational-tooling.md#make-doctor-recovery-cheat-sheet) |
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

## Demo tenant (local only)

Sprint 58 ships a one-command composer that builds a fully populated
demo tenant on a local `docker compose up` stack. Use it for screenshots,
walkthroughs, and Lighthouse / perf baselines — never against a deployed
environment.

```bash
docker compose up -d
alembic upgrade head
make demo-tenant            # ~2-3 min; idempotent on re-run
```

The composer runs seven steps in sequence:

1. `smoke_setup.py --full` — provisions the `demo-wm-dc` tenant
   (id `uuid5(DNS, "demo-wm-dc.tagpulse.local")`), zones, telemetry
   model, rules, RBAC, and rotates a fresh admin API key.
2. `simulate_devices.py --seed-only` — registers 10 RFID readers.
3. `simulate_inventory.py --seed-only` — seeds 60 inventory units across
   4-6 products.
4. `simulate_assets.py --iterations 20 --interval 0.1` — creates 12
   bound assets and emits a short burst of movement reads.
5. `backfill_history.py --days 3 --reads 5000` — replays 3 days of
   historical reads via `POST /tag-reads/batch?backfill=true`. The
   `backfill=true` flag suppresses rules, alerts, and the read-frequency
   rollup so the history doesn't trigger 5000 stale notifications.
6. `seed_alerts.py` — produces 4 live alerts (high-temperature reads
   above 30 °C, fired via the normal ingest path) plus 3 resolved
   alerts inserted directly to give the UI an alert-resolution timeline.
7. `seed_transfer.py` — creates a `demo-wm-recipient` tenant and one
   in-flight cross-tenant transfer of 3 EPCs.

The final line prints `export TAGPULSE_API_KEY=<key>` so you can
`eval $(make demo-tenant | tail -1)` and start hitting the API.

Environment knobs:

| Variable | Effect |
| --- | --- |
| `DEMO_KEEP_KEY=1` | Reuse the existing `$TAGPULSE_API_KEY` instead of rotating. Required if you want to keep an open browser session. |
| `DEMO_SKIP_BACKFILL=1` | Skip step 5 (saves ~30 s; the dashboard's history view will be empty). |
| `DEMO_RESET_FORCE=1` | Bypass the "looks-local" guard in `make demo-tenant-reset` (only set this if you know what you're doing). |

To start over:

```bash
make demo-tenant-reset      # drops demo + recipient tenants and all their rows
make demo-tenant            # rebuild from scratch
```

### Continuous simulator

After the composer finishes, `make sim-start` launches a long-running
background service that keeps the tenant animated for review sessions:

```bash
export TAGPULSE_API_KEY=<key>   # from `make demo-tenant` final line
make sim-start                  # docker compose --profile sim up -d sim
make sim-status                 # ps + tail last 50 log lines
make sim-stop                   # stop + remove the sim container
```

What it does each second:

- emits realistic tag reads against the demo tenant, gated by a token
  bucket (default **200 reads/min**, configurable via `SIM_RATE_PER_MIN`;
  hard ceiling **600/min** enforced inside the loop);
- applies a shift schedule — 1.5 × during ±30 min around 08:00 and
  13:00 local, 0.3 × during 20:00 – 06:00, 1.0 × otherwise;
- every ~minute, a 5 % chance to take one reader offline for 3–8 min;
- every 15 min, fires one high-temperature read on a random device so
  the dashboard's alerts panel stays warm.

Knobs:

| Variable | Effect |
| --- | --- |
| `SIM_RATE_PER_MIN=400 make sim-start` | Push harder (capped at 600/min). |
| `SIM_DURATION=30m make sim-start` | Run for a bounded window then stop. |
| `SIM_SEED=42 …` | Deterministic PRNG for reproducible runs. |

The same binary runs as a manual-trigger Azure Container Apps Job in the
dev environment (Sprint 58 D6). The job is **dev-only** — the script
aborts non-zero if `ENV` is set to anything other than `dev`:

```bash
# Dev only. Reuses the tools-job image; HTTP egress to the dev API only.
scripts/azd-job.sh dev sim_loop.py -- --duration 8h --rate 200
```

## Pointers

- Full architecture: [architecture.md](architecture.md)
- Azure-specific layout: [azure-architecture.md](azure-architecture.md)
- Developer / laptop on-ramp: [quickstart.md](quickstart.md)
- All runbooks: [runbooks/README.md](runbooks/README.md)
- SLOs: [observability/slos.md](observability/slos.md)
- Edge / Pi reference client (v2 wire format, smoke publisher, canary, TLS handshake recipes): [../clients/pi/README.md](../clients/pi/README.md)
