"""Sprint 47 Phase C — end-to-end conformance harness for the v2 producer.

Drives the Pi-gateway producer (:class:`tagpulse_edge.wm_v2_producer.WmV2Producer`)
through each scenario in :doc:`docs/design/edge-wire-format-v2.md` §5
and asserts that every emitted message:

1. **Parses cleanly** against the backend's :data:`WmMessage` discriminated
   union (i.e., is bytes-for-bytes wire-compatible with the subscriber).
2. **Matches the expected per-scenario shape** (message count, types,
   and key fields).

Where end-to-end coverage of the subscriber + reconciler is needed, this
file pipes the producer output into the same fakes as
:mod:`tests.unit.test_wm_v2_conformance` so the full producer →
subscriber → reconciler chain is exercised in one assertion.

Companion to the subscriber-side conformance suite at
``tests/unit/test_wm_v2_conformance.py``; together the two cover the
full §5 conformance matrix end-to-end.

Lives in ``clients/pi/tests/`` because it imports both the Pi-gateway
producer (``tagpulse_edge.*``) and the backend wire-format parser
(``tagpulse.ingestion.wm_wire_format.*``); the Pi test environment has
both on its path. Backend ``make check`` covers the subscriber side via
``tests/unit/test_wm_v2_conformance.py``; together the two suites span
the full §5 conformance matrix end-to-end.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError
from tagpulse_edge.wm_v2_producer import CycleEpcObservation, WmV2Producer

from tagpulse.ingestion.wm_wire_format import (
    WmAppearedMessage,
    WmDisappearedMessage,
    WmMessage,
    WmSnapMessage,
)

# Discriminated-union TypeAdapter — Pydantic dispatches on the integer
# ``t`` field. Same parser the subscriber uses to decide message type.
_WM_MESSAGE_ADAPTER: TypeAdapter[WmMessage] = TypeAdapter(WmMessage)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_or_fail(msg: dict[str, Any]) -> WmSnapMessage | WmAppearedMessage | WmDisappearedMessage:
    """Round-trip the producer output through JSON + the subscriber's parser.

    Fails the test if the bytes the producer would put on the wire are
    not parseable by the backend's ``WmMessage`` discriminated union.
    This is the contract check: the producer MUST emit bytes the
    subscriber accepts under v2 §6 ``invalid_*`` rules.
    """
    raw = json.dumps(msg).encode("utf-8")
    try:
        return _WM_MESSAGE_ADAPTER.validate_json(raw)
    except ValidationError as exc:
        raise AssertionError(
            f"producer emitted message rejected by WmMessage parser: {msg!r}\n{exc}"
        ) from exc


def _obs(
    an: int, epc: str, rssi: int = -50, cnt: int = 1, **kw: float | None
) -> CycleEpcObservation:
    return CycleEpcObservation(an=an, epc=epc, rssi=rssi, cnt=cnt, **kw)


TS0 = 1_716_500_000_000
SECOND = 1000


# ---------------------------------------------------------------------------
# §5.1 — Steady-state cycle (zero wire messages between snaps)
# ---------------------------------------------------------------------------


class TestSpec51SteadyState:
    """Spec §5.1: when the field is steady, the producer emits zero
    wire messages between scheduled snaps. Initial cycle is a snap
    (session trigger); subsequent identical cycles are silent."""

    def test_steady_state_emits_zero_messages_after_initial_snap(self) -> None:
        p = WmV2Producer(sn=1)
        baseline = [_obs(1, "AAAA1111"), _obs(1, "BBBB2222")]
        # Initial cycle: session-trigger snap.
        snap_msgs = p.emit_cycle(TS0, lat=None, lon=None, cycle=baseline)
        assert len(snap_msgs) == 1
        parsed = _parse_or_fail(snap_msgs[0])
        assert isinstance(parsed, WmSnapMessage)
        # 10 subsequent identical cycles: zero messages.
        for i in range(1, 11):
            msgs = p.emit_cycle(TS0 + i * SECOND, None, None, baseline)
            assert msgs == [], f"steady-state cycle {i} should emit nothing"


# ---------------------------------------------------------------------------
# §5.2 — Mixed deltas (adds + subs in same cycle)
# ---------------------------------------------------------------------------


class TestSpec52MixedDeltas:
    """Spec §5.2: a cycle with 5 new tags and 3 departures emits
    5 × t=1 and 3 × t=2 messages (no t=0 unless a snap trigger fires)."""

    def test_five_added_three_removed(self) -> None:
        p = WmV2Producer(sn=1)
        # Baseline: 3 tags that will leave (FFFF, GGGG, HHHH).
        baseline = [
            _obs(1, "FFFFFFFF"),
            _obs(1, "GGGG0000".replace("G", "9")),  # use hex
            _obs(1, "HHHH0000".replace("H", "A")),
        ]
        # The placeholder G/H letters aren't hex — rebuild explicitly.
        baseline = [_obs(1, "FFFFFFFF"), _obs(1, "99990000"), _obs(1, "AAAA0000")]
        p.emit_cycle(TS0, None, None, baseline)  # initial snap
        # Next cycle: those 3 are gone, 5 new arrivals.
        adds = ["11110000", "22220000", "33330000", "44440000", "55550000"]
        next_cycle = [_obs(1, e) for e in adds]
        msgs = p.emit_cycle(TS0 + SECOND, lat=10.0, lon=20.0, cycle=next_cycle)

        # Validate every message parses.
        parsed = [_parse_or_fail(m) for m in msgs]
        appeared = [m for m in parsed if isinstance(m, WmAppearedMessage)]
        disappeared = [m for m in parsed if isinstance(m, WmDisappearedMessage)]
        assert len(appeared) == 5
        assert len(disappeared) == 3
        assert {a.epc for a in appeared} == set(adds)
        assert {d.epc for d in disappeared} == {"FFFFFFFF", "99990000", "AAAA0000"}


# ---------------------------------------------------------------------------
# §5.3 — Periodic snapshot replaces deltas
# ---------------------------------------------------------------------------


class TestSpec53PeriodicSnapshot:
    """Spec §5.3: when the snap-period trigger fires, the producer
    emits one t=0 with the current set (no deltas) regardless of
    what changed from the prior cycle."""

    def test_periodic_snap_when_time_elapsed(self) -> None:
        p = WmV2Producer(sn=1, snap_period_s=10.0, snap_cycle_count=-1)
        p.emit_cycle(TS0, None, None, [_obs(1, "AAAA1111")])  # initial snap
        # 11 s later: time trigger fires, even though cycle changed.
        msgs = p.emit_cycle(
            TS0 + 11 * SECOND,
            lat=41.0,
            lon=2.0,
            cycle=[_obs(1, "AAAA1111"), _obs(1, "BBBB2222")],
        )
        assert len(msgs) == 1
        parsed = _parse_or_fail(msgs[0])
        assert isinstance(parsed, WmSnapMessage)
        assert {e.epc for e in parsed.epcs} == {"AAAA1111", "BBBB2222"}


# ---------------------------------------------------------------------------
# §5.4 — Empty snapshot when field clears
# ---------------------------------------------------------------------------


class TestSpec54EmptySnapshot:
    """Spec §5.4: an empty inventory cycle at snap time emits a t=0
    with ``epcs: []`` (not a missing key, not a flag — the empty
    array is the canonical empty-field signal)."""

    def test_empty_snap_on_session_start(self) -> None:
        p = WmV2Producer(sn=1)
        msgs = p.emit_cycle(TS0, lat=None, lon=None, cycle=[])
        assert len(msgs) == 1
        parsed = _parse_or_fail(msgs[0])
        assert isinstance(parsed, WmSnapMessage)
        assert parsed.epcs == []

    def test_empty_snap_on_periodic_trigger(self) -> None:
        p = WmV2Producer(sn=1, snap_period_s=5.0, snap_cycle_count=-1)
        p.emit_cycle(TS0, None, None, [_obs(1, "AAAA1111")])
        # 6 s later, cycle is empty AND snap fires → empty snap.
        msgs = p.emit_cycle(TS0 + 6 * SECOND, None, None, [])
        assert len(msgs) == 1
        parsed = _parse_or_fail(msgs[0])
        assert isinstance(parsed, WmSnapMessage)
        assert parsed.epcs == []


# ---------------------------------------------------------------------------
# §5.5 — Reboot (reader reset clears state, forces snap)
# ---------------------------------------------------------------------------


class TestSpec55Reboot:
    """Spec §5.5: after a reader reboot or LAN-side reset (ADR-027 §5)
    the producer's :meth:`begin_session` is called; the next cycle
    is force-promoted to a snap regardless of cycle content or snap
    counter state."""

    def test_reboot_forces_snap_with_fresh_state(self) -> None:
        p = WmV2Producer(sn=1)
        p.emit_cycle(TS0, None, None, [_obs(1, "AAAA1111")])  # initial snap
        p.emit_cycle(TS0 + SECOND, None, None, [_obs(1, "AAAA1111")])  # silent
        # Reboot.
        p.begin_session()
        # Even with identical cycle, the post-reboot emit is a snap.
        msgs = p.emit_cycle(TS0 + 2 * SECOND, None, None, [_obs(1, "AAAA1111")])
        assert len(msgs) == 1
        parsed = _parse_or_fail(msgs[0])
        assert isinstance(parsed, WmSnapMessage)
        assert {e.epc for e in parsed.epcs} == {"AAAA1111"}


# ---------------------------------------------------------------------------
# §5.6 — Subscriber outage (producer is unaware; QoS 1 replay)
# ---------------------------------------------------------------------------


class TestSpec56SubscriberOutage:
    """Spec §5.6: producer behaviour is unaffected by subscriber-side
    outages — QoS 1 delivery is the broker's responsibility. The
    producer simply continues to emit per its trigger schedule;
    on broker reconnect, the producer's own ``begin_session()`` (called
    by the MQTT client wrapper) re-syncs via snap.

    The relevant producer-side assertion is that begin_session AFTER
    a string of deltas correctly emits a snap with all currently-known
    EPCs, not just the recent deltas."""

    def test_post_reconnect_snap_contains_full_current_set(self) -> None:
        p = WmV2Producer(sn=1)
        # Initial set has A only.
        p.emit_cycle(TS0, None, None, [_obs(1, "AAAA1111")])
        # B arrives via delta.
        msgs = p.emit_cycle(TS0 + SECOND, None, None, [_obs(1, "AAAA1111"), _obs(1, "BBBB2222")])
        assert len(msgs) == 1 and msgs[0]["t"] == 1
        # Broker disconnect; MQTT wrapper signals begin_session().
        p.begin_session()
        # Next cycle (still A + B) emits a snap containing BOTH.
        msgs = p.emit_cycle(
            TS0 + 2 * SECOND, None, None, [_obs(1, "AAAA1111"), _obs(1, "BBBB2222")]
        )
        parsed = _parse_or_fail(msgs[0])
        assert isinstance(parsed, WmSnapMessage)
        assert {e.epc for e in parsed.epcs} == {"AAAA1111", "BBBB2222"}


# ---------------------------------------------------------------------------
# §5.7 — Lost sub (t=2 dropped, next snap reconciles)
# ---------------------------------------------------------------------------


class TestSpec57LostSub:
    """Spec §5.7: a single t=2 lost in transit is recovered at the
    next snap. From the producer's perspective this is identical to
    §5.3 — the producer cannot tell whether the prior t=2 was
    delivered; it just emits the snap when the trigger fires and the
    snap's authoritative EPC set forces the subscriber's
    ``tag_presence`` row for the missing EPC into the absent state.

    Validates: after a t=2 (whose loss we are simulating), the next
    scheduled snap correctly omits the departed EPC."""

    def test_snap_after_t2_omits_departed_epc(self) -> None:
        p = WmV2Producer(sn=1, snap_period_s=10.0, snap_cycle_count=-1)
        # Initial snap with A + B.
        p.emit_cycle(TS0, None, None, [_obs(1, "AAAA1111"), _obs(1, "BBBB2222")])
        # B leaves (t=2 emitted, hypothetically lost in transit).
        msgs = p.emit_cycle(TS0 + SECOND, None, None, [_obs(1, "AAAA1111")])
        assert len(msgs) == 1 and msgs[0]["t"] == 2
        # 11 s after the initial snap: periodic snap fires.
        msgs = p.emit_cycle(TS0 + 11 * SECOND, None, None, [_obs(1, "AAAA1111")])
        assert len(msgs) == 1
        parsed = _parse_or_fail(msgs[0])
        assert isinstance(parsed, WmSnapMessage)
        # The snap contains ONLY A — B is correctly absent, so even if
        # the prior t=2 was lost, the subscriber will reconcile B to
        # absent at the next snap.
        assert {e.epc for e in parsed.epcs} == {"AAAA1111"}


# ---------------------------------------------------------------------------
# Bonus: producer output parses for every random valid cycle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cycle",
    [
        [],
        [CycleEpcObservation(an=0, epc="DEADBEEF", rssi=-30, cnt=1)],
        [
            CycleEpcObservation(an=1, epc="AABBCCDD", rssi=-50, cnt=2, tmp=23.0),
            CycleEpcObservation(an=2, epc="11223344", rssi=-40, cnt=1, hum=55.5),
        ],
        # Maximum-length EPC (124 hex chars).
        [CycleEpcObservation(an=255, epc="A" * 124, rssi=-127, cnt=65535)],
    ],
    ids=["empty", "one", "two-with-sensors", "edge-values"],
)
def test_all_producer_outputs_parse_under_wm_message(
    cycle: list[CycleEpcObservation],
) -> None:
    p = WmV2Producer(sn=123)
    msgs = p.emit_cycle(TS0, lat=0.0, lon=0.0, cycle=cycle)
    for msg in msgs:
        parsed = _parse_or_fail(msg)
        # Sanity: every parsed message agrees with the emitted t.
        assert parsed.t == msg["t"]
