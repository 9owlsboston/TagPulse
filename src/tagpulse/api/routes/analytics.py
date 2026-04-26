"""Analytics query API routes."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.tenant_auth import Tenant, get_current_tenant
from tagpulse.models.database import AnalyticsResultModel
from tagpulse.repositories.timescaledb.session import get_session

router = APIRouter(prefix="/analytics", tags=["analytics"])


class AnalyticsResultResponse(BaseModel):
    """A single analytics result."""

    id: UUID
    module_name: str
    device_id: UUID
    metric_name: str
    metric_value: float
    computed_at: datetime


@router.get("/read-frequency", response_model=list[AnalyticsResultResponse])
async def get_read_frequency(
    device_id: UUID | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    metric: str = Query(default="reads_per_minute"),
    limit: int = Query(default=100, ge=1, le=1000),
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> list[AnalyticsResultResponse]:
    """Query read frequency analytics results."""
    stmt = (
        select(AnalyticsResultModel)
        .where(
            AnalyticsResultModel.tenant_id == tenant.id,
            AnalyticsResultModel.module_name == "read_frequency",
            AnalyticsResultModel.metric_name == metric,
        )
        .order_by(AnalyticsResultModel.computed_at.desc())
    )
    if device_id is not None:
        stmt = stmt.where(AnalyticsResultModel.device_id == device_id)
    if start is not None:
        stmt = stmt.where(AnalyticsResultModel.computed_at >= start)
    if end is not None:
        stmt = stmt.where(AnalyticsResultModel.computed_at <= end)
    stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return [
        AnalyticsResultResponse(
            id=row.id,
            module_name=row.module_name,
            device_id=row.device_id,
            metric_name=row.metric_name,
            metric_value=row.metric_value,
            computed_at=row.computed_at,
        )
        for row in result.scalars()
    ]
