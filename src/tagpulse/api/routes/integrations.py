"""Integration CRUD API routes + delivery history."""

import logging
import time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.integrations.service import IntegrationService
from tagpulse.models.integration_schemas import (
    DeliveryResponse,
    IntegrationCreate,
    IntegrationResponse,
    IntegrationUpdate,
)
from tagpulse.repositories.timescaledb.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.post("", response_model=IntegrationResponse, status_code=201)
async def create_integration(
    body: IntegrationCreate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    session: AsyncSession = Depends(get_session),
) -> IntegrationResponse:
    """Create an integration target (webhook, SSE, export)."""
    service = IntegrationService(session)
    return await service.create(user.tenant_id, body)


@router.get("", response_model=list[IntegrationResponse])
async def list_integrations(
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    session: AsyncSession = Depends(get_session),
) -> list[IntegrationResponse]:
    """List all integration targets."""
    service = IntegrationService(session)
    return await service.list_all(user.tenant_id)


@router.get("/{integration_id}", response_model=IntegrationResponse)
async def get_integration(
    integration_id: UUID,
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    session: AsyncSession = Depends(get_session),
) -> IntegrationResponse:
    """Get an integration target by ID."""
    service = IntegrationService(session)
    result = await service.get(user.tenant_id, integration_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Integration not found") from None
    return result


@router.patch("/{integration_id}", response_model=IntegrationResponse)
async def update_integration(
    integration_id: UUID,
    body: IntegrationUpdate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    session: AsyncSession = Depends(get_session),
) -> IntegrationResponse:
    """Update an integration target."""
    service = IntegrationService(session)
    result = await service.update(user.tenant_id, integration_id, body)
    if result is None:
        raise HTTPException(status_code=404, detail="Integration not found") from None
    return result


@router.delete("/{integration_id}", status_code=204)
async def delete_integration(
    integration_id: UUID,
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete an integration target."""
    service = IntegrationService(session)
    deleted = await service.delete_integration(user.tenant_id, integration_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Integration not found") from None


@router.get(
    "/{integration_id}/deliveries",
    response_model=list[DeliveryResponse],
)
async def list_deliveries(
    integration_id: UUID,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    session: AsyncSession = Depends(get_session),
) -> list[DeliveryResponse]:
    """List delivery history for an integration target."""
    service = IntegrationService(session)
    return await service.list_deliveries(user.tenant_id, integration_id, limit=limit, offset=offset)


class WebhookTestResult(BaseModel):
    status_code: int | None
    response_time_ms: float
    error: str | None = None


@router.post(
    "/{integration_id}/test",
    response_model=WebhookTestResult,
)
async def test_integration(
    integration_id: UUID,
    user: AuthenticatedUser = require_role("admin", "editor"),
    session: AsyncSession = Depends(get_session),
) -> WebhookTestResult:
    """Send a test payload to a webhook integration (Sprint 27 C1)."""
    service = IntegrationService(session)
    integration = await service.get(user.tenant_id, integration_id)
    if integration is None:
        raise HTTPException(status_code=404, detail="Integration not found")
    if integration.type != "webhook":
        raise HTTPException(
            status_code=400, detail="Test is only supported for webhook integrations"
        )

    url = integration.config.get("url") if integration.config else None
    if not url:
        raise HTTPException(status_code=400, detail="Webhook URL not configured")

    import httpx

    test_payload = {
        "event": "test",
        "integration_id": str(integration_id),
        "tenant_id": str(user.tenant_id),
        "message": "This is a test event from TagPulse",
    }

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json=test_payload,
                headers={"X-TagPulse-Event": "test"},
            )
        elapsed_ms = (time.monotonic() - start) * 1000
        return WebhookTestResult(
            status_code=resp.status_code,
            response_time_ms=round(elapsed_ms, 1),
        )
    except httpx.ConnectError:
        elapsed_ms = (time.monotonic() - start) * 1000
        return WebhookTestResult(
            status_code=None,
            response_time_ms=round(elapsed_ms, 1),
            error="Connection refused",
        )
    except httpx.TimeoutException:
        elapsed_ms = (time.monotonic() - start) * 1000
        return WebhookTestResult(
            status_code=None,
            response_time_ms=round(elapsed_ms, 1),
            error="Timeout (10s)",
        )
    except httpx.HTTPError as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.warning("webhook test-fire failed: %s", exc)
        return WebhookTestResult(
            status_code=None,
            response_time_ms=round(elapsed_ms, 1),
            error=str(exc),
        )
