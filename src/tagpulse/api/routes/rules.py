"""Rules & Alerts API routes."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.audit import AuditLogger
from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.models.rule_schemas import (
    AlertResponse,
    RuleCreate,
    RuleResponse,
    RuleUpdate,
)
from tagpulse.repositories.timescaledb.session import get_session
from tagpulse.rules import RulesService, SignalingScopeCapExceededError
from tagpulse.rules.templates import get_template, get_templates

router = APIRouter(tags=["rules"])


# Sprint 41 Phase B2: shared 409 message format. The detail body has
# the structured fields the UI needs to render "Cap reached for X / Y"
# without parsing prose.
def _cap_exceeded_to_http(
    exc: SignalingScopeCapExceededError,
) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "error": "signaling_scope_cap_exceeded",
            "event_type": exc.event_type,
            "category_id": str(exc.category_id) if exc.category_id else None,
            "current_count": exc.current_count,
            "cap": exc.cap,
            "override_hint": (
                "Admin callers can bypass with ?override=true; the override is "
                "recorded in the audit log."
            ),
        },
    )


async def _record_cap_override(
    session: AsyncSession,
    *,
    user: AuthenticatedUser,
    rule_id: UUID,
    event_type: str,
    category_ids: list[UUID],
) -> None:
    """Audit-log entry written when an admin uses ``?override=true``.

    Phase B writes one row per override; the changes blob captures the
    full scope so operators can later trace which categories were
    affected. ``resource_id`` is the rule id (or the placeholder UUID
    for create-time overrides where the rule hasn't been persisted
    yet — callers pass the just-created rule id).
    """

    audit = AuditLogger(session=session)
    await audit.log(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        action="signaling.cap_override",
        resource_type="rule",
        resource_id=rule_id,
        changes={
            "event_type": event_type,
            "category_ids": [str(c) for c in category_ids],
        },
    )


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
    override: bool = Query(
        default=False,
        description=(
            "Sprint 41 Phase B2: admin-only bypass of the per-scope active "
            "signaling-rule cap. Recorded in the audit log."
        ),
    ),
    user: AuthenticatedUser = require_role("admin", "editor"),
    session: AsyncSession = Depends(get_session),
) -> RuleResponse:
    """Create a new rule."""
    if override and user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="override=true requires admin role",
        ) from None
    service = RulesService(session)
    try:
        response = await service.create_rule(user.tenant_id, body, allow_cap_override=override)
    except SignalingScopeCapExceededError as exc:
        raise _cap_exceeded_to_http(exc) from exc
    if override and body.event_type is not None:
        await _record_cap_override(
            session,
            user=user,
            rule_id=response.id,
            event_type=body.event_type,
            category_ids=list(body.category_ids),
        )
    return response


@router.get("/rules", response_model=list[RuleResponse])
async def list_rules(
    enabled_only: bool = Query(default=False),
    kind: Literal["legacy", "signaling"] | None = Query(
        default=None,
        description=(
            "Sprint 41 Phase B1: filter by rule discriminator. "
            "'signaling' = rules with event_type populated; "
            "'legacy' = rules with event_type NULL. "
            "Omit for all rules (backwards-compatible)."
        ),
    ),
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    session: AsyncSession = Depends(get_session),
) -> list[RuleResponse]:
    """List all rules for the tenant."""
    service = RulesService(session)
    return await service.list_rules(user.tenant_id, enabled_only=enabled_only, kind=kind)


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
    override: bool = Query(
        default=False,
        description=(
            "Sprint 41 Phase B2: admin-only bypass of the per-scope active "
            "signaling-rule cap. Recorded in the audit log."
        ),
    ),
    user: AuthenticatedUser = require_role("admin", "editor"),
    session: AsyncSession = Depends(get_session),
) -> RuleResponse:
    """Update a rule (partial update)."""
    if override and user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="override=true requires admin role",
        ) from None
    service = RulesService(session)
    try:
        result = await service.update_rule(
            user.tenant_id, rule_id, body, allow_cap_override=override
        )
    except SignalingScopeCapExceededError as exc:
        raise _cap_exceeded_to_http(exc) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Rule not found") from None
    if override and result.event_type is not None:
        await _record_cap_override(
            session,
            user=user,
            rule_id=result.id,
            event_type=result.event_type,
            category_ids=list(result.category_ids),
        )
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
        raise HTTPException(status_code=404, detail="Alert not found") from None
