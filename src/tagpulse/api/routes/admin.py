"""Admin billing and usage API routes."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.tenant_auth import Tenant, get_current_tenant
from tagpulse.models.database import TenantUsageDetail
from tagpulse.models.tenant_schemas import UsageRecord, UsageSummary
from tagpulse.repositories.timescaledb.session import get_session

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/usage", response_model=list[UsageRecord])
async def get_usage(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> list[UsageRecord]:
    """Get daily usage records for the authenticated tenant."""
    stmt = select(TenantUsageDetail).where(
        TenantUsageDetail.tenant_id == tenant.id
    )
    if start is not None:
        stmt = stmt.where(TenantUsageDetail.usage_date >= start)
    if end is not None:
        stmt = stmt.where(TenantUsageDetail.usage_date <= end)
    stmt = stmt.order_by(TenantUsageDetail.usage_date.desc())
    result = await session.execute(stmt)
    return [
        UsageRecord(
            tenant_id=row.tenant_id,
            usage_date=row.usage_date,
            dimension=row.dimension,
            quantity=row.quantity,
            unit=row.unit,
        )
        for row in result.scalars()
    ]


@router.get("/usage/summary", response_model=list[UsageSummary])
async def get_usage_summary(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> list[UsageSummary]:
    """Get aggregated usage totals per dimension for a billing period."""
    stmt = (
        select(
            TenantUsageDetail.tenant_id,
            TenantUsageDetail.dimension,
            func.sum(TenantUsageDetail.quantity).label("total_quantity"),
            TenantUsageDetail.unit,
        )
        .where(TenantUsageDetail.tenant_id == tenant.id)
        .group_by(
            TenantUsageDetail.tenant_id,
            TenantUsageDetail.dimension,
            TenantUsageDetail.unit,
        )
    )
    if start is not None:
        stmt = stmt.where(TenantUsageDetail.usage_date >= start)
    if end is not None:
        stmt = stmt.where(TenantUsageDetail.usage_date <= end)
    result = await session.execute(stmt)
    return [
        UsageSummary(
            tenant_id=row.tenant_id,
            dimension=row.dimension,
            total_quantity=row.total_quantity,
            unit=row.unit,
        )
        for row in result
    ]
