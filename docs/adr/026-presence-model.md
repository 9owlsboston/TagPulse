# ADR-026: Server-side presence model — `tag_presence` + synchronous reconciler

- Status: Proposed (Sprint 46, May 2026)
- Implements: the server-side data model and reconciliation algorithm
  defined in [docs/design/edge-wire-format-v2.md](../design/edge-wire-format-v2.md)
  §4.
- Related: ADR [003 TimescaleDB storage](003-timescaledb-storage.md)
  (storage substrate), ADR [010 Internal event bus](010-internal-event-bus.md)
  (`signaling.*` topics), ADR [021 Configurable Sensing Events](021-configurable-sensing-events.md)
  (consumer of `signaling.tag_appeared` / `tag_disappeared`),
  ADR [025 Edge wire format v2](025-edge-wire-format-v2.md) (produces v2
  messages this model consumes).

## Context

ADR 025 establishes that v2 producers send authoritative "what's at this
producer right now?" snapshots (`t=0`) plus incremental transition
deltas (`t=1` / `t=2`). The server now needs a place to store the
current presence state and a deterministic way to update it.

Reconstructing presence on-demand from `tag_reads` was the v1 approach.
It has three problems:

1. **Query cost scales with window size.** "What's at reader R right
   now?" requires scanning `tag_reads` for the past N seconds; the
   choice of N is operator-tunable and gets wrong both ways (too
   short → false `gone`, too long → stale `present`).
2. **No event boundary.** Presence-based rules (`on_appearance` /
   `on_disappearance`, ADR 021 v2) need an *edge-triggered* signal.
   A per-window-scan model can't produce one without state.
3. **Snap semantics have nowhere to land.** v2's snap is a complete
   declaration that "anything I don't list is gone." With no presence
   table, there's nothing to compare the snap against.

A draft model considered buffering snap messages in a per-`(sn, seq)`
window for up to 10 s before reconciling, on the assumption that a snap
arrived as N messages. The KISS pass to ADR 025 collapsed snaps into
single MQTT messages, which removed the need for any windowing or
per-producer in-memory state on the server.

## Decision

1. **Add a `tag_presence` table** (not a hypertable — it's
   small-row-count, frequently-updated current-state). Schema lives in
   [edge-wire-format-v2.md §4.1](../design/edge-wire-format-v2.md#41-data-model).

   ```sql
   CREATE TABLE tag_presence (
       tenant_id        UUID NOT NULL REFERENCES tenants(id),
       device_id        UUID NOT NULL REFERENCES devices(id),
       epc              TEXT NOT NULL,
       first_seen_at    TIMESTAMPTZ NOT NULL,
       last_seen_at     TIMESTAMPTZ NOT NULL,
       last_rssi        REAL,
       last_antenna     SMALLINT,
       last_cycle_count INTEGER,
       PRIMARY KEY (tenant_id, device_id, epc)
   );
   CREATE INDEX ON tag_presence (tenant_id, last_seen_at DESC);
   ```

   No `last_seq` column (no `seq` on the wire). No `suspect` flag
   (no buffered-snap state to be suspicious about).

2. **Reconcile synchronously on snap receipt** in a new module
   `src/tagpulse/ingestion/presence_reconciler.py`. Algorithm in spec
   §4.2 — single transaction per snap: upsert every EPC in `epcs[]`,
   delete every EPC for the same `(tenant, device)` not in the snap,
   emit `signaling.tag_appeared` / `signaling.tag_disappeared` for the
   diff. No window, no buffer, no timer.

3. **Apply deltas immediately.** `t=1` upserts the row and emits
   `signaling.tag_appeared`; `t=2` deletes the row and emits
   `signaling.tag_disappeared`. Idempotent under MQTT-QoS-1
   redelivery: `t=1` for an already-present EPC just bumps
   `last_seen_at`; `t=2` for an already-absent EPC is a no-op.

4. **Single subscriber replica.** The reconciler is not designed for
   concurrent writers against the same `(tenant, device)` partition.
   Worker container app pinned to `minReplicas: 1, maxReplicas: 1`
   in Bicep. Documented in spec §9.2 #1 with the ACA
   rolling-revision-deploy caveat (brief flapping during deploys is
   expected and bounded).

5. **Two new event-bus topics** added to
   `src/tagpulse/events/protocol.py`:
   - `Topic.SIGNALING_TAG_APPEARED`
   - `Topic.SIGNALING_TAG_DISAPPEARED`

   Envelope follows the ADR-010 conventions, keys on
   `(tenant_id, device_id, epc)`, carries `last_rssi` and
   `last_antenna` for the appeared case. These join
   `Topic.SIGNALING_ATTRIBUTION_SETTLED` (Sprint 41) as inputs to the
   future `signaling.<event_type>.on_appearance` /
   `on_disappearance` rule kinds (ADR 021 v2 extension, Sprint 47+,
   not part of this ADR).

6. **`tag_reads` keeps getting written.** Every `t=0` entry, `t=1`,
   and `t=2` produces a `tag_reads` row (spec §4.3) for historical
   queries, attribution replay, and trilateration (ADR 024). The
   `tag_presence` table is purely the live edge; the historical
   record is unchanged.

7. **The two writes are not cross-pool transactional.** `tag_reads`
   lives on a Timescale hypertable, `tag_presence` on a regular
   table; both use the same Postgres pool today, but the design must
   tolerate them eventually living in different pools. Mitigation
   pattern: write `tag_reads` first (durable history), then
   `tag_presence`. A `tag_presence` write failure after a successful
   `tag_reads` write surfaces as a structured warning; the next snap
   reasserts truth. See spec §9.2 #3.

## Consequences

**Positive:**

- "What's at this producer right now?" is a single primary-key lookup.
- Presence-based rules get an edge-triggered input with no
  reconstruction logic.
- No in-memory snap-window state means no deploy-time loss surface, no
  per-producer memory growth, no timeout tuning. The KISS pass to ADR
  025 was what made this possible; we should not regress.
- The model is the same for Pi-gateway and reader-direct producers —
  the reconciler doesn't know or care which it's talking to.

**Negative / costs:**

- `tag_presence` grows with active-EPC cardinality. No retention
  policy applies (it's current-state, not history); operator must
  prune on producer decommission. Spec §9.2 #2 captures this — when
  cardinality drives this past comfort, a TTL job becomes mandatory.
- Single-replica subscriber caps ingest throughput at one worker's
  capacity. Per spec §9.2 #1, sharding would require moving to AKS
  (for stable replica ordinals) or introducing Redis as a presence
  coordinator. Neither is in v2 scope.
- ACA rolling-revision deploys briefly run two reconciler replicas;
  expect transient flapping on every deploy. Self-heals at next snap.
  Operators trained via runbook addendum.

## Non-goals

- **Cross-reader presence consolidation.** This ADR ships
  per-producer presence only. A fleet-wide "where is EPC X right
  now?" view requires a roll-up (different consumers, different
  conflict-resolution rules); backlogged (spec §9.3 #4).
- **Historical presence replay.** `tag_presence` is current-state.
  To reconstruct "what was at reader R at 14:32 last Tuesday",
  query `tag_reads`. No `tag_presence_history` table.
- **Redis-backed reconciler state.** Discussed in spec §9.2 #1 as
  the long-term path to multi-replica sharding; out of scope until
  per-broker volume demands it.

## Open questions

1. Should `tag_presence` rows for a producer be deleted on device
   decommission, or retained as a tombstone for audit? Default
   today: cascade delete via FK on `devices.id`. Operator-facing
   API for "drain this producer's presence without deleting the
   producer" is not in v2 scope.
2. The two-table write ordering (§4.3 / §9.2 #3) is documented but
   not yet enforced by tests. Conformance suite should include a
   fault-injection test that drops the `tag_presence` write after a
   successful `tag_reads` write and verifies next-snap recovery.
3. Compression / retention defaults for the additional `tag_reads`
   volume v2 produces (snap entries become rows). Likely fine under
   existing policies; revisit after one sprint of production data.

## Decision history

- v1 (this version): adopt the synchronous, single-replica
  reconciler design from spec §4 as of commit `155f1e5`. Reflects
  the KISS-pass simplification (no `seq`, no snap window) committed
  the same day.
