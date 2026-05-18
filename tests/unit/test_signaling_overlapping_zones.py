"""Unit tests for ``tagpulse.signaling.overlapping_zones`` (Sprint 41 Phase D2/D3).

The OverlappingZones module has two layers:

* :func:`aggregate` — pure aggregation over a materialised
  ``AttributionRead`` list. These tests live in this file.
* :class:`OverlappingZonesProcessor` — DB-backed wrapper. Tests in
  :mod:`tests/unit/test_signaling_overlapping_zones_processor.py`.

Synthetic-stream scenarios (Phase D3) are at the bottom of this file:
single-zone attribution, genuine overlap, and bleed rejection — the three
operator-facing behaviours the ADR-021 v2 ratification called out.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from tagpulse.signaling.isolated_zones import ZoneCandidate
from tagpulse.signaling.overlapping_zones import (
    AggregationConfig,
    AttributionRead,
    ZoneAttribution,
    aggregate,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_SQUARE_POLYGON = {
    "type": "Polygon",
    "coordinates": [
        [
            [0.0, 0.0],
            [1.0, 0.0],
            [1.0, 1.0],
            [0.0, 1.0],
            [0.0, 0.0],
        ]
    ],
}


def _reader_zone(
    *,
    reader_ids: tuple[str, ...],
    zone_id: UUID | None = None,
    created_at: datetime | None = None,
) -> ZoneCandidate:
    return ZoneCandidate(
        id=zone_id or uuid4(),
        kind="reader_bound",
        created_at=created_at or datetime.now(UTC),
        fixed_reader_ids=reader_ids,
    )


def _geofence_zone(
    *,
    zone_id: UUID | None = None,
    polygon: dict | None = None,
    bbox: tuple[float, float, float, float] = (0.0, 1.0, 0.0, 1.0),
    created_at: datetime | None = None,
) -> ZoneCandidate:
    return ZoneCandidate(
        id=zone_id or uuid4(),
        kind="geofence",
        created_at=created_at or datetime.now(UTC),
        polygon_geojson=polygon if polygon is not None else _SQUARE_POLYGON,
        bbox_min_lat=bbox[0],
        bbox_max_lat=bbox[1],
        bbox_min_lon=bbox[2],
        bbox_max_lon=bbox[3],
    )


def _make_read(
    *,
    asset_id: UUID,
    reader_id: UUID,
    timestamp: datetime,
    signal_strength: float | None = -50.0,
    latitude: float | None = None,
    longitude: float | None = None,
) -> AttributionRead:
    return AttributionRead(
        asset_id=asset_id,
        reader_id=reader_id,
        timestamp=timestamp,
        signal_strength=signal_strength,
        latitude=latitude,
        longitude=longitude,
    )


def _site_map(*zones: ZoneCandidate) -> dict[UUID, UUID]:
    site = uuid4()
    return {z.id: site for z in zones}


# ---------------------------------------------------------------------------
# AggregationConfig
# ---------------------------------------------------------------------------


def test_aggregation_config_defaults_are_safe() -> None:
    cfg = AggregationConfig()
    assert cfg.aggregation_window_s == 60
    assert cfg.min_rssi_dbm == -80.0
    assert cfg.zone_bleed_filter is True
    assert 0.0 < cfg.zone_bleed_share_threshold < 1.0
    assert cfg.aging_weight == 1.0
    assert cfg.time_error_filter_s == 5


def test_aggregation_config_from_rule_config_full() -> None:
    cfg = AggregationConfig.from_rule_config(
        {
            "cadence_minutes": 5,  # irrelevant; lives outside processor_config
            "processor_config": {
                "aggregation_window_s": 30,
                "min_rssi_dbm": -65,
                "zone_bleed_filter": False,
                "aging_weight": 0.9,
                "time_error_filter": 0,
            },
        }
    )
    assert cfg.aggregation_window_s == 30
    assert cfg.min_rssi_dbm == -65.0
    assert cfg.zone_bleed_filter is False
    assert cfg.aging_weight == 0.9
    assert cfg.time_error_filter_s == 0


def test_aggregation_config_from_rule_config_missing_block_uses_defaults() -> None:
    cfg = AggregationConfig.from_rule_config({"cadence_minutes": 5})
    assert cfg.aggregation_window_s == 60
    assert cfg.min_rssi_dbm == -80.0


def test_aggregation_config_from_rule_config_explicit_null_rssi_disables_floor() -> None:
    cfg = AggregationConfig.from_rule_config({"processor_config": {"min_rssi_dbm": None}})
    assert cfg.min_rssi_dbm is None


# ---------------------------------------------------------------------------
# aggregate(): empty cases
# ---------------------------------------------------------------------------


def test_aggregate_no_reads_returns_empty() -> None:
    zone = _reader_zone(reader_ids=(str(uuid4()),))
    result = aggregate(
        reads=[],
        zones=[zone],
        site_by_zone=_site_map(zone),
        config=AggregationConfig(),
        window_end=datetime.now(UTC),
    )
    assert result == []


def test_aggregate_no_zones_returns_empty() -> None:
    asset, reader = uuid4(), uuid4()
    now = datetime.now(UTC)
    result = aggregate(
        reads=[_make_read(asset_id=asset, reader_id=reader, timestamp=now)],
        zones=[],
        site_by_zone={},
        config=AggregationConfig(),
        window_end=now,
    )
    assert result == []


def test_aggregate_read_with_no_zone_match_returns_empty() -> None:
    asset, reader, unrelated = uuid4(), uuid4(), uuid4()
    zone = _reader_zone(reader_ids=(str(unrelated),))
    now = datetime.now(UTC)
    result = aggregate(
        reads=[_make_read(asset_id=asset, reader_id=reader, timestamp=now)],
        zones=[zone],
        site_by_zone=_site_map(zone),
        config=AggregationConfig(),
        window_end=now,
    )
    assert result == []


# ---------------------------------------------------------------------------
# aggregate(): single-zone case (IsolatedZones-equivalent)
# ---------------------------------------------------------------------------


def test_aggregate_single_zone_full_confidence() -> None:
    """An asset whose ALL reads come from one zone gets confidence 1.0."""
    asset, reader = uuid4(), uuid4()
    zone = _reader_zone(reader_ids=(str(reader),))
    now = datetime.now(UTC)
    reads = [
        _make_read(asset_id=asset, reader_id=reader, timestamp=now - timedelta(seconds=i))
        for i in range(5)
    ]
    result = aggregate(
        reads=reads,
        zones=[zone],
        site_by_zone=_site_map(zone),
        config=AggregationConfig(),
        window_end=now,
    )
    assert len(result) == 1
    attr = result[0]
    assert isinstance(attr, ZoneAttribution)
    assert attr.asset_id == asset
    assert attr.zone_id == zone.id
    assert attr.confidence == 1.0
    assert attr.contributing_reads == 5
    assert attr.contributing_readers == (reader,)


def test_aggregate_multiple_assets_independent_confidence() -> None:
    """Two assets in two different zones each get their own confidence 1.0."""
    asset_a, asset_b = uuid4(), uuid4()
    reader_a, reader_b = uuid4(), uuid4()
    zone_a = _reader_zone(reader_ids=(str(reader_a),))
    zone_b = _reader_zone(reader_ids=(str(reader_b),))
    now = datetime.now(UTC)
    reads = [
        _make_read(asset_id=asset_a, reader_id=reader_a, timestamp=now),
        _make_read(asset_id=asset_b, reader_id=reader_b, timestamp=now),
    ]
    result = aggregate(
        reads=reads,
        zones=[zone_a, zone_b],
        site_by_zone=_site_map(zone_a, zone_b),
        config=AggregationConfig(),
        window_end=now,
    )
    assert len(result) == 2
    by_asset = {r.asset_id: r for r in result}
    assert by_asset[asset_a].zone_id == zone_a.id
    assert by_asset[asset_a].confidence == 1.0
    assert by_asset[asset_b].zone_id == zone_b.id
    assert by_asset[asset_b].confidence == 1.0


# ---------------------------------------------------------------------------
# aggregate(): genuine overlap (the whole point of OverlappingZones)
# ---------------------------------------------------------------------------


def test_aggregate_genuine_overlap_picks_both_zones() -> None:
    """A reader assigned to BOTH a reader-bound zone and a geofence
    polygon contributes evidence to both zones simultaneously. The
    asset gets two attributions, each with confidence 0.5."""
    asset, reader = uuid4(), uuid4()
    # Reader-bound zone the reader is fixed in.
    reader_zone = _reader_zone(reader_ids=(str(reader),))
    # Geofence zone whose polygon contains the read coordinates.
    geofence = _geofence_zone()
    now = datetime.now(UTC)
    # Five reads from the same reader at (0.5, 0.5) — inside polygon.
    reads = [
        _make_read(
            asset_id=asset,
            reader_id=reader,
            timestamp=now - timedelta(seconds=i),
            latitude=0.5,
            longitude=0.5,
        )
        for i in range(5)
    ]
    result = aggregate(
        reads=reads,
        zones=[reader_zone, geofence],
        site_by_zone=_site_map(reader_zone, geofence),
        config=AggregationConfig(zone_bleed_filter=False, aging_weight=1.0),
        window_end=now,
    )
    assert len(result) == 2
    zones_seen = {r.zone_id for r in result}
    assert zones_seen == {reader_zone.id, geofence.id}
    for attr in result:
        assert attr.confidence == 0.5
        assert attr.contributing_reads == 5


def test_aggregate_uneven_overlap_confidence_reflects_share() -> None:
    """Asset has 8 reads from a reader-bound zone and 2 reads (from a
    different reader) that fall inside a geofence. The reader-bound
    zone should get confidence 0.8, the geofence 0.2."""
    asset = uuid4()
    reader_in_zone, mobile_reader = uuid4(), uuid4()
    reader_zone = _reader_zone(reader_ids=(str(reader_in_zone),))
    geofence = _geofence_zone()
    now = datetime.now(UTC)
    reads = []
    for i in range(8):
        reads.append(
            _make_read(
                asset_id=asset, reader_id=reader_in_zone, timestamp=now - timedelta(seconds=i)
            )
        )
    for i in range(2):
        reads.append(
            _make_read(
                asset_id=asset,
                reader_id=mobile_reader,
                timestamp=now - timedelta(seconds=i),
                latitude=0.5,
                longitude=0.5,
            )
        )
    result = aggregate(
        reads=reads,
        zones=[reader_zone, geofence],
        site_by_zone=_site_map(reader_zone, geofence),
        config=AggregationConfig(zone_bleed_filter=False, aging_weight=1.0),
        window_end=now,
    )
    by_zone = {r.zone_id: r for r in result}
    assert by_zone[reader_zone.id].confidence == 0.8
    assert by_zone[geofence.id].confidence == 0.2


# ---------------------------------------------------------------------------
# aggregate(): RSSI floor
# ---------------------------------------------------------------------------


def test_aggregate_rssi_floor_drops_weak_reads() -> None:
    asset, reader = uuid4(), uuid4()
    zone = _reader_zone(reader_ids=(str(reader),))
    now = datetime.now(UTC)
    # min_rssi_dbm = -70: -50 and -65 pass; -75 and -90 drop.
    reads = [
        _make_read(asset_id=asset, reader_id=reader, timestamp=now, signal_strength=-50.0),
        _make_read(asset_id=asset, reader_id=reader, timestamp=now, signal_strength=-65.0),
        _make_read(asset_id=asset, reader_id=reader, timestamp=now, signal_strength=-75.0),
        _make_read(asset_id=asset, reader_id=reader, timestamp=now, signal_strength=-90.0),
    ]
    result = aggregate(
        reads=reads,
        zones=[zone],
        site_by_zone=_site_map(zone),
        config=AggregationConfig(min_rssi_dbm=-70.0),
        window_end=now,
    )
    assert len(result) == 1
    assert result[0].contributing_reads == 2


def test_aggregate_rssi_none_always_passes_floor() -> None:
    """Non-RSSI hardware (e.g. mobile GPS-only readers) reports None
    for signal_strength and must always pass the floor."""
    asset, reader = uuid4(), uuid4()
    zone = _reader_zone(reader_ids=(str(reader),))
    now = datetime.now(UTC)
    reads = [_make_read(asset_id=asset, reader_id=reader, timestamp=now, signal_strength=None)]
    result = aggregate(
        reads=reads,
        zones=[zone],
        site_by_zone=_site_map(zone),
        config=AggregationConfig(min_rssi_dbm=-50.0),  # high floor would drop a -65 read
        window_end=now,
    )
    assert len(result) == 1


def test_aggregate_rssi_floor_disabled_when_none() -> None:
    asset, reader = uuid4(), uuid4()
    zone = _reader_zone(reader_ids=(str(reader),))
    now = datetime.now(UTC)
    reads = [_make_read(asset_id=asset, reader_id=reader, timestamp=now, signal_strength=-99.0)]
    result = aggregate(
        reads=reads,
        zones=[zone],
        site_by_zone=_site_map(zone),
        config=AggregationConfig(min_rssi_dbm=None),
        window_end=now,
    )
    assert len(result) == 1


# ---------------------------------------------------------------------------
# aggregate(): window enforcement
# ---------------------------------------------------------------------------


def test_aggregate_drops_reads_outside_window() -> None:
    """Reads older than ``aggregation_window_s + time_error_filter_s``
    must be dropped silently."""
    asset, reader = uuid4(), uuid4()
    zone = _reader_zone(reader_ids=(str(reader),))
    now = datetime.now(UTC)
    config = AggregationConfig(aggregation_window_s=60, time_error_filter_s=5)
    reads = [
        _make_read(asset_id=asset, reader_id=reader, timestamp=now),  # in window
        _make_read(
            asset_id=asset, reader_id=reader, timestamp=now - timedelta(seconds=30)
        ),  # in window
        _make_read(
            asset_id=asset, reader_id=reader, timestamp=now - timedelta(seconds=300)
        ),  # way too old
    ]
    result = aggregate(
        reads=reads,
        zones=[zone],
        site_by_zone=_site_map(zone),
        config=config,
        window_end=now,
    )
    assert len(result) == 1
    assert result[0].contributing_reads == 2


def test_aggregate_time_error_filter_extends_window() -> None:
    """A read with timestamp at ``window_start - 3s`` must pass when
    ``time_error_filter_s = 5``."""
    asset, reader = uuid4(), uuid4()
    zone = _reader_zone(reader_ids=(str(reader),))
    now = datetime.now(UTC)
    config = AggregationConfig(aggregation_window_s=60, time_error_filter_s=5)
    # 63s in the past — outside the 60s window but inside the 65s skew window.
    edge_read = _make_read(asset_id=asset, reader_id=reader, timestamp=now - timedelta(seconds=63))
    result = aggregate(
        reads=[edge_read],
        zones=[zone],
        site_by_zone=_site_map(zone),
        config=config,
        window_end=now,
    )
    assert len(result) == 1


def test_aggregate_drops_future_reads_beyond_skew() -> None:
    """Reads with ``timestamp > window_end`` are out-of-window and
    should not contribute. (A reader whose clock is far in the future
    is broken; we don't try to absorb arbitrary forward skew.)"""
    asset, reader = uuid4(), uuid4()
    zone = _reader_zone(reader_ids=(str(reader),))
    now = datetime.now(UTC)
    config = AggregationConfig()
    reads = [_make_read(asset_id=asset, reader_id=reader, timestamp=now + timedelta(seconds=30))]
    result = aggregate(
        reads=reads,
        zones=[zone],
        site_by_zone=_site_map(zone),
        config=config,
        window_end=now,
    )
    assert result == []


# ---------------------------------------------------------------------------
# aggregate(): aging weight
# ---------------------------------------------------------------------------


def test_aggregate_aging_weight_decays_older_reads() -> None:
    """With ``aging_weight=0.5`` and ``aging_bucket_s=60``, a read in
    the current bucket weighs 1.0; a read one bucket older weighs 0.5.
    A 1-recent + 1-old setup in one zone gives that zone weight 1.5,
    not 2.0. With only one zone, the asset still ends at confidence 1.0
    (share = bucket.weight / total = 1.0), but the contributing_reads
    count is the actual number of contributing reads (2)."""
    asset, reader = uuid4(), uuid4()
    zone = _reader_zone(reader_ids=(str(reader),))
    now = datetime.now(UTC)
    config = AggregationConfig(
        aggregation_window_s=120,
        time_error_filter_s=0,
        aging_weight=0.5,
        aging_bucket_s=60,
    )
    reads = [
        _make_read(asset_id=asset, reader_id=reader, timestamp=now),
        _make_read(
            asset_id=asset, reader_id=reader, timestamp=now - timedelta(seconds=75)
        ),  # 1 bucket old
    ]
    result = aggregate(
        reads=reads,
        zones=[zone],
        site_by_zone=_site_map(zone),
        config=config,
        window_end=now,
    )
    assert len(result) == 1
    assert result[0].contributing_reads == 2
    assert result[0].confidence == 1.0


def test_aggregate_aging_weight_shifts_overlap_in_favour_of_recent_zone() -> None:
    """Two zones: an asset has 1 fresh read in zone A and 1 old read
    (1 bucket old) in zone B. With ``aging_weight=0.5`` zone A's
    weight is 1.0 and zone B's weight is 0.5, so zone A's confidence
    is 1.0 / 1.5 ≈ 0.667 and zone B's is 0.333."""
    asset = uuid4()
    reader_a, reader_b = uuid4(), uuid4()
    zone_a = _reader_zone(reader_ids=(str(reader_a),))
    zone_b = _reader_zone(reader_ids=(str(reader_b),))
    now = datetime.now(UTC)
    config = AggregationConfig(
        aggregation_window_s=120,
        time_error_filter_s=0,
        zone_bleed_filter=False,
        aging_weight=0.5,
        aging_bucket_s=60,
    )
    reads = [
        _make_read(asset_id=asset, reader_id=reader_a, timestamp=now),
        _make_read(asset_id=asset, reader_id=reader_b, timestamp=now - timedelta(seconds=75)),
    ]
    result = aggregate(
        reads=reads,
        zones=[zone_a, zone_b],
        site_by_zone=_site_map(zone_a, zone_b),
        config=config,
        window_end=now,
    )
    by_zone = {r.zone_id: r for r in result}
    assert abs(by_zone[zone_a.id].confidence - (1.0 / 1.5)) < 1e-9
    assert abs(by_zone[zone_b.id].confidence - (0.5 / 1.5)) < 1e-9


# ---------------------------------------------------------------------------
# aggregate(): zone-bleed filter (D3 synthetic-stream scenario)
# ---------------------------------------------------------------------------


def test_aggregate_bleed_filter_drops_tiny_share() -> None:
    """Asset has 9 reads from zone A and 1 read from zone B. With
    ``zone_bleed_filter=True`` and the default 10% threshold,
    zone B's 10% share lands AT the threshold (>=) and survives; drop
    one more — at 8/9 vs 1/9 ≈ 11.1% it still survives. To force a drop,
    use 19/1 → B is 5% which is below 10%."""
    asset = uuid4()
    reader_a, reader_b = uuid4(), uuid4()
    zone_a = _reader_zone(reader_ids=(str(reader_a),))
    zone_b = _reader_zone(reader_ids=(str(reader_b),))
    now = datetime.now(UTC)
    config = AggregationConfig(zone_bleed_filter=True, zone_bleed_share_threshold=0.10)
    reads = [_make_read(asset_id=asset, reader_id=reader_a, timestamp=now) for _ in range(19)] + [
        _make_read(asset_id=asset, reader_id=reader_b, timestamp=now)
    ]
    result = aggregate(
        reads=reads,
        zones=[zone_a, zone_b],
        site_by_zone=_site_map(zone_a, zone_b),
        config=config,
        window_end=now,
    )
    assert len(result) == 1
    assert result[0].zone_id == zone_a.id
    assert result[0].confidence == 1.0  # renormalised after B dropped


def test_aggregate_bleed_filter_disabled_keeps_tiny_share() -> None:
    """With ``zone_bleed_filter=False`` the same 19/1 split keeps both
    attributions, B at confidence 0.05."""
    asset = uuid4()
    reader_a, reader_b = uuid4(), uuid4()
    zone_a = _reader_zone(reader_ids=(str(reader_a),))
    zone_b = _reader_zone(reader_ids=(str(reader_b),))
    now = datetime.now(UTC)
    config = AggregationConfig(zone_bleed_filter=False)
    reads = [_make_read(asset_id=asset, reader_id=reader_a, timestamp=now) for _ in range(19)] + [
        _make_read(asset_id=asset, reader_id=reader_b, timestamp=now)
    ]
    result = aggregate(
        reads=reads,
        zones=[zone_a, zone_b],
        site_by_zone=_site_map(zone_a, zone_b),
        config=config,
        window_end=now,
    )
    assert len(result) == 2
    by_zone = {r.zone_id: r for r in result}
    assert by_zone[zone_a.id].confidence == 0.95
    assert by_zone[zone_b.id].confidence == 0.05


# ---------------------------------------------------------------------------
# aggregate(): payload + ordering invariants
# ---------------------------------------------------------------------------


def test_aggregate_output_is_deterministically_ordered() -> None:
    """Output must be sorted by (asset_id, zone_id) so callers and
    tests can compare outputs without sort gymnastics."""
    asset_a, asset_b = uuid4(), uuid4()
    reader_a, reader_b = uuid4(), uuid4()
    zone_a = _reader_zone(reader_ids=(str(reader_a),))
    zone_b = _reader_zone(reader_ids=(str(reader_b),))
    now = datetime.now(UTC)
    reads = [
        _make_read(asset_id=asset_a, reader_id=reader_a, timestamp=now),
        _make_read(asset_id=asset_b, reader_id=reader_b, timestamp=now),
    ]
    result = aggregate(
        reads=reads,
        zones=[zone_a, zone_b],
        site_by_zone=_site_map(zone_a, zone_b),
        config=AggregationConfig(),
        window_end=now,
    )
    assert result == sorted(result, key=lambda r: (str(r.asset_id), str(r.zone_id)))


def test_aggregate_attribution_carries_site_and_window_bounds() -> None:
    asset, reader = uuid4(), uuid4()
    zone = _reader_zone(reader_ids=(str(reader),))
    site_id = uuid4()
    now = datetime.now(UTC)
    config = AggregationConfig(aggregation_window_s=60)
    result = aggregate(
        reads=[_make_read(asset_id=asset, reader_id=reader, timestamp=now)],
        zones=[zone],
        site_by_zone={zone.id: site_id},
        config=config,
        window_end=now,
    )
    assert len(result) == 1
    attr = result[0]
    assert attr.site_id == site_id
    assert attr.window_end == now
    assert attr.window_start == now - timedelta(seconds=60)


def test_aggregate_drops_zone_without_site_lookup_entry() -> None:
    """A zone missing from ``site_by_zone`` must be skipped rather
    than produce a half-formed attribution. Defensive: site_by_zone
    is expected to be the parallel of the zones list, but if a caller
    builds them inconsistently we'd rather drop than crash."""
    asset, reader = uuid4(), uuid4()
    zone = _reader_zone(reader_ids=(str(reader),))
    now = datetime.now(UTC)
    result = aggregate(
        reads=[_make_read(asset_id=asset, reader_id=reader, timestamp=now)],
        zones=[zone],
        site_by_zone={},  # missing entry for the zone
        config=AggregationConfig(),
        window_end=now,
    )
    assert result == []


# ---------------------------------------------------------------------------
# Phase D3 synthetic-stream scenarios
# ---------------------------------------------------------------------------


def test_synthetic_stream_single_zone_attribution() -> None:
    """Operator scenario A: 10 reads spread over 60s, all from the
    same reader inside zone X. OverlappingZones must produce one
    attribution with confidence 1.0 — matching the IsolatedZones result
    so operators can swap processors and see the same alerts when only
    one zone is involved."""
    asset, reader = uuid4(), uuid4()
    zone_x = _reader_zone(reader_ids=(str(reader),))
    now = datetime.now(UTC)
    stream = [
        _make_read(
            asset_id=asset,
            reader_id=reader,
            timestamp=now - timedelta(seconds=i * 6),
            signal_strength=-55.0,
        )
        for i in range(10)
    ]
    result = aggregate(
        reads=stream,
        zones=[zone_x],
        site_by_zone=_site_map(zone_x),
        config=AggregationConfig(),
        window_end=now,
    )
    assert len(result) == 1
    assert result[0].asset_id == asset
    assert result[0].zone_id == zone_x.id
    assert result[0].confidence == 1.0
    assert result[0].contributing_reads == 10


def test_synthetic_stream_genuine_overlap_picks_both_zones() -> None:
    """Operator scenario B: an asset moves through a hallway where
    Zone X (reader 1) and Zone Y (reader 2) genuinely overlap — both
    readers see the same tag over the same window with roughly equal
    counts. Both attributions must come out with confidence ~0.5."""
    asset = uuid4()
    reader_1, reader_2 = uuid4(), uuid4()
    zone_x = _reader_zone(reader_ids=(str(reader_1),))
    zone_y = _reader_zone(reader_ids=(str(reader_2),))
    now = datetime.now(UTC)
    stream = []
    for i in range(6):
        stream.append(
            _make_read(
                asset_id=asset,
                reader_id=reader_1,
                timestamp=now - timedelta(seconds=i * 6),
            )
        )
        stream.append(
            _make_read(
                asset_id=asset,
                reader_id=reader_2,
                timestamp=now - timedelta(seconds=i * 6 + 3),
            )
        )
    result = aggregate(
        reads=stream,
        zones=[zone_x, zone_y],
        site_by_zone=_site_map(zone_x, zone_y),
        config=AggregationConfig(),
        window_end=now,
    )
    assert len(result) == 2
    by_zone = {r.zone_id: r for r in result}
    # Equal evidence in both zones → confidence exactly 0.5 each.
    assert by_zone[zone_x.id].confidence == 0.5
    assert by_zone[zone_y.id].confidence == 0.5


def test_synthetic_stream_bleed_rejection() -> None:
    """Operator scenario C: an asset is firmly in Zone X (most of its
    reads) but a few reads from a far-side reader (Zone Z) bleed in.
    With the zone-bleed filter at default 10%, Zone Z should be dropped
    and only the Zone X attribution survive at confidence 1.0."""
    asset = uuid4()
    in_zone_reader = uuid4()
    bleed_reader = uuid4()
    zone_x = _reader_zone(reader_ids=(str(in_zone_reader),))
    zone_z = _reader_zone(reader_ids=(str(bleed_reader),))
    now = datetime.now(UTC)
    stream = [
        _make_read(asset_id=asset, reader_id=in_zone_reader, timestamp=now - timedelta(seconds=i))
        for i in range(20)
    ]
    # Bleed: 1 stray read from the far reader (~5% of asset's total).
    stream.append(_make_read(asset_id=asset, reader_id=bleed_reader, timestamp=now))
    result = aggregate(
        reads=stream,
        zones=[zone_x, zone_z],
        site_by_zone=_site_map(zone_x, zone_z),
        config=AggregationConfig(zone_bleed_filter=True, zone_bleed_share_threshold=0.10),
        window_end=now,
    )
    assert len(result) == 1
    assert result[0].zone_id == zone_x.id
    assert result[0].confidence == 1.0
