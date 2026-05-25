"""Cross-tenant admin operations (Sprint 13b — Multi-tier Foundations).

Every method on :class:`AdminRepository` takes an explicit ``tenant_id`` (or
operates over the cross-tenant index) and is intended to be reached only from
routes guarded by :func:`tagpulse.core.user_auth.require_role` ``("admin")``.

Keeping cross-tenant queries here makes audit trivial — anything outside this
module is implicitly single-tenant. Per
[docs/design/storage-strategy.md §6 Q2](../../docs/design/storage-strategy.md).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import TenantModel


@dataclass(slots=True)
class TenantPoolBinding:
    """Read-only view of a tenant's pool routing — used by ops dashboards."""

    tenant_id: uuid.UUID
    slug: str
    db_pool_key: str


class AdminRepository:
    """Tenant-explicit, cross-tenant queries. Intended for admin-only routes."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_tenant_pool_bindings(
        self, db_pool_key: str | None = None
    ) -> Sequence[TenantPoolBinding]:
        """List ``(tenant_id, slug, db_pool_key)`` triples, optionally filtered.

        Used by the future shared→sovereign promotion runbook to confirm which
        tenants land in each pool before kicking off a ``pg_dump`` -filtered
        data move.
        """
        stmt = select(TenantModel.id, TenantModel.slug, TenantModel.db_pool_key).order_by(
            TenantModel.slug
        )
        if db_pool_key is not None:
            stmt = stmt.where(TenantModel.db_pool_key == db_pool_key)
        result = await self._session.execute(stmt)
        return [
            TenantPoolBinding(tenant_id=row[0], slug=row[1], db_pool_key=row[2])
            for row in result.all()
        ]

    async def count_tenants_per_pool(self) -> dict[str, int]:
        """Histogram of tenants by ``db_pool_key`` (for capacity planning)."""
        stmt = (
            select(TenantModel.db_pool_key, func.count(TenantModel.id))
            .group_by(TenantModel.db_pool_key)
            .order_by(TenantModel.db_pool_key)
        )
        result = await self._session.execute(stmt)
        return {key: count for key, count in result.all()}
