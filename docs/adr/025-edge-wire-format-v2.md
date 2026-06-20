# ADR-025: Edge wire format v2 — JSON-over-MQTT with snap-based presence

- Status: **Amended** (Sprint 67, Jun 2026 — see [Amendment 1](#amendment-1--wm-compact-dialect-v2-sprint-67)). Previously **Accepted** (Sprint 46, May 2026) — ratified after Phases A–F shipped on the `sprint-46/edge-wire-format-v2-backend` branch (commits `3e0d124` Phase B, `2d63dd9` Phase C, `cc7d09b` Phase D, `a47c91d` Phase E + F). Originally **Proposed** in Sprint 46 (May 2026). Producer side ships in Sprint 47.
- Implements: the JSON schema and behaviours defined in
  [docs/design/edge-wire-format-v2.md](../design/edge-wire-format-v2.md).
- Related: ADR [002 MQTT for device connectivity](002-mqtt-device-connectivity.md)
  (topic taxonomy), ADR [012 mTLS for MQTT](012-mtls-for-mqtt.md) (transport
  security), ADR [013 Subject-scoped telemetry](013-telemetry-subject-scoping.md)
  + ADR [014 Multi-subject telemetry ingest](014-telemetry-multi-subject-ingest.md)
  (telemetry envelope), ADR [026 Server-side presence model](026-presence-model.md)
  (consumes v2 messages).

## Context

The v1 wire format predates the platform's "producer-agnostic" framing
(spec §1.5). It assumes every producer is a Pi-class gateway with
unlimited bandwidth and storage, and emits one MQTT message per tag read
with no notion of presence — every downstream consumer that wants "what's
in the field right now?" must reconstruct it from a sliding window of
`tag_reads`.

Two production realities forced a redesign:

1. **Reader-direct producers** (RFID readers emitting MQTT without a Pi
   gateway, e.g. the WM SKU in the current pilot) have constrained
   bandwidth (cellular) and constrained CPU (no SQLite buffer, no
   Python). The v1 firehose pattern doesn't fit.
2. **Presence-based downstream rules** (`signaling.<event_type>.on_appearance`
   / `on_disappearance`, planned for Sprint 47+) need a reliable
   "this EPC is here / not here right now" signal that survives
   producer reboots, subscriber restarts, and intermittent links.
   Reconstructing this from `tag_reads` windows is brittle and
   per-query expensive.

An initial draft (committed 2026-05-20) modelled snapshots as N messages
glued together by a per-cycle `seq` counter, with a server-side window
buffer that closed on either the next `seq` or a 10 s timeout. Informal
review with the WM reader developers (June 2026) flagged this as
gratuitously complex: their producer derives deltas by diffing
successive CSV exports, has no internal cycle counter, and gains nothing
from exposing one on the wire. The KISS pass that followed (committed
2026-05-23) collapsed the snap into a single MQTT message carrying an
`epcs[]` array and dropped `seq` entirely.

## Decision

1. **Adopt the wire format specified in
   [docs/design/edge-wire-format-v2.md](../design/edge-wire-format-v2.md)
   as the v2 contract.** That document is the source of truth for field
   names, message types, semantics, error handling, and the
   server-side mapping table. This ADR captures the high-order
   decisions and rationale; consult the spec for byte-level detail.

2. **Three message types over one topic, distinguished by `t`:**
   - `t=0` snap — single message, full EPC set in `epcs[]` (possibly
     empty). Authoritative declaration of "what's at this producer
     right now."
   - `t=1` appeared — one EPC just entered the field. Standalone, no
     dependency on any other message.
   - `t=2` disappeared — one EPC just left the field. Standalone.

   The snap is the recovery primitive; deltas are bandwidth-efficient
   updates between snaps.

3. **No on-the-wire sequence counter.** Producer reboots, cycle
   tracking, and replay detection are producer-internal concerns. The
   server treats every new MQTT session as an opportunity for the
   producer to send a snap (per §3.3 trigger 3); this re-syncs state
   without needing a counter to compare. See spec §3.6 and §8.3 for the
   reasoning.

4. **Empty cycle is signalled by `"epcs":[]`, not a boolean flag.** An
   empty array is structurally unambiguous; a missing array is a
   protocol error (§6). Earlier `"empty":true` flag dropped before
   any code shipped.

5. **MQTT-level atomicity replaces application-level grouping.** A
   single MQTT message published at QoS 1 is the atomic unit. Producers
   that cannot fit a snap in one ~10 KB message must adopt the
   per-cycle profile (spec §3.8 Profile B) — emit a snap every cycle as
   the only message type. No multi-message snap shape exists in v2.

6. **Topic versioning is reserved for future major changes.** v2 ships
   on the existing topic `devices/{tenant_id}/{device_id}/tag-reads`,
   implicitly. A future v3 (e.g., binary protocol) would publish on a
   versioned suffix (`tag-reads-v3` or similar) so v2 and v3 producers
   can coexist on the same broker. Payload-level `v` field is kept on
   the reserved-names list as cheap insurance but is not used in v2.

7. **The producer is unspecified.** The same wire contract applies
   whether the producer is a Pi-class gateway translating from a CSV
   stream, or RFID firmware emitting directly. How the producer
   detects transitions internally (CSV diff, event callback, polling)
   is out of scope. The spec defines *what* gets sent and *when*; it
   does not dictate *how*.

## Consequences

**Positive:**

- One contract serves both Pi-gateway and reader-direct producers; no
  per-SKU dialect.
- Snap-on-reconnect plus single-message atomicity means subscriber
  state is fully recoverable within one snap cadence (default 300 s)
  with no per-producer state on the server. Restart safety drops out
  for free.
- Bandwidth savings are real on cellular: §2.3 estimates a >10×
  reduction vs v1 firehose for typical workloads.
- The presence-based rule family (`on_appearance` /
  `on_disappearance`) gains a direct, unambiguous input — no
  windowed reconstruction.

**Negative / costs:**

- v1 and v2 must coexist on the same topic during cutover (no payload
  discriminator). The subscriber distinguishes by message shape (v2
  has a `t` field). Acceptable because v1's payload shape is fixed
  and we control the rollout cadence.
- Snap loss is silent. If a snap is lost (broker outage longer than
  QoS 1 persistence, producer crash mid-publish), the server runs on
  stale presence until the next snap arrives — bounded by snap
  cadence. Acceptable for the target use case (warehouse, yard); not
  acceptable for safety-critical applications, which would need ADR
  amendment.
- The CSV-diff implementation that WM is likely to adopt cannot
  detect a dropped CSV cycle on its own, and v2 has no mechanism to
  surface that gap to the server. Self-heals at next snap. See
  §9.2 #2 / §9.2 #3 for the precise failure modes.

## Non-goals

- **Binary wire format.** Deferred to v3, gated on measured
  bandwidth justifying the cost (§9.3).
- **Server-to-producer config push.** Snap cadence, RSSI floor,
  antenna mask, etc. are producer-side config in v2.0. v2.1 will add
  a separate `devices/{tenant_id}/{device_id}/config` topic
  (§9.3 #1).
- **Heartbeat (`t=3`) and reader-error (`t=4`) message types.** v2.1
  (§9.3 #2).
- **Cross-reader presence consolidation.** `tag_presence` is
  per-producer; "where is EPC X across the whole fleet right now?"
  requires a separate roll-up view, backlogged (§9.3 #4).

## Open questions

All v2-spec-internal questions are resolved (§8.1 / §8.2) or removed
(§8.3 KISS pass). The remaining open items in spec §8.4 (Q-LAN-1..7)
concern the **reader-to-Pi LAN-side contract** between WM and
TagPulse-as-integrator, not the cellular MQTT wire that this ADR
covers. They will be tracked in a companion document
(`docs/design/reader-to-edge-contract.md`, not yet drafted) and do not
block ratification of this ADR.

## Decision history

- v1 (this version): adopt the no-`seq`, single-message-snap shape
  defined in spec §3 as of commit `155f1e5`.
- v2 (Sprint 67): [Amendment 1](#amendment-1--wm-compact-dialect-v2-sprint-67) — add the opt-in WM compact dialect (`v:2`).

---

## Amendment 1 — WM compact dialect (`v:2`) (Sprint 67)

**Status:** Accepted, Jun 2026. **Spec:** [docs/design/edge-wire-format-v2.md §12](../design/edge-wire-format-v2.md). **Supersedes:** Decision item 6 ("payload-level `v` ... not used in v2") — `v` is now load-bearing; and partially qualifies the "no per-SKU dialect" positive consequence.

### Context

WM, the platform's sole edge producer in pilot today, prototyped a more compact `epcs[]` encoding and measured a **~35 % per-message reduction** by replacing keyed per-EPC objects with fixed-position tuples. They also surfaced four firmware realities that diverge from the ratified v2.0 wire: (a) their reader keys on a **provisioning UUID**, not a numeric serial; (b) firmware emits **ISO-8601 wall-clock** timestamps; (c) they carry a **firmware/SW version** (`fw`) per message; (d) their reader is **single-antenna-per-message**, so a per-message `ant` is more natural than per-entry `an`. They additionally requested that **add (`t=1`) and delete (`t=2`) carry a list of EPCs** (reader envelope + EPC list), symmetric with the snap, rather than one-EPC-per-message.

### Decision

1. **Introduce a payload-level dialect selector, the reserved `v` field.** `v` absent → v2.0 keyed format (unchanged). `v==2` → WM compact dialect (spec §12). `v` present but `!=2` → reject (`unknown_wire_version`). This activates the `v` field that decision item 6 reserved-but-shelved. The discriminator order is **`v` (dialect) then `t` (message type)**.

2. **The dialect is purely additive and opt-in.** All §2–§6 byte shapes, the v2.0 conformance fixtures, and the Pi-gateway reference producer are untouched. Only messages that explicitly set `v:2` take the new path.

3. **Accept WM's proposed shape verbatim** (spec §12.2–§12.3): a **single uniform 5-tuple** `[epc, rssi, cnt, tmp, hum]` for **all three** message types — WM ships one serializer for snap/add/delete. On delete the reading slots are `null` or `0` and are ignored (only `epc` is used); the parser accepts either placeholder (one `[CONFIRM WM]` detail tracked in spec §12.3). Plus envelope `ant`, `fw`, string `sn`, ISO-8601 `ts`, and float `rssi`. Reading slots are not range-checked in `v:2` (WM-authoritative).

   **Forward-compat hardening (post-merge, Sprint 67 follow-up):** `fw` is treated as an **opaque version token accepting string *or* number** — float can't order versions (`1.10` collapses to `1.1`; as floats `1.10 < 1.9`), so WM is advised to move to a semver string, and the parser tolerates the migration with no wire break. The positional tuple parser is **append-tolerant** (length ≥ 5; trailing slots reserved/ignored) so a future field such as the `[NEEDS WM]` peak-RSSI `rpk` can be added without a coordinated break. Both are non-load-bearing-metadata relaxations; neither changes semantics.

4. **Knowingly accept the two choices that cost bandwidth** — string `sn` (~36 B UUID) and ISO-8601 `ts` (~20 B) — because WM's current SKU cannot emit a numeric serial or epoch-ms, and WM is the only edge producer today. Recorded as a deliberate concession (spec §12.7) so a future numeric-`sn`/epoch-`ts` SKU can tighten them without re-opening the design. Net payload is still well below v2.0 keyed.

5. **Semantics are unchanged.** §3 cycle/diff/snap model, §4.1 presence, §4.2 reconciliation, and §4.6 telemetry fan-out apply identically — `v:2` lowers to the same `TagReadCreate` / `tag_presence` rows. Only deserialization differs.

### Consequences

**Positive:**
- WM unblocked on their measured bandwidth win without weakening the ratified format.
- The `v` switch makes future dialects (or a true v2.2) cheap and backward-safe; v2.0 fixtures pin the no-`v` path forever.
- add/delete symmetry (batched EPC lists) matches how WM's firmware actually diffs cycles.

**Negative / costs:**
- **A per-SKU dialect now exists** — the original "one contract, no per-SKU dialect" positive is qualified. Justified while the producer population is N=1; the negotiation switch keeps the blast radius contained.
- **Multi-antenna observation is unrepresentable in `v:2`** (single envelope `ant`). A future multi-antenna WM reader must use v2.0 keyed entries or a `v:2` revision.
- **String `sn` / ISO `ts` partially walk back the §2.3 bandwidth goal** — accepted and quantified (spec §12.7).
- Two parse paths to maintain in `wm_wire_format.py` + the subscriber; mitigated by sharing the §6 DLQ reason vocabulary and the downstream mapping.
