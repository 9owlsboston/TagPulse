"""Dead letter and audit log admin routes."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.audit import AuditLogger
from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.models.database import DeadLetterEventModel
from tagpulse.repositories.timescaledb.session import get_session

router = APIRouter(prefix="/admin", tags=["admin"])


# -- Dead Letter --


class DeadLetterResponse(BaseModel):
    """Dead-lettered event."""

    id: UUID
    tenant_id: UUID | None
    topic: str
    payload: dict[str, object]
    error_message: str
    retry_count: int
    status: str
    failed_at: datetime


@router.get("/dead-letter", response_model=list[DeadLetterResponse])
async def list_dead_letters(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> list[DeadLetterResponse]:
    """List dead-lettered events for this tenant."""
    stmt = (
        select(DeadLetterEventModel)
        .where(DeadLetterEventModel.tenant_id == user.tenant_id)
        .order_by(DeadLetterEventModel.failed_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [
        DeadLetterResponse(
            id=row.id,
            tenant_id=row.tenant_id,
            topic=row.topic,
            payload=row.payload,
            error_message=row.error_message,
            retry_count=row.retry_count,
            status=row.status,
            failed_at=row.failed_at,
        )
        for row in result.scalars()
    ]


@router.post("/dead-letter/{event_id}/retry", status_code=204)
async def retry_dead_letter(
    event_id: UUID,
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Mark a dead-lettered event for retry."""
    stmt = (
        update(DeadLetterEventModel)
        .where(
            DeadLetterEventModel.id == event_id,
            DeadLetterEventModel.tenant_id == user.tenant_id,
        )
        .values(status="retried", retry_count=DeadLetterEventModel.retry_count + 1)
        .returning(DeadLetterEventModel.id)
    )
    result = await session.execute(stmt)
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Dead letter event not found") from None


@router.delete("/dead-letter/{event_id}", status_code=204)
async def abandon_dead_letter(
    event_id: UUID,
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Abandon a dead-lettered event."""
    stmt = (
        update(DeadLetterEventModel)
        .where(
            DeadLetterEventModel.id == event_id,
            DeadLetterEventModel.tenant_id == user.tenant_id,
        )
        .values(status="abandoned")
        .returning(DeadLetterEventModel.id)
    )
    result = await session.execute(stmt)
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Dead letter event not found") from None


# -- Audit Logs --


@router.get("/audit-logs")
async def list_audit_logs(
    resource_type: str | None = Query(default=None),
    actions: str | None = Query(
        default=None,
        description="Comma-separated list of actions to filter by (e.g. "
        "'device.token_rotated,device.cert_attached').",
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, object]]:
    """List audit logs for this tenant."""
    audit = AuditLogger(session)
    action_list = (
        [a.strip() for a in actions.split(",") if a.strip()] if actions else None
    )
    return await audit.list_logs(
        user.tenant_id,
        resource_type=resource_type,
        actions=action_list,
        limit=limit,
        offset=offset,
    )


# -- Tag Collisions (cross-tenant; admin only) --


@router.get("/tag-collisions")
async def get_tag_collisions(
    binding_value: str = Query(min_length=1, max_length=256),
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Return cross-tenant collision count for a binding_value.

    Per docs/design/assets-and-zones.md §11 Q3: returns only the count of
    *other* tenants with an active binding for this value, never their
    identities. Increments ``tagpulse_tag_collisions_global_total``.
    """
    from tagpulse.api.services.asset_service import AssetService
    from tagpulse.repositories.timescaledb.assets import (
        TimescaleAssetRepository,
        TimescaleAssetTagBindingRepository,
    )

    service = AssetService(
        asset_repo=TimescaleAssetRepository(session),
        binding_repo=TimescaleAssetTagBindingRepository(session),
        audit=AuditLogger(session),
    )
    count = await service.count_other_tenant_collisions(
        user.tenant_id, binding_value
    )
    return {"binding_value": binding_value, "other_tenant_count": count}
