"""Dashboard endpoints (Sprint 54 Phase 54.3, Sprint 57 Phase 57.6).

Thin route layer over :mod:`tagpulse.services.dashboard`. The
landing page calls ``GET /dashboard/summary`` once per page-load
and renders the Dashboard KPI tiles off the eight aggregate
counts in :class:`DashboardSummary`. Open to all logged-in roles
(``admin`` / ``editor`` / ``viewer``) — these are aggregates only,
not row-level data.

``GET /dashboard/sparklines`` returns the matching 7-day downsampled
trend series for each tile, fed to inline ``<TpSparkline>``
components on the Dashboard tiles. Same role gating as ``/summary``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.models.schemas import DashboardSparklines, DashboardSummary
from tagpulse.repositories.timescaledb.session import get_session
from tagpulse.services import dashboard as dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
async def get_dashboard_summary(
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    session: AsyncSession = Depends(get_session),
) -> DashboardSummary:
    """Return one row of aggregate counts for the caller's tenant."""
    return await dashboard_service.get_summary(session, user.tenant_id)


@router.get("/sparklines", response_model=DashboardSparklines)
async def get_dashboard_sparklines(
    days: int = Query(7, ge=1, le=30, description="Look-back window in days."),
    bucket_hours: int = Query(
        6,
        ge=1,
        le=24,
        description="Bucket width in hours; default yields 28 points over 7 days.",
    ),
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    session: AsyncSession = Depends(get_session),
) -> DashboardSparklines:
    """Return 7-day downsampled trend series for each Dashboard KPI tile."""
    return await dashboard_service.get_sparklines(
        session, user.tenant_id, days=days, bucket_hours=bucket_hours
    )
