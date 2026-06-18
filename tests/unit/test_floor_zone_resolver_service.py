"""Unit tests for the FloorZoneResolver (Sprint 64 follow-up)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from tagpulse.api.services.floor_zone_resolver import FloorZoneResolver

TENANT = uuid4()


@dataclass
class _Device:
    site_id: UUID | None


@dataclass
class _Site:
    coord_system: dict | None


@dataclass
class _Antenna:
    port: int
    x: float | None
    y: float | None


@dataclass
class _Zone:
    id: UUID
    name: str


class _FakeDeviceRepo:
    def __init__(self, site_id: UUID | None) -> None:
        self.site_id = site_id
        self.calls = 0

    async def get(self, tenant_id: UUID, device_id: UUID) -> _Device | None:
        self.calls += 1
        return _Device(site_id=self.site_id)


class _FakeSiteRepo:
    def __init__(self, coord_system: dict | None) -> None:
        self.coord_system = coord_system

    async def get(self, tenant_id: UUID, site_id: UUID) -> _Site:
        return _Site(coord_system=self.coord_system)


class _FakeAntennaRepo:
    def __init__(self, antennas: list[_Antenna]) -> None:
        self.antennas = antennas

    async def list_for_device(self, tenant_id: UUID, device_id: UUID) -> list[_Antenna]:
        return self.antennas


class _FakeZoneRepo:
    def __init__(self, zone: _Zone | None) -> None:
        self.zone = zone
        self.last_xy: tuple[float, float] | None = None

    async def get_floor_zone_for_point(
        self, tenant_id: UUID, site_id: UUID, x: float, y: float
    ) -> _Zone | None:
        self.last_xy = (x, y)
        return self.zone


def _resolver(
    *,
    site_id: UUID | None,
    coord_system: dict | None,
    antennas: list[_Antenna],
    zone: _Zone | None,
) -> tuple[FloorZoneResolver, _FakeDeviceRepo, _FakeZoneRepo]:
    drepo = _FakeDeviceRepo(site_id)
    zrepo = _FakeZoneRepo(zone)
    r = FloorZoneResolver(
        device_repo=drepo,
        site_repo=_FakeSiteRepo(coord_system),
        antenna_repo=_FakeAntennaRepo(antennas),
        zone_repo=zrepo,
    )
    return r, drepo, zrepo


SITE = uuid4()
ZONE = _Zone(id=uuid4(), name="Bay A")


class TestFloorZoneResolver:
    async def test_resolves_floor_zone(self) -> None:
        r, _d, z = _resolver(
            site_id=SITE,
            coord_system={"extent_x": 10, "extent_y": 10},
            antennas=[_Antenna(port=0, x=5, y=5)],
            zone=ZONE,
        )
        ref = await r.resolve(TENANT, uuid4(), None)
        assert ref is not None and ref.name == "Bay A"
        assert z.last_xy == (5, 5)

    async def test_uses_specific_port_then_falls_back_to_port0(self) -> None:
        r, _d, z = _resolver(
            site_id=SITE,
            coord_system={"extent_x": 10, "extent_y": 10},
            antennas=[_Antenna(port=0, x=1, y=1), _Antenna(port=3, x=8, y=9)],
            zone=ZONE,
        )
        await r.resolve(TENANT, uuid4(), 3)
        assert z.last_xy == (8, 9)  # used port 3, not port 0

    async def test_none_without_site(self) -> None:
        r, _d, _z = _resolver(site_id=None, coord_system=None, antennas=[], zone=ZONE)
        assert await r.resolve(TENANT, uuid4(), None) is None

    async def test_none_when_site_not_floor(self) -> None:
        r, _d, _z = _resolver(
            site_id=SITE, coord_system=None, antennas=[_Antenna(0, 5, 5)], zone=ZONE
        )
        assert await r.resolve(TENANT, uuid4(), None) is None

    async def test_none_when_antenna_unsurveyed(self) -> None:
        r, _d, _z = _resolver(
            site_id=SITE,
            coord_system={"extent_x": 10, "extent_y": 10},
            antennas=[_Antenna(port=0, x=None, y=None)],
            zone=ZONE,
        )
        assert await r.resolve(TENANT, uuid4(), None) is None

    async def test_device_lookup_cached(self) -> None:
        r, d, _z = _resolver(
            site_id=SITE,
            coord_system={"extent_x": 10, "extent_y": 10},
            antennas=[_Antenna(0, 5, 5)],
            zone=ZONE,
        )
        did = uuid4()
        await r.resolve(TENANT, did, None)
        await r.resolve(TENANT, did, None)
        assert d.calls == 1
