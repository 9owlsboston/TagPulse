"""TimescaleDB implementation of inventory repositories.

Sprint 15b — see docs/design/tracking-modes.md §4.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import (
    LotModel,
    ProductModel,
    StockItemModel,
    StockMovementModel,
    TagDataMappingModel,
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

# ---- Mappers ----


def _product_to_response(row: ProductModel) -> ProductResponse:
    return ProductResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        sku=row.sku,
        gtin=row.gtin,
        name=row.name,
        category=row.category,
        unit=row.unit,
        attributes=row.attributes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _lot_to_response(row: LotModel) -> LotResponse:
    return LotResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        product_id=row.product_id,
        lot_code=row.lot_code,
        manufactured_at=row.manufactured_at,
        expires_at=row.expires_at,
        metadata=row.metadata_,
        created_at=row.created_at,
    )


def _stock_item_to_response(row: StockItemModel) -> StockItemResponse:
    return StockItemResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        product_id=row.product_id,
        lot_id=row.lot_id,
        binding_value=row.binding_value,
        binding_kind=row.binding_kind,
        state=row.state,
        current_zone_id=row.current_zone_id,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        consumed_at=row.consumed_at,
        metadata=row.metadata_,
    )


def _movement_to_response(row: StockMovementModel) -> StockMovementResponse:
    return StockMovementResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        stock_item_id=row.stock_item_id,
        from_zone_id=row.from_zone_id,
        to_zone_id=row.to_zone_id,
        movement_type=row.movement_type,
        quantity=row.quantity,
        device_id=row.device_id,
        occurred_at=row.occurred_at,
    )


def _mapping_to_response(row: TagDataMappingModel) -> TagDataMappingResponse:
    return TagDataMappingResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        scope_kind=row.scope_kind,
        scope_id=row.scope_id,
        semantic_field=row.semantic_field,
        tag_data_key=row.tag_data_key,
        transform=row.transform,
        created_at=row.created_at,
    )


# ---- Products ----


class TimescaleProductRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, tenant_id: uuid.UUID, payload: ProductCreate
    ) -> ProductResponse:
        row = ProductModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            sku=payload.sku,
            gtin=payload.gtin,
            name=payload.name,
            category=payload.category,
            unit=payload.unit,
            attributes=payload.attributes,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ValueError("sku already in use for this tenant") from exc
        return _product_to_response(row)

    async def get(
        self, tenant_id: uuid.UUID, product_id: uuid.UUID
    ) -> ProductResponse | None:
        stmt = select(ProductModel).where(
            ProductModel.id == product_id,
            ProductModel.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _product_to_response(row) if row else None

    async def get_by_gtin(
        self, tenant_id: uuid.UUID, gtin: str
    ) -> ProductResponse | None:
        stmt = select(ProductModel).where(
            ProductModel.tenant_id == tenant_id, ProductModel.gtin == gtin
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _product_to_response(row) if row else None

    async def list(  # noqa: A003 - mirroring existing repo idiom
        self,
        tenant_id: uuid.UUID,
        *,
        category: str | None = None,
        q: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[ProductResponse]:
        stmt = select(ProductModel).where(ProductModel.tenant_id == tenant_id)
        if category is not None:
            stmt = stmt.where(ProductModel.category == category)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                (ProductModel.name.ilike(like))
                | (ProductModel.sku.ilike(like))
                | (ProductModel.gtin.ilike(like))
            )
        stmt = (
            stmt.order_by(ProductModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [_product_to_response(r) for r in result.scalars()]

    async def update(
        self,
        tenant_id: uuid.UUID,
        product_id: uuid.UUID,
        patch: ProductUpdate,
    ) -> ProductResponse | None:
        stmt = select(ProductModel).where(
            ProductModel.id == product_id,
            ProductModel.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        for k, v in patch.model_dump(exclude_unset=True).items():
            setattr(row, k, v)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ValueError("sku already in use for this tenant") from exc
        return _product_to_response(row)

    async def delete(
        self, tenant_id: uuid.UUID, product_id: uuid.UUID
    ) -> bool:
        """Hard delete; only allowed when no stock_items reference it.

        Includes terminal-state items (consumed/expired/lost) — the FK
        ``stock_items.product_id`` has no ``ON DELETE`` clause, so any
        referencing row will fail the delete with a 500 otherwise.
        """
        ref_stmt = select(func.count(StockItemModel.id)).where(
            StockItemModel.tenant_id == tenant_id,
            StockItemModel.product_id == product_id,
        )
        ref_count = (await self._session.execute(ref_stmt)).scalar_one()
        if ref_count > 0:
            raise ValueError(
                f"cannot delete product with {ref_count} stock items "
                "(retire it instead)"
            )
        stmt = select(ProductModel).where(
            ProductModel.id == product_id,
            ProductModel.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True


# ---- Lots ----


class TimescaleLotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, tenant_id: uuid.UUID, product_id: uuid.UUID, payload: LotCreate
    ) -> LotResponse:
        row = LotModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            product_id=product_id,
            lot_code=payload.lot_code,
            manufactured_at=payload.manufactured_at,
            expires_at=payload.expires_at,
            metadata_=payload.metadata,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ValueError(
                "lot_code already exists for this product"
            ) from exc
        return _lot_to_response(row)

    async def get(
        self, tenant_id: uuid.UUID, lot_id: uuid.UUID
    ) -> LotResponse | None:
        stmt = select(LotModel).where(
            LotModel.id == lot_id, LotModel.tenant_id == tenant_id
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _lot_to_response(row) if row else None

    async def list_for_product(
        self,
        tenant_id: uuid.UUID,
        product_id: uuid.UUID,
        *,
        expiring_within_days: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[LotResponse]:
        stmt = select(LotModel).where(
            LotModel.tenant_id == tenant_id,
            LotModel.product_id == product_id,
        )
        if expiring_within_days is not None:
            cutoff = datetime.now(UTC).timestamp() + (
                expiring_within_days * 86400
            )
            stmt = stmt.where(
                LotModel.expires_at.isnot(None),
                LotModel.expires_at <= datetime.fromtimestamp(cutoff, tz=UTC),
            )
        stmt = (
            stmt.order_by(LotModel.expires_at.asc().nullslast())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [_lot_to_response(r) for r in result.scalars()]

    async def list_all(
        self,
        tenant_id: uuid.UUID,
        *,
        expiring_within_days: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[LotResponse]:
        """Cross-product lot list, ordered by soonest expiry."""
        stmt = select(LotModel).where(LotModel.tenant_id == tenant_id)
        if expiring_within_days is not None:
            cutoff = datetime.now(UTC) + timedelta(days=expiring_within_days)
            stmt = stmt.where(
                LotModel.expires_at.isnot(None),
                LotModel.expires_at <= cutoff,
            )
        stmt = (
            stmt.order_by(LotModel.expires_at.asc().nullslast())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [_lot_to_response(r) for r in result.scalars()]

    async def update(
        self,
        tenant_id: uuid.UUID,
        lot_id: uuid.UUID,
        patch: LotUpdate,
    ) -> LotResponse | None:
        stmt = select(LotModel).where(
            LotModel.id == lot_id, LotModel.tenant_id == tenant_id
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        data = patch.model_dump(exclude_unset=True)
        if "metadata" in data:
            data["metadata_"] = data.pop("metadata")
        for k, v in data.items():
            setattr(row, k, v)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ValueError(
                "lot_code already exists for this product"
            ) from exc
        return _lot_to_response(row)


# ---- Stock items ----


class TimescaleStockItemRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, tenant_id: uuid.UUID, payload: StockItemCreate
    ) -> StockItemResponse:
        row = StockItemModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            product_id=payload.product_id,
            lot_id=payload.lot_id,
            binding_value=payload.binding_value,
            binding_kind=payload.binding_kind,
            state="in_stock",
            metadata_=payload.metadata,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ValueError(
                "active stock item with this binding_value already exists"
            ) from exc
        return _stock_item_to_response(row)

    async def get(
        self, tenant_id: uuid.UUID, stock_item_id: uuid.UUID
    ) -> StockItemResponse | None:
        stmt = select(StockItemModel).where(
            StockItemModel.id == stock_item_id,
            StockItemModel.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _stock_item_to_response(row) if row else None

    async def get_active_by_binding(
        self,
        tenant_id: uuid.UUID,
        binding_kind: str,
        binding_value: str,
    ) -> StockItemResponse | None:
        stmt = select(StockItemModel).where(
            StockItemModel.tenant_id == tenant_id,
            StockItemModel.binding_kind == binding_kind,
            StockItemModel.binding_value == binding_value,
            StockItemModel.state.notin_(["consumed", "expired", "lost"]),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _stock_item_to_response(row) if row else None

    async def list(  # noqa: A003
        self,
        tenant_id: uuid.UUID,
        *,
        product_id: uuid.UUID | None = None,
        lot_id: uuid.UUID | None = None,
        zone_id: uuid.UUID | None = None,
        state: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[StockItemResponse]:
        stmt = select(StockItemModel).where(
            StockItemModel.tenant_id == tenant_id
        )
        if product_id is not None:
            stmt = stmt.where(StockItemModel.product_id == product_id)
        if lot_id is not None:
            stmt = stmt.where(StockItemModel.lot_id == lot_id)
        if zone_id is not None:
            stmt = stmt.where(StockItemModel.current_zone_id == zone_id)
        if state is not None:
            stmt = stmt.where(StockItemModel.state == state)
        stmt = (
            stmt.order_by(StockItemModel.last_seen_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [_stock_item_to_response(r) for r in result.scalars()]

    async def update(
        self,
        tenant_id: uuid.UUID,
        stock_item_id: uuid.UUID,
        patch: StockItemUpdate,
    ) -> StockItemResponse | None:
        stmt = select(StockItemModel).where(
            StockItemModel.id == stock_item_id,
            StockItemModel.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        data = patch.model_dump(exclude_unset=True)
        if "metadata" in data:
            data["metadata_"] = data.pop("metadata")
        if data.get("state") == "consumed" and row.consumed_at is None:
            row.consumed_at = datetime.now(UTC)
        for k, v in data.items():
            setattr(row, k, v)
        await self._session.flush()
        return _stock_item_to_response(row)

    async def record_observation(
        self,
        tenant_id: uuid.UUID,
        stock_item_id: uuid.UUID,
        *,
        zone_id: uuid.UUID | None,
        observed_at: datetime,
    ) -> tuple[uuid.UUID | None, uuid.UUID | None] | None:
        """Update zone + last_seen on a stock_item; return (prev_zone, new_zone).

        Returns ``None`` if the stock item is missing or no longer eligible
        (consumed/expired/lost). Always bumps ``last_seen_at``; only writes
        ``current_zone_id`` when ``zone_id`` differs from the prior value.
        """
        stmt = select(StockItemModel).where(
            StockItemModel.id == stock_item_id,
            StockItemModel.tenant_id == tenant_id,
            StockItemModel.state.notin_(["consumed", "expired", "lost"]),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        prev_zone = row.current_zone_id
        row.last_seen_at = observed_at
        if zone_id != prev_zone:
            row.current_zone_id = zone_id
        await self._session.flush()
        return prev_zone, zone_id

    async def stock_levels(
        self,
        tenant_id: uuid.UUID,
        *,
        product_id: uuid.UUID | None = None,
        zone_id: uuid.UUID | None = None,
    ) -> Sequence[StockLevelRow]:
        params: dict[str, object] = {"tenant_id": tenant_id}
        where = ["tenant_id = :tenant_id"]
        if product_id is not None:
            where.append("product_id = :product_id")
            params["product_id"] = product_id
        if zone_id is not None:
            where.append("current_zone_id = :zone_id")
            params["zone_id"] = zone_id
        sql = (
            "SELECT product_id, lot_id, current_zone_id AS zone_id, quantity "
            "FROM stock_levels WHERE " + " AND ".join(where) +
            " ORDER BY quantity DESC"
        )
        result = await self._session.execute(text(sql), params)
        return [
            StockLevelRow(
                product_id=r["product_id"],
                lot_id=r["lot_id"],
                zone_id=r["zone_id"],
                quantity=int(r["quantity"]),
            )
            for r in result.mappings().all()
        ]


# ---- Stock movements ----


class TimescaleStockMovementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(
        self,
        tenant_id: uuid.UUID,
        stock_item_id: uuid.UUID,
        *,
        from_zone_id: uuid.UUID | None,
        to_zone_id: uuid.UUID | None,
        movement_type: str,
        device_id: uuid.UUID | None,
        occurred_at: datetime,
        quantity: int = 1,
    ) -> StockMovementResponse:
        row = StockMovementModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            stock_item_id=stock_item_id,
            from_zone_id=from_zone_id,
            to_zone_id=to_zone_id,
            movement_type=movement_type,
            quantity=quantity,
            device_id=device_id,
            occurred_at=occurred_at,
        )
        self._session.add(row)
        await self._session.flush()
        return _movement_to_response(row)

    async def list(  # noqa: A003
        self,
        tenant_id: uuid.UUID,
        *,
        stock_item_id: uuid.UUID | None = None,
        product_id: uuid.UUID | None = None,
        zone_id: uuid.UUID | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[StockMovementResponse]:
        stmt = select(StockMovementModel).where(
            StockMovementModel.tenant_id == tenant_id
        )
        if stock_item_id is not None:
            stmt = stmt.where(StockMovementModel.stock_item_id == stock_item_id)
        if product_id is not None:
            # Subquery via JOIN on stock_items.
            stmt = stmt.where(
                StockMovementModel.stock_item_id.in_(
                    select(StockItemModel.id).where(
                        StockItemModel.tenant_id == tenant_id,
                        StockItemModel.product_id == product_id,
                    )
                )
            )
        if zone_id is not None:
            stmt = stmt.where(
                (StockMovementModel.from_zone_id == zone_id)
                | (StockMovementModel.to_zone_id == zone_id)
            )
        if since is not None:
            stmt = stmt.where(StockMovementModel.occurred_at >= since)
        if until is not None:
            stmt = stmt.where(StockMovementModel.occurred_at <= until)
        stmt = (
            stmt.order_by(StockMovementModel.occurred_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [_movement_to_response(r) for r in result.scalars()]


# ---- Tag data mappings ----


class TimescaleTagDataMappingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, tenant_id: uuid.UUID, payload: TagDataMappingCreate
    ) -> TagDataMappingResponse:
        row = TagDataMappingModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            scope_kind=payload.scope_kind,
            scope_id=payload.scope_id,
            semantic_field=payload.semantic_field,
            tag_data_key=payload.tag_data_key,
            transform=payload.transform,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ValueError(
                "mapping already exists for this scope + semantic_field"
            ) from exc
        return _mapping_to_response(row)

    async def list(  # noqa: A003
        self,
        tenant_id: uuid.UUID,
        *,
        scope_kind: str | None = None,
        scope_id: uuid.UUID | None = None,
    ) -> Sequence[TagDataMappingResponse]:
        stmt = select(TagDataMappingModel).where(
            TagDataMappingModel.tenant_id == tenant_id
        )
        if scope_kind is not None:
            stmt = stmt.where(TagDataMappingModel.scope_kind == scope_kind)
        if scope_id is not None:
            stmt = stmt.where(TagDataMappingModel.scope_id == scope_id)
        stmt = stmt.order_by(TagDataMappingModel.created_at.asc())
        result = await self._session.execute(stmt)
        return [_mapping_to_response(r) for r in result.scalars()]

    async def delete(
        self, tenant_id: uuid.UUID, mapping_id: uuid.UUID
    ) -> bool:
        stmt = select(TagDataMappingModel).where(
            TagDataMappingModel.id == mapping_id,
            TagDataMappingModel.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True
