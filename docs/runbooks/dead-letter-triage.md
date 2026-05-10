# Runbook: dead-letter triage

**Owner:** on-call engineer
**Sprint introduced:** 28 (E3)
**Companion docs:**
- [SLO catalog](../observability/slos.md) — SLO #4 burn rate.
- [`mqtt-outage.md`](mqtt-outage.md) — broker-side failures.
- [`incident-template.md`](incident-template.md) — escalate to here
  if rate is sustained > 1 hour.

The `dead_letter_events` table is the catch-all for messages our
pipeline could not process. Sprint 28 C3 added a `source` column so
each row tells you who wrote it; this runbook is structured around
that classifier.

```sql
SELECT source, count(*) AS rows, max(failed_at) AS latest
  FROM dead_letter_events
 WHERE failed_at > now() - interval '24 hours'
 GROUP BY source
 ORDER BY rows DESC;
```

## Source: `event_bus`

Written by `AsyncEventBus._persist_dead_letter` when an internal
event handler raises. **This is always a code bug** — handlers are
expected to be total functions over the event payload.

### Triage

```sql
SELECT topic, error_message, count(*)
  FROM dead_letter_events
 WHERE source = 'event_bus'
   AND failed_at > now() - interval '1 hour'
 GROUP BY topic, error_message
 ORDER BY 3 DESC LIMIT 20;
```

| `error_message` pattern              | Likely root cause                          | Action                                    |
| ------------------------------------ | ------------------------------------------ | ----------------------------------------- |
| `KeyError: 'tenant_id'`              | Event published without expected field     | Find the publisher; fix payload contract  |
| `AttributeError: NoneType has no '…'` | Handler dereferenced a missing FK         | Backfill data or add a None-guard         |
| `IntegrityError: …unique violation`  | Idempotency missing in handler             | Make handler use ON CONFLICT or pre-check |
| anything new                         | Read the stack via App Insights span by id | Owner = handler module owner              |

### Replay

If the bug is fixed and the rows are reprocessable:

```bash
scripts/azd-job.sh production replay_dead_letter.py \
  --source event_bus \
  --since '2026-05-09 00:00:00+00' \
  --max-rows 1000
```

The script (TODO: add when needed) re-publishes via the bus with
`status='replayed'` and increments `retry_count`. Don't replay rows
older than 24h without lead approval — the original event semantics
may no longer apply.

## Source: `tag_read_rejected`

Written by `TimescaleTagReadRepository.record_rejection` when a
tag-read fails the clock-window guard
(`settings.ingest_clock_enforce`). **Bursts during device boot or
clock drift are normal.** A sustained > 100/hour rate suggests:

- A misbehaving device fleet (clock-skew on a batch of devices).
- A customer running an integration test that backdates timestamps.

### Triage

```sql
SELECT
  payload->>'device_id' AS device_id,
  count(*) AS rejections
  FROM dead_letter_events
 WHERE source = 'tag_read_rejected'
   AND failed_at > now() - interval '1 hour'
 GROUP BY 1
 ORDER BY 2 DESC LIMIT 20;
```

If a single device dominates, that device has a clock problem — open
a ticket against the customer with the device-id and the
`payload->>'timestamp'` values. Until they fix it, the platform is
behaving correctly by rejecting.

### Disabling for a tenant temporarily

If a customer is blocked and clock-skew is < ~2h:

```bash
scripts/azd-job.sh production set_tenant_setting.py \
  --tenant-id <TID> --key ingest_clock_enforce --value false
# Remember to revert once they fix their clocks.
```

These rows are **not replayable** — the rejection was correct under
policy, replaying would reintroduce bad data.

## Source: `mqtt_subscriber`

Written by `MqttSubscriber._persist_mqtt_drop` when a payload arrives
on a known topic but fails Pydantic validation. Sprint 28 C3 only
persists `invalid_schema` drops (not raw JSON-parse failures, which
would flood under broker spam).

### Triage

```sql
SELECT
  topic,
  error_message,
  payload->>'tag_id' AS tag_id,
  count(*)
  FROM dead_letter_events
 WHERE source = 'mqtt_subscriber'
   AND failed_at > now() - interval '1 hour'
 GROUP BY topic, error_message, payload->>'tag_id'
 ORDER BY 4 DESC LIMIT 20;
```

| `error_message`                          | Likely root cause                            | Action                                                |
| ---------------------------------------- | -------------------------------------------- | ----------------------------------------------------- |
| `mqtt tag_read invalid_schema`           | Edge-firmware regression on a device cohort  | Notify customer; check fleet version distribution     |
| `mqtt status invalid_schema`             | Custom integration sending wrong shape       | Reach out with the broken payload                     |
| `mqtt subject_telemetry invalid_schema`  | New telemetry-model rejected by metrics list | Confirm the model definition matches publisher schema |

### Replay

These are **not auto-replayable** — the schema mismatch means
re-running through the same parser would re-fail. Once the publisher
is fixed, customer should re-publish from their side. If a small,
known-good batch needs salvaging, do a one-off SQL fixup with lead
review.

## Source: `other`

Reserved. If you see this, somebody added a writer without picking
one of the named sources — open a code-review issue. The CHECK
constraint allows it for forward-compat, but production data should
not contain it.

## Hygiene

- The `ix_dead_letter_events_source_failed_at` composite index makes
  the per-source queries here cheap. If they're slow, check the
  index exists (`\d dead_letter_events`).
- Old triaged rows can be archived: nothing in the platform reads
  rows with `status IN ('replayed', 'archived')`. A retention job
  (TODO Sprint 29 backlog) will move > 30d rows to cold storage.
- This runbook should grow with experience — when you triage a new
  pattern, append it to the right source's table above.
