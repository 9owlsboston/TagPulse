"""CRUD API routes for the device registry."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from tagpulse.api.dependencies import get_device_service
from tagpulse.api.services.device_service import DeviceNotFoundError, DeviceService
from tagpulse.core.tenant_auth import Tenant, get_current_tenant
from tagpulse.models.schemas import DeviceCreate, DeviceResponse, DeviceUpdate

router = APIRouter(prefix="/device-registry", tags=["device-registry"])


@router.post("", response_model=DeviceResponse, status_code=201)
async def register_device(
    body: DeviceCreate,
    tenant: Tenant = Depends(get_current_tenant),
    service: DeviceService = Depends(get_device_service),
) -> DeviceResponse:
    """Register a new device (reader)."""
    return await service.register(tenant.id, body)


@router.get("", response_model=list[DeviceResponse])
async def list_devices(
    status: str | None = Query(default=None),
    device_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    tenant: Tenant = Depends(get_current_tenant),
    service: DeviceService = Depends(get_device_service),
) -> list[DeviceResponse]:
    """List devices with optional filters."""
    return await service.list_devices(
        tenant.id, status=status, device_type=device_type, limit=limit, offset=offset
    )


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(
    device_id: UUID,
    tenant: Tenant = Depends(get_current_tenant),
    service: DeviceService = Depends(get_device_service),
) -> DeviceResponse:
    """Get a single device by ID."""
    try:
        return await service.get(tenant.id, device_id)
    except DeviceNotFoundError:
        raise HTTPException(status_code=404, detail="Device not found") from None


@router.patch("/{device_id}", response_model=DeviceResponse)
async def update_device(
    device_id: UUID,
    body: DeviceUpdate,
    tenant: Tenant = Depends(get_current_tenant),
    service: DeviceService = Depends(get_device_service),
) -> DeviceResponse:
    """Update device fields (partial update)."""
    try:
        return await service.update(tenant.id, device_id, body)
    except DeviceNotFoundError:
        raise HTTPException(status_code=404, detail="Device not found") from None


@router.post("/{device_id}/decommission", response_model=DeviceResponse)
async def decommission_device(
    device_id: UUID,
    tenant: Tenant = Depends(get_current_tenant),
    service: DeviceService = Depends(get_device_service),
) -> DeviceResponse:
    """Decommission a device — sets status to 'decommissioned'."""
    try:
        return await service.decommission(tenant.id, device_id)
    except DeviceNotFoundError:
        raise HTTPException(status_code=404, detail="Device not found") from None
