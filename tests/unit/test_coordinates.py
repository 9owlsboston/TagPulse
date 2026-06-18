"""Unit tests for the floor → geographic projection seam (Sprint 64)."""

from __future__ import annotations

import math

from tagpulse.core.coordinates import floor_to_geo
from tagpulse.models.schemas import CoordSystem


def _cs(**kw: object) -> CoordSystem:
    base: dict[str, object] = {"extent_x": 1000, "extent_y": 1000}
    base.update(kw)
    return CoordSystem.model_validate(base)


def test_returns_none_without_geo_anchor() -> None:
    assert floor_to_geo(10, 20, _cs()) is None


def test_anchor_point_maps_to_anchor_latlon() -> None:
    cs = _cs(geo_anchor={"lat": 47.6, "lng": -122.3, "x": 0, "y": 0})
    lat, lon = floor_to_geo(0, 0, cs)  # type: ignore[misc]
    assert math.isclose(lat, 47.6, abs_tol=1e-9)
    assert math.isclose(lon, -122.3, abs_tol=1e-9)


def test_north_offset_increases_latitude() -> None:
    # No rotation → +y is north. 111320 m ≈ 1° latitude.
    cs = _cs(geo_anchor={"lat": 0.0, "lng": 0.0, "x": 0, "y": 0})
    lat, lon = floor_to_geo(0, 111_320, cs)  # type: ignore[misc]
    assert math.isclose(lat, 1.0, abs_tol=1e-3)
    assert math.isclose(lon, 0.0, abs_tol=1e-6)


def test_east_offset_increases_longitude_scaled_by_latitude() -> None:
    # At the equator, 111320 m east ≈ 1° longitude.
    cs = _cs(geo_anchor={"lat": 0.0, "lng": 0.0, "x": 0, "y": 0})
    lat, lon = floor_to_geo(111_320, 0, cs)  # type: ignore[misc]
    assert math.isclose(lon, 1.0, abs_tol=1e-3)
    assert math.isclose(lat, 0.0, abs_tol=1e-6)


def test_feet_units_scale_offsets() -> None:
    cs = _cs(units="feet", geo_anchor={"lat": 0.0, "lng": 0.0, "x": 0, "y": 0})
    # 111320 m / 0.3048 ≈ 365223 ft north ≈ 1° latitude.
    lat, _lon = floor_to_geo(0, 111_320 / 0.3048, cs)  # type: ignore[misc]
    assert math.isclose(lat, 1.0, abs_tol=1e-3)


def test_rotation_90_maps_local_y_to_east() -> None:
    # rotation_deg = 90 → local +y points east. A +y offset should move
    # longitude, not latitude.
    cs = _cs(rotation_deg=90, geo_anchor={"lat": 0.0, "lng": 0.0, "x": 0, "y": 0})
    lat, lon = floor_to_geo(0, 111_320, cs)  # type: ignore[misc]
    assert math.isclose(lon, 1.0, abs_tol=1e-3)
    assert math.isclose(lat, 0.0, abs_tol=1e-6)
