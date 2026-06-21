"""Unit tests for consolidation read resolution (Sprint 71, ADR-034)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from tagpulse.repositories.timescaledb.consolidation_source import (
    RawConsolidationRead,
    build_resolved_reads,
)
from tagpulse.signaling.isolated_zones import ZoneCandidate

NOW = datetime(2026, 6, 20, 12, 0, 0, tzinfo=UTC)

# A geofence square around (42.0, -71.0).
_GEO_RING = {
    "type": "Polygon",
    "coordinates": [[[-71.1, 41.9], [-70.9, 41.9], [-70.9, 42.1], [-71.1, 42.1], [-71.1, 41.9]]],
}


def _reader_zone(zone_id, reader_id) -> ZoneCandidate:
    return ZoneCandidate(
        id=zone_id,
        kind="reader_bound",
        created_at=NOW,
        fixed_reader_ids=(str(reader_id),),
    )


def _geofence_zone(zone_id) -> ZoneCandidate:
    return ZoneCandidate(
        id=zone_id,
        kind="geofence",
        created_at=NOW,
        polygon_geojson=_GEO_RING,
    )


def test_reader_read_resolves_to_reader_frame_and_zone() -> None:
    asset, reader, zone, site = uuid4(), uuid4(), uuid4(), uuid4()
    raw = RawConsolidationRead(
        asset_id=asset,
        tag_key="a",
        reader_id=reader,
        ts=NOW,
        rssi=-50.0,
        lat=None,
        lon=None,
        sensor_data={"temperature_c": 4.5, "humidity_pct": 60, "read_count": 3},
    )
    out = build_resolved_reads([raw], [_reader_zone(zone, reader)], {zone: site})
    [r] = out[asset]
    assert r.frame == "reader"
    assert r.zone_id == zone
    assert r.site_id == site
    assert r.read_count == 3
    assert r.temperature_c == 4.5
    assert r.humidity_pct == 60.0


def test_gps_inside_geofence_resolves_to_geo_zone() -> None:
    asset, zone, site = uuid4(), uuid4(), uuid4()
    raw = RawConsolidationRead(
        asset_id=asset,
        tag_key="a",
        reader_id=uuid4(),
        ts=NOW,
        rssi=None,
        lat=42.0,
        lon=-71.0,
        sensor_data=None,
    )
    out = build_resolved_reads([raw], [_geofence_zone(zone)], {zone: site})
    [r] = out[asset]
    assert r.frame == "geo"
    assert r.zone_id == zone
    assert r.site_id == site


def test_gps_outside_any_geofence_is_in_transit() -> None:
    asset = uuid4()
    raw = RawConsolidationRead(
        asset_id=asset,
        tag_key="a",
        reader_id=uuid4(),
        ts=NOW,
        rssi=None,
        lat=10.0,
        lon=10.0,
        sensor_data=None,
    )
    out = build_resolved_reads([raw], [_geofence_zone(uuid4())], {})
    [r] = out[asset]
    assert r.frame == "geo"
    assert r.zone_id is None


def test_no_reader_zone_no_gps_resolves_to_none() -> None:
    asset = uuid4()
    raw = RawConsolidationRead(
        asset_id=asset,
        tag_key="a",
        reader_id=uuid4(),
        ts=NOW,
        rssi=-50.0,
        lat=None,
        lon=None,
        sensor_data={"read_count": "bad"},  # non-numeric → default 1
    )
    out = build_resolved_reads([raw], [], {})
    [r] = out[asset]
    assert r.frame == "none"
    assert r.zone_id is None
    assert r.read_count == 1


def test_reader_bound_wins_over_geofence() -> None:
    asset, reader, rzone, gzone = uuid4(), uuid4(), uuid4(), uuid4()
    raw = RawConsolidationRead(
        asset_id=asset,
        tag_key="a",
        reader_id=reader,
        ts=NOW,
        rssi=-50.0,
        lat=42.0,
        lon=-71.0,
        sensor_data=None,
    )
    zones = [_geofence_zone(gzone), _reader_zone(rzone, reader)]
    out = build_resolved_reads([raw], zones, {rzone: uuid4(), gzone: uuid4()})
    [r] = out[asset]
    assert r.frame == "reader"
    assert r.zone_id == rzone


def test_reads_grouped_by_asset() -> None:
    a1, a2, reader, zone = uuid4(), uuid4(), uuid4(), uuid4()
    raws = [
        RawConsolidationRead(a1, "a", reader, NOW, -50.0, None, None, None),
        RawConsolidationRead(a1, "b", reader, NOW, -50.0, None, None, None),
        RawConsolidationRead(a2, "c", reader, NOW, -50.0, None, None, None),
    ]
    out = build_resolved_reads(raws, [_reader_zone(zone, reader)], {zone: uuid4()})
    assert len(out[a1]) == 2
    assert len(out[a2]) == 1
