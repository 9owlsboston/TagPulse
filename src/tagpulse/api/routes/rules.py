"""Rules & Alerts API routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.models.rule_schemas import (
    AlertResponse,
    RuleCreate,
    RuleResponse,
    RuleUpdate,
)
from tagpulse.repositories.timescaledb.session import get_session
from tagpulse.rules import RulesService
from tagpulse.rules.templates import get_template, get_templates

router = APIRouter(tags=["rules"])


# -- Rule templates (Sprint 20) --


@router.get("/rule-templates")
async def list_rule_templates(
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
) -> list[dict[str, object]]:
    """List built-in rule templates the UI can offer as starting points.

    The ``requires_subject_kind`` field is a discoverability hint — the
    backend does not gate access. The UI is expected to filter the list
    against the tenant's configured ``telemetry_subject_kinds`` and
    available ``telemetry_models`` rows.
    """
    return [tpl.to_dict() for tpl in get_templates()]


@router.get("/rule-templates/{template_key}")
async def get_rule_template(
    template_key: str,
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
) -> dict[str, object]:
    tpl = get_template(template_key)
    if tpl is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return tpl.to_dict()


# -- Rules CRUD --


@router.post("/rules", response_model=RuleResponse, status_code=201)
async def create_rule(
    body: RuleCreate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    session: AsyncSession = Depends(get_session),
) -> RuleResponse:
    """Create a new rule."""
    service = RulesService(session)
    return await service.create_rule(user.tenant_id, body)


@router.get("/rules", response_model=list[RuleResponse])
async def list_rules(
    enabled_only: bool = Query(default=False),
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    session: AsyncSession = Depends(get_session),
) -> list[RuleResponse]:
    """List all rules for the tenant."""
    service = RulesService(session)
    return await service.list_rules(user.tenant_id, enabled_only=enabled_only)


@router.get("/rules/{rule_id}", response_model=RuleResponse)
async def get_rule(
    rule_id: UUID,
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    session: AsyncSession = Depends(get_session),
) -> RuleResponse:
    """Get a single rule by ID."""
    service = RulesService(session)
    result = await service.get_rule(user.tenant_id, rule_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Rule not found") from None
    return result


@router.patch("/rules/{rule_id}", response_model=RuleResponse)
async def update_rule(
    rule_id: UUID,
    body: RuleUpdate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    session: AsyncSession = Depends(get_session),
) -> RuleResponse:
    """Update a rule (partial update)."""
    service = RulesService(session)
    result = await service.update_rule(user.tenant_id, rule_id, body)
    if result is None:
        raise HTTPException(status_code=404, detail="Rule not found") from None
    return result


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: UUID,
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a rule."""
    service = RulesService(session)
    deleted = await service.delete_rule(user.tenant_id, rule_id)
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
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    session: AsyncSession = Depends(get_session),
) -> list[AlertResponse]:
    """List alert history with filters."""
    service = RulesService(session)
    return await service.list_alerts(
        user.tenant_id,
        rule_id=rule_id,
        device_id=device_id,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.post("/alerts/{alert_id}/acknowledge", status_code=204)
async def acknowledge_alert(
    alert_id: UUID,
    user: AuthenticatedUser = require_role("admin", "editor"),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Acknowledge an alert."""
    service = RulesService(session)
    acked = await service.acknowledge_alert(user.tenant_id, alert_id)
    if not acked:
        raise HTTPException(
            status_code=404, detail="Alert not found"
        ) from None
