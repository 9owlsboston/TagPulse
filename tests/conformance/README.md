# TagPulse Edge Conformance Suite

These tests certify that a candidate edge implementation satisfies the
on-the-wire contract documented in
[docs/design/edge-device-contract.md](../../docs/design/edge-device-contract.md)
§3 — they are intentionally **stubs** in this iteration. Sprint 16 ships the
spec + the reference client; the harness binds to a *device under test* in a
later sprint.

## What's here today

- `test_clock.py` — clock window round-trip (§3.5).
- `test_dedup.py` — dedup + ENTER/EXIT semantics (§3.3).
- `test_buffer.py` — offline drain within budget (§3.7).
- `test_heartbeat.py` — LWT + heartbeat cadence (§3.6).

Each test currently asserts only the contract surface: it imports the
contract constants from `tagpulse.ingestion.clock` (§3.5) and confirms they
match the values the spec promises. As soon as a candidate device exposes a
test harness on `localhost`, real round-trip checks land here.

Per [docs/design/edge-device-contract.md §11 Q4](../../docs/design/edge-device-contract.md),
the suite stays in-repo until it grows enough to need an independent release
cadence.
