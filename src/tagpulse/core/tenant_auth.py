"""Tenant authentication dependency — extracts tenant from JWT, API key, or X-Tenant-ID header."""

from collections.abc import Iterable
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


class IngestAuth:
    """Bundle of tenant + principal for the HTTP ingest endpoints.

    Unlike :func:`get_current_tenant`, the ingest dependency accepts device
    principals (``role="device"``). Routes need the principal (not just the
    tenant) to enforce per-device binding + backfill rules, so both are
    surfaced here.
    """

    def __init__(self, tenant: Tenant, principal: AuthenticatedUser) -> None:
        self.tenant = tenant
        self.principal = principal


async def _resolve_tenant(user: AuthenticatedUser, session: AsyncSession) -> Tenant:
    stmt = select(TenantModel).where(TenantModel.id == user.tenant_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=401, detail="Tenant not found or inactive")
    return Tenant(id=row.id, name=row.name, slug=row.slug, plan=row.plan)


async def get_current_tenant(
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Tenant:
    """Extract tenant from the authenticated user (JWT, API key, or X-Tenant-ID).

    Delegates authentication to get_current_user, then looks up the tenant plan.

    Device principals (``role="device"``) are **rejected** here: this dependency
    guards the broad console surface (queries, analytics, admin, config, …). A
    per-device token must only reach the ingest endpoints, which use
    :func:`get_ingest_auth` instead.
    """
    if user.role == "device":
        raise HTTPException(status_code=403, detail="Device principals cannot access this endpoint")
    return await _resolve_tenant(user, session)


async def get_ingest_auth(
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> IngestAuth:
    """Ingest-scoped auth: accepts human **and** device principals."""
    tenant = await _resolve_tenant(user, session)
    return IngestAuth(tenant=tenant, principal=user)


def enforce_device_ingest(
    principal: AuthenticatedUser,
    device_ids: Iterable[UUID],
    *,
    backfill: bool,
) -> None:
    """Least-privilege guard for device principals on tag-read ingest.

    A device principal may only ingest rows for its **own** ``device_id`` and
    may not use ``backfill`` (rule/analytics suppression is an admin-replay
    tool). No-op for human principals. Multi-reader gateway relay (one
    principal, many device_ids) is intentionally out of scope — that is the
    scoped-subject-set model owned by I-75YC.
    """
    if principal.role != "device":
        return
    if backfill:
        raise HTTPException(status_code=403, detail="Device principals may not use backfill")
    for device_id in device_ids:
        if device_id != principal.device_id:
            raise HTTPException(
                status_code=403,
                detail="Device may only ingest reads for its own device_id",
            )
