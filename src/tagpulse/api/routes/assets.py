"""Assets & tag-binding CRUD API (Sprint 15 Phase B).

Permissions per docs/design/assets-and-zones.md §4:
- viewer+: GET
- editor+: POST/PATCH/DELETE assets + bindings
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from tagpulse.api.dependencies import get_asset_service
from tagpulse.api.label_filter import LabelFilterError, parse_label_filter
from tagpulse.api.services.asset_service import AssetNotFoundError, AssetService
from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.models.schemas import (
    AssetCreate,
    AssetCurrentLocation,
    AssetLoadRequest,
    AssetPathPoint,
    AssetResponse,
    AssetTagBindingCreate,
    AssetTagBindingResponse,
    AssetUnloadRequest,
    AssetUpdate,
    ExternalLocationCreate,
    ExternalLocationResponse,
    ManifestResponse,
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
    request: Request,
    asset_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    category_id: UUID | None = Query(
        default=None,
        description=(
            "Sprint 37 — server-side filter on the ``assets.category_id`` FK "
            "(ADR 019). Combines with ``asset_type``/``status``/``q``/"
            "``labels[…]`` via AND."
        ),
    ),
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: AssetService = Depends(get_asset_service),
) -> list[AssetResponse]:
    try:
        labels = parse_label_filter(request.query_params)
    except LabelFilterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return await service.list_assets(
        user.tenant_id,
        asset_type=asset_type,
        status=status,
        category_id=category_id,
        q=q,
        labels=labels,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/current-locations",
    response_model=list[AssetCurrentLocation],
)
async def list_assets_current_locations(
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: AssetService = Depends(get_asset_service),
) -> list[AssetCurrentLocation]:
    """Bulk current-location feed for the Assets list page.

    One row per asset that has *any* known position (RFID or external),
    ordered newest-first. Powers the live Last-seen / Location columns
    without N+1 fetches.
    """
    return await service.list_current_locations(user.tenant_id, limit=limit, offset=offset)


@router.get("/{asset_id}", response_model=AssetResponse)
async def get_asset(
    asset_id: UUID,
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: AssetService = Depends(get_asset_service),
) -> AssetResponse:
    asset = await service.get_asset(user.tenant_id, asset_id, with_latest_telemetry=True)
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
        asset = await service.update_asset(user.tenant_id, user.user_id, asset_id, body)
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
        return await service.bind_tag(user.tenant_id, user.user_id, asset_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/{asset_id}/bindings", response_model=list[AssetTagBindingResponse])
async def list_bindings(
    asset_id: UUID,
    active_only: bool = Query(default=False),
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: AssetService = Depends(get_asset_service),
) -> list[AssetTagBindingResponse]:
    return await service.list_bindings(user.tenant_id, asset_id, active_only=active_only)


@router.delete("/{asset_id}/bindings/{binding_value}", status_code=204)
async def unbind_tag(
    asset_id: UUID,
    binding_value: str,
    user: AuthenticatedUser = require_role("admin", "editor"),
    service: AssetService = Depends(get_asset_service),
) -> None:
    unbound = await service.unbind_tag(user.tenant_id, user.user_id, asset_id, binding_value)
    if not unbound:
        raise HTTPException(status_code=404, detail="Active binding not found")


# -- Carrier semantics (Phase C) --


@router.post("/{asset_id}/load", response_model=AssetResponse)
async def load_asset(
    asset_id: UUID,
    body: AssetLoadRequest,
    user: AuthenticatedUser = require_role("admin", "editor"),
    service: AssetService = Depends(get_asset_service),
) -> AssetResponse:
    """Attach `asset_id` to `body.parent_asset_id` (carrier). Idempotent."""
    try:
        return await service.load_onto_carrier(
            user.tenant_id,
            user.user_id,
            asset_id,
            body.parent_asset_id,
            body.at,
        )
    except AssetNotFoundError:
        raise HTTPException(status_code=404, detail="Asset not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/{asset_id}/unload", response_model=AssetResponse)
async def unload_asset(
    asset_id: UUID,
    body: AssetUnloadRequest,
    user: AuthenticatedUser = require_role("admin", "editor"),
    service: AssetService = Depends(get_asset_service),
) -> AssetResponse:
    """Detach `asset_id` from its current carrier. Idempotent."""
    try:
        return await service.unload_from_carrier(user.tenant_id, user.user_id, asset_id, body.at)
    except AssetNotFoundError:
        raise HTTPException(status_code=404, detail="Asset not found") from None


@router.get("/{asset_id}/manifest", response_model=ManifestResponse)
async def get_manifest(
    asset_id: UUID,
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: AssetService = Depends(get_asset_service),
) -> ManifestResponse:
    """Return the recursive containment tree rooted at `asset_id`."""
    try:
        return await service.get_manifest(user.tenant_id, asset_id)
    except AssetNotFoundError:
        raise HTTPException(status_code=404, detail="Asset not found") from None


# -- External (non-RFID) positions (Phase C) --


@router.post(
    "/{asset_id}/external-position",
    response_model=ExternalLocationResponse,
    status_code=201,
)
async def record_external_position(
    asset_id: UUID,
    body: ExternalLocationCreate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    service: AssetService = Depends(get_asset_service),
) -> ExternalLocationResponse:
    """Record a non-RFID position fix (TMS push, manual check-in, etc.)."""
    try:
        return await service.record_external_position(user.tenant_id, user.user_id, asset_id, body)
    except AssetNotFoundError:
        raise HTTPException(status_code=404, detail="Asset not found") from None


@router.get(
    "/{asset_id}/external-positions",
    response_model=list[ExternalLocationResponse],
)
async def list_external_positions(
    asset_id: UUID,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: AssetService = Depends(get_asset_service),
) -> list[ExternalLocationResponse]:
    asset = await service.get_asset(user.tenant_id, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return await service.list_external_positions(
        user.tenant_id, asset_id, limit=limit, offset=offset
    )


@router.get(
    "/{asset_id}/current-location",
    response_model=AssetCurrentLocation,
)
async def get_asset_current_location(
    asset_id: UUID,
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: AssetService = Depends(get_asset_service),
) -> AssetCurrentLocation:
    """Latest known position for the asset, sourced from RFID or external feeds."""
    asset = await service.get_asset(user.tenant_id, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    location = await service.get_current_location(user.tenant_id, asset_id)
    if location is None:
        raise HTTPException(status_code=404, detail="No location recorded yet")
    return location


@router.get("/{asset_id}/path", response_model=list[AssetPathPoint])
async def get_asset_path(
    asset_id: UUID,
    since: datetime = Query(...),
    until: datetime = Query(...),
    limit: int = Query(default=1000, ge=1, le=10000),
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: AssetService = Depends(get_asset_service),
) -> list[AssetPathPoint]:
    """Merged RFID + external-fix timeline for the asset, ascending by time."""
    asset = await service.get_asset(user.tenant_id, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    if until <= since:
        raise HTTPException(status_code=400, detail="`until` must be after `since`")
    return await service.get_asset_path(
        user.tenant_id, asset_id, since=since, until=until, limit=limit
    )
