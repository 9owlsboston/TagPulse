"""TimescaleDB repository for ``asset_positions`` — floor-frame ``(x, y)`` fixes.

Sprint 65 Phase 1 — BYO precomputed positions. The first writer into the
headless Sprint 59 ``asset_positions`` hypertable (migration 051). See
[floor-position-estimation.md](../../../../docs/design/floor-position-estimation.md).

RLS (migration 051) isolates rows by ``tenant_id = current_setting(
'app.current_tenant_id')``; the service sets that GUC per request and always
stamps ``tenant_id`` from the authenticated tenant, so a body can never write
cross-tenant.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import AssetPositionModel
from tagpulse.models.schemas import FloorPositionCreate, FloorPositionResponse


def _to_response(row: AssetPositionModel) -> FloorPositionResponse:
    return FloorPositionResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        asset_id=row.asset_id,
        site_id=row.site_id,
        recorded_at=row.time,
        x=float(row.x),
        y=float(row.y),
        z=float(row.z) if row.z is not None else None,
        confidence=float(row.confidence),
        source=row.source,
        metadata=row.metadata_,
    )


class TimescaleAssetPositionRepository:
    """Persists and reads ``asset_positions`` floor-frame ``(x, y)`` fixes."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(
        self,
        tenant_id: uuid.UUID,
        asset_id: uuid.UUID,
        *,
        recorded_at: datetime,
        position: FloorPositionCreate,
        source: str,
    ) -> FloorPositionResponse:
        row = AssetPositionModel(
            id=uuid.uuid4(),
            time=recorded_at,
            tenant_id=tenant_id,
            asset_id=asset_id,
            site_id=position.site_id,
            x=position.x,
            y=position.y,
            z=position.z,
            confidence=position.confidence,
            source=source,
            metadata_=position.metadata,
        )
        self._session.add(row)
        await self._session.flush()
        return _to_response(row)

    async def list_floor_path(
        self,
        tenant_id: uuid.UUID,
        asset_id: uuid.UUID,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        source: str | None = None,
        limit: int = 500,
    ) -> list[FloorPositionResponse]:
        stmt = select(AssetPositionModel).where(
            AssetPositionModel.tenant_id == tenant_id,
            AssetPositionModel.asset_id == asset_id,
        )
        if since is not None:
            stmt = stmt.where(AssetPositionModel.time >= since)
        if until is not None:
            stmt = stmt.where(AssetPositionModel.time <= until)
        if source is not None:
            stmt = stmt.where(AssetPositionModel.source == source)
        stmt = stmt.order_by(AssetPositionModel.time.asc()).limit(limit)
        result = await self._session.execute(stmt)
        return [_to_response(r) for r in result.scalars()]
