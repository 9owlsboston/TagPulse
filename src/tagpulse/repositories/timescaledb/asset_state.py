"""TimescaleDB repository for ``asset_state_history`` (Sprint 71, ADR-034).

Read side of the fused asset-state snapshots written by the consolidation worker
(:mod:`tagpulse.workers.consolidation_worker`). ``latest`` powers
``GET /assets/{id}/state`` ("is"); ``history`` powers ``…/state/history`` ("was").

RLS (migration 058) isolates rows by ``tenant_id``; the service sets the GUC per
request, so a query can never read cross-tenant.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import AssetStateHistoryModel
from tagpulse.models.schemas import AssetStateResponse


def _to_response(row: AssetStateHistoryModel) -> AssetStateResponse:
    return AssetStateResponse(
        asset_id=row.asset_id,
        time=row.time,
        frame=row.frame,
        zone_id=row.zone_id,
        site_id=row.site_id,
        latitude=row.lat,
        longitude=row.lon,
        x=row.x,
        y=row.y,
        temperature_c=row.temperature_c,
        humidity_pct=row.humidity_pct,
        sample_count=row.sample_count,
        tag_count=row.tag_count,
        confidence=row.confidence,
    )


class TimescaleAssetStateRepository:
    """Reads fused asset-state snapshots from ``asset_state_history``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def latest(self, tenant_id: UUID, asset_id: UUID) -> AssetStateResponse | None:
        """The most recent fused snapshot for an asset, or ``None``."""
        stmt = (
            select(AssetStateHistoryModel)
            .where(
                AssetStateHistoryModel.tenant_id == tenant_id,
                AssetStateHistoryModel.asset_id == asset_id,
            )
            .order_by(AssetStateHistoryModel.time.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalars().first()
        return _to_response(row) if row is not None else None

    async def history(
        self,
        tenant_id: UUID,
        asset_id: UUID,
        *,
        since: datetime | None = None,
        limit: int = 200,
    ) -> list[AssetStateResponse]:
        """Snapshots for an asset, newest-first, optionally bounded by ``since``."""
        stmt = select(AssetStateHistoryModel).where(
            AssetStateHistoryModel.tenant_id == tenant_id,
            AssetStateHistoryModel.asset_id == asset_id,
        )
        if since is not None:
            stmt = stmt.where(AssetStateHistoryModel.time >= since)
        stmt = stmt.order_by(AssetStateHistoryModel.time.desc()).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_response(r) for r in rows]
