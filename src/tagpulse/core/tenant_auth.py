"""Tenant authentication dependency — extracts tenant_id from API key header."""

from uuid import UUID

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    tenant_id_header: str | None = Security(api_key_header),
    session: AsyncSession = Depends(get_session),
) -> Tenant:
    """Extract and validate tenant from X-Tenant-ID header.

    In production, this would validate a JWT or API key.
    For now, it looks up the tenant by UUID from the header.
    """
    if tenant_id_header is None:
        raise HTTPException(status_code=401, detail="X-Tenant-ID header required")

    try:
        tenant_id = UUID(tenant_id_header)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid tenant ID format") from None

    stmt = select(TenantModel).where(
        TenantModel.id == tenant_id, TenantModel.status == "active"
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=401, detail="Tenant not found or inactive")

    return Tenant(id=row.id, name=row.name, slug=row.slug, plan=row.plan)
