"""Floor-coordinate ↔ geographic projection (Sprint 64 / ADR-024 seam).

``floor_to_geo`` projects a floor-local ``(x, y)`` to ``(lat, lon)`` using a
site's :class:`~tagpulse.models.schemas.CoordSystem` geo-anchor. This is the
**seam** for a future unified geographic overlay (mobile + fixed readers on one
map): it ships and is tested now, but no map consumes it yet. The underlying
antenna/reader/asset coordinates never change — only this projection is added
when the unified view is built.

The math is an equirectangular (local-tangent-plane) approximation, which is
accurate to well under a metre at warehouse/campus scales (sub-km offsets).
"""

from __future__ import annotations

import math

from tagpulse.models.schemas import CoordSystem

# WGS84 mean metres per degree of latitude.
_METERS_PER_DEG_LAT = 111_320.0
_FEET_TO_METERS = 0.3048


def floor_to_geo(x: float, y: float, coord_system: CoordSystem) -> tuple[float, float] | None:
    """Project floor-local ``(x, y)`` to ``(lat, lon)``.

    Returns ``None`` when the site has no ``geo_anchor`` (it is geographic-only
    or floor-only and cannot be placed on a world map).

    Convention: the floor frame has ``+x`` right and ``+y`` up on the plan;
    ``geo_anchor`` pins ``(geo_anchor.x, geo_anchor.y)`` to ``(lat, lng)``;
    ``rotation_deg`` is the bearing of the floor ``+y`` axis clockwise from true
    north.
    """
    anchor = coord_system.geo_anchor
    if anchor is None:
        return None

    # Offset from the anchor in floor units, then to metres.
    dx = x - anchor.x
    dy = y - anchor.y
    if coord_system.units == "feet":
        dx *= _FEET_TO_METERS
        dy *= _FEET_TO_METERS

    # Rotate the local frame into the geographic (east, north) frame. The local
    # +y axis points at bearing θ from north; +x at θ + 90°.
    theta = math.radians(coord_system.rotation_deg)
    sin_t = math.sin(theta)
    cos_t = math.cos(theta)
    east_m = dy * sin_t + dx * cos_t
    north_m = dy * cos_t - dx * sin_t

    d_lat = north_m / _METERS_PER_DEG_LAT
    meters_per_deg_lon = _METERS_PER_DEG_LAT * math.cos(math.radians(anchor.lat))
    # Guard the degenerate near-pole case (warehouses are never there, but keep
    # the function total rather than dividing by ~0).
    d_lon = east_m / meters_per_deg_lon if abs(meters_per_deg_lon) > 1e-9 else 0.0

    return (anchor.lat + d_lat, anchor.lng + d_lon)
