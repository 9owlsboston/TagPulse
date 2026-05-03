"""TimescaleDB implementation of site and zone repositories (Sprint 15)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import SiteModel, ZoneModel
from tagpulse.models.schemas import (
    SiteCreate,
    SiteResponse,
    SiteUpdate,
    ZoneCreate,
    ZoneResponse,
    ZoneUpdate,
)


def _site_to_response(row: SiteModel) -> SiteResponse:
    return SiteResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        address=row.address,
        default_timezone=row.default_timezone,
        metadata=row.metadata_,
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
            [uuid.UUID(str(r)) for r in row.fixed_reader_ids]
            if row.fixed_reader_ids
            else None
        ),
        polygon_geojson=row.polygon_geojson,
        metadata=row.metadata_,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class TimescaleSiteRepository:
    """Persists sites to TimescaleDB."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, tenant_id: uuid.UUID, site: SiteCreate
    ) -> SiteResponse:
        row = SiteModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            name=site.name,
            address=site.address,
            default_timezone=site.default_timezone,
            metadata_=site.metadata,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ValueError(
                f"Site name '{site.name}' already exists for this tenant"
            ) from exc
        return _site_to_response(row)

    async def get(
        self, tenant_id: uuid.UUID, site_id: uuid.UUID
    ) -> SiteResponse | None:
        stmt = select(SiteModel).where(
            SiteModel.id == site_id, SiteModel.tenant_id == tenant_id
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _site_to_response(row) if row else None

    async def list(
        self, tenant_id: uuid.UUID, *, limit: int = 100, offset: int = 0
    ) -> list[SiteResponse]:
        stmt = (
            select(SiteModel)
            .where(SiteModel.tenant_id == tenant_id)
            .order_by(SiteModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [_site_to_response(row) for row in result.scalars()]

    async def update(
        self,
        tenant_id: uuid.UUID,
        site_id: uuid.UUID,
        patch: SiteUpdate,
    ) -> SiteResponse | None:
        stmt = select(SiteModel).where(
            SiteModel.id == site_id, SiteModel.tenant_id == tenant_id
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        patch_data = patch.model_dump(exclude_unset=True)
        if "metadata" in patch_data:
            patch_data["metadata_"] = patch_data.pop("metadata")
        for key, value in patch_data.items():
            setattr(row, key, value)
        await self._session.flush()
        return _site_to_response(row)

    async def delete(self, tenant_id: uuid.UUID, site_id: uuid.UUID) -> bool:
        stmt = select(SiteModel).where(
            SiteModel.id == site_id, SiteModel.tenant_id == tenant_id
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True


class TimescaleZoneRepository:
    """Persists zones to TimescaleDB."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, tenant_id: uuid.UUID, zone: ZoneCreate
    ) -> ZoneResponse:
        row = ZoneModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            site_id=zone.site_id,
            name=zone.name,
            kind=zone.kind,
            fixed_reader_ids=(
                [str(r) for r in zone.fixed_reader_ids]
                if zone.fixed_reader_ids
                else None
            ),
            polygon_geojson=zone.polygon_geojson,
            metadata_=zone.metadata,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ValueError(
                f"Zone '{zone.name}' already exists in this site"
            ) from exc
        return _zone_to_response(row)

    async def get(
        self, tenant_id: uuid.UUID, zone_id: uuid.UUID
    ) -> ZoneResponse | None:
        stmt = select(ZoneModel).where(
            ZoneModel.id == zone_id, ZoneModel.tenant_id == tenant_id
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _zone_to_response(row) if row else None

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        site_id: uuid.UUID | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[ZoneResponse]:
        stmt = select(ZoneModel).where(ZoneModel.tenant_id == tenant_id)
        if site_id is not None:
            stmt = stmt.where(ZoneModel.site_id == site_id)
        stmt = stmt.order_by(ZoneModel.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return [_zone_to_response(row) for row in result.scalars()]

    async def update(
        self,
        tenant_id: uuid.UUID,
        zone_id: uuid.UUID,
        patch: ZoneUpdate,
    ) -> ZoneResponse | None:
        stmt = select(ZoneModel).where(
            ZoneModel.id == zone_id, ZoneModel.tenant_id == tenant_id
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        patch_data = patch.model_dump(exclude_unset=True)
        if "metadata" in patch_data:
            patch_data["metadata_"] = patch_data.pop("metadata")
        if "fixed_reader_ids" in patch_data and patch_data["fixed_reader_ids"]:
            patch_data["fixed_reader_ids"] = [
                str(r) for r in patch_data["fixed_reader_ids"]
            ]
        for key, value in patch_data.items():
            setattr(row, key, value)
        await self._session.flush()
        return _zone_to_response(row)

    async def delete(self, tenant_id: uuid.UUID, zone_id: uuid.UUID) -> bool:
        stmt = select(ZoneModel).where(
            ZoneModel.id == zone_id, ZoneModel.tenant_id == tenant_id
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
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
