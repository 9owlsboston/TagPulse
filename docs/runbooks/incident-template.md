# Runbook: incident-response template

**Owner:** on-call engineer (page-receiver fills this in live)
**Sprint introduced:** 28 (E1)
**Use this for:** any SEV-1 or SEV-2 incident — start a copy in
`docs/runbooks/incidents/<YYYY-MM-DD>-<slug>.md`, link it from the
PagerDuty / OpsGenie incident, and update it as you go.

> **First five minutes.** Don't read past this section before doing
> the three things below. The rest is for the second half of the
> incident.

## ⏱ First five minutes

1. **Acknowledge the page.** Whoever holds the rotation page for this
   service. If you're not on-call, hand off explicitly — silent
   double-acks lose ownership.
2. **Open this file.** Copy `docs/runbooks/incident-template.md` to
   `docs/runbooks/incidents/<YYYY-MM-DD>-<slug>.md` and start
   filling in §"Status updates" as a running log.
3. **Stop the bleed before debugging.** Roll back the most recent
   deploy if it's within 30 min of the alert (`az containerapp
   revision activate --revision <previous>`). Drain a misbehaving
   tenant from rate limits if scoped (`scripts/azd-job.sh production
   set_tenant_rate_limit.py --tenant-id … --rps 0`). Debugging can
   wait; outages can't.

## Status updates (running log)

> Append entries top-down. Timestamps in UTC. Don't edit prior
> entries — strike them through if wrong.

| UTC time | Author | Update |
| -------- | ------ | ------ |
| HH:MM    |        | Page received: alert "<X>" fired on env=<env>. |
| HH:MM    |        | Confirmed via `make doctor ENV=<env>` — <result>. |
| HH:MM    |        | Mitigation applied: <action>.            |
| HH:MM    |        | Recovery confirmed via <signal>.         |

## Severity

| SEV | Trigger                                                      | Action                                          |
| --- | ------------------------------------------------------------ | ----------------------------------------------- |
| 1   | Customer-visible outage (5xx > 5%, ingest stalled > 15 min)  | Page on-call + lead. Status page update.        |
| 2   | Significant degradation (p95 > 2× normal, error budget burn) | Page on-call. Status page if customer-visible.  |
| 3   | Internal-only (background job failing, dashboards broken)    | File ticket. No page.                           |

## Triage commands

Run these in order; stop when you find the broken one.

```bash
# 1. Health snapshot from the operator's box.
make doctor ENV=production

# 2. End-to-end ingestion (broker → subscriber → DB).
scripts/azd-job.sh production mqtt_canary.py

# 3. Tail the api + worker logs.
make logs ENV=production    # the script picks api by default; --kind worker for worker

# 4. SLO burn snapshot in App Insights workbook (Sprint 28 D3).
#    Look at availability + p95 panels — single tenant or platform-wide?
```

## Common failure-mode → runbook map

| Symptom from triage                                       | Runbook                                             |
| --------------------------------------------------------- | --------------------------------------------------- |
| Mosquitto ACI not Running, or canary fails                | [`mqtt-outage.md`](mqtt-outage.md) (Sprint 28 C4)   |
| `make doctor` flags `pg state != Ready`                   | [`db-failover-and-restore.md`](db-failover-and-restore.md) (Sprint 28 E2) |
| `dead_letter_events` rate spike, source = `event_bus`     | [`dead-letter-triage.md`](dead-letter-triage.md) (Sprint 28 E3) — handler bug |
| `dead_letter_events` rate spike, source = `mqtt_subscriber` | [`dead-letter-triage.md`](dead-letter-triage.md) — misbehaving device |
| KV secret expired or auth_failed spike                    | [`secret-rotation.md`](secret-rotation.md)          |
| All 5xx confined to one tenant_id (D5 pivot)              | rate-limit / drain that tenant; not a platform incident |

## Communication

- **Internal:** post in `#tagpulse-incidents` Slack channel within 5
  min of acknowledging. Updates every 15 min, even if "still
  investigating".
- **External (customers):** SEV-1 → status page within 10 min;
  SEV-2 → status page only if customer-visible. The lead approves
  external comms.
- **End of incident:** post resolution in the same channel; mark the
  status-page incident resolved.

## Post-incident

Within **48 hours** of resolution:

1. Schedule a 30-min post-mortem (blameless — focus on systems, not
   people).
2. Fill in §"Timeline" + §"Root cause" + §"Action items" in the
   incident file.
3. Open issues for each action item with the `incident-action`
   label; link them from the post-mortem.
4. Update [`slos.md`](../observability/slos.md) if the alert chain
   missed the failure or fired late.
5. Update this template if any step here was wrong or missing.

## Timeline (fill in post-incident)

| UTC time | Event |
| -------- | ----- |
|          |       |

## Root cause (fill in post-incident)

- **Trigger:** what change or external event started it.
- **Contributing factors:** what made it worse / why didn't earlier
  alerts catch it.
- **Why detection took N minutes:** be honest — too noisy? wrong
  threshold? metric not yet exported?
- **Customer impact:** number of failed requests, tenants affected,
  duration.

## Action items (fill in post-incident)

- [ ] Issue #__ — fix the root cause.
- [ ] Issue #__ — improve detection (add metric / alert / dashboard).
- [ ] Issue #__ — improve response (runbook gap, missing tooling).
