"""Unit tests for scripts/simulate_floor_positioning.py (Sprint 66, Phase 2)."""

from __future__ import annotations

import importlib.util
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "simulate_floor_positioning.py"
_SPEC = importlib.util.spec_from_file_location("simulate_floor_positioning", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
sfp = importlib.util.module_from_spec(_SPEC)
sys.modules["simulate_floor_positioning"] = sfp
_SPEC.loader.exec_module(sfp)

from tagpulse.services.positioning import PositionStrategy  # noqa: E402

NOW = datetime(2026, 6, 19, 12, 0, 0, tzinfo=UTC)


def test_rssi_decreases_monotonically_with_distance() -> None:
    near = sfp.rssi_from_distance(1.0)
    mid = sfp.rssi_from_distance(10.0)
    far = sfp.rssi_from_distance(100.0)
    assert near > mid > far
    assert near == -40.0  # rssi0 at the reference distance
    assert far >= -90.0  # clamped to the floor


def test_nearest_k_returns_k_closest_in_order() -> None:
    readers = sfp.grid_readers(100.0, 100.0, 4)
    ranked = sfp.nearest_k(10.0, 10.0, readers, 2)
    assert len(ranked) == 2
    assert ranked[0][1] <= ranked[1][1]


def test_grid_readers_are_inside_the_floor() -> None:
    readers = sfp.grid_readers(600.0, 400.0, 4)
    assert len(readers) == 4
    assert all(0 <= r.x <= 600 and 0 <= r.y <= 400 for r in readers)


def test_estimator_recovers_a_centered_asset() -> None:
    # 4 corner readers, asset dead-centre → estimate near the middle.
    readers = sfp.grid_readers(100.0, 100.0, 4)
    asset = sfp.PlacedAsset(name="a", epc="e", epc_hex="E2800000000000000000000A", x=50.0, y=50.0)
    cfg = PositionStrategy(half_life_s=1.0e9, rssi_floor_dbm=-127.0)
    fix, err = sfp.estimate_for_asset(asset, readers, k=4, config=cfg, now=NOW)
    assert fix is not None
    assert err is not None
    # Noiseless, symmetric coverage → small error.
    assert err < 15.0


def test_ground_truth_rmse_is_reasonable_noiseless() -> None:
    rng = random.Random(7)  # noqa: S311 — deterministic test fixture, not crypto
    readers = sfp.grid_readers(600.0, 400.0, 9)
    assets = sfp.place_assets(20, 600.0, 400.0, rng)
    # Each placed asset carries a display-only uppercase-hex EPC (24 chars, even).
    assert all(len(a.epc_hex) == 24 and int(a.epc_hex, 16) >= 0 for a in assets)
    cfg = PositionStrategy(half_life_s=1.0e9, rssi_floor_dbm=-127.0)
    errors = []
    for asset in assets:
        _fix, err = sfp.estimate_for_asset(asset, readers, k=4, config=cfg, now=NOW)
        if err is not None:
            errors.append(err)
    assert errors
    rmse = (sum(e * e for e in errors) / len(errors)) ** 0.5
    # RSSI-centroid is coarse but bounded; assert it stays in a sane band.
    assert rmse < 120.0


def test_observations_count_matches_k() -> None:
    readers = sfp.grid_readers(100.0, 100.0, 6)
    asset = sfp.PlacedAsset(name="a", epc="e", epc_hex="E2800000000000000000000A", x=30.0, y=40.0)
    obs = sfp.observations_for_asset(asset, readers, 3, now=NOW)
    assert len(obs) == 3
    assert all(o.ts == NOW and o.cnt == 1 for o in obs)
