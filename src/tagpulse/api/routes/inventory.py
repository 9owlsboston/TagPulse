"""Inventory CRUD APIs (Sprint 15b).

Permissions per docs/design/tracking-modes.md §6:
- viewer+: GET
- editor+: POST/PATCH/DELETE on stock items + lots
- admin: products + tag_data_mappings
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from tagpulse.api.dependencies import get_inventory_service
from tagpulse.api.services.inventory_service import (
    InventoryService,
    ProductNotFoundError,
)
from tagpulse.core.user_auth import AuthenticatedUser, require_role
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
    StockMovementCreate,
    StockMovementResponse,
    TagDataMappingCreate,
    TagDataMappingResponse,
    TagDataMappingUpdate,
)

router = APIRouter(tags=["inventory"])


# -- Products --


@router.post("/products", response_model=ProductResponse, status_code=201)
async def create_product(
    body: ProductCreate,
    user: AuthenticatedUser = require_role("admin"),
    service: InventoryService = Depends(get_inventory_service),
) -> ProductResponse:
    try:
        return await service.create_product(user.tenant_id, user.user_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/products", response_model=list[ProductResponse])
async def list_products(
    category: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: InventoryService = Depends(get_inventory_service),
) -> list[ProductResponse]:
    return list(
        await service.list_products(
            user.tenant_id, category=category, q=q, limit=limit, offset=offset
        )
    )


@router.get("/products/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: UUID,
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: InventoryService = Depends(get_inventory_service),
) -> ProductResponse:
    product = await service.get_product(user.tenant_id, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.patch("/products/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: UUID,
    body: ProductUpdate,
    user: AuthenticatedUser = require_role("admin"),
    service: InventoryService = Depends(get_inventory_service),
) -> ProductResponse:
    try:
        product = await service.update_product(
            user.tenant_id, user.user_id, product_id, body
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.delete("/products/{product_id}", status_code=204)
async def delete_product(
    product_id: UUID,
    user: AuthenticatedUser = require_role("admin"),
    service: InventoryService = Depends(get_inventory_service),
) -> None:
    try:
        deleted = await service.delete_product(
            user.tenant_id, user.user_id, product_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if not deleted:
        raise HTTPException(status_code=404, detail="Product not found")


# -- Lots (nested under product) --


@router.post(
    "/products/{product_id}/lots", response_model=LotResponse, status_code=201
)
async def create_lot(
    product_id: UUID,
    body: LotCreate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    service: InventoryService = Depends(get_inventory_service),
) -> LotResponse:
    try:
        return await service.create_lot(
            user.tenant_id, user.user_id, product_id, body
        )
    except ProductNotFoundError:
        raise HTTPException(status_code=404, detail="Product not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/products/{product_id}/lots", response_model=list[LotResponse])
async def list_lots(
    product_id: UUID,
    expiring_within_days: int | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: InventoryService = Depends(get_inventory_service),
) -> list[LotResponse]:
    return list(
        await service.list_lots_for_product(
            user.tenant_id,
            product_id,
            expiring_within_days=expiring_within_days,
            limit=limit,
            offset=offset,
        )
    )


@router.get("/lots", response_model=list[LotResponse])
async def list_all_lots(
    expiring_within_days: int | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: InventoryService = Depends(get_inventory_service),
) -> list[LotResponse]:
    """Cross-product lot list. Used by the UI Lot Expiry Queue page."""
    return list(
        await service.list_lots(
            user.tenant_id,
            expiring_within_days=expiring_within_days,
            limit=limit,
            offset=offset,
        )
    )


@router.get("/lots/{lot_id}", response_model=LotResponse)
async def get_lot(
    lot_id: UUID,
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: InventoryService = Depends(get_inventory_service),
) -> LotResponse:
    """Fetch a single lot. Sprint 19: embeds ``latest_telemetry`` when
    the tenant has opted into ``subject_kind='lot'``."""
    lot = await service.get_lot(user.tenant_id, lot_id, with_latest_telemetry=True)
    if lot is None:
        raise HTTPException(status_code=404, detail="Lot not found")
    return lot


@router.patch("/lots/{lot_id}", response_model=LotResponse)
async def update_lot(
    lot_id: UUID,
    body: LotUpdate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    service: InventoryService = Depends(get_inventory_service),
) -> LotResponse:
    try:
        lot = await service.update_lot(
            user.tenant_id, user.user_id, lot_id, body
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if lot is None:
        raise HTTPException(status_code=404, detail="Lot not found")
    return lot


@router.delete("/lots/{lot_id}", status_code=204)
async def delete_lot(
    lot_id: UUID,
    user: AuthenticatedUser = require_role("admin"),
    service: InventoryService = Depends(get_inventory_service),
) -> None:
    try:
        deleted = await service.delete_lot(user.tenant_id, user.user_id, lot_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if not deleted:
        raise HTTPException(status_code=404, detail="Lot not found")


# -- Stock items --


@router.post(
    "/stock-items", response_model=StockItemResponse, status_code=201
)
async def create_stock_item(
    body: StockItemCreate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    service: InventoryService = Depends(get_inventory_service),
) -> StockItemResponse:
    try:
        return await service.create_stock_item(
            user.tenant_id, user.user_id, body
        )
    except ProductNotFoundError:
        raise HTTPException(status_code=404, detail="Product not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/stock-items", response_model=list[StockItemResponse])
async def list_stock_items(
    product_id: UUID | None = Query(default=None),
    lot_id: UUID | None = Query(default=None),
    zone_id: UUID | None = Query(default=None),
    state: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: InventoryService = Depends(get_inventory_service),
) -> list[StockItemResponse]:
    return list(
        await service.list_stock_items(
            user.tenant_id,
            product_id=product_id,
            lot_id=lot_id,
            zone_id=zone_id,
            state=state,
            limit=limit,
            offset=offset,
        )
    )


@router.get("/stock-items/{stock_item_id}", response_model=StockItemResponse)
async def get_stock_item(
    stock_item_id: UUID,
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: InventoryService = Depends(get_inventory_service),
) -> StockItemResponse:
    item = await service.get_stock_item(user.tenant_id, stock_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Stock item not found")
    return item


@router.patch(
    "/stock-items/{stock_item_id}", response_model=StockItemResponse
)
async def update_stock_item(
    stock_item_id: UUID,
    body: StockItemUpdate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    service: InventoryService = Depends(get_inventory_service),
) -> StockItemResponse:
    item = await service.update_stock_item(
        user.tenant_id, user.user_id, stock_item_id, body
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Stock item not found")
    return item


@router.delete("/stock-items/{stock_item_id}", status_code=204)
async def delete_stock_item(
    stock_item_id: UUID,
    force: bool = Query(default=False),
    user: AuthenticatedUser = require_role("admin"),
    service: InventoryService = Depends(get_inventory_service),
) -> None:
    try:
        deleted = await service.delete_stock_item(
            user.tenant_id, user.user_id, stock_item_id, force=force
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if not deleted:
        raise HTTPException(status_code=404, detail="Stock item not found")


# -- Aggregated views --


@router.get("/stock-levels", response_model=list[StockLevelRow])
async def stock_levels(
    product_id: UUID | None = Query(default=None),
    zone_id: UUID | None = Query(default=None),
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: InventoryService = Depends(get_inventory_service),
) -> list[StockLevelRow]:
    return list(
        await service.stock_levels(
            user.tenant_id, product_id=product_id, zone_id=zone_id
        )
    )


@router.get("/stock-movements", response_model=list[StockMovementResponse])
async def stock_movements(
    stock_item_id: UUID | None = Query(default=None),
    product_id: UUID | None = Query(default=None),
    zone_id: UUID | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: InventoryService = Depends(get_inventory_service),
) -> list[StockMovementResponse]:
    return list(
        await service.list_movements(
            user.tenant_id,
            stock_item_id=stock_item_id,
            product_id=product_id,
            zone_id=zone_id,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )
    )


@router.post(
    "/stock-movements", response_model=StockMovementResponse, status_code=201
)
async def create_stock_movement(
    body: StockMovementCreate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    service: InventoryService = Depends(get_inventory_service),
) -> StockMovementResponse:
    """Create a manual stock adjustment (enter/exit/adjustment)."""
    try:
        return await service.create_manual_movement(
            user.tenant_id, user.user_id, body
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


# -- Tag data mappings (admin) --


@router.post(
    "/tag-data-mappings",
    response_model=TagDataMappingResponse,
    status_code=201,
)
async def create_tag_data_mapping(
    body: TagDataMappingCreate,
    user: AuthenticatedUser = require_role("admin"),
    service: InventoryService = Depends(get_inventory_service),
) -> TagDataMappingResponse:
    try:
        return await service.create_tag_data_mapping(
            user.tenant_id, user.user_id, body
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get(
    "/tag-data-mappings", response_model=list[TagDataMappingResponse]
)
async def list_tag_data_mappings(
    scope_kind: str | None = Query(default=None),
    scope_id: UUID | None = Query(default=None),
    user: AuthenticatedUser = require_role("admin"),
    service: InventoryService = Depends(get_inventory_service),
) -> list[TagDataMappingResponse]:
    return list(
        await service.list_tag_data_mappings(
            user.tenant_id, scope_kind=scope_kind, scope_id=scope_id
        )
    )


@router.delete("/tag-data-mappings/{mapping_id}", status_code=204)
async def delete_tag_data_mapping(
    mapping_id: UUID,
    user: AuthenticatedUser = require_role("admin"),
    service: InventoryService = Depends(get_inventory_service),
) -> None:
    deleted = await service.delete_tag_data_mapping(
        user.tenant_id, user.user_id, mapping_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Mapping not found")


@router.patch(
    "/tag-data-mappings/{mapping_id}", response_model=TagDataMappingResponse
)
async def update_tag_data_mapping(
    mapping_id: UUID,
    body: TagDataMappingUpdate,
    user: AuthenticatedUser = require_role("admin"),
    service: InventoryService = Depends(get_inventory_service),
) -> TagDataMappingResponse:
    try:
        result = await service.update_tag_data_mapping(
            user.tenant_id, user.user_id, mapping_id, body
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if result is None:
        raise HTTPException(status_code=404, detail="Mapping not found")
    return result
