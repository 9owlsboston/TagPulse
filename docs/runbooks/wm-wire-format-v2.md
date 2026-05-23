# Runbook: v2 wire-format presence & operational counters

**Applies to:** Sprint 46 onward.
**Sources:** [`docs/design/edge-wire-format-v2.md`](../design/edge-wire-format-v2.md) (authoritative spec), [`docs/guides/device-developer-guide.md` §3.4](../guides/device-developer-guide.md#34-v2-wire-format--presence-oriented-sprint-46) (producer-facing summary), Sprint 46 PR (Phase A–F).

This runbook is what an on-call operator needs when:

- a dashboard widget asks **"what's at reader X right now?"**
- a customer reports tags that should be present but aren't (or vice versa),
- a Phase E counter spikes and you need to know what it actually means, or
- the subscriber goes through a rolling deploy and presence rows briefly disagree.

The v2 wire format ships on the same `…/tag-reads` topic as v1; both are
supported indefinitely (spec §9.1 #4). All v2 state lives in the new
`tag_presence` table (migration `042_tag_presence.py`).

---

## 1. "What's at reader X right now?"

Authoritative answer is one SQL query against `tag_presence`. Use
`scripts/get_kv_secret.py` or your usual psql wrapper to connect; the
table is RLS-scoped so set the GUC first.

```sql
-- Required: the RLS policy keys off this GUC.
SELECT set_config('app.current_tenant_id',
                  '11111111-1111-1111-1111-111111111111', true);

-- Currently-present EPCs at one device, freshest last_seen first.
SELECT epc,
       last_seen,
       last_rssi,
       last_antenna
FROM   tag_presence
WHERE  tenant_id = '11111111-1111-1111-1111-111111111111'
  AND  device_id = '00000000-0000-0000-0000-000000000002'
  AND  status    = 'present'
ORDER  BY last_seen DESC;
```

Variants you'll want often:

```sql
-- Tags recently gone (left within the last hour).
SELECT epc, last_seen
FROM   tag_presence
WHERE  tenant_id = :tenant
  AND  device_id = :device
  AND  status    = 'gone'
  AND  last_seen > now() - interval '1 hour'
ORDER  BY last_seen DESC;

-- Distinct EPC count per device, present only.
SELECT device_id, count(*) AS present_epcs
FROM   tag_presence
WHERE  tenant_id = :tenant
  AND  status    = 'present'
GROUP  BY device_id
ORDER  BY present_epcs DESC;

-- Has this EPC been seen anywhere in the tenant?
SELECT device_id, status, last_seen
FROM   tag_presence
WHERE  tenant_id = :tenant
  AND  epc       = 'E2801160AAAA1111'
ORDER  BY last_seen DESC;
```

> `tag_presence` is **state**, not history. For "where was this EPC at 14:32"
> queries, use `tag_reads` (snap deltas still land there for `t=0`/`t=1`
> messages). `t=2` disappearances do **not** write to `tag_reads` per
> spec §4.3 — they only transition `tag_presence.status` from `present`
> to `gone`.

---

## 2. Phase E counter cheat-sheet

All counters live under the `tagpulse_` prefix and are emitted via the
shared OTel meter (see [`src/tagpulse/core/otel_metrics.py`](../../src/tagpulse/core/otel_metrics.py)).
On the deployed `dev` stack they roll up to Azure Monitor; locally they
come out via the standard exporter pipeline.

| Metric | What rising means | Operator action |
|---|---|---|
| `tagpulse_mqtt_wm_rejections_total{reason}` | A producer sent malformed v2 messages. `reason` ∈ {`missing_type`, `unknown_type`, `invalid_epc`, `missing_required_field`, `epcs_wrong_type`, `invalid_snap_entry`, `explicit_null`, `invalid_json`, `invalid_schema`}. | Find the device via the matching `mqtt_drops` row (drops are persisted with topic + raw payload); fix firmware. |
| `tagpulse_mqtt_wm_snap_large_total{sn}` | A `t=0` snap exceeded the soft cap (5,000 entries). NOT rejected. | Likely runaway tag population or a misconfigured antenna. Inspect the reader; consider increasing snap cadence or splitting the install. |
| `tagpulse_mqtt_wm_sub_no_presence_total` | The subscriber received a `t=2` (disappeared) for an EPC it has never seen. Single bumps are normal after rolling deploys or reader cold-start; sustained non-zero rate suggests the subscriber lost state (no snap-on-reconnect from producer) or the producer is sending deltas without snaps. | Check the producer's snap cadence (default 300 s or every 100 cycles). Verify `clean_session=false` so the broker queues across reconnects. |
| `tagpulse_presence_reconcile_duration_seconds{t}` (histogram) | Wall-clock time spent in the reconciler. `t` ∈ {`snap`, `appeared`, `disappeared`}. | `snap` p95 should track snap entry count linearly. Sudden growth without entry-count growth = DB latency. |
| `tagpulse_presence_entries_total{status}` | DB write throughput for presence rows. `status` ∈ {`present`, `gone`}. | A useful denominator for the duration histogram. |
| `tagpulse_signaling_tag_appeared_total{source}` | Tag-appeared events fanned out to the internal bus. `source` ∈ {`snap`, `delta`}. | Compare against per-device read rates to confirm the reader is producing deltas, not just snaps. |
| `tagpulse_signaling_tag_disappeared_total{source}` | Tag-disappeared events fanned out to the internal bus. | A persistent zero on a busy reader means the producer never sends `t=2`; the subscriber will only learn about exits when the next snap arrives. |

The full table of rejection reasons + their schema mapping is in
[`docs/design/edge-wire-format-v2.md` §6](../design/edge-wire-format-v2.md).

---

## 3. Rolling-deploy gotcha — two subscriber replicas

Per spec §9.2 #1, during an Azure Container Apps rolling deploy the
subscriber may briefly run with **two replicas** of the new revision
plus a draining old replica (typically 30–60 s). Both can receive the
same MQTT message because the broker fans out QoS 1 to every subscriber
of the topic; the database is the arbiter via `tag_presence`'s primary
key + `on conflict do update`.

**What you may see during the window:**

- Brief spike in `tagpulse_presence_entries_total{status=present}` —
  duplicate upserts, harmless.
- Two `_emit()` publishes for the same `appeared` transition; the event
  bus dedups by `(tenant, device, epc, status)` for a short TTL but a
  duplicate analytics-rule fire is possible.
- `last_rssi` / `last_antenna` may flap between two values if the two
  replicas processed the same message in different orders (TimescaleDB
  upsert is last-writer-wins by transaction commit time).

**When to act:** never, unless the inconsistency persists for **more
than 10 minutes**. Self-heal time bound is **one snap cadence** (300 s
default); the next `t=0` from the producer puts every row back to
authoritative state.

**Diagnosis if it doesn't heal:**

```bash
# Confirm the rollout actually completed.
az containerapp revision list -n tpdev-worker -g tagpulse-dev-rg \
  --query '[].{name:name, active:properties.active, trafficWeight:properties.trafficWeight}' \
  -o table

# Tail the subscriber to confirm only one replica is logging.
make logs ENV=dev SERVICE=worker SINCE=5m | grep -i 'wm_v2\|reconcile_snap'
```

If two revisions are still both active, kill the older one explicitly:

```bash
az containerapp revision deactivate -n tpdev-worker -g tagpulse-dev-rg \
  --revision <old-revision-name>
```

---

## 4. Reproducing a customer-reported "tag stuck present" issue

The most common v2-era support ticket is "the dashboard shows this tag
as present but it left hours ago." Triage path:

```sql
-- 1. Find the row.
SELECT * FROM tag_presence
WHERE tenant_id = :tenant AND epc = :epc;
```

If `status='present'` but `last_seen` is old:

1. **Check producer health.** Did the reader send any `t=0` snap recently?
   ```sql
   SELECT max(received_at) FROM tag_reads
   WHERE tenant_id = :tenant AND device_id = :device;
   ```
   A snap re-asserts every EPC currently in field; without snaps a
   missed `t=2` leaves the row stale forever.

2. **Check rejection counters.** If `tagpulse_mqtt_wm_rejections_total`
   spiked recently, the snaps may be arriving but being dropped. Inspect
   `mqtt_drops`:
   ```sql
   SELECT received_at, reason, payload
   FROM mqtt_drops
   WHERE topic LIKE '%/' || :device || '/tag-reads'
   ORDER BY received_at DESC LIMIT 5;
   ```

3. **Force-heal.** No remote command channel exists yet (Sprint 26+).
   Either wait for the next producer snap or, in an emergency, mark the
   row gone manually:
   ```sql
   UPDATE tag_presence
   SET    status = 'gone', last_seen = now()
   WHERE  tenant_id = :tenant AND device_id = :device AND epc = :epc;
   ```
   Then file a follow-up: the reader either lost its presence cache
   without snap-on-reconnect, or its exit-timeout is misconfigured.

---

## 5. Where to go next

| You want to… | Read this |
|---|---|
| Send your first v2 message | [`docs/guides/device-developer-guide.md` §3.4](../guides/device-developer-guide.md#34-v2-wire-format--presence-oriented-sprint-46) |
| Read the authoritative wire spec | [`docs/design/edge-wire-format-v2.md`](../design/edge-wire-format-v2.md) |
| Inspect the reconciler code | [`src/tagpulse/ingestion/presence_reconciler.py`](../../src/tagpulse/ingestion/presence_reconciler.py) |
| Inspect the v2 dispatch + counter wiring | [`src/tagpulse/ingestion/mqtt_subscriber.py`](../../src/tagpulse/ingestion/mqtt_subscriber.py) (search `_handle_wm_v2_message`) |
| See all OTel counter definitions | [`src/tagpulse/core/otel_metrics.py`](../../src/tagpulse/core/otel_metrics.py) (search "Sprint 46 Phase E") |
