"""Unit tests for ``tagpulse_edge.wm_v2_producer`` (Sprint 47, ADR-025).

Covers v2 wire-format correctness, snap triggers, delta diff, sensor
omission, EPC validation, and reset/begin_session semantics. End-to-end
conformance against the backend subscriber lives in
``tests/conformance/test_wm_v2_end_to_end.py``.
"""

from __future__ import annotations

import pytest
from tagpulse_edge.wm_v2_producer import (
    SNAP_SOFT_CAP_ENTRIES,
    CycleEpcObservation,
    WmV2Producer,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _obs(
    an: int, epc: str, rssi: int = -50, cnt: int = 1, **kwargs: float | None
) -> CycleEpcObservation:
    return CycleEpcObservation(an=an, epc=epc, rssi=rssi, cnt=cnt, **kwargs)


TS0 = 1_716_500_000_000
SECOND = 1000


# ---------------------------------------------------------------------------
# First-emit-is-snap (session trigger, v2 §3.3 trigger 3)
# ---------------------------------------------------------------------------


def test_first_emit_is_always_a_snap_even_empty() -> None:
    p = WmV2Producer(sn=42)
    msgs = p.emit_cycle(TS0, lat=None, lon=None, cycle=[])
    assert len(msgs) == 1
    assert msgs[0]["t"] == 0
    assert msgs[0]["sn"] == 42
    assert msgs[0]["ts"] == TS0
    assert msgs[0]["lat"] is None
    assert msgs[0]["lon"] is None
    assert msgs[0]["epcs"] == []


def test_first_emit_snap_includes_all_entries() -> None:
    p = WmV2Producer(sn=1)
    msgs = p.emit_cycle(
        TS0,
        lat=41.4,
        lon=2.17,
        cycle=[_obs(1, "AABBCCDD"), _obs(2, "EE112233")],
    )
    assert len(msgs) == 1
    snap = msgs[0]
    assert snap["t"] == 0
    assert {e["epc"] for e in snap["epcs"]} == {"AABBCCDD", "EE112233"}
    assert all(set(e.keys()) == {"an", "epc", "rssi", "cnt"} for e in snap["epcs"])


# ---------------------------------------------------------------------------
# Delta cycles (v2 §3.1)
# ---------------------------------------------------------------------------


def test_delta_emits_adds_and_subs() -> None:
    p = WmV2Producer(sn=7)
    # Establish baseline via initial snap.
    p.emit_cycle(TS0, None, None, [_obs(1, "AAAA1111"), _obs(1, "BBBB2222")])
    # Next cycle: AAAA1111 leaves, CCCC3333 arrives.
    msgs = p.emit_cycle(
        TS0 + 5 * SECOND,
        lat=10.0,
        lon=20.0,
        cycle=[_obs(1, "BBBB2222"), _obs(1, "CCCC3333")],
    )
    types = [m["t"] for m in msgs]
    assert sorted(types) == [1, 2]
    add = next(m for m in msgs if m["t"] == 1)
    sub = next(m for m in msgs if m["t"] == 2)
    assert add["epc"] == "CCCC3333"
    assert add["an"] == 1
    assert add["lat"] == 10.0 and add["lon"] == 20.0
    # t=2 minimal payload: only t, sn, ts, epc.
    assert sub["epc"] == "AAAA1111"
    assert set(sub.keys()) == {"t", "sn", "ts", "epc"}


def test_delta_no_change_emits_zero_messages() -> None:
    p = WmV2Producer(sn=1)
    p.emit_cycle(TS0, None, None, [_obs(1, "AAAA1111")])
    msgs = p.emit_cycle(TS0 + SECOND, None, None, [_obs(1, "AAAA1111")])
    assert msgs == []


def test_tag_moves_antennas_emits_add_only_for_new_antenna() -> None:
    p = WmV2Producer(sn=1)
    p.emit_cycle(TS0, None, None, [_obs(1, "AAAA1111")])
    # Now seen on BOTH antennas — t=1 for (an=2, AAAA1111); no t=2.
    msgs = p.emit_cycle(TS0 + SECOND, None, None, [_obs(1, "AAAA1111"), _obs(2, "AAAA1111")])
    assert len(msgs) == 1
    assert msgs[0]["t"] == 1
    assert msgs[0]["an"] == 2
    assert msgs[0]["epc"] == "AAAA1111"


def test_tag_drops_one_antenna_keeps_other_no_messages() -> None:
    p = WmV2Producer(sn=1)
    p.emit_cycle(TS0, None, None, [_obs(1, "AAAA1111"), _obs(2, "AAAA1111")])
    # Lost on antenna 2, still on 1 — EPC still present, no t=2.
    msgs = p.emit_cycle(TS0 + SECOND, None, None, [_obs(1, "AAAA1111")])
    assert msgs == []


def test_tag_leaves_all_antennas_emits_single_t2() -> None:
    p = WmV2Producer(sn=1)
    p.emit_cycle(TS0, None, None, [_obs(1, "AAAA1111"), _obs(2, "AAAA1111")])
    msgs = p.emit_cycle(TS0 + SECOND, None, None, [])
    assert len(msgs) == 1
    assert msgs[0]["t"] == 2
    assert msgs[0]["epc"] == "AAAA1111"


# ---------------------------------------------------------------------------
# Snap triggers (v2 §3.3)
# ---------------------------------------------------------------------------


def test_snap_period_triggers_snap() -> None:
    p = WmV2Producer(sn=1, snap_period_s=10.0, snap_cycle_count=-1)
    p.emit_cycle(TS0, None, None, [_obs(1, "AAAA1111")])
    # 11 s later — past period.
    msgs = p.emit_cycle(TS0 + 11 * SECOND, None, None, [_obs(1, "AAAA1111")])
    assert len(msgs) == 1 and msgs[0]["t"] == 0


def test_snap_cycle_count_triggers_snap() -> None:
    # snap_cycle_count=3 means: after 3 delta cycles, the next is snap.
    # Initial snap + 3 deltas + 1 snap = 5 emit_cycle calls total.
    p = WmV2Producer(sn=1, snap_period_s=-1, snap_cycle_count=3)
    p.emit_cycle(TS0, None, None, [_obs(1, "AAAA1111")])  # snap (initial)
    for i in range(1, 4):
        msgs = p.emit_cycle(TS0 + i * SECOND, None, None, [_obs(1, "AAAA1111")])
        assert all(m["t"] != 0 for m in msgs), f"cycle {i} should be delta"
    msgs = p.emit_cycle(TS0 + 4 * SECOND, None, None, [_obs(1, "AAAA1111")])  # snap
    assert len(msgs) == 1 and msgs[0]["t"] == 0


def test_begin_session_forces_snap_and_clears_state() -> None:
    p = WmV2Producer(sn=1)
    p.emit_cycle(TS0, None, None, [_obs(1, "AAAA1111")])
    p.begin_session()
    # After begin_session(): last_cycle was cleared, so even with the
    # same cycle content, this re-emits a snap (not zero messages, not
    # deltas).
    msgs = p.emit_cycle(TS0 + SECOND, None, None, [_obs(1, "AAAA1111")])
    assert len(msgs) == 1 and msgs[0]["t"] == 0
    assert len(msgs[0]["epcs"]) == 1


def test_disabled_period_and_cycle_count_only_snap_via_begin_session() -> None:
    p = WmV2Producer(sn=1, snap_period_s=-1, snap_cycle_count=-1)
    # Initial snap (session trigger).
    p.emit_cycle(TS0, None, None, [_obs(1, "AAAA1111")])
    # No more snaps until begin_session().
    for i in range(1, 50):
        msgs = p.emit_cycle(TS0 + i * SECOND, None, None, [_obs(1, "AAAA1111")])
        assert all(m["t"] != 0 for m in msgs)


# ---------------------------------------------------------------------------
# Profile B (snap-only)
# ---------------------------------------------------------------------------


def test_profile_b_every_cycle_is_snap() -> None:
    p = WmV2Producer(sn=1, snap_period_s=-1, snap_cycle_count=0)
    for i in range(5):
        msgs = p.emit_cycle(TS0 + i * SECOND, None, None, [_obs(1, "AAAA1111")])
        assert len(msgs) == 1
        assert msgs[0]["t"] == 0


# ---------------------------------------------------------------------------
# Sensor field omission (v2 §2.2, §6 explicit_null)
# ---------------------------------------------------------------------------


def test_sensor_none_omits_keys_on_snap_entry() -> None:
    p = WmV2Producer(sn=1)
    msgs = p.emit_cycle(TS0, None, None, [_obs(1, "AAAA1111", tmp=None, hum=None)])
    entry = msgs[0]["epcs"][0]
    assert "tmp" not in entry
    assert "hum" not in entry


def test_sensor_some_present_keeps_those_keys() -> None:
    p = WmV2Producer(sn=1)
    msgs = p.emit_cycle(TS0, None, None, [_obs(1, "AAAA1111", tmp=23.5, hum=None)])
    entry = msgs[0]["epcs"][0]
    assert entry["tmp"] == 23.5
    assert "hum" not in entry


def test_sensor_omission_on_appeared_message() -> None:
    p = WmV2Producer(sn=1)
    p.emit_cycle(TS0, None, None, [])  # initial snap (empty)
    msgs = p.emit_cycle(TS0 + SECOND, None, None, [_obs(1, "AAAA1111", tmp=None, hum=44.0)])
    assert len(msgs) == 1 and msgs[0]["t"] == 1
    assert "tmp" not in msgs[0]
    assert msgs[0]["hum"] == 44.0


# ---------------------------------------------------------------------------
# Validation: EPC, ranges, dedup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_epc",
    [
        "",  # too short
        "AAA",  # too short and odd
        "AAAA111",  # odd length
        "AAAAGGGG",  # not hex
        "AA" * 70,  # too long (140 > 124)
    ],
)
def test_invalid_epc_rejected(bad_epc: str) -> None:
    p = WmV2Producer(sn=1)
    with pytest.raises(ValueError):
        p.emit_cycle(TS0, None, None, [_obs(1, bad_epc)])


def test_epc_normalized_to_upper() -> None:
    p = WmV2Producer(sn=1)
    msgs = p.emit_cycle(TS0, None, None, [_obs(1, "aabbccdd")])
    assert msgs[0]["epcs"][0]["epc"] == "AABBCCDD"


@pytest.mark.parametrize(
    "field, value",
    [
        ("an", -1),
        ("an", 256),
        ("rssi", 1),
        ("rssi", -200),
        ("cnt", 0),
        ("cnt", 70_000),
    ],
)
def test_out_of_range_field_rejected(field: str, value: int) -> None:
    p = WmV2Producer(sn=1)
    kwargs = {"an": 1, "epc": "AABBCCDD", "rssi": -50, "cnt": 1}
    kwargs[field] = value
    obs = CycleEpcObservation(**kwargs)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        p.emit_cycle(TS0, None, None, [obs])


def test_duplicate_an_epc_in_cycle_rejected() -> None:
    p = WmV2Producer(sn=1)
    with pytest.raises(ValueError, match="duplicate"):
        p.emit_cycle(TS0, None, None, [_obs(1, "AABBCCDD"), _obs(1, "AABBCCDD")])


def test_lat_lon_out_of_range_rejected() -> None:
    p = WmV2Producer(sn=1)
    with pytest.raises(ValueError):
        p.emit_cycle(TS0, lat=91.0, lon=0.0, cycle=[])
    with pytest.raises(ValueError):
        p.emit_cycle(TS0, lat=0.0, lon=181.0, cycle=[])


def test_negative_ts_ms_rejected() -> None:
    p = WmV2Producer(sn=1)
    with pytest.raises(ValueError):
        p.emit_cycle(-1, None, None, [])


# ---------------------------------------------------------------------------
# Wire-shape sanity (extra="forbid" mirror)
# ---------------------------------------------------------------------------


def test_snap_envelope_has_no_top_level_epc_an_rssi_cnt() -> None:
    p = WmV2Producer(sn=1)
    msgs = p.emit_cycle(TS0, None, None, [_obs(1, "AABBCCDD")])
    snap = msgs[0]
    for forbidden in ("epc", "an", "rssi", "cnt", "tmp", "hum"):
        assert forbidden not in snap, f"snap must not carry top-level {forbidden!r}"


def test_appeared_envelope_has_no_epcs_array() -> None:
    p = WmV2Producer(sn=1)
    p.emit_cycle(TS0, None, None, [])
    msgs = p.emit_cycle(TS0 + SECOND, None, None, [_obs(1, "AABBCCDD")])
    assert "epcs" not in msgs[0]


def test_disappeared_envelope_minimal_keys() -> None:
    p = WmV2Producer(sn=1)
    p.emit_cycle(TS0, None, None, [_obs(1, "AABBCCDD")])
    msgs = p.emit_cycle(TS0 + SECOND, None, None, [])
    assert msgs[0] == {"t": 2, "sn": 1, "ts": TS0 + SECOND, "epc": "AABBCCDD"}


# ---------------------------------------------------------------------------
# Soft cap warning
# ---------------------------------------------------------------------------


def test_snap_exceeds_soft_cap_logs_warning_but_emits(caplog: pytest.LogCaptureFixture) -> None:
    p = WmV2Producer(sn=1)
    # Generate SNAP_SOFT_CAP_ENTRIES + 1 unique EPCs at antenna 0.
    cycle = [
        CycleEpcObservation(an=0, epc=f"{i:08X}", rssi=-50, cnt=1)
        for i in range(SNAP_SOFT_CAP_ENTRIES + 1)
    ]
    with caplog.at_level("WARNING", logger="tagpulse_edge.wm_v2_producer"):
        msgs = p.emit_cycle(TS0, None, None, cycle)
    assert len(msgs) == 1 and msgs[0]["t"] == 0
    assert len(msgs[0]["epcs"]) == SNAP_SOFT_CAP_ENTRIES + 1
    assert any("soft_cap" in rec.message for rec in caplog.records)
