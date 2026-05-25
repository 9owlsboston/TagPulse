"""Dashboard endpoints (Sprint 54 Phase 54.3).

Thin route layer over :mod:`tagpulse.services.dashboard`. The
landing page calls ``GET /dashboard/summary`` once per page-load
and renders the Dashboard KPI tiles off the eight aggregate
counts in :class:`DashboardSummary`. Open to all logged-in roles
(``admin`` / ``editor`` / ``viewer``) — these are aggregates only,
not row-level data.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.models.schemas import DashboardSummary
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
