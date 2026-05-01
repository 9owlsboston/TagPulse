"""Tenant authentication dependency — extracts tenant from JWT, API key, or X-Tenant-ID header."""

from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.user_auth import AuthenticatedUser, get_current_user
from tagpulse.models.database import TenantModel
from tagpulse.repositories.timescaledb.session import get_session

api_key_header = APIKeyHeader(name="X-Tenant-ID", auto_error=False)


class Tenant:
    """Represents the authenticated tenant for the current request."""

    def __init__(self, id: UUID, name: str, slug: str, plan: str) -> None:
        self.id = id
        self.name = name
        self.slug = slug
        self.plan = plan


async def get_current_tenant(
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Tenant:
    """Extract tenant from the authenticated user (JWT, API key, or X-Tenant-ID).

    Delegates authentication to get_current_user, then looks up the tenant plan.
    """
    stmt = select(TenantModel).where(TenantModel.id == user.tenant_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=401, detail="Tenant not found or inactive")

    return Tenant(id=row.id, name=row.name, slug=row.slug, plan=row.plan)
