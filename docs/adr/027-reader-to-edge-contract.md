# ADR-027: Reader-to-Edge LAN-side contract — CSV-over-TCP, Pi owns wall clock

- Status: **Proposed** (Sprint 47, May 2026)
- Implements: the LAN-side contract specified in
  [docs/design/reader-to-edge-contract.md](../design/reader-to-edge-contract.md),
  resolving [edge-wire-format-v2.md §8.4](../design/edge-wire-format-v2.md#84-open--wm-reader-to-edge-lan-side-contract)
  Q-LAN-1..Q-LAN-7.
- Related: ADR [025 Edge wire format v2](025-edge-wire-format-v2.md)
  (the cellular-side wire this contract feeds), ADR [026 Server-side
  presence model](026-presence-model.md) (downstream consumer of v2),
  ADR [011 Device identity roadmap](011-device-identity-roadmap.md)
  (cellular-side trust model; LAN side is physically trusted today),
  ADR [012 mTLS for MQTT](012-mtls-for-mqtt.md) (cellular-side
  transport security; LAN side is plaintext).

## Context

[ADR-025](025-edge-wire-format-v2.md) ratified the **cellular-side**
wire format v2 (Sprint 46) but left the **LAN-side** contract between
the RFID reader hardware and a co-located TagPulse Pi-gateway
unresolved. v2 §8.4 (Q-LAN-1..Q-LAN-7) explicitly deferred that
contract to a follow-up sprint.

This matters because:

1. The Pi-gateway reference implementation (`clients/pi/tagpulse_edge/`)
   needs a concrete reader interface to integrate against for Sprint 47
   Phase B. Without it the producer module can be written in the
   abstract but cannot be tested against any real input shape.
2. Reader-firmware vendors (currently WM, experimentally) need a
   normative spec to target. The current WM sample CSV
   (`https://github.com/weimin-peng/hello-world/blob/main/data.csv`)
   has a header-row typo (`issi` for `rssi`) and ambiguous semantics
   around empty cycles and sensor-read failures — symptoms of an
   un-spec'd interface.
3. The clock-discipline question from v2 §8 Q7 (who NTP-syncs?) cannot
   be deferred indefinitely: low-cost reader SKUs have no RTC, so the
   wall-clock burden falls on the Pi by physical necessity. This
   decision should be ratified, not implicit.

The contract design considered three structural options:

- **A. JSON-over-MQTT on the LAN side** (mirror cellular). Rejected:
  imposes an MQTT broker dependency on the LAN segment, which
  industrial-deployment partners flagged as a non-starter for sites
  that have a reader-and-Pi-only loop with no other LAN
  infrastructure. Also the bandwidth savings over CSV are negligible
  on a LAN.
- **B. Vendor-defined binary protocols, Pi adapts per vendor**.
  Rejected: explodes the per-vendor integration cost; one CSV-over-TCP
  Pi-side parser supports any conforming vendor with no vendor-specific
  code path.
- **C. Normative CSV-over-{TCP|file|serial}, Pi-side parser is
  vendor-agnostic.** **Chosen.** Matches the WM sample's actual shape,
  trivially implementable on any reader (even one whose only output is
  a USB-serial port or a writable file), and keeps the Pi-side parser
  to ~200 lines.

## Decision

1. **Adopt the LAN-side contract specified in
   [docs/design/reader-to-edge-contract.md](../design/reader-to-edge-contract.md)
   as the v0.1 reader-to-edge contract.** That document is the
   source of truth for record shape, transport options, capability
   descriptor, reset semantics, and conformance criteria. This ADR
   captures the high-order decisions and rationale; consult the spec
   for byte-level detail.

2. **CSV-over-line-stream is the only normative record format.**
   Records are CSV per spec §3.1; framing is one record per
   `\n`-delimited line; transport is one of TCP (preferred), file
   watch, or serial / USB-CDC (spec §2). UDP and broadcast transports
   are explicitly excluded.

3. **The Pi-gateway owns the wall clock; the reader emits a monotonic
   counter only.** Spec §3.2. This places NTP discipline on the Pi
   (already a hard requirement under v1 / [edge-device-contract.md
   §3.5](../design/edge-device-contract.md#35-clock-rules)) and lets
   reader-SKU vendors ship without an RTC. Every v2 cellular message's
   `ts` field is the Pi's `datetime.now(UTC)` at cycle-end.

4. **Cycle boundaries are explicit: every cycle MUST be closed by a
   `cycle_end` or `cycle_empty` record.** Spec §3.3, §3.4, §3.6.
   Implicit cycle close (e.g., timeout-based) is rejected; readers
   that cannot signal cycle boundaries cannot be used with v2.

5. **Sensor-read failure is encoded by column omission, never by
   sentinel.** Spec §3.5. Mirrors v2 §2.2's "omit, never `null`" rule
   on the cellular side so the Pi can pass-through without
   translation.

6. **Per-SKU capability descriptor is mandatory** (spec §4). The Pi
   validates inputs against it at startup; a SKU with
   `supports_cycle_empty: false` cannot be deployed under v2 and the
   Pi refuses to start.

7. **Reset is signalled by either an explicit `reset` record or by
   `reader_ts_ms` regression** (spec §5). Either path triggers
   `WmV2Producer.begin_session()` so the next cellular message is a
   snap (v2 §3.3 trigger 3).

8. **The LAN side is physically trusted** (Pi-and-reader on an
   isolated LAN segment / direct serial / shared filesystem). No
   shared secret, no TLS, no authentication on the LAN today. If a
   future deployment shares LAN infrastructure with untrusted hosts,
   spec v0.2 adds a shared-secret HMAC; that is out of scope for
   Sprint 47.

## Consequences

### Positive

- **Vendor lock-in eliminated.** Any reader satisfying spec §3 +
  §4 + §5 works with the Pi-gateway. WM is the first vendor; future
  SKUs follow the same contract.
- **Pi-side complexity stays bounded.** One CSV parser + capability
  validator + cycle assembler ≈ 300 LOC. No per-vendor code path.
- **Resolves v2 §8 Q7 cleanly.** Pi owns the wall clock; this is the
  only viable place to put it for the SKU classes we actually expect
  to ship.
- **Forward-compatible** via unknown-`record_kind`-ignored rule
  (spec §3.3) and via the capability descriptor's open shape (vendors
  can add fields).

### Negative

- **LAN security is deferred to v0.2.** Acceptable for current
  deployments (direct cabling / isolated LANs); becomes a liability
  for shared-LAN sites. Tracked as a known gap in spec §8.
- **Two integration surfaces to maintain.** Pi-gateway operators
  must now reason about both the cellular contract (v2) and the LAN
  contract (this) when debugging. Mitigated by Pi-gateway logging
  both inbound LAN records and outbound MQTT messages at DEBUG.
- **`reader_ts_ms` overflow at 2^64 ms** (≈585M years) — practically
  irrelevant, but the rollover-detection rule (spec §5 path 2) would
  briefly mis-fire as a session boundary at the rollover instant.
  Accepted.
- **Sensor-failure-as-omission is fragile to parser bugs.** A
  vendor that emits `,nan,` or `,,` produces records the Pi rejects
  outright (spec §3.5). This is by design — the Pi refuses to guess
  intent — but it raises the bar for vendor firmware testing.

### Risks accepted

- Spec is **v0.1 Proposed.** Real-world driving from WM firmware
  integration may surface ambiguities that force a v0.2 revision
  (e.g., per-tag TID / user-memory carrying, antenna-mux event
  signalling, configuration-ack semantics). Sprint 47 review will
  re-check after the producer is wired end-to-end against the fake-
  reader harness.

## Alternatives considered (extended)

- **Vendor-binary protocol per SKU.** Rejected per Context above.
- **Reader writes directly to MQTT, no Pi at all.** This is v2 Shape 1
  (cellular-direct), already supported. ADR-027 is specifically for
  the Shape 2 / Shape 3 deployments where a Pi is in the loop. The
  two shapes co-exist; the choice is per-deployment, not a fork.
- **gRPC / protobuf on the LAN side.** Rejected: imposes a build-time
  dependency on the reader firmware (protoc, generated stubs); CSV is
  trivially producible by any toolchain that can write bytes.
- **NTP on the reader instead of the Pi.** Rejected: most reader SKUs
  have no NTP client and no RTC. Forcing them to add one excludes the
  cheap-and-cheerful class of readers we want to support.

## Status & rollout

- **Sprint 47, this ADR:** Proposed.
- **Sprint 47 Phase B:** Pi-gateway reference implementation
  (`clients/pi/tagpulse_edge/wm_v2_producer.py`) integrates against
  this contract via a fake reader in `tests/conformance/`.
- **Promotion to Accepted:** after at least one vendor (WM) reviews
  and signs off, and after the Phase B integration validates the
  contract end-to-end. Target: Sprint 48 or as part of the v2 GA
  promotion.
- **Future spec versions:** v0.2 may add LAN-side authentication,
  per-tag TID/user-memory, and reader-side configuration-ack. Each
  amendment will be a follow-up ADR (027.1, 027.2, …) or a
  superseding ADR if the change is structural.
