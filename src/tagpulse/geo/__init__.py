"""Pure-Python geospatial primitives for the TagPulse geofence engine.

Per [docs/design/geofencing-and-map.md §4](../../../docs/design/geofencing-and-map.md):
no PostGIS dependency in v1. We rely on a SQL bbox prefilter (cheap) plus an
in-process ray-casting test (≤500 vertices per polygon, ≤3 candidates after
prefilter is the design budget).

When real workloads break the budget, ADR-013 (PostGIS adoption) opens — the
trigger conditions are the OTel histograms instrumented in
``tagpulse.geo.metrics`` and consumed by Prometheus alerts.
"""

from __future__ import annotations

from typing import Any

# Latitude bound is ±90, longitude bound is ±180 (no antimeridian handling in v1).
_MIN_LAT = -90.0
_MAX_LAT = 90.0
_MIN_LON = -180.0
_MAX_LON = 180.0
_MAX_VERTICES = 500


class PolygonValidationError(ValueError):
    """Raised when a GeoJSON Polygon doesn't satisfy the v1 contract.

    Per design §3 the v1 contract is:

    - Single ring (no holes, no MultiPolygon).
    - First vertex equals last vertex.
    - ≤500 vertices.
    - All coordinates in valid lat/lon ranges.
    """


def validate_polygon(geojson: dict[str, Any]) -> list[tuple[float, float]]:
    """Validate a GeoJSON Polygon and return its ring as ``[(lon, lat), …]``.

    GeoJSON stores coordinates as ``[longitude, latitude]`` (RFC 7946 §3.1.1).
    """
    if not isinstance(geojson, dict):
        raise PolygonValidationError("polygon_geojson must be an object")
    if geojson.get("type") != "Polygon":
        raise PolygonValidationError(
            "polygon_geojson.type must be 'Polygon' (MultiPolygon not supported in v1)"
        )
    coords = geojson.get("coordinates")
    if not isinstance(coords, list) or len(coords) != 1:
        raise PolygonValidationError(
            "polygon_geojson.coordinates must be a single linear ring (no holes in v1)"
        )
    ring = coords[0]
    if not isinstance(ring, list) or len(ring) < 4:
        raise PolygonValidationError(
            "polygon ring must have at least 4 vertices (3 corners + closing vertex)"
        )
    if len(ring) > _MAX_VERTICES:
        raise PolygonValidationError(f"polygon ring exceeds {_MAX_VERTICES}-vertex cap")
    out: list[tuple[float, float]] = []
    for i, vertex in enumerate(ring):
        if (
            not isinstance(vertex, list | tuple)
            or len(vertex) < 2
            or not isinstance(vertex[0], int | float)
            or not isinstance(vertex[1], int | float)
        ):
            raise PolygonValidationError(f"polygon vertex {i} must be a [lon, lat] pair")
        lon = float(vertex[0])
        lat = float(vertex[1])
        if not (_MIN_LON <= lon <= _MAX_LON):
            raise PolygonValidationError(f"polygon vertex {i} longitude {lon} out of range")
        if not (_MIN_LAT <= lat <= _MAX_LAT):
            raise PolygonValidationError(f"polygon vertex {i} latitude {lat} out of range")
        out.append((lon, lat))
    if out[0] != out[-1]:
        raise PolygonValidationError("polygon ring must be closed (first vertex == last vertex)")
    return out


def compute_bbox(
    ring: list[tuple[float, float]],
) -> tuple[float, float, float, float]:
    """Return ``(min_lat, max_lat, min_lon, max_lon)`` for a validated ring."""
    lons = [lon for lon, _ in ring]
    lats = [lat for _, lat in ring]
    return (min(lats), max(lats), min(lons), max(lons))


def point_in_polygon(lat: float, lon: float, ring: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon test.

    Returns ``True`` for points strictly inside or on the boundary of the
    polygon. The closing duplicate vertex is ignored (we iterate edges
    ``ring[i] -> ring[i+1]``).
    """
    inside = False
    n = len(ring) - 1  # last == first; iterate n edges
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]  # (lon, lat)
        xj, yj = ring[j]
        # Edge crosses the horizontal ray pointing east from (lon, lat)?
        if (yi > lat) != (yj > lat):
            x_intersect = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_intersect:
                inside = not inside
        j = i
    return inside


def bbox_contains(
    *,
    bbox_min_lat: float | None,
    bbox_max_lat: float | None,
    bbox_min_lon: float | None,
    bbox_max_lon: float | None,
    lat: float,
    lon: float,
) -> bool:
    """Cheap bbox prefilter; ``True`` if the point falls within the bbox."""
    if bbox_min_lat is None or bbox_max_lat is None or bbox_min_lon is None or bbox_max_lon is None:
        return False
    return bbox_min_lat <= lat <= bbox_max_lat and bbox_min_lon <= lon <= bbox_max_lon


__all__ = [
    "PolygonValidationError",
    "bbox_contains",
    "compute_bbox",
    "point_in_polygon",
    "validate_polygon",
]
