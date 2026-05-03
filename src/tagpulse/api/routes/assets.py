"""Assets & tag-binding CRUD API (Sprint 15 Phase B).

Permissions per docs/design/assets-and-zones.md §4:
- viewer+: GET
- editor+: POST/PATCH/DELETE assets + bindings
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from tagpulse.api.dependencies import get_asset_service
from tagpulse.api.services.asset_service import AssetService
from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.models.schemas import (
    AssetCreate,
    AssetResponse,
    AssetTagBindingCreate,
    AssetTagBindingResponse,
    AssetUpdate,
)

router = APIRouter(prefix="/assets", tags=["assets"])


@router.post("", response_model=AssetResponse, status_code=201)
async def create_asset(
    body: AssetCreate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    service: AssetService = Depends(get_asset_service),
) -> AssetResponse:
    try:
        return await service.create_asset(user.tenant_id, user.user_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("", response_model=list[AssetResponse])
async def list_assets(
    asset_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: AssetService = Depends(get_asset_service),
) -> list[AssetResponse]:
    return await service.list_assets(
        user.tenant_id,
        asset_type=asset_type,
        status=status,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.get("/{asset_id}", response_model=AssetResponse)
async def get_asset(
    asset_id: UUID,
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: AssetService = Depends(get_asset_service),
) -> AssetResponse:
    asset = await service.get_asset(user.tenant_id, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@router.patch("/{asset_id}", response_model=AssetResponse)
async def update_asset(
    asset_id: UUID,
    body: AssetUpdate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    service: AssetService = Depends(get_asset_service),
) -> AssetResponse:
    try:
        asset = await service.update_asset(
            user.tenant_id, user.user_id, asset_id, body
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@router.delete("/{asset_id}", status_code=204)
async def retire_asset(
    asset_id: UUID,
    user: AuthenticatedUser = require_role("admin", "editor"),
    service: AssetService = Depends(get_asset_service),
) -> None:
    """Soft-delete: marks status='retired'."""
    deleted = await service.retire_asset(user.tenant_id, user.user_id, asset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Asset not found")


# -- Bindings --


@router.post(
    "/{asset_id}/bindings",
    response_model=AssetTagBindingResponse,
    status_code=201,
)
async def bind_tag(
    asset_id: UUID,
    body: AssetTagBindingCreate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    service: AssetService = Depends(get_asset_service),
) -> AssetTagBindingResponse:
    asset = await service.get_asset(user.tenant_id, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    try:
        return await service.bind_tag(
            user.tenant_id, user.user_id, asset_id, body
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get(
    "/{asset_id}/bindings", response_model=list[AssetTagBindingResponse]
)
async def list_bindings(
    asset_id: UUID,
    active_only: bool = Query(default=False),
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: AssetService = Depends(get_asset_service),
) -> list[AssetTagBindingResponse]:
    return await service.list_bindings(
        user.tenant_id, asset_id, active_only=active_only
    )


@router.delete("/{asset_id}/bindings/{binding_value}", status_code=204)
async def unbind_tag(
    asset_id: UUID,
    binding_value: str,
    user: AuthenticatedUser = require_role("admin", "editor"),
    service: AssetService = Depends(get_asset_service),
) -> None:
    unbound = await service.unbind_tag(
        user.tenant_id, user.user_id, asset_id, binding_value
    )
    if not unbound:
        raise HTTPException(status_code=404, detail="Active binding not found")
