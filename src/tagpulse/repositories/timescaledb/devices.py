"""TimescaleDB implementation of the DeviceRepository protocol."""

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.api.label_filter import apply_label_filter
from tagpulse.core.device_status import effective_connection_state
from tagpulse.models.database import DeviceModel, SiteModel
from tagpulse.models.schemas import DeviceCreate, DeviceResponse, DeviceUpdate


class TimescaleDeviceRepository:
    """Persists device registry data to TimescaleDB/PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, tenant_id: uuid.UUID, device: DeviceCreate) -> DeviceResponse:
        if device.site_id is not None:
            await self._assert_site_in_tenant(tenant_id, device.site_id)
        row = DeviceModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            name=device.name,
            device_type=device.device_type,
            metadata_=device.metadata,
            configuration=device.configuration,
            firmware_version=device.firmware_version,
            site_id=device.site_id,
        )
        self._session.add(row)
        await self._session.flush()
        return _to_response(row)

    async def _assert_site_in_tenant(self, tenant_id: uuid.UUID, site_id: uuid.UUID) -> None:
        """Guard tenant isolation: a device's ``site_id`` must be the caller's."""
        stmt = select(SiteModel.id).where(SiteModel.id == site_id, SiteModel.tenant_id == tenant_id)
        result = await self._session.execute(stmt)
        if result.scalar_one_or_none() is None:
            raise ValueError(f"Site {site_id} not found for this tenant")

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
        labels: dict[str, list[str]] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DeviceResponse]:
        stmt = select(DeviceModel).where(DeviceModel.tenant_id == tenant_id)
        if status is not None:
            stmt = stmt.where(DeviceModel.status == status)
        if device_type is not None:
            stmt = stmt.where(DeviceModel.device_type == device_type)
        stmt = apply_label_filter(
            stmt,
            tenant_id=tenant_id,
            entity_type="device",
            entity_id_col=DeviceModel.id,
            labels=labels,
        )
        stmt = stmt.order_by(DeviceModel.created_at.desc()).limit(limit).offset(offset)
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
        if patch_data.get("site_id") is not None:
            await self._assert_site_in_tenant(tenant_id, patch_data["site_id"])
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

    async def record_connection_state(
        self, tenant_id: uuid.UUID, device_id: uuid.UUID, connection_state: str
    ) -> None:
        stmt = (
            update(DeviceModel)
            .where(DeviceModel.id == device_id, DeviceModel.tenant_id == tenant_id)
            .values(connection_state=connection_state)
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
        # Resolve the *effective* online status from freshness: a stored
        # ``online`` whose ``last_seen`` has gone stale reads as ``offline``
        # (the column drifts when a disconnect is missed). Shared with the
        # dashboard "Readers online" tile so the card and the Readers page agree.
        connection_state=effective_connection_state(row.connection_state, row.last_seen),
        last_seen=row.last_seen,
        mobility=row.mobility,
        site_id=row.site_id,
        token_prefix=row.token_prefix,
        token_rotated_at=row.token_rotated_at,
        cert_thumbprint=row.cert_thumbprint,
        cert_subject=row.cert_subject,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
