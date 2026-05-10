# TagPulse Service Level Objectives

**Sprint introduced:** 28 (D1)
**Owner:** platform team
**Review cadence:** quarterly, or after any SEV-1 incident
**Companion docs:**
- [observability KQL + workbook](../../ops/azure-monitor/) (Sprint 28 D3)
- [runbooks/](../runbooks/) — every red SLO has a runbook
- [azure-architecture.md](../azure-architecture.md) — what each SLO measures

---

## Why these four

The platform has dozens of plausible signals (DB latency, KV secret
freshness, dead-letter rate, …). We picked the smallest set that
**every customer-visible failure mode flows through**:

| SLO                          | Failure it catches                                        | Owner action                                  |
| ---------------------------- | --------------------------------------------------------- | --------------------------------------------- |
| API availability             | A revision is up but returning 5xx; gateway/DNS/TLS down  | Roll back revision, check ACA logs            |
| API p95 latency              | DB lock storm, cold-start regression, leaked connections  | Profile + scale, run `make doctor`            |
| Ingestion freshness          | Broker stalled, subscriber wedged, schema-rejection flood | [`mqtt-outage.md`](../runbooks/mqtt-outage.md) |
| Dead-letter burn rate        | Bug in a handler, malformed external feed                 | [`dead-letter-triage.md`](../runbooks/dead-letter-triage.md) (Sprint 28 E3) |

Anything else operators care about (KV expiry, cert expiry, migration
drift) is covered by `make doctor` (Sprint 28 F4) — those are
**prerequisites**, not SLOs, because failing them doesn't immediately
break customer requests.

## SLO definitions

All windows are **rolling 28-day**, evaluated every 5 minutes via the
Azure Monitor workbook (Sprint 28 D3). Errors counted via
Application Insights `requests` and `customMetrics`.

### 1. API availability

> **99.5%** of HTTPS requests to `/api/v1/*` (excluding `/health/*`)
> return a status &lt; 500 over a rolling 28-day window.

- **Numerator:** `requests | where success == true and resultCode < 500`
- **Denominator:** `requests | where url has "/api/v1" and url !startswith "/health"`
- **Error budget:** 0.5% × (28d × 24h × 60m × ~10 RPM) ≈ **2,016 requests / 28 days**.
- **Burn-rate alerts (Sprint 28 D2):**
  - Fast: 14.4× burn over 1h → page on-call.
  - Slow: 6× burn over 6h → ticket.
- **Excludes:** `/health/*` (probes don't count against customer SLO),
  `/metrics`, `/docs`, `/openapi.json`.
- **Exemptions:** scheduled maintenance windows announced ≥ 24h ahead
  via the status page may be subtracted from the denominator.

### 2. API p95 latency

> The p95 of `/api/v1/tag-reads` and `/api/v1/telemetry/readings/ingest`
> request duration is &lt; **500 ms** measured over a rolling 28-day
> window.

- Why those two routes: ingestion is the only path where latency
  directly translates to backpressure on the broker / device fleet.
  Read-side latency is bounded by the SPA's loading-skeleton UX.
- **Source:** `requests | where url has_any ("tag-reads", "telemetry/readings/ingest") | summarize percentile(duration, 95) by bin(timestamp, 5m)`.
- **Burn-rate alerts:** sustained breach for 30 min → ticket.
- **Excludes:** the first 60s after any container revision restart
  (cold-start / connection-pool warm-up).

### 3. Ingestion freshness

> `tagpulse_mqtt_subscriber_last_message_age_seconds` is &lt; **300s**
> at least 99% of the 5-minute evaluation buckets over a rolling
> 28-day window.

- Requires a synthetic publisher in non-`dev` envs — we run
  `scripts/mqtt_canary.py` on a 5-minute schedule (Sprint 28 D2).
- **Source:** the OTel observable gauge added in Sprint 28 C1.
- **Why 300s:** matches the broker's keepalive (60s) × 5, so a single
  reconnect cycle never trips the alert.
- **Burn-rate alerts:** any single bucket > 600s for 5+ consecutive
  minutes → page on-call (`mqtt-outage.md`).

### 4. Dead-letter burn rate

> The hourly count of `dead_letter_events` rows (across all `source`
> values from Sprint 28 C3) is **less than 50** at p99 over a rolling
> 28-day window.

- **Source:** `tagpulse_dead_letter_events_total` counter (already
  emitted from `AsyncEventBus`) cross-checked with a SQL fallback
  `SELECT count(*) FROM dead_letter_events WHERE failed_at > now() - interval '1 hour'`
  for the workbook.
- **Per-source thresholds (in workbook, not SLO):**
  - `event_bus`: any non-zero value indicates a handler bug → triage.
  - `tag_read_rejected`: bursts are normal (clock-skew) — alert if
    sustained > 100/hour.
  - `mqtt_subscriber`: any sustained value indicates a
    misbehaving device → triage.
- **Burn-rate alerts:** > 200 rows / hour for 1h → page on-call;
  > 50 rows / hour for 6h → ticket.

## Error-budget policy

When any SLO has burned > 50% of its 28-day budget:

1. **Freeze new feature deploys** to the affected component until the
   budget recovers to > 75%.
2. Open a tracking issue with the `slo-budget` label.
3. Allocate ≥ 30% of the next sprint's capacity to reliability work
   on the failing component (this is what Sprint 28 itself is — the
   Sprint 27 retro flagged budget burn on freshness + secret-rotation
   readiness).

## What we deliberately did NOT make an SLO

- **Database latency.** It's a cause, not an effect. Captured by p95
  and the doctor's PG-state check.
- **Secret freshness.** A failed rotation degrades us hours later, not
  in real time. Owned by `secret-rotation.md` + KV expiry sweep.
- **Container restart count.** ACA restarts cleanly; what matters is
  the request-side impact, already in availability + p95.
- **Webhook delivery success.** Customer-configured destinations have
  their own reliability — we report failures via the dispatcher's
  metrics but don't own the upstream's uptime.

## Reviewing this doc

- After every SEV-1 incident: did the alert chain catch it? If not,
  what would have? Add the missing signal as an alert (not an SLO)
  unless the failure is end-to-end customer-visible.
- Every quarter: pull last-quarter burn-rate from the workbook; if a
  single SLO is consistently green with > 95% budget left, consider
  tightening; if consistently red, raise the question of whether the
  underlying system can meet it (and what the cost is).
