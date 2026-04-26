"""Rules & Alerts API routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.tenant_auth import Tenant, get_current_tenant
from tagpulse.models.rule_schemas import (
    AlertResponse,
    RuleCreate,
    RuleResponse,
    RuleUpdate,
)
from tagpulse.repositories.timescaledb.session import get_session
from tagpulse.rules import RulesService

router = APIRouter(tags=["rules"])


# -- Rules CRUD --


@router.post("/rules", response_model=RuleResponse, status_code=201)
async def create_rule(
    body: RuleCreate,
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> RuleResponse:
    """Create a new rule."""
    service = RulesService(session)
    return await service.create_rule(tenant.id, body)


@router.get("/rules", response_model=list[RuleResponse])
async def list_rules(
    enabled_only: bool = Query(default=False),
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> list[RuleResponse]:
    """List all rules for the tenant."""
    service = RulesService(session)
    return await service.list_rules(tenant.id, enabled_only=enabled_only)


@router.get("/rules/{rule_id}", response_model=RuleResponse)
async def get_rule(
    rule_id: UUID,
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> RuleResponse:
    """Get a single rule by ID."""
    service = RulesService(session)
    result = await service.get_rule(tenant.id, rule_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Rule not found") from None
    return result


@router.patch("/rules/{rule_id}", response_model=RuleResponse)
async def update_rule(
    rule_id: UUID,
    body: RuleUpdate,
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> RuleResponse:
    """Update a rule (partial update)."""
    service = RulesService(session)
    result = await service.update_rule(tenant.id, rule_id, body)
    if result is None:
        raise HTTPException(status_code=404, detail="Rule not found") from None
    return result


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: UUID,
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a rule."""
    service = RulesService(session)
    deleted = await service.delete_rule(tenant.id, rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Rule not found") from None


# -- Alerts --


@router.get("/alerts", response_model=list[AlertResponse])
async def list_alerts(
    rule_id: UUID | None = Query(default=None),
    device_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> list[AlertResponse]:
    """List alert history with filters."""
    service = RulesService(session)
    return await service.list_alerts(
        tenant.id,
        rule_id=rule_id,
        device_id=device_id,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.post("/alerts/{alert_id}/acknowledge", status_code=204)
async def acknowledge_alert(
    alert_id: UUID,
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Acknowledge an alert."""
    service = RulesService(session)
    acked = await service.acknowledge_alert(tenant.id, alert_id)
    if not acked:
        raise HTTPException(
            status_code=404, detail="Alert not found"
        ) from None
