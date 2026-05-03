"""Tenant configuration endpoints (Sprint 15b Phase F).

Exposes the read/write surface for ``tenants.tracking_modes`` so the admin UI
can flip between asset and inventory modes (or both) without a SQL update.
The flag controls which sidebar entries the UI shows and which ingestion
branches actually run (see ``IngestionService._enrich_with_inventory``).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.audit import AuditLogger
from tagpulse.core.tenant_auth import Tenant, get_current_tenant
from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.models.database import TenantModel
from tagpulse.repositories.timescaledb.session import get_session

router = APIRouter(prefix="/tenant", tags=["tenant"])

TrackingMode = Literal["asset", "inventory"]


class TenantConfig(BaseModel):
    """Read-only view of tenant-scoped feature flags."""

    id: str
    name: str
    slug: str
    plan: str
    tracking_modes: list[TrackingMode]


class TenantConfigUpdate(BaseModel):
    """Admin-only payload for toggling tenant feature flags."""

    tracking_modes: list[TrackingMode] = Field(min_length=1, max_length=2)


def _to_response(row: TenantModel) -> TenantConfig:
    return TenantConfig(
        id=str(row.id),
        name=row.name,
        slug=row.slug,
        plan=row.plan,
        tracking_modes=list(row.tracking_modes),  # type: ignore[arg-type]
    )


@router.get("/config", response_model=TenantConfig)
async def get_tenant_config(
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> TenantConfig:
    """Return the calling tenant's configuration (any role)."""
    row = await session.scalar(select(TenantModel).where(TenantModel.id == tenant.id))
    if row is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return _to_response(row)


@router.patch("/config", response_model=TenantConfig)
async def update_tenant_config(
    body: TenantConfigUpdate,
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> TenantConfig:
    """Update tenant feature flags (admin only). Deduplicated and audited."""
    row = await session.scalar(
        select(TenantModel).where(TenantModel.id == user.tenant_id)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    new_modes = sorted(set(body.tracking_modes))
    old_modes = sorted(set(row.tracking_modes))
    if new_modes != old_modes:
        row.tracking_modes = new_modes  # type: ignore[assignment]
        await session.flush()
        await AuditLogger(session=session).log(
            user.tenant_id,
            "tenant.config.update",
            "tenant",
            user.tenant_id,
            changes={"tracking_modes": {"from": old_modes, "to": new_modes}},
            user_id=user.user_id,
        )
    return _to_response(row)
