"""Tenant configuration endpoints (Sprint 15b Phase F).

Exposes the read/write surface for ``tenants.tracking_modes`` so the admin UI
can flip between asset and inventory modes (or both) without a SQL update.
The flag controls which sidebar entries the UI shows and which ingestion
branches actually run (see ``IngestionService._enrich_with_inventory``).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.audit import AuditLogger
from tagpulse.core.tenant_auth import Tenant, get_current_tenant
from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.models.database import TenantModel
from tagpulse.repositories.timescaledb.session import get_session
from tagpulse.services.map_config import (
    MapConfigError,
    MapConfigResponse,
    resolve_map_config,
)

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


# -- Sprint 17a: per-tenant map tile-provider config --


class TileProviderUpdate(BaseModel):
    """Admin payload for setting the tenant's tile provider.

    ``provider`` is the raw blob persisted to ``tenants.tile_provider``;
    schema is provider-specific (see ``services.map_config``).
    Pass ``None`` to fall back to the system default (OSM public).
    """

    provider: dict[str, Any] | None = None


@router.get("/map-config", response_model=MapConfigResponse)
async def get_map_config(
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> MapConfigResponse:
    """Resolved tile-provider config for the calling tenant (any role)."""
    row = await session.scalar(
        select(TenantModel).where(TenantModel.id == tenant.id)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    try:
        return resolve_map_config(row.tile_provider)
    except MapConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/map-config", response_model=MapConfigResponse)
async def update_map_config(
    body: TileProviderUpdate,
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> MapConfigResponse:
    """Set the tile provider for the calling tenant (admin only)."""
    row = await session.scalar(
        select(TenantModel).where(TenantModel.id == user.tenant_id)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    # Validate first so we never persist a blob the resolver can't render.
    try:
        resolved = resolve_map_config(body.provider)
    except MapConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    old_kind = (row.tile_provider or {}).get("kind") if row.tile_provider else None
    new_kind = (body.provider or {}).get("kind") if body.provider else None
    row.tile_provider = body.provider
    await session.flush()
    await AuditLogger(session=session).log(
        user.tenant_id,
        "tenant.map_config.update",
        "tenant",
        user.tenant_id,
        changes={"tile_provider.kind": {"from": old_kind, "to": new_kind}},
        user_id=user.user_id,
    )
    return resolved
