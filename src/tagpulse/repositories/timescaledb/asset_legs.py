"""TimescaleDB repository for ``asset_legs`` (Sprint 72, ADR-034 Phase 2).

Read side of the transit legs opened/closed by the ``AssetLegTracker``. ``list``
powers ``GET /assets/{id}/legs``; ``open_leg`` is attached to
``GET /assets/{id}/state`` as the in-transit block. RLS (migration 059) isolates
rows by ``tenant_id``.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import AssetLegModel
from tagpulse.models.schemas import AssetLegResponse


def _to_response(row: AssetLegModel) -> AssetLegResponse:
    return AssetLegResponse.model_validate(row)


class TimescaleAssetLegRepository:
    """Reads transit legs from ``asset_legs``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(
        self,
        tenant_id: UUID,
        asset_id: UUID,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[AssetLegResponse]:
        """Legs for an asset, newest-departure-first, optionally filtered by status."""
        stmt = select(AssetLegModel).where(
            AssetLegModel.tenant_id == tenant_id,
            AssetLegModel.asset_id == asset_id,
        )
        if status is not None:
            stmt = stmt.where(AssetLegModel.status == status)
        stmt = stmt.order_by(AssetLegModel.departed_at.desc()).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_response(r) for r in rows]

    async def open_leg(self, tenant_id: UUID, asset_id: UUID) -> AssetLegResponse | None:
        """The asset's currently-open leg, or ``None``."""
        stmt = (
            select(AssetLegModel)
            .where(
                AssetLegModel.tenant_id == tenant_id,
                AssetLegModel.asset_id == asset_id,
                AssetLegModel.status == "open",
            )
            .order_by(AssetLegModel.departed_at.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalars().first()
        return _to_response(row) if row is not None else None
