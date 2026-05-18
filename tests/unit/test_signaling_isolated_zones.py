"""Unit tests for ``tagpulse.signaling.isolated_zones`` (Sprint 41 Phase D1).

The IsolatedZones processor is a pure function over a materialised zone
candidate list. These tests exercise the three attribution paths
(reader-bound, geofence, combined) plus the deterministic-oldest
tiebreak and the malformed-polygon defence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tagpulse.signaling.isolated_zones import (
    IsolatedZoneAttribution,
    ZoneCandidate,
    attribute,
    attribute_geofence,
    attribute_reader_bound,
)

# Common polygon: 0.0..1.0 latitude × 0.0..1.0 longitude square.
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
_SQUARE_BBOX = (0.0, 1.0, 0.0, 1.0)  # (min_lat, max_lat, min_lon, max_lon)


def _reader_bound_zone(
    *,
    reader_ids: tuple[str, ...],
    created_at: datetime | None = None,
    zone_id: object | None = None,
) -> ZoneCandidate:
    return ZoneCandidate(
        id=zone_id or uuid4(),
        kind="reader_bound",
        created_at=created_at or datetime.now(UTC),
        fixed_reader_ids=reader_ids,
    )


def _geofence_zone(
    *,
    polygon: dict | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    created_at: datetime | None = None,
    zone_id: object | None = None,
) -> ZoneCandidate:
    poly = polygon if polygon is not None else _SQUARE_POLYGON
    bb = bbox if bbox is not None else _SQUARE_BBOX
    return ZoneCandidate(
        id=zone_id or uuid4(),
        kind="geofence",
        created_at=created_at or datetime.now(UTC),
        polygon_geojson=poly,
        bbox_min_lat=bb[0],
        bbox_max_lat=bb[1],
        bbox_min_lon=bb[2],
        bbox_max_lon=bb[3],
    )


# ---------------------------------------------------------------------------
# Reader-bound
# ---------------------------------------------------------------------------


def test_attribute_reader_bound_matches_reader_in_fixed_list() -> None:
    reader_id = uuid4()
    zone = _reader_bound_zone(reader_ids=(str(reader_id),))
    result = attribute_reader_bound(reader_id=reader_id, zones=[zone])
    assert result is not None
    assert isinstance(result, IsolatedZoneAttribution)
    assert result.zone_id == zone.id
    assert result.zone_kind == "reader_bound"
    assert result.source == "reader_bound"


def test_attribute_reader_bound_no_match_returns_none() -> None:
    reader_id = uuid4()
    other = uuid4()
    zone = _reader_bound_zone(reader_ids=(str(other),))
    assert attribute_reader_bound(reader_id=reader_id, zones=[zone]) is None


def test_attribute_reader_bound_ignores_geofence_zones() -> None:
    """A reader_bound caller must not be attributed to a geofence zone
    even if the geofence zone's polygon happened to include the reader."""
    reader_id = uuid4()
    geofence = _geofence_zone()
    assert attribute_reader_bound(reader_id=reader_id, zones=[geofence]) is None


def test_attribute_reader_bound_deterministic_oldest_tiebreak() -> None:
    """Two reader_bound zones both list the same reader. The one with
    the earlier ``created_at`` must win, regardless of input order."""
    reader_id = uuid4()
    older = _reader_bound_zone(
        reader_ids=(str(reader_id),),
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    newer = _reader_bound_zone(
        reader_ids=(str(reader_id),),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    # Pass in "newer first" order; oldest must still win.
    result = attribute_reader_bound(reader_id=reader_id, zones=[newer, older])
    assert result is not None
    assert result.zone_id == older.id


def test_attribute_reader_bound_empty_fixed_list_skipped() -> None:
    reader_id = uuid4()
    zone = ZoneCandidate(
        id=uuid4(),
        kind="reader_bound",
        created_at=datetime.now(UTC),
        fixed_reader_ids=None,
    )
    assert attribute_reader_bound(reader_id=reader_id, zones=[zone]) is None


# ---------------------------------------------------------------------------
# Geofence
# ---------------------------------------------------------------------------


def test_attribute_geofence_point_inside_polygon() -> None:
    zone = _geofence_zone()
    result = attribute_geofence(latitude=0.5, longitude=0.5, zones=[zone])
    assert result is not None
    assert result.zone_id == zone.id
    assert result.zone_kind == "geofence"
    assert result.source == "geofence"


def test_attribute_geofence_point_outside_bbox_short_circuits() -> None:
    zone = _geofence_zone()
    # Bbox is 0..1; (5, 5) is well outside.
    assert attribute_geofence(latitude=5.0, longitude=5.0, zones=[zone]) is None


def test_attribute_geofence_point_in_bbox_but_outside_polygon() -> None:
    """Bbox is the bounding box of the polygon; a point inside the bbox
    but outside an L-shaped polygon must not match. Build an L-shape so
    bbox prefilter passes but ray-cast rejects."""
    l_polygon = {
        "type": "Polygon",
        "coordinates": [
            [
                [0.0, 0.0],
                [1.0, 0.0],
                [1.0, 0.4],
                [0.4, 0.4],
                [0.4, 1.0],
                [0.0, 1.0],
                [0.0, 0.0],
            ]
        ],
    }
    zone = _geofence_zone(polygon=l_polygon, bbox=(0.0, 1.0, 0.0, 1.0))
    # (0.8, 0.8) is in bbox but in the L's notch.
    assert attribute_geofence(latitude=0.8, longitude=0.8, zones=[zone]) is None


def test_attribute_geofence_ignores_reader_bound_zones() -> None:
    reader_zone = _reader_bound_zone(reader_ids=(str(uuid4()),))
    assert attribute_geofence(latitude=0.5, longitude=0.5, zones=[reader_zone]) is None


def test_attribute_geofence_malformed_polygon_skipped() -> None:
    """A geofence zone with a malformed polygon (missing coordinates,
    wrong shape) must be silently skipped — the OverlappingZones
    processor calls this on every read so we can't afford to raise."""
    bad_polygon = {"type": "Polygon"}  # no coordinates
    bad_zone = _geofence_zone(polygon=bad_polygon, bbox=(0.0, 1.0, 0.0, 1.0))
    good_zone = _geofence_zone(
        created_at=datetime.now(UTC) + timedelta(seconds=1),  # newer; only used if bad skipped
    )
    result = attribute_geofence(latitude=0.5, longitude=0.5, zones=[bad_zone, good_zone])
    assert result is not None
    assert result.zone_id == good_zone.id


def test_attribute_geofence_deterministic_oldest_tiebreak() -> None:
    older = _geofence_zone(created_at=datetime(2025, 1, 1, tzinfo=UTC))
    newer = _geofence_zone(created_at=datetime(2026, 1, 1, tzinfo=UTC))
    # Same polygon; both contain (0.5, 0.5). Newer first in input.
    result = attribute_geofence(latitude=0.5, longitude=0.5, zones=[newer, older])
    assert result is not None
    assert result.zone_id == older.id


# ---------------------------------------------------------------------------
# Combined attribute()
# ---------------------------------------------------------------------------


def test_attribute_reader_bound_priority_over_geofence() -> None:
    """A read that satisfies both a reader_bound and a geofence zone
    must be attributed to the reader_bound zone — the
    ``IsolatedZones`` algorithm always tries reader-bound first."""
    reader_id = uuid4()
    reader_zone = _reader_bound_zone(reader_ids=(str(reader_id),))
    geofence = _geofence_zone()
    result = attribute(
        reader_id=reader_id,
        latitude=0.5,
        longitude=0.5,
        zones=[geofence, reader_zone],
    )
    assert result is not None
    assert result.zone_id == reader_zone.id


def test_attribute_falls_back_to_geofence_when_no_reader_match() -> None:
    reader_id = uuid4()
    other = uuid4()
    reader_zone = _reader_bound_zone(reader_ids=(str(other),))
    geofence = _geofence_zone()
    result = attribute(
        reader_id=reader_id,
        latitude=0.5,
        longitude=0.5,
        zones=[reader_zone, geofence],
    )
    assert result is not None
    assert result.zone_id == geofence.id


def test_attribute_no_match_returns_none() -> None:
    reader_id = uuid4()
    other = uuid4()
    reader_zone = _reader_bound_zone(reader_ids=(str(other),))
    far_geofence = _geofence_zone(
        polygon={
            "type": "Polygon",
            "coordinates": [
                [
                    [10.0, 10.0],
                    [11.0, 10.0],
                    [11.0, 11.0],
                    [10.0, 11.0],
                    [10.0, 10.0],
                ]
            ],
        },
        bbox=(10.0, 11.0, 10.0, 11.0),
    )
    assert (
        attribute(
            reader_id=reader_id,
            latitude=0.5,
            longitude=0.5,
            zones=[reader_zone, far_geofence],
        )
        is None
    )


def test_attribute_no_geofence_signal_returns_none() -> None:
    """If the read has no GPS and no reader-bound match, attribution
    can't proceed."""
    reader_id = uuid4()
    geofence = _geofence_zone()
    assert (
        attribute(
            reader_id=reader_id,
            latitude=None,
            longitude=None,
            zones=[geofence],
        )
        is None
    )


def test_attribute_no_reader_id_skips_reader_path() -> None:
    """A read without a reader_id still attempts geofence attribution."""
    geofence = _geofence_zone()
    result = attribute(
        reader_id=None,
        latitude=0.5,
        longitude=0.5,
        zones=[geofence],
    )
    assert result is not None
    assert result.zone_id == geofence.id


def test_attribute_empty_zone_list_returns_none() -> None:
    assert (
        attribute(
            reader_id=uuid4(),
            latitude=0.5,
            longitude=0.5,
            zones=[],
        )
        is None
    )
