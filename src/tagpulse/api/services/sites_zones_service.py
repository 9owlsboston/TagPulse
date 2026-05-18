"""Sites & Zones service (Sprint 15) — thin facade over repos with audit hooks."""

from __future__ import annotations

import logging
from uuid import UUID

from tagpulse.core.audit import AuditLogger
from tagpulse.models.schemas import (
    SiteCreate,
    SiteResponse,
    SiteUpdate,
    ZoneCreate,
    ZoneResponse,
    ZoneUpdate,
)
from tagpulse.repositories.timescaledb.sites_zones import (
    TimescaleSiteRepository,
    TimescaleZoneRepository,
)

logger = logging.getLogger(__name__)


class SiteZoneService:
    """CRUD operations for sites and zones with audit-log enrichment."""

    def __init__(
        self,
        site_repo: TimescaleSiteRepository,
        zone_repo: TimescaleZoneRepository,
        audit: AuditLogger,
    ) -> None:
        self._sites = site_repo
        self._zones = zone_repo
        self._audit = audit

    # -- Sites --

    async def create_site(
        self, tenant_id: UUID, user_id: UUID | None, payload: SiteCreate
    ) -> SiteResponse:
        site = await self._sites.create(tenant_id, payload)
        await self._audit.log(
            tenant_id=tenant_id,
            user_id=user_id,
            action="site.created",
            resource_type="site",
            resource_id=site.id,
            changes={"name": site.name},
        )
        return site

    async def get_site(self, tenant_id: UUID, site_id: UUID) -> SiteResponse | None:
        return await self._sites.get(tenant_id, site_id)

    async def list_sites(
        self,
        tenant_id: UUID,
        *,
        labels: dict[str, list[str]] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SiteResponse]:
        return await self._sites.list(tenant_id, labels=labels, limit=limit, offset=offset)

    async def update_site(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        site_id: UUID,
        patch: SiteUpdate,
    ) -> SiteResponse | None:
        site = await self._sites.update(tenant_id, site_id, patch)
        if site is not None:
            await self._audit.log(
                tenant_id=tenant_id,
                user_id=user_id,
                action="site.updated",
                resource_type="site",
                resource_id=site.id,
                changes=patch.model_dump(exclude_unset=True),
            )
        return site

    async def delete_site(self, tenant_id: UUID, user_id: UUID | None, site_id: UUID) -> bool:
        deleted = await self._sites.delete(tenant_id, site_id)
        if deleted:
            await self._audit.log(
                tenant_id=tenant_id,
                user_id=user_id,
                action="site.deleted",
                resource_type="site",
                resource_id=site_id,
            )
        return deleted

    # -- Zones --

    async def create_zone(
        self, tenant_id: UUID, user_id: UUID | None, payload: ZoneCreate
    ) -> ZoneResponse:
        zone = await self._zones.create(tenant_id, payload)
        await self._audit.log(
            tenant_id=tenant_id,
            user_id=user_id,
            action="zone.created",
            resource_type="zone",
            resource_id=zone.id,
            changes={"name": zone.name, "site_id": str(zone.site_id)},
        )
        return zone

    async def get_zone(self, tenant_id: UUID, zone_id: UUID) -> ZoneResponse | None:
        return await self._zones.get(tenant_id, zone_id)

    async def list_zones(
        self,
        tenant_id: UUID,
        *,
        site_id: UUID | None = None,
        labels: dict[str, list[str]] | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[ZoneResponse]:
        return await self._zones.list(
            tenant_id, site_id=site_id, labels=labels, limit=limit, offset=offset
        )

    async def update_zone(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        zone_id: UUID,
        patch: ZoneUpdate,
    ) -> ZoneResponse | None:
        zone = await self._zones.update(tenant_id, zone_id, patch)
        if zone is not None:
            await self._audit.log(
                tenant_id=tenant_id,
                user_id=user_id,
                action="zone.updated",
                resource_type="zone",
                resource_id=zone.id,
                changes=patch.model_dump(exclude_unset=True),
            )
        return zone

    async def delete_zone(self, tenant_id: UUID, user_id: UUID | None, zone_id: UUID) -> bool:
        deleted = await self._zones.delete(tenant_id, zone_id)
        if deleted:
            await self._audit.log(
                tenant_id=tenant_id,
                user_id=user_id,
                action="zone.deleted",
                resource_type="zone",
                resource_id=zone_id,
            )
        return deleted

    async def get_zone_for_reader(self, tenant_id: UUID, device_id: UUID) -> ZoneResponse | None:
        return await self._zones.get_zone_for_reader(tenant_id, device_id)
