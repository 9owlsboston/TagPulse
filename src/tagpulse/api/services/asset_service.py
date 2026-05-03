"""Asset & tag-binding service (Sprint 15 Phase B)."""

from __future__ import annotations

import logging
from uuid import UUID

from tagpulse.core.audit import AuditLogger
from tagpulse.core.otel_metrics import tag_collisions_global_counter
from tagpulse.models.schemas import (
    AssetCreate,
    AssetResponse,
    AssetTagBindingCreate,
    AssetTagBindingResponse,
    AssetUpdate,
)
from tagpulse.repositories.timescaledb.assets import (
    TimescaleAssetRepository,
    TimescaleAssetTagBindingRepository,
)

logger = logging.getLogger(__name__)


class AssetService:
    """CRUD operations for assets and tag bindings, with audit hooks."""

    def __init__(
        self,
        asset_repo: TimescaleAssetRepository,
        binding_repo: TimescaleAssetTagBindingRepository,
        audit: AuditLogger,
    ) -> None:
        self._assets = asset_repo
        self._bindings = binding_repo
        self._audit = audit

    # -- Assets --

    async def create_asset(
        self, tenant_id: UUID, user_id: UUID | None, payload: AssetCreate
    ) -> AssetResponse:
        asset = await self._assets.create(tenant_id, payload)
        await self._audit.log(
            tenant_id=tenant_id,
            user_id=user_id,
            action="asset.created",
            resource_type="asset",
            resource_id=asset.id,
            changes={"name": asset.name, "asset_type": asset.asset_type},
        )
        return asset

    async def get_asset(
        self, tenant_id: UUID, asset_id: UUID
    ) -> AssetResponse | None:
        return await self._assets.get(tenant_id, asset_id)

    async def list_assets(
        self,
        tenant_id: UUID,
        *,
        asset_type: str | None = None,
        status: str | None = None,
        q: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AssetResponse]:
        return await self._assets.list(
            tenant_id,
            asset_type=asset_type,
            status=status,
            q=q,
            limit=limit,
            offset=offset,
        )

    async def update_asset(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        asset_id: UUID,
        patch: AssetUpdate,
    ) -> AssetResponse | None:
        asset = await self._assets.update(tenant_id, asset_id, patch)
        if asset is not None:
            await self._audit.log(
                tenant_id=tenant_id,
                user_id=user_id,
                action="asset.updated",
                resource_type="asset",
                resource_id=asset.id,
                changes=patch.model_dump(exclude_unset=True),
            )
        return asset

    async def retire_asset(
        self, tenant_id: UUID, user_id: UUID | None, asset_id: UUID
    ) -> bool:
        deleted = await self._assets.delete(tenant_id, asset_id)
        if deleted:
            await self._audit.log(
                tenant_id=tenant_id,
                user_id=user_id,
                action="asset.retired",
                resource_type="asset",
                resource_id=asset_id,
            )
        return deleted

    # -- Bindings --

    async def bind_tag(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        asset_id: UUID,
        payload: AssetTagBindingCreate,
    ) -> AssetTagBindingResponse:
        binding = await self._bindings.create(tenant_id, asset_id, payload)
        await self._audit.log(
            tenant_id=tenant_id,
            user_id=user_id,
            action="asset.bound",
            resource_type="asset",
            resource_id=asset_id,
            changes={
                "binding_value": binding.binding_value,
                "binding_kind": binding.binding_kind,
            },
        )
        return binding

    async def list_bindings(
        self,
        tenant_id: UUID,
        asset_id: UUID,
        *,
        active_only: bool = False,
    ) -> list[AssetTagBindingResponse]:
        return await self._bindings.list_for_asset(
            tenant_id, asset_id, active_only=active_only
        )

    async def unbind_tag(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        asset_id: UUID,
        binding_value: str,
    ) -> bool:
        unbound = await self._bindings.unbind(tenant_id, asset_id, binding_value)
        if unbound:
            await self._audit.log(
                tenant_id=tenant_id,
                user_id=user_id,
                action="asset.unbound",
                resource_type="asset",
                resource_id=asset_id,
                changes={"binding_value": binding_value},
            )
        return unbound

    async def get_active_binding(
        self, tenant_id: UUID, binding_value: str
    ) -> AssetTagBindingResponse | None:
        """Ingestion hot-path lookup."""
        return await self._bindings.get_active_by_value(tenant_id, binding_value)

    # -- Admin --

    async def count_other_tenant_collisions(
        self, tenant_id: UUID, binding_value: str
    ) -> int:
        count = await self._bindings.count_other_tenant_collisions(
            tenant_id, binding_value
        )
        tag_collisions_global_counter.add(
            1,
            {
                "tenant_id": str(tenant_id),
                "had_collision": "true" if count > 0 else "false",
            },
        )
        return count
