# Runbook: Secret Rotation

> Sprint 28 B2. Single source of truth for every secret TagPulse stores in
> Azure Key Vault. For each secret: rotation cadence, who rotates, exact
> command, how to verify, and blast radius if compromised.
>
> See also: [device-token-rotation.md](device-token-rotation.md) (per-device
> tokens, separate from this runbook), [azd-survival-guide.md](azd-survival-guide.md)
> (general azd ops).

---

## Per-secret summary

| Secret name (in KV) | What it is | Cadence | Rotator | Blast radius |
|---|---|---|---|---|
| `tagpulse-test-corp-admin-key` | Demo tenant's admin API key (post-`smoke_setup.py --regenerate-key`) | Ad-hoc / on demo refresh | Engineer with KV `Secrets User` | Demo tenant only — no production data. |
| `mqtt-broker-username` | Mosquitto username (Sprint 27 D2) | 12-month | Engineer with KV `Secrets Officer` | All MQTT ingestion in env (paired with password) |
| `mqtt-broker-password` | Mosquitto password | 12-month, or on suspicion | Engineer with KV `Secrets Officer` | All MQTT ingestion in env (until devices reconnect with new creds) |
| `pg-admin-password` | Postgres admin password | 12-month, or on suspicion | Engineer with KV `Secrets Officer` + `Postgres Flexible Server Contributor` | Full DB read/write — restoration of from-backup may be required if compromised |
| `ui-deploy-token` (`AZURE_STATIC_WEB_APPS_API_TOKEN`) | Static Web App deploy token; lives in TagPulse-UI repo's GH env secrets | 90-day, automated cron in TagPulse-UI repo (D2) | Cron / engineer with `azd-ui-token-rotate.sh` access | UI deployments only; no runtime data exposure |
| `azd-cicd-sp-secret` | Service principal secret used by `deploy-azure.yml` GH workflow | 12-month | Engineer with subscription `User Access Administrator` | Full deploy capability against the env's RG |
| `tagpulse-tls-ca` / `tagpulse-tls-cert` / `tagpulse-tls-key` (Sprint 28 C6, future) | Mosquitto TLS cert/key for `:8883` listener | 12-month, auto-rotate via KV-managed cert | Auto / engineer with KV `Certificates Officer` | TLS interception of MQTT traffic |

> **Audit your env now:** `scripts/azd-kv-audit.sh <env>` (Sprint 28 B1).

---

## Rotation procedures

### `tagpulse-test-corp-admin-key`

```bash
scripts/azd-job.sh dev smoke_setup.py -- --regenerate-key
```

The tools-job's UAMI has `Key Vault Secrets Officer` on the env vault (Sprint 26
D3), so the freshly generated key is written directly to KV — no plaintext
in stdout. **Verify**:

```bash
make smoke ENV=dev   # uses the rotated key for the /tenant/config probe
```

**Dry-run preview**: `scripts/azd-job.sh dev smoke_setup.py -- --regenerate-key --dry-run` (when supported by `smoke_setup.py`).

### `mqtt-broker-password` (and `mqtt-broker-username`)

```bash
# 1. Generate new value
NEW=$(openssl rand -hex 32)

# 2. Write to KV (use --dry-run first)
az keyvault secret set --vault-name <KV> --name mqtt-broker-password --value "$NEW"

# 3. Restart Mosquitto so the new value is mounted from secretRef env var
scripts/azd-mqtt-restart.sh <env>

# 4. Verify ingestion
scripts/azd-mqtt-canary.py     # via azd-job; Sprint 28 C2
```

**Order matters**: KV update → Mosquitto restart → device-side credential update.
If devices update before the broker restarts, they'll fail auth and reconnect-loop
until the broker also updates. Coordinate during a low-traffic window.

### `pg-admin-password`

```bash
NEW=$(openssl rand -base64 32 | tr -d '/+=')

# 1. Update Postgres
PG=$(az postgres flexible-server list -g <rg> --query '[0].name' -o tsv)
az postgres flexible-server update -n "$PG" -g <rg> --admin-password "$NEW"

# 2. Update KV (this is the source of truth for api+worker secretRef)
az keyvault secret set --vault-name <KV> --name pg-admin-password --value "$NEW"

# 3. Restart api + worker so new secretRef value is mounted
az containerapp restart -n $(scripts/lib/aca-name api) -g <rg>
az containerapp restart -n $(scripts/lib/aca-name worker) -g <rg>

# 4. Verify
make smoke ENV=<env>
```

If the api/worker restart **before** KV is updated, they'll restart with the
old password and immediately fail to connect — `make doctor ENV=<env>` will
flag it. Recovery: complete the KV update + restart.

### `ui-deploy-token` (`AZURE_STATIC_WEB_APPS_API_TOKEN`)

```bash
scripts/azd-ui-token-rotate.sh <env>           # rotates + writes to UI repo env secret
scripts/azd-ui-token-rotate.sh <env> --dry-run # preview the gh + az calls
```

The 60-day idempotency gate prevents accidental double-rotation. Override with
`--force` only when you genuinely need to rotate again sooner (e.g., suspected
compromise).

### `azd-cicd-sp-secret`

```bash
# Re-run the bootstrap, which re-creates the SP secret and re-pushes it to GH
scripts/azd-cicd-setup.sh <env> --rotate-secret
```

**Verify**: open the next deploy run on `deploy-azure.yml` and confirm Azure
auth succeeds. The old secret is invalidated immediately on rotation.

### Mosquitto TLS cert/key (Sprint 28 C6, future)

When C6 ships, KV-managed certificate auto-rotation handles this automatically
on a 12-month cycle. Manual override:

```bash
az keyvault certificate create --vault-name <KV> --name mqtt-tls-cert \
  --policy "$(az keyvault certificate get-default-policy)"
scripts/azd-mqtt-restart.sh <env>     # Sprint 28 C5
```

---

## Cross-cutting

- **Always preview with `--dry-run` first** if the script supports it (Sprint 28 B3).
- **Always run `scripts/azd-kv-audit.sh <env>` after rotation** to confirm the
  new `updated` timestamp and that no secret slipped past its `expires`.
- **Audit log**: every UI-token rotation is appended to
  `deploy/azure/.audit/ui-token-rotation.jsonl`. Other rotations should
  follow the same pattern when a comparable wrapper script exists.
- **On suspicion of compromise**: rotate immediately + audit access logs in
  Log Analytics for the time window in question + open a SEV2 incident
  (`docs/runbooks/incident-template.md`).
- **Quarterly drill**: rotate one secret per quarter to keep the procedure warm,
  even if no rotation is technically due.

---

## Last validated

Sprint 28 (May 2026). When you complete a rotation, append a row to
`deploy/azure/.audit/rotation.log` (jsonl) so the next operator has context.
