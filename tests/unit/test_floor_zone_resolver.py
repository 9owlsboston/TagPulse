"""Unit tests for the floor-polygon zone resolver (Sprint 64 follow-up)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from tagpulse.models.database import ZoneModel
from tagpulse.repositories.timescaledb.sites_zones import (
    TimescaleZoneRepository,
    _polygon_ring,
)

TENANT = uuid4()
SITE = uuid4()

# A 10×10 square in floor coordinates.
_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
}
# A square offset to (20..30, 20..30).
_FAR = {
    "type": "Polygon",
    "coordinates": [[[20, 20], [30, 20], [30, 30], [20, 30], [20, 20]]],
}


def _zone(polygon: dict[str, Any], *, name: str, order: int) -> ZoneModel:
    z = ZoneModel()
    z.id = uuid4()
    z.tenant_id = TENANT
    z.site_id = SITE
    z.name = name
    z.kind = "geofence"
    z.fixed_reader_ids = None
    z.polygon_geojson = polygon
    z.bbox_min_lat = None
    z.bbox_max_lat = None
    z.bbox_min_lon = None
    z.bbox_max_lon = None
    z.metadata_ = None
    z.created_at = datetime(2026, 1, 1, tzinfo=UTC).replace(minute=order)
    z.updated_at = z.created_at
    return z


class _FakeResult:
    def __init__(self, rows: list[ZoneModel]) -> None:
        self._rows = rows

    def scalars(self) -> list[ZoneModel]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[ZoneModel]) -> None:
        self._rows = rows

    async def execute(self, _stmt: Any) -> _FakeResult:
        return _FakeResult(self._rows)


def _repo(rows: list[ZoneModel]) -> TimescaleZoneRepository:
    return TimescaleZoneRepository(_FakeSession(rows))  # type: ignore[arg-type]


class TestPolygonRing:
    def test_extracts_xy_ring(self) -> None:
        assert _polygon_ring(_SQUARE) == [
            (0.0, 0.0),
            (10.0, 0.0),
            (10.0, 10.0),
            (0.0, 10.0),
            (0.0, 0.0),
        ]

    def test_none_for_missing_or_degenerate(self) -> None:
        assert _polygon_ring(None) is None
        assert _polygon_ring({}) is None
        assert _polygon_ring({"type": "Polygon", "coordinates": [[[0, 0], [1, 1]]]}) is None


class TestGetFloorZoneForPoint:
    async def test_point_inside_resolves_zone(self) -> None:
        repo = _repo([_zone(_SQUARE, name="Bay A", order=1)])
        z = await repo.get_floor_zone_for_point(TENANT, SITE, 5, 5)
        assert z is not None
        assert z.name == "Bay A"

    async def test_point_outside_returns_none(self) -> None:
        repo = _repo([_zone(_SQUARE, name="Bay A", order=1)])
        assert await repo.get_floor_zone_for_point(TENANT, SITE, 50, 50) is None

    async def test_point_only_in_far_zone(self) -> None:
        repo = _repo([_zone(_SQUARE, name="Bay A", order=1), _zone(_FAR, name="Bay B", order=2)])
        z = await repo.get_floor_zone_for_point(TENANT, SITE, 25, 25)
        assert z is not None
        assert z.name == "Bay B"

    async def test_oldest_wins_on_overlap(self) -> None:
        # Two identical squares; the first (lowest created_at) wins.
        repo = _repo([_zone(_SQUARE, name="Older", order=1), _zone(_SQUARE, name="Newer", order=2)])
        z = await repo.get_floor_zone_for_point(TENANT, SITE, 5, 5)
        assert z is not None
        assert z.name == "Older"
