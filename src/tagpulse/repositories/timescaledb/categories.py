"""TimescaleDB repository for Categories.

Sprint 34; implements [ADR-019](../../../../docs/adr/019-categories.md).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import AssetModel, CategoryModel
from tagpulse.models.schemas import (
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
)


def _to_response(row: CategoryModel) -> CategoryResponse:
    return CategoryResponse.model_validate(row)


class CategoryInUseError(RuntimeError):
    """Raised when delete is attempted against a Category that still has assets."""

    def __init__(self, category_id: uuid.UUID, asset_count: int) -> None:
        super().__init__(f"Category {category_id} is in use by {asset_count} asset(s)")
        self.category_id = category_id
        self.asset_count = asset_count


class CategoryNameConflictError(ValueError):
    """Raised when a Category create/update would collide with an existing name."""


class TimescaleCategoryRepository:
    """Persists categories to TimescaleDB."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        category_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[CategoryResponse]:
        stmt = select(CategoryModel).where(CategoryModel.tenant_id == tenant_id)
        if category_type is not None:
            stmt = stmt.where(CategoryModel.category_type == category_type)
        stmt = stmt.order_by(CategoryModel.name.asc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return [_to_response(r) for r in result.scalars()]

    async def get(self, tenant_id: uuid.UUID, category_id: uuid.UUID) -> CategoryResponse | None:
        stmt = select(CategoryModel).where(
            CategoryModel.id == category_id,
            CategoryModel.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_response(row) if row else None

    async def create(self, tenant_id: uuid.UUID, payload: CategoryCreate) -> CategoryResponse:
        row = CategoryModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            name=payload.name,
            sku_upc=payload.sku_upc,
            description=payload.description,
            category_type=payload.category_type,
            required_pixels=payload.required_pixels,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise CategoryNameConflictError(
                f"Category '{payload.name}' already exists for this tenant"
            ) from exc
        return _to_response(row)

    async def update(
        self,
        tenant_id: uuid.UUID,
        category_id: uuid.UUID,
        patch: CategoryUpdate,
    ) -> CategoryResponse | None:
        stmt = select(CategoryModel).where(
            CategoryModel.id == category_id,
            CategoryModel.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        patch_data = patch.model_dump(exclude_unset=True)
        for k, v in patch_data.items():
            setattr(row, k, v)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise CategoryNameConflictError(
                f"Category '{patch.name}' already exists for this tenant"
            ) from exc
        return _to_response(row)

    async def count_referencing_assets(self, tenant_id: uuid.UUID, category_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(AssetModel)
            .where(
                AssetModel.tenant_id == tenant_id,
                AssetModel.category_id == category_id,
            )
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def delete(self, tenant_id: uuid.UUID, category_id: uuid.UUID) -> bool:
        """Hard delete. Raises ``CategoryInUseError`` if any asset still references it.

        Returns ``False`` if the category does not exist in this tenant.
        """
        in_use = await self.count_referencing_assets(tenant_id, category_id)
        if in_use > 0:
            raise CategoryInUseError(category_id, in_use)
        stmt = select(CategoryModel).where(
            CategoryModel.id == category_id,
            CategoryModel.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True
