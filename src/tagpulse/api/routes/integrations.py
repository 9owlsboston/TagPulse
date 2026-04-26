"""Integration CRUD API routes + delivery history."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.tenant_auth import Tenant, get_current_tenant
from tagpulse.integrations.service import IntegrationService
from tagpulse.models.integration_schemas import (
    DeliveryResponse,
    IntegrationCreate,
    IntegrationResponse,
    IntegrationUpdate,
)
from tagpulse.repositories.timescaledb.session import get_session

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.post("", response_model=IntegrationResponse, status_code=201)
async def create_integration(
    body: IntegrationCreate,
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> IntegrationResponse:
    """Create an integration target (webhook, SSE, export)."""
    service = IntegrationService(session)
    return await service.create(tenant.id, body)


@router.get("", response_model=list[IntegrationResponse])
async def list_integrations(
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> list[IntegrationResponse]:
    """List all integration targets."""
    service = IntegrationService(session)
    return await service.list_all(tenant.id)


@router.get("/{integration_id}", response_model=IntegrationResponse)
async def get_integration(
    integration_id: UUID,
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> IntegrationResponse:
    """Get an integration target by ID."""
    service = IntegrationService(session)
    result = await service.get(tenant.id, integration_id)
    if result is None:
        raise HTTPException(
            status_code=404, detail="Integration not found"
        ) from None
    return result


@router.patch("/{integration_id}", response_model=IntegrationResponse)
async def update_integration(
    integration_id: UUID,
    body: IntegrationUpdate,
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> IntegrationResponse:
    """Update an integration target."""
    service = IntegrationService(session)
    result = await service.update(tenant.id, integration_id, body)
    if result is None:
        raise HTTPException(
            status_code=404, detail="Integration not found"
        ) from None
    return result


@router.delete("/{integration_id}", status_code=204)
async def delete_integration(
    integration_id: UUID,
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete an integration target."""
    service = IntegrationService(session)
    deleted = await service.delete_integration(tenant.id, integration_id)
    if not deleted:
        raise HTTPException(
            status_code=404, detail="Integration not found"
        ) from None


@router.get(
    "/{integration_id}/deliveries",
    response_model=list[DeliveryResponse],
)
async def list_deliveries(
    integration_id: UUID,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> list[DeliveryResponse]:
    """List delivery history for an integration target."""
    service = IntegrationService(session)
    return await service.list_deliveries(
        tenant.id, integration_id, limit=limit, offset=offset
    )
