"""Unit tests for the pure asset-state consolidation core (Sprint 71, ADR-034)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tagpulse.services.consolidation import (
    AssetStateSnapshot,
    FusionStrategy,
    ResolvedRead,
    consolidate,
)

NOW = datetime(2026, 6, 20, 12, 0, 0, tzinfo=UTC)


def _read(
    asset_id,
    *,
    tag_key: str = "a",
    age_s: float = 0.0,
    read_count: int = 1,
    rssi: float | None = None,
    frame: str = "reader",
    zone_id=None,
    site_id=None,
    lat: float | None = None,
    lon: float | None = None,
    x: float | None = None,
    y: float | None = None,
    temperature_c: float | None = None,
    humidity_pct: float | None = None,
) -> ResolvedRead:
    return ResolvedRead(
        asset_id=asset_id,
        tag_key=tag_key,
        ts=NOW - timedelta(seconds=age_s),
        read_count=read_count,
        rssi=rssi,
        frame=frame,  # type: ignore[arg-type]
        zone_id=zone_id,
        site_id=site_id,
        lat=lat,
        lon=lon,
        x=x,
        y=y,
        temperature_c=temperature_c,
        humidity_pct=humidity_pct,
    )


def test_empty_below_min_reads_returns_none() -> None:
    asset = uuid4()
    assert consolidate([], asset_id=asset, now=NOW, config=FusionStrategy()) is None


def test_window_cut_drops_stale_reads() -> None:
    asset = uuid4()
    zone = uuid4()
    cfg = FusionStrategy(lookback_s=30.0, min_reads=1)
    fresh = _read(asset, zone_id=zone, age_s=5.0)
    stale = _read(asset, zone_id=zone, age_s=120.0, tag_key="b")
    snap = consolidate([fresh, stale], asset_id=asset, now=NOW, config=cfg)
    assert snap is not None
    assert snap.sample_count == 1
    assert snap.tag_count == 1


def test_zone_vote_read_count_times_recency() -> None:
    # zone_a: two fresh reads with read_count 3 each; zone_b: one read_count 1.
    asset = uuid4()
    zone_a, zone_b = uuid4(), uuid4()
    cfg = FusionStrategy(half_life_s=10.0, lookback_s=120.0)
    reads = [
        _read(asset, tag_key="a", zone_id=zone_a, read_count=3, age_s=1.0),
        _read(asset, tag_key="b", zone_id=zone_a, read_count=3, age_s=2.0),
        _read(asset, tag_key="c", zone_id=zone_b, read_count=1, age_s=0.0),
    ]
    snap = consolidate(reads, asset_id=asset, now=NOW, config=cfg)
    assert snap is not None
    assert snap.zone_id == zone_a
    assert snap.frame == "reader"
    assert snap.tag_count == 3
    assert snap.sample_count == 3
    assert 0.0 < snap.confidence <= 1.0


def test_environment_weighted_mean() -> None:
    asset = uuid4()
    zone = uuid4()
    cfg = FusionStrategy(half_life_s=0.0)  # last-wins isolates one read; use equal weights instead
    # Use a non-zero half-life so all reads contribute; equal ages → equal recency.
    cfg = FusionStrategy(half_life_s=10.0, lookback_s=120.0)
    reads = [
        _read(asset, tag_key="a", zone_id=zone, age_s=0.0, read_count=1, temperature_c=4.0),
        _read(asset, tag_key="b", zone_id=zone, age_s=0.0, read_count=3, temperature_c=8.0),
    ]
    snap = consolidate(reads, asset_id=asset, now=NOW, config=cfg)
    assert snap is not None
    # weighted mean = (4*1 + 8*3) / 4 = 7.0
    assert snap.temperature_c is not None
    assert abs(snap.temperature_c - 7.0) < 1e-9


def test_geo_frame_in_transit_competes_as_one_bucket() -> None:
    asset = uuid4()
    # Two geo reads (no zone) vs one stale reader read; recency favours geo.
    cfg = FusionStrategy(half_life_s=5.0, lookback_s=120.0)
    reads = [
        _read(asset, tag_key="a", frame="geo", zone_id=None, lat=42.0, lon=-71.0, age_s=1.0),
        _read(asset, tag_key="b", frame="geo", zone_id=None, lat=42.0, lon=-71.0, age_s=2.0),
        _read(asset, tag_key="c", frame="reader", zone_id=uuid4(), age_s=60.0),
    ]
    snap = consolidate(reads, asset_id=asset, now=NOW, config=cfg)
    assert snap is not None
    assert snap.frame == "geo"
    assert snap.zone_id is None
    assert snap.lat is not None and abs(snap.lat - 42.0) < 1e-9


def test_last_wins_when_half_life_zero() -> None:
    asset = uuid4()
    zone_old, zone_new = uuid4(), uuid4()
    cfg = FusionStrategy(half_life_s=0.0, lookback_s=120.0)
    reads = [
        _read(asset, tag_key="a", zone_id=zone_old, age_s=30.0, temperature_c=2.0),
        _read(asset, tag_key="b", zone_id=zone_new, age_s=1.0, temperature_c=9.0),
    ]
    snap = consolidate(reads, asset_id=asset, now=NOW, config=cfg)
    assert snap is not None
    assert snap.zone_id == zone_new
    assert snap.sample_count == 1
    assert snap.temperature_c == 9.0


def test_rssi_floor_excludes_weak_reads_from_location_only() -> None:
    asset = uuid4()
    zone = uuid4()
    cfg = FusionStrategy(half_life_s=10.0, lookback_s=120.0, rssi_floor_dbm=-70.0)
    # Weak read carries temp; it must still feed environment but not the vote.
    weak = _read(asset, tag_key="a", zone_id=zone, rssi=-90.0, temperature_c=5.0)
    snap = consolidate([weak], asset_id=asset, now=NOW, config=cfg)
    assert snap is not None
    # No locating read survived the floor → frame none, but env still computed.
    assert snap.frame == "none"
    assert snap.zone_id is None
    assert snap.temperature_c == 5.0


def test_frameless_reads_feed_environment_not_location() -> None:
    asset = uuid4()
    cfg = FusionStrategy(half_life_s=10.0, lookback_s=120.0)
    r = _read(asset, tag_key="a", frame="none", zone_id=None, humidity_pct=55.0)
    snap = consolidate([r], asset_id=asset, now=NOW, config=cfg)
    assert isinstance(snap, AssetStateSnapshot)
    assert snap.frame == "none"
    assert snap.humidity_pct == 55.0
    assert snap.confidence == 0.0
