# Runbook: tag-reads not showing up

**Owner:** on-call engineer
**Companion docs:**
- [mqtt-outage.md](mqtt-outage.md) — broker-level outage and the canary
- [wm-wire-format-v2.md](wm-wire-format-v2.md) — WM v2 reject reasons
- [operational-tooling.md](operational-tooling.md) — the `scripts/azd-*` toolbox
- [observability/slos.md](../observability/slos.md) — ingestion-freshness SLO

This runbook covers the failure mode where the broker is **up**, the
worker is **running**, the ingestion-freshness SLO is **green**, but a
specific operator says "I don't see my tag reads." That is, **per-device
or per-tenant** ingestion gaps, not a fleet-wide outage. For a fleet-wide
stall, start with [mqtt-outage.md](mqtt-outage.md).

All commands here run inside the deployed `tools` Container Apps job via
[`scripts/azd-job.sh`](../../scripts/azd-job.sh), which gives them VNet
+ UAMI access to the private MQTT broker and Postgres (a laptop has
neither).

---

## TL;DR decision tree

```
mqtt_tap.py shows nothing
  → device/broker/auth problem        → §1
mqtt_tap.py shows frames
  ├─ worker logs "Rejecting v2 wm …"
  │    → wire-format bug (publisher)  → §2
  ├─ worker logs "Tag read ingested"
  │    ├─ tag_reads_near empty
  │    │    → DB write path           → §4
  │    └─ tag_reads_near has rows
  │         → UI / query problem      → §5
  └─ worker logs silent on this device
       → routing / topic / tenant     → §3
```

---

## 1. Is the device actually publishing? — `mqtt_tap.py`

Read-only subscriber on the in-VNet broker. Prints one line per received
MQTT message; never writes to the DB. Pairs with
[`scripts/mqtt_canary.py`](../../scripts/mqtt_canary.py) (publisher) and
[`scripts/azd-logs.sh`](../../scripts/azd-logs.sh) (worker tail).

```bash
# Tap one reader for 60 s
scripts/azd-job.sh dev mqtt_tap.py -- \
  --device-id 889bd6fc-2bd3-4936-b0e2-fddfbd9fe5dc --duration 60

# Tap a whole tenant, stop after 100 messages or 5 min — whichever first
scripts/azd-job.sh dev mqtt_tap.py -- \
  --tenant-id 241d9b81-59da-5fb7-8f78-f58200978566 \
  --max-messages 100 --duration 300

# Tap everything the worker sees (default filters, 60 s)
scripts/azd-job.sh dev mqtt_tap.py -- --duration 60
```

| Observation | Interpretation | Next |
|---|---|---|
| **No frames at all** for the device window | device isn't publishing, broker auth failing, or wrong topic | check edge logs / device JWT; see [device-token-rotation.md](device-token-rotation.md) |
| Frames on a *different* topic than expected | publisher is using the wrong tenant/device id | fix the edge config; the topic must be `tenants/{tenant}/devices/{device}/tag-reads` |
| Frames present, well-formed | broker + auth + routing OK | go to §2 |

> **Tip.** Use `--show-bytes` if you suspect a payload-encoding issue
> (e.g. binary masquerading as JSON). Default output pretty-prints JSON
> when it parses; `--no-pretty` keeps the raw single-line form.

---

## 2. Is the worker accepting them? — worker logs

The subscriber lives in `tpdev-worker` (or `tpprod-worker`). Tail it and
filter for the ingestion service:

```bash
scripts/azd-logs.sh dev worker --since 15m \
  | grep -E "Rejecting v2 wm|Tag read ingested|Skipping (tag-read|message)|MQTT subscriber"
```

For a longer window (`--since` is row-count based and tops out around
`--tail 300`), query Log Analytics directly:

```bash
WS=$(az containerapp env show -n tpdev-env -g tagpulse-dev-rg \
       --query properties.appLogsConfiguration.logAnalyticsConfiguration.customerId -o tsv)

az monitor log-analytics query -w "$WS" --analytics-query "
  ContainerAppConsoleLogs_CL
  | where TimeGenerated > ago(24h)
  | where ContainerName_s == 'tpdev-worker'
  | where Log_s has_any ('Rejecting v2 wm','Tag read ingested','Skipping tag-read','Skipping message')
  | extend msg = extract('\"message\": \"([^\"]+)\"', 1, Log_s)
  | project TimeGenerated, msg
  | order by TimeGenerated desc
  | take 100
" -o tsv
```

| Log line | Meaning | Action |
|---|---|---|
| `Rejecting v2 wm message … reason=invalid_schema` | publisher wire-format bug | see [wm-wire-format-v2.md](wm-wire-format-v2.md); inspect `loc=` for the offending field. Common: `sn` sent as a UUID instead of an integer |
| `Rejecting v2 wm … reason=invalid_epc` / `epcs_wrong_type` | per-spec §6 violation | edge code or firmware bug |
| `Skipping tag-read with invalid JSON …` | malformed payload (legacy v1 path) | tap the device, look at raw bytes |
| `Skipping message with unparseable topic …` | publisher used a non-conforming topic | fix the edge topic |
| `Tag read ingested: device=… tag=… ts=…` | row was written to `tag_reads` | go to §4 to confirm |
| Nothing matching for that device | subscriber didn't see it (despite §1) | go to §3 |

The metrics counterpart of this table:

- `tagpulse_mqtt_messages_rejected_total{reason}` — every reject reason
  has a label here. Sustained spikes ≠ broker outage; it's a device bug.
- `tagpulse_mqtt_messages_processed_total` — should track §1 traffic.

---

## 3. Subscriber sees the broker but not this device

If §1 shows frames and §2 shows nothing for that device id:

1. Check the active subscription filters in the worker startup log:
   ```
   MQTT subscribed to tenants/+/devices/+/+, tenants/+/subjects/+/+/telemetry
   ```
   If those lines are missing, the subscriber crashed mid-startup —
   restart the revision (see §4 of [mqtt-outage.md](mqtt-outage.md)).
2. Confirm the topic the device is publishing to matches one of the
   filters. The fourth segment must be `tag-reads` (or one of the
   subject telemetry kinds); anything else is silently filtered out by
   the broker subscription, not by the worker.
3. Confirm the device is registered in the **expected tenant** with
   `scripts/azd-job.sh dev check_devices_online.py` — a publisher
   authenticated as tenant A cannot publish under tenant B's path
   (broker ACL rejects), and the rejection is broker-side, not in our
   logs.

---

## 4. Did it land in the DB? — `tag_reads_near.py`

```bash
# Last 5 min for one reader (window in seconds)
scripts/azd-job.sh dev tag_reads_near.py -- \
  --device-id 889bd6fc-2bd3-4936-b0e2-fddfbd9fe5dc \
  --before 300 --after 0 --limit 50

# Around a specific incident time
scripts/azd-job.sh dev tag_reads_near.py -- \
  --at 2026-06-18T03:50:00Z --before 120 --after 120 --order desc

# Most recent read for a reader (default --at = now, no upper bound)
scripts/azd-job.sh dev tag_reads_near.py -- \
  --device-id 889bd6fc-… --before 3600 --after 0 --limit 1
```

`--at` requires a tz suffix (`Z` or `±HH:MM`). The script prints one
JSON row per match plus a stderr summary (count, earliest, latest,
distinct devices/antennas). It is strictly observational.

| Result | Interpretation | Next |
|---|---|---|
| Rows present, recent | ingestion fine; problem is upstream of the DB query | go to §5 |
| Rows present but stale | ingestion stopped at time T; cross-reference §2 logs at T | likely a publisher/edge regression at T |
| No rows | worker logged "Tag read ingested" but the row isn't there | DB write path bug — restart the worker, capture an exception trace via `scripts/azd-logs.sh dev worker --follow` |

---

## 5. Rows are in the DB but UI shows nothing

If §4 has rows but the operator still doesn't see them, the issue is not
ingestion. Quick checks:

- The user's tenant matches the row's tenant (tenant scoping).
- The user's role grants `tag_reads:read` (RBAC).
- The UI's query window covers the row's `timestamp` (default
  Tag Reads view is "last 1 h" — easy to miss old reads).
- The Tag Reads column config isn't hiding the data — see
  [docs/user-guide.md](../user-guide.md) and `seed_ui_config.py`.
- Log Analytics shows `200`s (not `403`/`5xx`) on `GET /tag-reads`
  for that user.

If those all check out, escalate as an API/UI bug, not an ingestion
incident.

---

## Supporting commands

```bash
# Synthetic publish → DB round-trip (proves the whole pipeline).
# A failing canary doesn't isolate which hop is broken — see
# mqtt-outage.md §1 for the canary semantics.
scripts/azd-job.sh dev mqtt_canary.py -- \
  --tenant-id <uuid> --device-id <uuid> --timeout-seconds 30

# Confirm device-registry online status against the dashboard's window.
scripts/azd-job.sh dev check_devices_online.py

# Recover from a dropped terminal — re-tail the *last* run, no restart.
scripts/azd-job.sh dev mqtt_tap.py --update-only
```

## `azd-job.sh` gotchas

- The job runs the **deployed** image. Local edits to `scripts/*.py`
  won't take effect until the tools-job image is refreshed; the script
  refuses with exit `5` when the image is stale relative to the deployed
  api app. Pass `--allow-stale` only when you knowingly want the older
  image.
- The script also refuses on a dirty working tree or unpushed commits
  (exit `1`). Same reasoning. `--allow-stale` overrides.
- Exit `3` = job didn't reach Succeeded within 30 minutes. Re-tail with
  `--update-only` instead of re-running — that recovers a dropped
  terminal without restarting the job.
- Exit `4` = job ran but Failed/Stopped. Read the tail it printed; if it
  was truncated, `scripts/azd-logs.sh dev tools` shows the last
  execution.
