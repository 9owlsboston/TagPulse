"""Device self-registration and provisioning endpoints."""

import hashlib
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.user_auth import AuthenticatedUser, generate_device_token, require_role
from tagpulse.models.database import DeviceModel, TenantModel
from tagpulse.repositories.timescaledb.session import get_session

router = APIRouter(tags=["provisioning"])

provisioning_key_header = APIKeyHeader(name="X-Provisioning-Key", auto_error=False)


class ProvisionRequest(BaseModel):
    """Device self-registration request."""

    name: str = Field(min_length=1, max_length=255)
    device_type: str = Field(default="rfid_reader", max_length=50)


class ProvisionResponse(BaseModel):
    """Device self-registration result.

    Carries the freshly-minted per-device token (``tpd_…``). It is returned
    **once** here — the backend stores only its SHA-256 hash and it cannot be
    re-read later (rotate via ``POST /device-registry/{id}/rotate-token``). The
    token is **inert until an admin approves the device** (``get_current_user``
    requires ``status="active"``).
    """

    device_id: str
    status: str
    token: str
    token_prefix: str
    message: str


class ProvisionStatusResponse(BaseModel):
    """Device provisioning status."""

    device_name: str
    status: str


@router.post("/devices/provision", status_code=201, response_model=ProvisionResponse)
async def provision_device(
    body: ProvisionRequest,
    key: str | None = Security(provisioning_key_header),
    session: AsyncSession = Depends(get_session),
) -> ProvisionResponse:
    """Self-register a device using a tenant provisioning key.

    Mints the device's per-device token in the same step and returns it once
    (copy-once). The device holds the token through the approval wait; it only
    authenticates once an admin approves the device.
    """
    if not key:
        raise HTTPException(status_code=401, detail="X-Provisioning-Key required") from None

    # Find tenant by provisioning key
    prefix = key[:10]
    stmt = select(TenantModel).where(
        TenantModel.provisioning_key_prefix == prefix,
        TenantModel.status == "active",
    )
    result = await session.execute(stmt)
    tenant = result.scalar_one_or_none()

    if tenant is None:
        raise HTTPException(status_code=401, detail="Invalid provisioning key") from None

    key_hash = hashlib.sha256(key.encode()).hexdigest()
    if tenant.provisioning_key_hash != key_hash:
        raise HTTPException(status_code=401, detail="Invalid provisioning key") from None

    # Create device with pending status + mint its per-device token.
    raw_token, token_prefix, token_hash = generate_device_token(tenant.slug)
    device = DeviceModel(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name=body.name,
        device_type=body.device_type,
        status="pending",
        token_hash=token_hash,
        token_prefix=token_prefix,
        token_rotated_at=datetime.now(UTC),
    )
    session.add(device)
    await session.flush()

    return ProvisionResponse(
        device_id=str(device.id),
        status="pending",
        token=raw_token,
        token_prefix=token_prefix,
        message=(
            "Device registered. Awaiting admin approval. Store this token now — "
            "it is shown once and activates when the device is approved."
        ),
    )


@router.get("/devices/provision/status", response_model=ProvisionStatusResponse)
async def check_provision_status(
    device_name: str = Query(),
    key: str | None = Security(provisioning_key_header),
    session: AsyncSession = Depends(get_session),
) -> ProvisionStatusResponse:
    """Check provisioning status of a device."""
    if not key:
        raise HTTPException(status_code=401, detail="X-Provisioning-Key required") from None

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
        raise HTTPException(status_code=404, detail="Device not found") from None

    return ProvisionStatusResponse(device_name=device.name, status=device.status)


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
        raise HTTPException(status_code=404, detail="Pending device not found") from None
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
        raise HTTPException(status_code=404, detail="Pending device not found") from None
    device.status = "rejected"
    await session.flush()
