"""TimescaleDB implementation of asset and asset_tag_binding repositories.

Sprint 15 Phase B — see docs/design/assets-and-zones.md.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.api.label_filter import apply_label_filter
from tagpulse.models.database import AssetModel, AssetTagBindingModel
from tagpulse.models.schemas import (
    AssetCreate,
    AssetResponse,
    AssetTagBindingCreate,
    AssetTagBindingResponse,
    AssetUpdate,
)


def _asset_to_response(row: AssetModel) -> AssetResponse:
    return AssetResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        external_ref=row.external_ref,
        name=row.name,
        asset_type=row.asset_type,
        status=row.status,
        parent_asset_id=row.parent_asset_id,
        category_id=row.category_id,
        metadata=row.metadata_,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _binding_to_response(row: AssetTagBindingModel) -> AssetTagBindingResponse:
    return AssetTagBindingResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        asset_id=row.asset_id,
        binding_value=row.binding_value,
        binding_kind=row.binding_kind,
        bound_at=row.bound_at,
        unbound_at=row.unbound_at,
        metadata=row.metadata_,
    )


class TimescaleAssetRepository:
    """Persists assets to TimescaleDB."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, tenant_id: uuid.UUID, asset: AssetCreate) -> AssetResponse:
        row = AssetModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            external_ref=asset.external_ref,
            name=asset.name,
            asset_type=asset.asset_type,
            status=asset.status,
            parent_asset_id=asset.parent_asset_id,
            category_id=asset.category_id,
            metadata_=asset.metadata,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ValueError(
                f"Asset external_ref '{asset.external_ref}' already exists for this tenant"
            ) from exc
        return _asset_to_response(row)

    async def get(self, tenant_id: uuid.UUID, asset_id: uuid.UUID) -> AssetResponse | None:
        stmt = select(AssetModel).where(
            AssetModel.id == asset_id, AssetModel.tenant_id == tenant_id
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _asset_to_response(row) if row else None

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        asset_type: str | None = None,
        status: str | None = None,
        category_id: uuid.UUID | None = None,
        q: str | None = None,
        labels: dict[str, list[str]] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AssetResponse]:
        stmt = select(AssetModel).where(AssetModel.tenant_id == tenant_id)
        if asset_type is not None:
            stmt = stmt.where(AssetModel.asset_type == asset_type)
        if status is not None:
            stmt = stmt.where(AssetModel.status == status)
        if category_id is not None:
            stmt = stmt.where(AssetModel.category_id == category_id)
        if q:
            like = f"%{q}%"
            stmt = stmt.where((AssetModel.name.ilike(like)) | (AssetModel.external_ref.ilike(like)))
        stmt = apply_label_filter(
            stmt,
            tenant_id=tenant_id,
            entity_type="asset",
            entity_id_col=AssetModel.id,
            labels=labels,
        )
        stmt = stmt.order_by(AssetModel.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return [_asset_to_response(r) for r in result.scalars()]

    async def update(
        self,
        tenant_id: uuid.UUID,
        asset_id: uuid.UUID,
        patch: AssetUpdate,
    ) -> AssetResponse | None:
        stmt = select(AssetModel).where(
            AssetModel.id == asset_id, AssetModel.tenant_id == tenant_id
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        patch_data = patch.model_dump(exclude_unset=True)
        if "metadata" in patch_data:
            patch_data["metadata_"] = patch_data.pop("metadata")
        for k, v in patch_data.items():
            setattr(row, k, v)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ValueError("external_ref already in use for this tenant") from exc
        return _asset_to_response(row)

    async def delete(self, tenant_id: uuid.UUID, asset_id: uuid.UUID) -> bool:
        """Soft delete by marking status='retired'."""
        stmt = select(AssetModel).where(
            AssetModel.id == asset_id, AssetModel.tenant_id == tenant_id
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        row.status = "retired"
        await self._session.flush()
        return True

    async def set_parent(
        self,
        tenant_id: uuid.UUID,
        asset_id: uuid.UUID,
        parent_asset_id: uuid.UUID | None,
    ) -> tuple[AssetResponse, uuid.UUID | None] | None:
        """Set parent_asset_id and return (updated_asset, prior_parent_id).

        Returns None if the asset is not found in this tenant.
        """
        stmt = select(AssetModel).where(
            AssetModel.id == asset_id, AssetModel.tenant_id == tenant_id
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        prior = row.parent_asset_id
        row.parent_asset_id = parent_asset_id
        await self._session.flush()
        return _asset_to_response(row), prior

    async def get_descendants(
        self, tenant_id: uuid.UUID, asset_id: uuid.UUID
    ) -> Sequence[tuple[AssetResponse, int]]:
        """Return descendants of asset_id with their depth (1-based).

        Uses a recursive CTE; tenant_id is enforced at every level.
        Excludes the root itself; excludes retired assets.
        """
        from sqlalchemy import text

        stmt = text(
            """
            WITH RECURSIVE descendants AS (
                SELECT id, tenant_id, external_ref, name, asset_type, status,
                       parent_asset_id, category_id, metadata, created_at, updated_at,
                       1 AS depth
                FROM assets
                WHERE tenant_id = :tenant_id
                  AND parent_asset_id = :root_id
                  AND status != 'retired'
                UNION ALL
                SELECT a.id, a.tenant_id, a.external_ref, a.name, a.asset_type,
                       a.status, a.parent_asset_id, a.category_id, a.metadata, a.created_at,
                       a.updated_at, d.depth + 1
                FROM assets a
                JOIN descendants d ON a.parent_asset_id = d.id
                WHERE a.tenant_id = :tenant_id
                  AND a.status != 'retired'
            )
            SELECT * FROM descendants ORDER BY depth, name
            """
        )
        result = await self._session.execute(stmt, {"tenant_id": tenant_id, "root_id": asset_id})
        rows = result.mappings().all()
        out: list[tuple[AssetResponse, int]] = []
        for r in rows:
            out.append(
                (
                    AssetResponse(
                        id=r["id"],
                        tenant_id=r["tenant_id"],
                        external_ref=r["external_ref"],
                        name=r["name"],
                        asset_type=r["asset_type"],
                        status=r["status"],
                        parent_asset_id=r["parent_asset_id"],
                        category_id=r["category_id"],
                        metadata=r["metadata"],
                        created_at=r["created_at"],
                        updated_at=r["updated_at"],
                    ),
                    r["depth"],
                )
            )
        return out


class TimescaleAssetTagBindingRepository:
    """Persists asset_tag_bindings and supports lookup/collision queries."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        tenant_id: uuid.UUID,
        asset_id: uuid.UUID,
        payload: AssetTagBindingCreate,
    ) -> AssetTagBindingResponse:
        row = AssetTagBindingModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            asset_id=asset_id,
            binding_value=payload.binding_value,
            binding_kind=payload.binding_kind,
            metadata_=payload.metadata,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ValueError(
                f"binding_value '{payload.binding_value}' is already actively bound for this tenant"
            ) from exc
        return _binding_to_response(row)

    async def list_for_asset(
        self,
        tenant_id: uuid.UUID,
        asset_id: uuid.UUID,
        *,
        active_only: bool = False,
    ) -> list[AssetTagBindingResponse]:
        stmt = select(AssetTagBindingModel).where(
            AssetTagBindingModel.tenant_id == tenant_id,
            AssetTagBindingModel.asset_id == asset_id,
        )
        if active_only:
            stmt = stmt.where(AssetTagBindingModel.unbound_at.is_(None))
        stmt = stmt.order_by(AssetTagBindingModel.bound_at.desc())
        result = await self._session.execute(stmt)
        return [_binding_to_response(r) for r in result.scalars()]

    async def unbind(
        self,
        tenant_id: uuid.UUID,
        asset_id: uuid.UUID,
        binding_value: str,
    ) -> bool:
        """Mark the active binding for this (asset, value) as unbound."""
        stmt = select(AssetTagBindingModel).where(
            AssetTagBindingModel.tenant_id == tenant_id,
            AssetTagBindingModel.asset_id == asset_id,
            AssetTagBindingModel.binding_value == binding_value,
            AssetTagBindingModel.unbound_at.is_(None),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        row.unbound_at = datetime.now(UTC)
        await self._session.flush()
        return True

    async def get_active_by_value(
        self, tenant_id: uuid.UUID, binding_value: str
    ) -> AssetTagBindingResponse | None:
        """Lookup the active (asset, binding_value) pair (ingest hot path)."""
        stmt = select(AssetTagBindingModel).where(
            AssetTagBindingModel.tenant_id == tenant_id,
            AssetTagBindingModel.binding_value == binding_value,
            AssetTagBindingModel.unbound_at.is_(None),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _binding_to_response(row) if row else None

    async def count_other_tenant_collisions(self, tenant_id: uuid.UUID, binding_value: str) -> int:
        """Number of *other* tenants with an active binding for this value.

        Admin-only tooling per assets-and-zones.md §11 Q3 — never reveals tenant
        identities. Uses the global non-unique index on
        ``asset_tag_bindings(binding_value) WHERE unbound_at IS NULL``.
        Bypasses RLS — caller must be admin and gate access at the route layer.
        """
        stmt = select(func.count(func.distinct(AssetTagBindingModel.tenant_id))).where(
            AssetTagBindingModel.binding_value == binding_value,
            AssetTagBindingModel.unbound_at.is_(None),
            AssetTagBindingModel.tenant_id != tenant_id,
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)
