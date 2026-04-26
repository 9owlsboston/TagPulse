"""TimescaleDB implementation of the DeviceRepository protocol."""

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import DeviceModel
from tagpulse.models.schemas import DeviceCreate, DeviceResponse, DeviceUpdate


class TimescaleDeviceRepository:
    """Persists device registry data to TimescaleDB/PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, tenant_id: uuid.UUID, device: DeviceCreate) -> DeviceResponse:
        row = DeviceModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            name=device.name,
            device_type=device.device_type,
            metadata_=device.metadata,
            configuration=device.configuration,
            firmware_version=device.firmware_version,
        )
        self._session.add(row)
        await self._session.flush()
        return _to_response(row)

    async def get(self, tenant_id: uuid.UUID, device_id: uuid.UUID) -> DeviceResponse | None:
        stmt = select(DeviceModel).where(
            DeviceModel.id == device_id, DeviceModel.tenant_id == tenant_id
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _to_response(row) if row else None

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        status: str | None = None,
        device_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DeviceResponse]:
        stmt = select(DeviceModel).where(
            DeviceModel.tenant_id == tenant_id
        ).order_by(DeviceModel.created_at.desc())
        if status is not None:
            stmt = stmt.where(DeviceModel.status == status)
        if device_type is not None:
            stmt = stmt.where(DeviceModel.device_type == device_type)
        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return [_to_response(row) for row in result.scalars()]

    async def update(
        self, tenant_id: uuid.UUID, device_id: uuid.UUID, patch: DeviceUpdate
    ) -> DeviceResponse | None:
        stmt = select(DeviceModel).where(
            DeviceModel.id == device_id, DeviceModel.tenant_id == tenant_id
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
        return _to_response(row)

    async def decommission(
        self, tenant_id: uuid.UUID, device_id: uuid.UUID
    ) -> DeviceResponse | None:
        stmt = select(DeviceModel).where(
            DeviceModel.id == device_id, DeviceModel.tenant_id == tenant_id
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        row.status = "decommissioned"
        row.connection_state = "offline"
        await self._session.flush()
        return _to_response(row)

    async def update_status(
        self,
        tenant_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        connection_state: str,
        firmware_version: str | None = None,
    ) -> DeviceResponse | None:
        stmt = select(DeviceModel).where(
            DeviceModel.id == device_id, DeviceModel.tenant_id == tenant_id
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        row.connection_state = connection_state
        if firmware_version is not None:
            row.firmware_version = firmware_version
        await self._session.flush()
        return _to_response(row)

    async def record_last_seen(
        self, tenant_id: uuid.UUID, device_id: uuid.UUID, seen_at: datetime
    ) -> None:
        stmt = (
            update(DeviceModel)
            .where(DeviceModel.id == device_id, DeviceModel.tenant_id == tenant_id)
            .values(last_seen=seen_at)
        )
        await self._session.execute(stmt)


def _to_response(row: DeviceModel) -> DeviceResponse:
    return DeviceResponse(
        id=row.id,
        name=row.name,
        device_type=row.device_type,
        status=row.status,
        metadata=row.metadata_,
        configuration=row.configuration,
        firmware_version=row.firmware_version,
        connection_state=row.connection_state,
        last_seen=row.last_seen,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
