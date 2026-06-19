"""Unit tests for the rssi_weighted_centroid estimator (Sprint 66, Phase 2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tagpulse.services.positioning import (
    AntennaObservation,
    PositionStrategy,
    rssi_weighted_centroid,
)

NOW = datetime(2026, 6, 19, 12, 0, 0, tzinfo=UTC)


def _obs(x: float, y: float, rssi: float, age_s: float, cnt: int = 1) -> AntennaObservation:
    return AntennaObservation(
        antenna_id=uuid4(),
        x=x,
        y=y,
        rssi=rssi,
        cnt=cnt,
        ts=NOW - timedelta(seconds=age_s),
    )


# The three-antenna scenario from docs/design/floor-position-estimation.md.
def _worked_example() -> list[AntennaObservation]:
    return [
        _obs(10.0, 10.0, -55.0, age_s=0.5, cnt=20),
        _obs(40.0, 10.0, -67.0, age_s=6.0, cnt=8),
        _obs(25.0, 35.0, -61.0, age_s=2.0, cnt=12),
    ]


def test_worked_example_time_weighted_matches_design() -> None:
    cfg = PositionStrategy(half_life_s=3.0, rssi_floor_dbm=-127.0)
    fix = rssi_weighted_centroid(_worked_example(), now=NOW, config=cfg)
    assert fix is not None
    # Design doc: (14.5, 16.8).
    assert abs(fix.x - 14.5) < 0.2
    assert abs(fix.y - 16.8) < 0.2
    assert fix.antenna_count == 3


def test_worked_example_time_agnostic_matches_design() -> None:
    # Very large half-life ⇒ decay ≈ 1 ⇒ plain relative-RSSI centroid.
    cfg = PositionStrategy(half_life_s=1.0e9, rssi_floor_dbm=-127.0)
    fix = rssi_weighted_centroid(_worked_example(), now=NOW, config=cfg)
    assert fix is not None
    # Design doc time-agnostic: (16.4, 18.3).
    assert abs(fix.x - 16.4) < 0.2
    assert abs(fix.y - 18.3) < 0.2


def test_tau_zero_is_last_one_wins() -> None:
    cfg = PositionStrategy(half_life_s=0.0, rssi_floor_dbm=-127.0)
    fix = rssi_weighted_centroid(_worked_example(), now=NOW, config=cfg)
    assert fix is not None
    # Freshest antenna is R1 at (10, 10), age 0.5s.
    assert (round(fix.x), round(fix.y)) == (10, 10)
    assert fix.antenna_count == 1


def test_single_antenna_snaps_to_it_with_low_confidence() -> None:
    cfg = PositionStrategy(half_life_s=5.0, rssi_floor_dbm=-127.0)
    fix = rssi_weighted_centroid([_obs(7.0, 8.0, -50.0, age_s=0.0)], now=NOW, config=cfg)
    assert fix is not None
    assert (fix.x, fix.y) == (7.0, 8.0)
    assert fix.confidence <= 0.3


def test_centroid_is_inside_the_convex_hull() -> None:
    cfg = PositionStrategy(half_life_s=5.0, rssi_floor_dbm=-127.0)
    obs = _worked_example()
    fix = rssi_weighted_centroid(obs, now=NOW, config=cfg)
    assert fix is not None
    # A non-negative-weight centroid is a convex combination → within the bbox.
    assert min(o.x for o in obs) <= fix.x <= max(o.x for o in obs)
    assert min(o.y for o in obs) <= fix.y <= max(o.y for o in obs)


def test_rssi_floor_filters_weak_observations() -> None:
    cfg = PositionStrategy(half_life_s=5.0, rssi_floor_dbm=-60.0, min_antennas=1)
    obs = [
        _obs(10.0, 10.0, -55.0, age_s=0.0),  # kept
        _obs(40.0, 10.0, -70.0, age_s=0.0),  # below floor → dropped
    ]
    fix = rssi_weighted_centroid(obs, now=NOW, config=cfg)
    assert fix is not None
    assert (fix.x, fix.y) == (10.0, 10.0)
    assert fix.antenna_count == 1


def test_lookback_drops_stale_observations() -> None:
    cfg = PositionStrategy(half_life_s=5.0, lookback_s=10.0, rssi_floor_dbm=-127.0)
    obs = [
        _obs(10.0, 10.0, -55.0, age_s=1.0),  # fresh, kept
        _obs(40.0, 10.0, -55.0, age_s=30.0),  # older than lookback → dropped
    ]
    fix = rssi_weighted_centroid(obs, now=NOW, config=cfg)
    assert fix is not None
    assert fix.antenna_count == 1
    assert (fix.x, fix.y) == (10.0, 10.0)


def test_min_antennas_gate_returns_none() -> None:
    cfg = PositionStrategy(half_life_s=5.0, min_antennas=3, rssi_floor_dbm=-127.0)
    obs = [_obs(10.0, 10.0, -55.0, age_s=0.0), _obs(40.0, 10.0, -60.0, age_s=0.0)]
    assert rssi_weighted_centroid(obs, now=NOW, config=cfg) is None


def test_empty_observations_returns_none() -> None:
    cfg = PositionStrategy()
    assert rssi_weighted_centroid([], now=NOW, config=cfg) is None


def test_strongest_observation_per_antenna_wins() -> None:
    cfg = PositionStrategy(half_life_s=1.0e9, rssi_floor_dbm=-127.0)
    antenna = uuid4()
    # Two reads on the SAME antenna; the stronger one defines its contribution.
    obs = [
        AntennaObservation(antenna_id=antenna, x=10.0, y=10.0, rssi=-70.0, cnt=1, ts=NOW),
        AntennaObservation(antenna_id=antenna, x=10.0, y=10.0, rssi=-50.0, cnt=1, ts=NOW),
        _obs(40.0, 40.0, -60.0, age_s=0.0),
    ]
    fix = rssi_weighted_centroid(obs, now=NOW, config=cfg)
    assert fix is not None
    # Only two distinct antennas contribute (the dup is collapsed).
    assert fix.antenna_count == 2


def test_default_config_round_trips_from_jsonb_dict() -> None:
    # Mirrors parsing tenants.position_strategy JSONB.
    cfg = PositionStrategy.model_validate(
        {"strategy": "rssi_weighted_centroid", "half_life_s": 2.5, "recompute_interval_s": 1.0}
    )
    assert cfg.half_life_s == 2.5
    assert cfg.recompute_interval_s == 1.0
    assert cfg.min_antennas == 1  # default
