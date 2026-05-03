"""Sites & Zones CRUD API (Sprint 15).

Permissions:
- viewer+: read
- editor+: not allowed (admin-only writes — see assets-and-zones.md §4)
- admin: write (create/update/delete)
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from tagpulse.api.dependencies import get_site_zone_service
from tagpulse.api.services.sites_zones_service import SiteZoneService
from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.models.schemas import (
    SiteCreate,
    SiteResponse,
    SiteUpdate,
    ZoneCreate,
    ZoneResponse,
    ZoneUpdate,
)

router = APIRouter(tags=["sites-zones"])


# -- Sites --


@router.post("/sites", response_model=SiteResponse, status_code=201)
async def create_site(
    body: SiteCreate,
    user: AuthenticatedUser = require_role("admin"),
    service: SiteZoneService = Depends(get_site_zone_service),
) -> SiteResponse:
    try:
        return await service.create_site(user.tenant_id, user.user_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/sites", response_model=list[SiteResponse])
async def list_sites(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: SiteZoneService = Depends(get_site_zone_service),
) -> list[SiteResponse]:
    return await service.list_sites(user.tenant_id, limit=limit, offset=offset)


@router.get("/sites/{site_id}", response_model=SiteResponse)
async def get_site(
    site_id: UUID,
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: SiteZoneService = Depends(get_site_zone_service),
) -> SiteResponse:
    site = await service.get_site(user.tenant_id, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found")
    return site


@router.patch("/sites/{site_id}", response_model=SiteResponse)
async def update_site(
    site_id: UUID,
    body: SiteUpdate,
    user: AuthenticatedUser = require_role("admin"),
    service: SiteZoneService = Depends(get_site_zone_service),
) -> SiteResponse:
    site = await service.update_site(user.tenant_id, user.user_id, site_id, body)
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found")
    return site


@router.delete("/sites/{site_id}", status_code=204)
async def delete_site(
    site_id: UUID,
    user: AuthenticatedUser = require_role("admin"),
    service: SiteZoneService = Depends(get_site_zone_service),
) -> None:
    deleted = await service.delete_site(user.tenant_id, user.user_id, site_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Site not found")


# -- Zones --


@router.post("/zones", response_model=ZoneResponse, status_code=201)
async def create_zone(
    body: ZoneCreate,
    user: AuthenticatedUser = require_role("admin"),
    service: SiteZoneService = Depends(get_site_zone_service),
) -> ZoneResponse:
    site = await service.get_site(user.tenant_id, body.site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Parent site not found")
    # Schema-level validation (ZoneCreate.model_validator) already enforces
    # kind/payload consistency — see models/schemas.py.
    try:
        return await service.create_zone(user.tenant_id, user.user_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/zones", response_model=list[ZoneResponse])
async def list_zones(
    site_id: UUID | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: SiteZoneService = Depends(get_site_zone_service),
) -> list[ZoneResponse]:
    return await service.list_zones(
        user.tenant_id, site_id=site_id, limit=limit, offset=offset
    )


@router.get("/zones/{zone_id}", response_model=ZoneResponse)
async def get_zone(
    zone_id: UUID,
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: SiteZoneService = Depends(get_site_zone_service),
) -> ZoneResponse:
    zone = await service.get_zone(user.tenant_id, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail="Zone not found")
    return zone


@router.patch("/zones/{zone_id}", response_model=ZoneResponse)
async def update_zone(
    zone_id: UUID,
    body: ZoneUpdate,
    user: AuthenticatedUser = require_role("admin"),
    service: SiteZoneService = Depends(get_site_zone_service),
) -> ZoneResponse:
    zone = await service.update_zone(user.tenant_id, user.user_id, zone_id, body)
    if zone is None:
        raise HTTPException(status_code=404, detail="Zone not found")
    return zone


@router.delete("/zones/{zone_id}", status_code=204)
async def delete_zone(
    zone_id: UUID,
    user: AuthenticatedUser = require_role("admin"),
    service: SiteZoneService = Depends(get_site_zone_service),
) -> None:
    deleted = await service.delete_zone(user.tenant_id, user.user_id, zone_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Zone not found")
