"""Device self-registration and provisioning endpoints."""

import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.models.database import DeviceModel, TenantModel
from tagpulse.repositories.timescaledb.session import get_session

router = APIRouter(tags=["provisioning"])

provisioning_key_header = APIKeyHeader(
    name="X-Provisioning-Key", auto_error=False
)


class ProvisionRequest(BaseModel):
    """Device self-registration request."""

    name: str = Field(min_length=1, max_length=255)
    device_type: str = Field(default="rfid_reader", max_length=50)


class ProvisionStatusResponse(BaseModel):
    """Device provisioning status."""

    device_name: str
    status: str


@router.post("/devices/provision", status_code=201)
async def provision_device(
    body: ProvisionRequest,
    key: str | None = Security(provisioning_key_header),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Self-register a device using a tenant provisioning key."""
    if not key:
        raise HTTPException(
            status_code=401, detail="X-Provisioning-Key required"
        ) from None

    # Find tenant by provisioning key
    prefix = key[:10]
    stmt = select(TenantModel).where(
        TenantModel.provisioning_key_prefix == prefix,
        TenantModel.status == "active",
    )
    result = await session.execute(stmt)
    tenant = result.scalar_one_or_none()

    if tenant is None:
        raise HTTPException(
            status_code=401, detail="Invalid provisioning key"
        ) from None

    key_hash = hashlib.sha256(key.encode()).hexdigest()
    if tenant.provisioning_key_hash != key_hash:
        raise HTTPException(
            status_code=401, detail="Invalid provisioning key"
        ) from None

    # Create device with pending status
    device = DeviceModel(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name=body.name,
        device_type=body.device_type,
        status="pending",
    )
    session.add(device)
    await session.flush()

    return {
        "device_id": str(device.id),
        "status": "pending",
        "message": "Device registered. Awaiting admin approval.",
    }


@router.get("/devices/provision/status", response_model=ProvisionStatusResponse)
async def check_provision_status(
    device_name: str = Query(),
    key: str | None = Security(provisioning_key_header),
    session: AsyncSession = Depends(get_session),
) -> ProvisionStatusResponse:
    """Check provisioning status of a device."""
    if not key:
        raise HTTPException(
            status_code=401, detail="X-Provisioning-Key required"
        ) from None

    prefix = key[:10]
    stmt = select(TenantModel).where(
        TenantModel.provisioning_key_prefix == prefix,
        TenantModel.status == "active",
    )
    result = await session.execute(stmt)
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=401, detail="Invalid key") from None

    device_stmt = select(DeviceModel).where(
        DeviceModel.tenant_id == tenant.id,
        DeviceModel.name == device_name,
    )
    device_result = await session.execute(device_stmt)
    device = device_result.scalar_one_or_none()
    if device is None:
        raise HTTPException(
            status_code=404, detail="Device not found"
        ) from None

    return ProvisionStatusResponse(
        device_name=device.name, status=device.status
    )


@router.post("/device-registry/{device_id}/approve", status_code=204)
async def approve_device(
    device_id: uuid.UUID,
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Approve a pending device (admin only)."""
    stmt = select(DeviceModel).where(
        DeviceModel.id == device_id,
        DeviceModel.tenant_id == user.tenant_id,
        DeviceModel.status == "pending",
    )
    result = await session.execute(stmt)
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(
            status_code=404, detail="Pending device not found"
        ) from None
    device.status = "active"
    await session.flush()


@router.post("/device-registry/{device_id}/reject", status_code=204)
async def reject_device(
    device_id: uuid.UUID,
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Reject a pending device (admin only)."""
    stmt = select(DeviceModel).where(
        DeviceModel.id == device_id,
        DeviceModel.tenant_id == user.tenant_id,
        DeviceModel.status == "pending",
    )
    result = await session.execute(stmt)
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(
            status_code=404, detail="Pending device not found"
        ) from None
    device.status = "rejected"
    await session.flush()
