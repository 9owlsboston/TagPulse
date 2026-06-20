"""Unit tests for the floor-position source mapping helpers (Sprint 66)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tagpulse.repositories.timescaledb.floor_position_source import (
    RawRead,
    _read_count_of,
    build_floor_observations,
    resolve_antenna_xy,
)

TS = datetime(2026, 6, 19, 12, 0, 0, tzinfo=UTC)


def test_resolve_antenna_exact_port_wins() -> None:
    dev = uuid4()
    a1, a0 = uuid4(), uuid4()
    index = {(dev, 1): (a1, 10.0, 20.0), (dev, 0): (a0, 1.0, 2.0)}
    assert resolve_antenna_xy(index, dev, 1) == (a1, 10.0, 20.0)


def test_resolve_antenna_falls_back_to_port_zero() -> None:
    dev = uuid4()
    a0 = uuid4()
    index = {(dev, 0): (a0, 1.0, 2.0)}
    # Port 3 not surveyed → fall back to the reader's port-0 spot.
    assert resolve_antenna_xy(index, dev, 3) == (a0, 1.0, 2.0)


def test_resolve_antenna_none_when_unsurveyed() -> None:
    dev = uuid4()
    assert resolve_antenna_xy({}, dev, 2) is None


def test_build_observations_happy_path() -> None:
    dev, site, asset, ant = uuid4(), uuid4(), uuid4(), uuid4()
    reads = [RawRead(device_id=dev, port=1, rssi=-55.0, epc="urn:epc:a", ts=TS)]
    obs = build_floor_observations(
        reads,
        device_site={dev: site},
        antenna_index={(dev, 1): (ant, 10.0, 20.0)},
        epc_to_asset={"urn:epc:a": asset},
    )
    assert len(obs) == 1
    o = obs[0]
    assert (o.site_id, o.asset_id, o.antenna_id) == (site, asset, ant)
    assert (o.x, o.y, o.rssi, o.cnt) == (10.0, 20.0, -55.0, 1)
    assert o.ts == TS


def test_build_observations_carries_real_read_count() -> None:
    """``read_count`` (WM ``cnt``) flows from the raw read onto the observation
    so the count-weight estimator extension has live data (it currently
    defaults to 1 when absent)."""
    dev, site, asset, ant = uuid4(), uuid4(), uuid4(), uuid4()
    reads = [RawRead(device_id=dev, port=1, rssi=-55.0, epc="urn:epc:a", ts=TS, read_count=4)]
    obs = build_floor_observations(
        reads,
        device_site={dev: site},
        antenna_index={(dev, 1): (ant, 10.0, 20.0)},
        epc_to_asset={"urn:epc:a": asset},
    )
    assert obs[0].cnt == 4


@pytest.mark.parametrize(
    ("sensor_data", "expected"),
    [
        (None, 1),
        ({}, 1),
        ({"read_count": 5}, 5),
        ({"read_count": 3.0}, 3),  # float floored to int
        ({"read_count": 0}, 1),  # non-positive clamped to 1
        ({"read_count": -2}, 1),
        ({"read_count": True}, 1),  # bool is not a count
        ({"read_count": "7"}, 1),  # non-numeric ignored
        ({"temperature_c": 4.2}, 1),  # absent key
    ],
)
def test_read_count_of(sensor_data: dict[str, object] | None, expected: int) -> None:
    assert _read_count_of(sensor_data) == expected


def test_build_observations_uses_port_zero_fallback() -> None:
    dev, site, asset, a0 = uuid4(), uuid4(), uuid4(), uuid4()
    reads = [RawRead(device_id=dev, port=5, rssi=-50.0, epc="e", ts=TS)]
    obs = build_floor_observations(reads, {dev: site}, {(dev, 0): (a0, 3.0, 4.0)}, {"e": asset})
    assert len(obs) == 1
    assert (obs[0].x, obs[0].y, obs[0].antenna_id) == (3.0, 4.0, a0)


def test_build_observations_drops_unresolvable_rows() -> None:
    dev, other_dev, site, asset, ant = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    reads = [
        RawRead(device_id=dev, port=1, rssi=-55.0, epc="unknown", ts=TS),  # epc not bound
        RawRead(device_id=other_dev, port=1, rssi=-55.0, epc="e", ts=TS),  # device not sited
        RawRead(device_id=dev, port=9, rssi=-55.0, epc="e", ts=TS),  # antenna not surveyed
    ]
    obs = build_floor_observations(
        reads,
        device_site={dev: site},
        antenna_index={(dev, 1): (ant, 10.0, 20.0)},
        epc_to_asset={"e": asset},
    )
    # Only rows with a bound EPC + sited device + surveyed antenna survive.
    # Row 3 (port 9) has no port-0 fallback here → dropped.
    assert obs == []
