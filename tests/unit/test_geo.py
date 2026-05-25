"""Unit tests for ``tagpulse.geo`` — point-in-polygon + polygon validation."""

from __future__ import annotations

import pytest

from tagpulse.geo import (
    PolygonValidationError,
    bbox_contains,
    compute_bbox,
    point_in_polygon,
    validate_polygon,
)

SQUARE = {
    "type": "Polygon",
    "coordinates": [
        [
            [-122.42, 37.78],
            [-122.41, 37.78],
            [-122.41, 37.79],
            [-122.42, 37.79],
            [-122.42, 37.78],
        ]
    ],
}


# ---- validate_polygon ----


def test_validate_square_returns_ring() -> None:
    ring = validate_polygon(SQUARE)
    assert len(ring) == 5
    assert ring[0] == ring[-1]


def test_validate_rejects_non_polygon_type() -> None:
    with pytest.raises(PolygonValidationError, match="Polygon"):
        validate_polygon({"type": "Point", "coordinates": [0, 0]})


def test_validate_rejects_multipolygon_v1() -> None:
    with pytest.raises(PolygonValidationError, match="Polygon"):
        validate_polygon({"type": "MultiPolygon", "coordinates": []})


def test_validate_rejects_holes() -> None:
    poly = {
        "type": "Polygon",
        "coordinates": [SQUARE["coordinates"][0], SQUARE["coordinates"][0]],
    }
    with pytest.raises(PolygonValidationError, match="single linear ring"):
        validate_polygon(poly)


def test_validate_rejects_unclosed_ring() -> None:
    poly = {
        "type": "Polygon",
        "coordinates": [
            [
                [-122.42, 37.78],
                [-122.41, 37.78],
                [-122.41, 37.79],
                [-122.42, 37.79],
            ]
        ],
    }
    with pytest.raises(PolygonValidationError, match="closed"):
        validate_polygon(poly)


def test_validate_rejects_too_few_vertices() -> None:
    poly = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [1, 1], [0, 0]]],
    }
    with pytest.raises(PolygonValidationError, match="at least 4"):
        validate_polygon(poly)


def test_validate_rejects_lat_out_of_range() -> None:
    poly = {
        "type": "Polygon",
        "coordinates": [
            [[0, 0], [1, 95], [1, 0], [0, 0]],
        ],
    }
    with pytest.raises(PolygonValidationError, match="latitude"):
        validate_polygon(poly)


def test_validate_rejects_lon_out_of_range() -> None:
    poly = {
        "type": "Polygon",
        "coordinates": [
            [[200, 0], [1, 0], [1, 1], [200, 0]],
        ],
    }
    with pytest.raises(PolygonValidationError, match="longitude"):
        validate_polygon(poly)


def test_validate_rejects_over_500_vertices() -> None:
    # 501 distinct vertices + closing duplicate = 502 — over the cap.
    coords = [[i / 100.0, 0.0] for i in range(501)]
    coords.append(coords[0])
    poly = {"type": "Polygon", "coordinates": [coords]}
    with pytest.raises(PolygonValidationError, match="500"):
        validate_polygon(poly)


# ---- compute_bbox ----


def test_compute_bbox_square() -> None:
    ring = validate_polygon(SQUARE)
    min_lat, max_lat, min_lon, max_lon = compute_bbox(ring)
    assert (min_lat, max_lat) == (37.78, 37.79)
    assert (min_lon, max_lon) == (-122.42, -122.41)


# ---- point_in_polygon ----


def test_point_inside_square() -> None:
    ring = validate_polygon(SQUARE)
    assert point_in_polygon(37.785, -122.415, ring) is True


def test_point_outside_square() -> None:
    ring = validate_polygon(SQUARE)
    assert point_in_polygon(40.0, -122.415, ring) is False
    assert point_in_polygon(37.785, -100.0, ring) is False


def test_concave_polygon_excludes_notch() -> None:
    # L-shape: large square with a bite taken out of the upper-right.
    poly = {
        "type": "Polygon",
        "coordinates": [
            [
                [0, 0],
                [4, 0],
                [4, 2],
                [2, 2],
                [2, 4],
                [0, 4],
                [0, 0],
            ]
        ],
    }
    ring = validate_polygon(poly)
    assert point_in_polygon(1, 1, ring) is True  # in the L
    assert point_in_polygon(3, 3, ring) is False  # in the notch
    assert point_in_polygon(3, 1, ring) is True  # in the foot


def test_point_on_vertex_or_edge_is_deterministic() -> None:
    """Edge/vertex behavior isn't strictly defined for ray-casting; just
    assert determinism — same input always returns the same answer.
    """
    ring = validate_polygon(SQUARE)
    a = point_in_polygon(37.78, -122.42, ring)
    b = point_in_polygon(37.78, -122.42, ring)
    assert a == b


# ---- bbox_contains ----


def test_bbox_contains_happy_path() -> None:
    assert bbox_contains(
        bbox_min_lat=0.0,
        bbox_max_lat=10.0,
        bbox_min_lon=0.0,
        bbox_max_lon=10.0,
        lat=5.0,
        lon=5.0,
    )


def test_bbox_contains_outside() -> None:
    assert not bbox_contains(
        bbox_min_lat=0.0,
        bbox_max_lat=10.0,
        bbox_min_lon=0.0,
        bbox_max_lon=10.0,
        lat=20.0,
        lon=5.0,
    )


def test_bbox_contains_handles_none() -> None:
    assert not bbox_contains(
        bbox_min_lat=None,
        bbox_max_lat=10.0,
        bbox_min_lon=0.0,
        bbox_max_lon=10.0,
        lat=5.0,
        lon=5.0,
    )
