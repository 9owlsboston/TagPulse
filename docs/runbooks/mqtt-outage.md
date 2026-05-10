# Runbook: MQTT broker outage / ingestion stall

**Owner:** on-call engineer
**Sprint introduced:** 28 (C4)
**Companion docs:**
- [secret-rotation.md](secret-rotation.md) — broker credential rotation
- [observability/slos.md](../observability/slos.md) — ingestion-freshness SLO (Sprint 28 D1)

This runbook covers the failure mode where the worker container is up
but tag-reads have stopped flowing, or where an alert on
`tagpulse_mqtt_subscriber_last_message_age_seconds` (Sprint 28 C1, D2)
has fired.

---

## 1. Triage (≤ 2 min)

Run the doctor and the canary in parallel:

```bash
make doctor ENV=production
scripts/azd-job.sh production mqtt_canary.py
```

| Doctor red                              | Canary | Most likely root cause                                      |
| --------------------------------------- | ------ | ----------------------------------------------------------- |
| `mqtt aci state != Running`             | fails  | Mosquitto crashed → §3 restart                              |
| `mqtt aci state == Running`             | fails  | subscriber stuck or auth drift → §4 worker restart          |
| `mqtt aci state == Running`             | passes | metric stale only — confirm the alert isn't a false positive |
| `pg state != Ready`                     | fails  | DB-side stall — see `db-failover-and-restore.md`            |
| `kv secret 'mqtt-broker-password' soon` | passes | rotation overdue — schedule §6                              |

If the canary passes, the data path is healthy. Treat the alert as a
metric-only issue and check the Azure Monitor workbook (Sprint 28 D3)
for false-positive history before escalating.

## 2. Confirm the symptom in metrics

Open the Azure Monitor workbook **TagPulse Ingestion Health** (Sprint
28 D3). Look at:

- `tagpulse_mqtt_subscriber_last_message_age_seconds` — should be
  &lt; 60 in normal traffic, &lt; 300 in low-traffic windows.
- `tagpulse_mqtt_reconnect_attempts_total{reason}` — a spike in
  `auth_failed` means credential drift; `connection_refused` /
  `timeout` means broker unreachable.
- `tagpulse_mqtt_messages_rejected_total{reason}` — `invalid_schema`
  spikes mean a misbehaving device, not a broker outage.

## 3. Restart Mosquitto (broker side)

```bash
scripts/azd-mqtt-restart.sh production
```

Sprint 28 C5 — stops + starts the ACI, waits for `Running`, then tails
10s of logs. After the script returns:

1. Wait 30s for the worker's exponential-backoff reconnect (1, 2, 4,
   8, 16 → caps at 30s).
2. Re-run `scripts/azd-job.sh production mqtt_canary.py`.
3. Confirm `tagpulse_mqtt_subscriber_last_message_age_seconds` is
   trending back to baseline.

## 4. Restart the worker (subscriber side)

If the broker is healthy but the subscriber is stuck (e.g. blocked on
a malformed payload that crashed the loop, or a hung DB session):

```bash
az containerapp revision restart \
  -g "$(azd env get-value AZURE_RESOURCE_GROUP)" \
  -n "$(scripts/azd-name.sh worker)" \
  --revision "$(az containerapp revision list \
       -g $(azd env get-value AZURE_RESOURCE_GROUP) \
       -n $(scripts/azd-name.sh worker) \
       --query '[?properties.active].name' -o tsv | head -n1)"
```

Or scale to 0 then back to 1:

```bash
az containerapp update -g "$RG" -n "$WORKER" --min-replicas 0 --max-replicas 0
sleep 10
az containerapp update -g "$RG" -n "$WORKER" --min-replicas 1 --max-replicas 3
```

## 5. Drain dead-letter backlog (post-incident)

After an outage, schema-level rejections from §2 will sit in
`dead_letter_events` with `source='mqtt_subscriber'` (Sprint 28 C3).
Triage them per [dead-letter-triage.md](dead-letter-triage.md) (Sprint
28 E3):

```sql
SELECT topic, error_message, count(*)
  FROM dead_letter_events
 WHERE source = 'mqtt_subscriber'
   AND failed_at > now() - interval '1 hour'
 GROUP BY topic, error_message
 ORDER BY 3 DESC;
```

## 6. Rotate broker credentials (if root cause = auth_failed spike)

See [secret-rotation.md](secret-rotation.md) §"mqtt-broker-password".
TL;DR:

```bash
scripts/azd-ui-token-rotate.sh production --kind mqtt --dry-run   # preview
scripts/azd-ui-token-rotate.sh production --kind mqtt             # execute
```

Then `scripts/azd-mqtt-restart.sh production` so Mosquitto reloads the
new password file, and `az containerapp revision restart` on the
worker so the subscriber picks up the new env var.

## 7. Escalation

- Broker won't start after restart → check ACI events:
  `az container show -g $RG -n $ACI --query 'containers[0].instanceView.events'`
- Persistent `auth_failed` after rotation → KV secret may not have
  propagated; `make doctor ENV=production` will flag a stale revision.
- Subscriber repeatedly OOMs → resize worker container in
  `deploy/azure/bicep/modules/aca-worker.bicep` (CPU/memory) and
  re-deploy. Tracked as a backlog item in `docs/roadmap.md`.
- Page the platform lead if ingestion is still stalled 15 min after
  starting this runbook.

## Appendix: known false positives

- **Maintenance window deploys** — rolling worker revisions briefly
  detach all subscribers; `last_message_age_seconds` can spike to
  ~30s. The D2 alert uses a 5-minute evaluation window precisely to
  ride through this.
- **Low-traffic tenants overnight** — for environments with one
  device, the gauge can legitimately reach 300s+. The alert excludes
  `dev` for this reason; staging/production must keep a synthetic
  publisher running (the canary, on a 5-min schedule).
