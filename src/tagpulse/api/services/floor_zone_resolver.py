"""Floor-zone resolution for the tag-reads location descriptor (Sprint 64).

Encapsulates the **accurate D5 path**: a fixed read resolves to a floor zone by
point-in-polygon of its antenna's surveyed ``(x, y)`` against the device's
site's floor polygons. Used by :class:`~tagpulse.api.services.query_service.QueryService`
*before* the coarse ``reader_bound`` fallback. All lookups are cached for the
duration of a single query page so a 1000-row page does at most one lookup per
distinct device / site / ``(device, port)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class FloorRef:
    id: UUID
    name: str


class FloorZoneResolver:
    """Resolves a fixed read to a floor zone, or ``None`` to fall back."""

    def __init__(
        self, device_repo: object, site_repo: object, antenna_repo: object, zone_repo: object
    ) -> None:
        self._device_repo = device_repo
        self._site_repo = site_repo
        self._antenna_repo = antenna_repo
        self._zone_repo = zone_repo
        self._device_site: dict[UUID, UUID | None] = {}
        self._site_is_floor: dict[UUID, bool] = {}
        self._antenna_xy: dict[tuple[UUID, int], tuple[float, float] | None] = {}

    async def _site_for_device(self, tenant_id: UUID, device_id: UUID) -> UUID | None:
        if device_id not in self._device_site:
            device = await self._device_repo.get(tenant_id, device_id)  # type: ignore[attr-defined]
            self._device_site[device_id] = device.site_id if device else None
        return self._device_site[device_id]

    async def _is_floor_site(self, tenant_id: UUID, site_id: UUID) -> bool:
        if site_id not in self._site_is_floor:
            site = await self._site_repo.get(tenant_id, site_id)  # type: ignore[attr-defined]
            self._site_is_floor[site_id] = bool(site and site.coord_system is not None)
        return self._site_is_floor[site_id]

    async def _antenna_position(
        self, tenant_id: UUID, device_id: UUID, port: int
    ) -> tuple[float, float] | None:
        """Surveyed ``(x, y)`` for ``port``, falling back to port 0 (the reader's
        nominal location) per the port-0 model."""
        key = (device_id, port)
        if key not in self._antenna_xy:
            antennas = await self._antenna_repo.list_for_device(tenant_id, device_id)  # type: ignore[attr-defined]
            by_port = {a.port: a for a in (antennas or [])}
            chosen = by_port.get(port) or by_port.get(0)
            self._antenna_xy[key] = (
                (chosen.x, chosen.y)
                if chosen is not None and chosen.x is not None and chosen.y is not None
                else None
            )
        return self._antenna_xy[key]

    async def resolve(
        self, tenant_id: UUID, device_id: UUID, reader_antenna: int | None
    ) -> FloorRef | None:
        """Return the containing floor zone, or ``None`` to use the fallback."""
        site_id = await self._site_for_device(tenant_id, device_id)
        if site_id is None or not await self._is_floor_site(tenant_id, site_id):
            return None
        port = reader_antenna if reader_antenna is not None else 0
        xy = await self._antenna_position(tenant_id, device_id, port)
        if xy is None:
            return None
        zone = await self._zone_repo.get_floor_zone_for_point(  # type: ignore[attr-defined]
            tenant_id, site_id, xy[0], xy[1]
        )
        return FloorRef(id=zone.id, name=zone.name) if zone else None
