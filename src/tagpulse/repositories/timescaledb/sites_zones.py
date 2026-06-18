"""TimescaleDB implementation of site and zone repositories (Sprint 15)."""

from __future__ import annotations

import builtins
import time
import uuid
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.api.label_filter import apply_label_filter
from tagpulse.geo import (
    PolygonValidationError,
    bbox_contains,
    compute_bbox,
    point_in_polygon,
    validate_polygon,
)
from tagpulse.models.database import SiteModel, ZoneModel
from tagpulse.models.schemas import (
    CoordSystem,
    SiteCreate,
    SiteKind,
    SiteResponse,
    SiteUpdate,
    ZoneCreate,
    ZoneResponse,
    ZoneUpdate,
)
from tagpulse.repositories.timescaledb.labels import TimescaleLabelRepository

# Sprint 17a §4.1: tenant-level cache of all geofence zones (TTL 30s).
# Zones change rarely, so caching the *full* geofence list per tenant lets the
# hot tag-read path skip SQL entirely on cache hit and run the bbox prefilter
# in-process. Tradeoff: a polygon edit takes up to 30s to propagate; acceptable
# for v1 — zone edits are rare admin actions, not high-frequency events.
_GEOFENCE_CACHE_TTL_S = 30.0
_GEOFENCE_CACHE_MAX_TENANTS = 1024
_GEOFENCE_CACHE: dict[uuid.UUID, tuple[float, list[ZoneResponse]]] = {}


def _geofence_cache_invalidate(tenant_id: uuid.UUID) -> None:
    """Invalidate the geofence list cache for ``tenant_id``.

    Called on zone create/update/delete so a polygon edit takes effect on the
    next ingest tick instead of waiting for the TTL.
    """
    _GEOFENCE_CACHE.pop(tenant_id, None)


def _bbox_for(
    polygon: dict[str, Any] | None,
) -> tuple[float, float, float, float] | None:
    """Validate ``polygon`` and return its bbox, or None if no polygon set.

    Raises ``ValueError`` (via ``PolygonValidationError``) on invalid input so
    the API surface returns a 422 with a descriptive message instead of a 500.
    """
    if polygon is None:
        return None
    try:
        ring = validate_polygon(polygon)
    except PolygonValidationError:
        raise
    return compute_bbox(ring)


def _site_to_response(row: SiteModel) -> SiteResponse:
    return SiteResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        # DB CHECK ck_sites_kind guarantees one of the SiteKind literals.
        kind=cast(SiteKind, row.kind),
        address=row.address,
        street_line1=row.street_line1,
        street_line2=row.street_line2,
        city=row.city,
        region=row.region,
        postal_code=row.postal_code,
        country=row.country,
        latitude=row.latitude,
        longitude=row.longitude,
        default_timezone=row.default_timezone,
        metadata=row.metadata_,
        coord_system=(CoordSystem.model_validate(row.coord_system) if row.coord_system else None),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _zone_to_response(row: ZoneModel) -> ZoneResponse:
    return ZoneResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        site_id=row.site_id,
        name=row.name,
        kind=row.kind,
        fixed_reader_ids=(
            [uuid.UUID(str(r)) for r in row.fixed_reader_ids] if row.fixed_reader_ids else None
        ),
        polygon_geojson=row.polygon_geojson,
        bbox_min_lat=row.bbox_min_lat,
        bbox_max_lat=row.bbox_max_lat,
        bbox_min_lon=row.bbox_min_lon,
        bbox_max_lon=row.bbox_max_lon,
        metadata=row.metadata_,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _polygon_ring(polygon: dict[str, Any] | None) -> builtins.list[tuple[float, float]] | None:
    """Extract a GeoJSON polygon's outer ring as ``(x, y)`` tuples (or None)."""
    if not polygon:
        return None
    coords = polygon.get("coordinates")
    if not coords:
        return None
    ring = coords[0]
    if not ring or len(ring) < 4:  # need ≥3 distinct vertices + closing point
        return None
    return [(float(p[0]), float(p[1])) for p in ring]


class TimescaleSiteRepository:
    """Persists sites to TimescaleDB."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, tenant_id: uuid.UUID, site: SiteCreate) -> SiteResponse:
        row = SiteModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            name=site.name,
            kind=site.kind,
            address=site.address,
            street_line1=site.street_line1,
            street_line2=site.street_line2,
            city=site.city,
            region=site.region,
            postal_code=site.postal_code,
            country=site.country,
            latitude=site.latitude,
            longitude=site.longitude,
            default_timezone=site.default_timezone,
            metadata_=site.metadata,
            coord_system=(site.coord_system.model_dump(mode="json") if site.coord_system else None),
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ValueError(f"Site name '{site.name}' already exists for this tenant") from exc
        return _site_to_response(row)

    async def get(self, tenant_id: uuid.UUID, site_id: uuid.UUID) -> SiteResponse | None:
        stmt = select(SiteModel).where(SiteModel.id == site_id, SiteModel.tenant_id == tenant_id)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _site_to_response(row) if row else None

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        labels: dict[str, list[str]] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SiteResponse]:
        stmt = select(SiteModel).where(SiteModel.tenant_id == tenant_id)
        stmt = apply_label_filter(
            stmt,
            tenant_id=tenant_id,
            entity_type="site",
            entity_id_col=SiteModel.id,
            labels=labels,
        )
        stmt = stmt.order_by(SiteModel.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return [_site_to_response(row) for row in result.scalars()]

    async def update(
        self,
        tenant_id: uuid.UUID,
        site_id: uuid.UUID,
        patch: SiteUpdate,
    ) -> SiteResponse | None:
        stmt = select(SiteModel).where(SiteModel.id == site_id, SiteModel.tenant_id == tenant_id)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        patch_data = patch.model_dump(exclude_unset=True)
        if "metadata" in patch_data:
            patch_data["metadata_"] = patch_data.pop("metadata")
        if "coord_system" in patch_data:
            # JSONB column needs a JSON-safe dict (UUID origin_device_id → str);
            # explicit null clears the frame back to geographic-only.
            patch_data["coord_system"] = (
                patch.coord_system.model_dump(mode="json") if patch.coord_system else None
            )
        for key, value in patch_data.items():
            setattr(row, key, value)
        await self._session.flush()
        return _site_to_response(row)

    async def delete(self, tenant_id: uuid.UUID, site_id: uuid.UUID) -> bool:
        stmt = select(SiteModel).where(SiteModel.id == site_id, SiteModel.tenant_id == tenant_id)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return False
        # ADR-020 Phase B: drop orphan entity_labels rows before the
        # site row goes away. Without this, count_associations() on
        # the parent label keeps inflating and DELETE /labels/{id}
        # would block forever with an unresolvable 409.
        await TimescaleLabelRepository(self._session).delete_for_entity(tenant_id, "site", site_id)
        await self._session.delete(row)
        await self._session.flush()
        return True


class TimescaleZoneRepository:
    """Persists zones to TimescaleDB."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, tenant_id: uuid.UUID, zone: ZoneCreate) -> ZoneResponse:
        bbox = _bbox_for(zone.polygon_geojson)
        row = ZoneModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            site_id=zone.site_id,
            name=zone.name,
            kind=zone.kind,
            fixed_reader_ids=(
                [str(r) for r in zone.fixed_reader_ids] if zone.fixed_reader_ids else None
            ),
            polygon_geojson=zone.polygon_geojson,
            bbox_min_lat=bbox[0] if bbox else None,
            bbox_max_lat=bbox[1] if bbox else None,
            bbox_min_lon=bbox[2] if bbox else None,
            bbox_max_lon=bbox[3] if bbox else None,
            metadata_=zone.metadata,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ValueError(f"Zone '{zone.name}' already exists in this site") from exc
        _geofence_cache_invalidate(tenant_id)
        return _zone_to_response(row)

    async def get(self, tenant_id: uuid.UUID, zone_id: uuid.UUID) -> ZoneResponse | None:
        stmt = select(ZoneModel).where(ZoneModel.id == zone_id, ZoneModel.tenant_id == tenant_id)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _zone_to_response(row) if row else None

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        site_id: uuid.UUID | None = None,
        labels: dict[str, list[str]] | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[ZoneResponse]:
        stmt = select(ZoneModel).where(ZoneModel.tenant_id == tenant_id)
        if site_id is not None:
            stmt = stmt.where(ZoneModel.site_id == site_id)
        stmt = apply_label_filter(
            stmt,
            tenant_id=tenant_id,
            entity_type="zone",
            entity_id_col=ZoneModel.id,
            labels=labels,
        )
        stmt = stmt.order_by(ZoneModel.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return [_zone_to_response(row) for row in result.scalars()]

    async def update(
        self,
        tenant_id: uuid.UUID,
        zone_id: uuid.UUID,
        patch: ZoneUpdate,
    ) -> ZoneResponse | None:
        stmt = select(ZoneModel).where(ZoneModel.id == zone_id, ZoneModel.tenant_id == tenant_id)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        patch_data = patch.model_dump(exclude_unset=True)
        if "metadata" in patch_data:
            patch_data["metadata_"] = patch_data.pop("metadata")
        if "fixed_reader_ids" in patch_data and patch_data["fixed_reader_ids"]:
            patch_data["fixed_reader_ids"] = [str(r) for r in patch_data["fixed_reader_ids"]]
        if "polygon_geojson" in patch_data:
            bbox = _bbox_for(patch_data["polygon_geojson"])
            patch_data["bbox_min_lat"] = bbox[0] if bbox else None
            patch_data["bbox_max_lat"] = bbox[1] if bbox else None
            patch_data["bbox_min_lon"] = bbox[2] if bbox else None
            patch_data["bbox_max_lon"] = bbox[3] if bbox else None
        for key, value in patch_data.items():
            setattr(row, key, value)
        await self._session.flush()
        _geofence_cache_invalidate(tenant_id)
        return _zone_to_response(row)

    async def delete(self, tenant_id: uuid.UUID, zone_id: uuid.UUID) -> bool:
        stmt = select(ZoneModel).where(ZoneModel.id == zone_id, ZoneModel.tenant_id == tenant_id)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return False
        # ADR-020 Phase B: drop orphan entity_labels rows before the
        # zone row goes away (see Site.delete for the same pattern).
        await TimescaleLabelRepository(self._session).delete_for_entity(tenant_id, "zone", zone_id)
        await self._session.delete(row)
        await self._session.flush()
        _geofence_cache_invalidate(tenant_id)
        return True

    async def get_zone_for_reader(
        self, tenant_id: uuid.UUID, device_id: uuid.UUID
    ) -> ZoneResponse | None:
        """Return the (deterministically-oldest) reader-bound zone for a device.

        Implements the "one zone per reader" rule from
        `docs/design/assets-and-zones.md` §11 Q4: if multiple zones list the
        same reader, return the one with the lowest ``created_at``.
        """
        device_str = str(device_id)
        stmt = (
            select(ZoneModel)
            .where(
                ZoneModel.tenant_id == tenant_id,
                ZoneModel.kind == "reader_bound",
                ZoneModel.fixed_reader_ids.contains([device_str]),
            )
            .order_by(ZoneModel.created_at.asc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _zone_to_response(row) if row else None

    async def find_geofence_candidates(
        self, tenant_id: uuid.UUID, lat: float, lon: float
    ) -> builtins.list[ZoneResponse]:
        """Bbox prefilter for the geofence engine (Sprint 17a §4.1).

        Cached at tenant level (TTL 30s, per design §4.1): the *full* set of
        geofence zones is loaded once per tenant per TTL, then bbox prefilter
        runs in-process. SQL is bypassed entirely on cache hit. Cache is
        invalidated synchronously by ``create``/``update``/``delete``.
        """
        now = time.monotonic()
        cached = _GEOFENCE_CACHE.get(tenant_id)
        if cached is not None and (now - cached[0]) < _GEOFENCE_CACHE_TTL_S:
            zones = cached[1]
        else:
            stmt = (
                select(ZoneModel)
                .where(
                    ZoneModel.tenant_id == tenant_id,
                    ZoneModel.kind == "geofence",
                    ZoneModel.polygon_geojson.is_not(None),
                )
                .order_by(ZoneModel.created_at.asc())
            )
            result = await self._session.execute(stmt)
            zones = [_zone_to_response(row) for row in result.scalars()]
            # Bounded LRU-ish: drop the oldest tenant entry when we hit the cap.
            if (
                tenant_id not in _GEOFENCE_CACHE
                and len(_GEOFENCE_CACHE) >= _GEOFENCE_CACHE_MAX_TENANTS
            ):
                try:
                    oldest = next(iter(_GEOFENCE_CACHE))
                    _GEOFENCE_CACHE.pop(oldest, None)
                except StopIteration:  # pragma: no cover
                    pass
            _GEOFENCE_CACHE[tenant_id] = (now, zones)

        return [
            z
            for z in zones
            if bbox_contains(
                lat=lat,
                lon=lon,
                bbox_min_lat=z.bbox_min_lat,
                bbox_max_lat=z.bbox_max_lat,
                bbox_min_lon=z.bbox_min_lon,
                bbox_max_lon=z.bbox_max_lon,
            )
        ]

    async def get_floor_zone_for_point(
        self, tenant_id: uuid.UUID, site_id: uuid.UUID, x: float, y: float
    ) -> ZoneResponse | None:
        """Resolve the floor zone containing a floor-local point ``(x, y)``.

        The accurate D5 path: on a site with a ``coord_system``, a zone's
        ``polygon_geojson`` is interpreted as **floor coordinates**, and the
        coordinate-agnostic ray-casting :func:`point_in_polygon` engine finds the
        containing zone. Lowest ``created_at`` wins (mirrors
        :meth:`get_zone_for_reader` determinism). No bbox prefilter (floor sites
        have few zones; a full scan is cheap — see the design doc).
        """
        stmt = (
            select(ZoneModel)
            .where(
                ZoneModel.tenant_id == tenant_id,
                ZoneModel.site_id == site_id,
                ZoneModel.polygon_geojson.is_not(None),
            )
            .order_by(ZoneModel.created_at.asc())
        )
        result = await self._session.execute(stmt)
        for row in result.scalars():
            ring = _polygon_ring(row.polygon_geojson)
            # point_in_polygon treats ring as (x, y) pairs and args as (y, x);
            # in floor space x≡lon, y≡lat, so this is a direct floor test.
            if ring and point_in_polygon(y, x, ring):
                return _zone_to_response(row)
        return None
