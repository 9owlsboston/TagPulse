"""Inventory service (Sprint 15b — products, lots, stock items, movements,
tag_data_mappings).

Audit hooks fire on every mutation per docs/design/admin-ui.md and
docs/design/tracking-modes.md.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from tagpulse.core.audit import AuditLogger
from tagpulse.core.telemetry_caches import (
    LATEST_TELEMETRY_CACHE,
    SUBJECT_KINDS_CACHE,
)
from tagpulse.models.schemas import (
    LotCreate,
    LotResponse,
    LotUpdate,
    ProductCreate,
    ProductResponse,
    ProductUpdate,
    StockItemCreate,
    StockItemResponse,
    StockItemUpdate,
    StockLevelRow,
    StockMovementResponse,
    TagDataMappingCreate,
    TagDataMappingResponse,
)
from tagpulse.repositories.timescaledb.inventory import (
    TimescaleLotRepository,
    TimescaleProductRepository,
    TimescaleStockItemRepository,
    TimescaleStockMovementRepository,
    TimescaleTagDataMappingRepository,
)
from tagpulse.repositories.timescaledb.telemetry import (
    TimescaleTelemetryReadingsRepository,
)
from tagpulse.repositories.timescaledb.tenants import TimescaleTenantRepository

logger = logging.getLogger(__name__)


class ProductNotFoundError(Exception):
    """Raised when a product is missing for the caller's tenant."""


class StockItemNotFoundError(Exception):
    """Raised when a stock_item is missing for the caller's tenant."""


class InventoryService:
    """CRUD + state transitions for inventory entities."""

    def __init__(
        self,
        product_repo: TimescaleProductRepository,
        lot_repo: TimescaleLotRepository,
        stock_repo: TimescaleStockItemRepository,
        movement_repo: TimescaleStockMovementRepository,
        mapping_repo: TimescaleTagDataMappingRepository,
        audit: AuditLogger,
        telemetry_readings_repo: (
            TimescaleTelemetryReadingsRepository | None
        ) = None,
        tenant_repo: TimescaleTenantRepository | None = None,
    ) -> None:
        self._products = product_repo
        self._lots = lot_repo
        self._stock = stock_repo
        self._movements = movement_repo
        self._mappings = mapping_repo
        self._audit = audit
        self._telemetry_readings = telemetry_readings_repo
        self._tenant_repo = tenant_repo

    # -- Products --

    async def create_product(
        self, tenant_id: UUID, user_id: UUID | None, payload: ProductCreate
    ) -> ProductResponse:
        product = await self._products.create(tenant_id, payload)
        await self._audit.log(
            tenant_id=tenant_id,
            user_id=user_id,
            action="product.created",
            resource_type="product",
            resource_id=product.id,
            changes={"sku": product.sku, "name": product.name},
        )
        return product

    async def get_product(
        self, tenant_id: UUID, product_id: UUID
    ) -> ProductResponse | None:
        return await self._products.get(tenant_id, product_id)

    async def list_products(
        self,
        tenant_id: UUID,
        *,
        category: str | None = None,
        q: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[ProductResponse]:
        return await self._products.list(
            tenant_id, category=category, q=q, limit=limit, offset=offset
        )

    async def update_product(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        product_id: UUID,
        patch: ProductUpdate,
    ) -> ProductResponse | None:
        result = await self._products.update(tenant_id, product_id, patch)
        if result is not None:
            await self._audit.log(
                tenant_id=tenant_id,
                user_id=user_id,
                action="product.updated",
                resource_type="product",
                resource_id=product_id,
                changes=patch.model_dump(exclude_unset=True),
            )
        return result

    async def delete_product(
        self, tenant_id: UUID, user_id: UUID | None, product_id: UUID
    ) -> bool:
        deleted = await self._products.delete(tenant_id, product_id)
        if deleted:
            await self._audit.log(
                tenant_id=tenant_id,
                user_id=user_id,
                action="product.deleted",
                resource_type="product",
                resource_id=product_id,
            )
        return deleted

    # -- Lots --

    async def create_lot(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        product_id: UUID,
        payload: LotCreate,
    ) -> LotResponse:
        product = await self._products.get(tenant_id, product_id)
        if product is None:
            raise ProductNotFoundError(product_id)
        lot = await self._lots.create(tenant_id, product_id, payload)
        await self._audit.log(
            tenant_id=tenant_id,
            user_id=user_id,
            action="lot.created",
            resource_type="lot",
            resource_id=lot.id,
            changes={"lot_code": lot.lot_code, "product_id": str(product_id)},
        )
        return lot

    async def list_lots_for_product(
        self,
        tenant_id: UUID,
        product_id: UUID,
        *,
        expiring_within_days: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[LotResponse]:
        return await self._lots.list_for_product(
            tenant_id,
            product_id,
            expiring_within_days=expiring_within_days,
            limit=limit,
            offset=offset,
        )

    async def list_lots(
        self,
        tenant_id: UUID,
        *,
        expiring_within_days: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[LotResponse]:
        """Cross-product lot list, ordered by soonest expiry first."""
        return await self._lots.list_all(
            tenant_id,
            expiring_within_days=expiring_within_days,
            limit=limit,
            offset=offset,
        )

    async def get_lot(
        self,
        tenant_id: UUID,
        lot_id: UUID,
        *,
        with_latest_telemetry: bool = False,
    ) -> LotResponse | None:
        """Fetch a single lot, optionally with embedded latest telemetry.

        Sprint 19: when ``with_latest_telemetry=True`` and the tenant
        has opted into ``subject_kind='lot'`` telemetry, embeds up to
        5 most-recent metrics per the same contract as
        :meth:`AssetService.get_asset`.
        """
        lot = await self._lots.get(tenant_id, lot_id)
        if lot is None or not with_latest_telemetry:
            return lot
        if self._telemetry_readings is None or self._tenant_repo is None:
            return lot
        kinds = SUBJECT_KINDS_CACHE.get(tenant_id)
        if kinds is None:
            kinds = tuple(
                await self._tenant_repo.get_telemetry_subject_kinds(tenant_id)
            )
            SUBJECT_KINDS_CACHE.set(tenant_id, kinds)
        if "lot" not in kinds:
            return lot
        cache_key = (tenant_id, "lot", lot_id)
        latest = LATEST_TELEMETRY_CACHE.get(cache_key)
        if latest is None:
            latest = await self._telemetry_readings.latest_per_metric(
                tenant_id=tenant_id,
                subject_kind="lot",
                subject_id=lot_id,
                limit=5,
            )
            LATEST_TELEMETRY_CACHE.set(cache_key, latest)
        return lot.model_copy(update={"latest_telemetry": latest})

    async def update_lot(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        lot_id: UUID,
        patch: LotUpdate,
    ) -> LotResponse | None:
        result = await self._lots.update(tenant_id, lot_id, patch)
        if result is not None:
            await self._audit.log(
                tenant_id=tenant_id,
                user_id=user_id,
                action="lot.updated",
                resource_type="lot",
                resource_id=lot_id,
                changes=patch.model_dump(exclude_unset=True),
            )
        return result

    # -- Stock items --

    async def create_stock_item(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        payload: StockItemCreate,
    ) -> StockItemResponse:
        product = await self._products.get(tenant_id, payload.product_id)
        if product is None:
            raise ProductNotFoundError(payload.product_id)
        item = await self._stock.create(tenant_id, payload)
        await self._audit.log(
            tenant_id=tenant_id,
            user_id=user_id,
            action="stock_item.created",
            resource_type="stock_item",
            resource_id=item.id,
            changes={
                "product_id": str(item.product_id),
                "binding_value": item.binding_value,
            },
        )
        return item

    async def get_stock_item(
        self, tenant_id: UUID, stock_item_id: UUID
    ) -> StockItemResponse | None:
        return await self._stock.get(tenant_id, stock_item_id)

    async def list_stock_items(
        self,
        tenant_id: UUID,
        *,
        product_id: UUID | None = None,
        lot_id: UUID | None = None,
        zone_id: UUID | None = None,
        state: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[StockItemResponse]:
        return await self._stock.list(
            tenant_id,
            product_id=product_id,
            lot_id=lot_id,
            zone_id=zone_id,
            state=state,
            limit=limit,
            offset=offset,
        )

    async def update_stock_item(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        stock_item_id: UUID,
        patch: StockItemUpdate,
    ) -> StockItemResponse | None:
        result = await self._stock.update(tenant_id, stock_item_id, patch)
        if result is not None:
            await self._audit.log(
                tenant_id=tenant_id,
                user_id=user_id,
                action="stock_item.updated",
                resource_type="stock_item",
                resource_id=stock_item_id,
                changes=patch.model_dump(exclude_unset=True),
            )
        return result

    async def stock_levels(
        self,
        tenant_id: UUID,
        *,
        product_id: UUID | None = None,
        zone_id: UUID | None = None,
    ) -> Sequence[StockLevelRow]:
        return await self._stock.stock_levels(
            tenant_id, product_id=product_id, zone_id=zone_id
        )

    # -- Stock movements (read-only via API; ingestion writes them) --

    async def list_movements(
        self,
        tenant_id: UUID,
        *,
        stock_item_id: UUID | None = None,
        product_id: UUID | None = None,
        zone_id: UUID | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[StockMovementResponse]:
        return await self._movements.list(
            tenant_id,
            stock_item_id=stock_item_id,
            product_id=product_id,
            zone_id=zone_id,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )

    # -- Tag data mappings (admin) --

    async def create_tag_data_mapping(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        payload: TagDataMappingCreate,
    ) -> TagDataMappingResponse:
        mapping = await self._mappings.create(tenant_id, payload)
        await self._audit.log(
            tenant_id=tenant_id,
            user_id=user_id,
            action="tag_data_mapping.created",
            resource_type="tag_data_mapping",
            resource_id=mapping.id,
            changes={
                "scope_kind": mapping.scope_kind,
                "semantic_field": mapping.semantic_field,
                "tag_data_key": mapping.tag_data_key,
            },
        )
        return mapping

    async def list_tag_data_mappings(
        self,
        tenant_id: UUID,
        *,
        scope_kind: str | None = None,
        scope_id: UUID | None = None,
    ) -> Sequence[TagDataMappingResponse]:
        return await self._mappings.list(
            tenant_id, scope_kind=scope_kind, scope_id=scope_id
        )

    async def delete_tag_data_mapping(
        self, tenant_id: UUID, user_id: UUID | None, mapping_id: UUID
    ) -> bool:
        deleted = await self._mappings.delete(tenant_id, mapping_id)
        if deleted:
            await self._audit.log(
                tenant_id=tenant_id,
                user_id=user_id,
                action="tag_data_mapping.deleted",
                resource_type="tag_data_mapping",
                resource_id=mapping_id,
            )
        return deleted
