# Runbook: PostgreSQL failover and point-in-time restore

**Owner:** on-call engineer; platform lead approves any restore
**Sprint introduced:** 28 (E2)
**Companion docs:**
- [SLO catalog](../observability/slos.md) — DB latency is a *cause* of
  SLO #2 burn, not an SLO itself.
- [`secret-rotation.md`](secret-rotation.md) — `pg-admin-password`.
- [Azure architecture](../azure-architecture.md) §6 — PG15 Flex
  Standard_B1ms, private VNet only.

This runbook covers two distinct failure modes:

1. **Server-side failure** — the PG flex server is unreachable,
   crashed, or stuck in a bad state.
2. **Data-side corruption** — the server is up but a bad migration,
   bad backfill, or an `UPDATE` without `WHERE` mangled rows.
   Requires point-in-time restore (PITR).

> ⚠️ **PITR creates a new server.** It does NOT roll back data on the
> existing one. You must cut traffic over (and back) explicitly. Do
> not start a PITR without the platform lead's approval — there's a
> nontrivial cost and a multi-hour data-divergence window.

## A. Server failure (no data corruption)

### A1. Verify the symptom

```bash
make doctor ENV=production       # 'pg state' line

# The azd env exposes the server FQDN, not its short name. Derive the
# short name (first dotted label) before passing it to `az`.
PG_FQDN="$(azd env get-value postgresFqdn)"
PG_NAME="${PG_FQDN%%.*}"
az postgres flexible-server show \
  --resource-group "$(azd env get-value AZURE_RESOURCE_GROUP)" \
  --name "$PG_NAME" \
  --query 'state' -o tsv
```

> Earlier versions of this runbook referenced `POSTGRES_SERVER_NAME`,
> which is not emitted by our Bicep `outputs`. Using it produces
> `ERROR: key not found in environment values` and an empty `--name`
> argument that hangs the `az` call. Always derive from `postgresFqdn`.

| State                | Meaning                                  | Action               |
| -------------------- | ---------------------------------------- | -------------------- |
| `Ready`              | Server is up — recheck connectivity      | A2                   |
| `Stopped`            | Server is stopped (cost-saving in dev)   | A3                   |
| `Updating` / `Starting` | Azure is mid-operation; wait 5 min   | Re-check             |
| `Disabled` / others  | Severe — page lead; consider PITR (§B)   | A4 + B               |

### A2. Connectivity (server `Ready` but `make doctor` red)

The server is up but our containers can't reach it. Most likely:
private DNS, NSG, or firewall regression.

```bash
# Confirm private endpoint resolves from inside the ACA env.
scripts/azd-job.sh production resolve_pg_host.py
# (compare returned IP to the PE in deploy/azure/bicep/modules/pg.bicep)
```

If DNS is wrong, re-deploy the PG module: `azd deploy --service pg`.
If NSG was edited out-of-band, restore from git via `azd up`.

### A3. Server stopped (dev only — production should never stop)

```bash
scripts/azd-pg-ensure-running.sh production
```

The script polls until `state == Ready`. Production servers have
auto-start disabled — if production stopped, treat it as a SEV-1
and skip to §B (corruption can't be ruled out).

#### A3a. Drain the api's stale connection pool (recommended)

asyncpg's pool holds TCP sockets that the Flex server severed when
it stopped. The api will reconnect on the next request, but in-flight
requests can stall for the full TCP keepalive window (~2 min). Force
a clean restart of the active api revision:

```bash
RG="$(azd env get-value AZURE_RESOURCE_GROUP)"
API="$(az containerapp list -g "$RG" \
        --query "[?contains(name,'api')].name | [0]" -o tsv)"
REV="$(az containerapp revision list -n "$API" -g "$RG" \
        --query "[?properties.active].name | [0]" -o tsv)"
az containerapp revision restart -n "$API" -g "$RG" --revision "$REV"
```

The same restart is wired into the `azd` `predeploy` hook, so a
subsequent `azd deploy` self-heals automatically. Skip this step if
`make smoke ENV=<env>` already passes after A3.

### A4. Forced failover to standby

PG Flex Burstable tier (Standard_B1ms) does NOT have HA. We are
single-AZ, single-instance. There is no in-place failover. If the
server is unrecoverable:

1. Open SEV-1 incident.
2. Begin PITR (§B) — this is the *only* recovery path until we
   upgrade the SKU. Documented as a backlog risk in `docs/roadmap.md`
   under "Database HA".

## B. Point-in-time restore

> Use only when: data is corrupted, server is unrecoverable, or
> someone shipped a bad migration that altered tons of rows.

### B1. Decide the restore time

Pick a UTC timestamp from BEFORE the corruption began. Round DOWN
to the nearest minute. Get it from:

- The git commit time of the bad migration / deploy.
- The first dead_letter row that mentions the corruption.
- The first customer report (be conservative — earlier is safer).

```bash
# Confirm the source server still has PITR coverage at that timestamp.
az postgres flexible-server show-connection-string \
  --resource-group "$(azd env get-value AZURE_RESOURCE_GROUP)" \
  --server-name "$(azd env get-value POSTGRES_SERVER_NAME)" \
  --query 'earliestRestoreDate' -o tsv
```

If your target time is BEFORE `earliestRestoreDate`, PITR cannot help
— escalate to lead, may need a logical-backup restore from a
separate retention store.

### B2. Create the restore (creates a NEW server)

```bash
SRC=$(azd env get-value POSTGRES_SERVER_NAME)
RG=$(azd env get-value AZURE_RESOURCE_GROUP)
LOC=$(azd env get-value AZURE_LOCATION)
NEW="${SRC}-restore-$(date -u +%Y%m%d-%H%M)"
RESTORE_TIME="2026-05-09T14:23:00Z"   # ⚠️ EDIT to your chosen time

az postgres flexible-server restore \
  --resource-group "$RG" \
  --name "$NEW" \
  --source-server "$SRC" \
  --restore-time "$RESTORE_TIME" \
  --location "$LOC"
```

This takes 15–45 min for a Burstable server. Watch progress:

```bash
az postgres flexible-server show -g "$RG" -n "$NEW" --query 'state' -o tsv
```

### B3. Validate the restored server

Connect via a temporary jumpbox or the existing tools-job:

```bash
# Re-point a tools-job at the restored server for read-only checks.
scripts/azd-job.sh production verify_pg_restore.py --pg-host "${NEW}.postgres.database.azure.com"
```

Run the verification SQL from the incident report (e.g. "row count of
table X should be ≥ N", "no NULL in column Y added by bad migration").

### B4. Cutover traffic

This is the irreversible step. Lead approval required.

1. Drop traffic to the api: `az containerapp update -g $RG -n $API --min-replicas 0 --max-replicas 0`.
2. Update KV secret `pg-admin-password` if the restored server has a
   different password (PITR copies the original — usually unchanged).
3. Update KV secret `pg-host` (or env var) to point to the new server.
4. Bring the api back: `az containerapp update -g $RG -n $API --min-replicas 1 --max-replicas 3`.
5. Run `scripts/azd-job.sh production mqtt_canary.py` to confirm
   end-to-end health.
6. Rename the old server to `${SRC}-quarantine-$(date)` so nobody
   accidentally connects to it. Don't delete for 7 days — useful for
   forensic comparison.

### B5. Post-cutover

- Status-page update: data restored to <RESTORE_TIME>, any writes
  between then and now are lost.
- Reach out to affected tenants individually with what they need to
  re-submit (this is what makes data-corruption incidents expensive
  — make sure §B is genuinely needed before starting).
- Schedule the post-mortem (template in
  [`incident-template.md`](incident-template.md)).

## C. Routine: connection-pool exhaustion

Symptom: api 5xx with `QueuePool limit of size N overflow M reached`
in logs. Not a server failure — it's our app holding sessions too
long.

```bash
# Confirm.
make logs ENV=production --kind api | grep -i 'pool\|connection'
# Mitigation: bounce the api revision (drops all open sessions cleanly).
az containerapp revision restart -g "$RG" -n "$API" \
  --revision "$(az containerapp revision list -g $RG -n $API \
    --query '[?properties.active].name' -o tsv | head -n1)"
```

If this recurs more than once a week, it's a code regression — open
an issue with `db-pool` label and link to the most recent occurrence
in the incident log.

## D. Backups and retention

- **Built-in:** PG Flex retains transaction logs for 7 days
  (Burstable). Configurable up to 35 days — increase via
  `deploy/azure/bicep/modules/pg.bicep` and re-deploy.
- **No off-Azure backup.** Captured as a backlog item in
  `docs/roadmap.md` ("DR: cross-region backup"). Until then, PITR
  within the 7-day window is our only recovery path.
- The doctor (Sprint 28 F4) checks `earliestRestoreDate` is &lt;= 7d
  ago and red-flags it if drift indicates the server is in a bad
  state.
