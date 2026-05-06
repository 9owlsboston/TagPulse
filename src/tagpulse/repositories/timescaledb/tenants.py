"""Minimal tenant repository — currently only exposes ``get_tracking_modes``.

Lives outside ``tagpulse.tenants`` so the ingestion hot path can pull tenant
flags without dragging in the full admin/provisioning surface. If/when more
tenant-scoped reads land, fold them into the same class.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import TenantModel


class TimescaleTenantRepository:
    """Read-only tenant lookup helpers used by ingestion enrichment."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_tracking_modes(self, tenant_id: uuid.UUID) -> list[str]:
        """Return ``tenants.tracking_modes`` for a tenant, defaulting to ``['asset']``.

        A missing tenant is treated as the safe default so a stale event from
        a freshly-deleted tenant doesn't raise.
        """
        stmt = select(TenantModel.tracking_modes).where(
            TenantModel.id == tenant_id
        )
        result = await self._session.execute(stmt)
        modes = result.scalar_one_or_none()
        if modes is None:
            return ["asset"]
        return list(modes)

    async def get_telemetry_subject_kinds(
        self, tenant_id: uuid.UUID
    ) -> list[str]:
        """Return ``tenants.telemetry_subject_kinds`` for a tenant.

        Defaults to ``['device']`` for an unknown tenant — same safe
        fallback pattern as :meth:`get_tracking_modes`. Sprint 19's
        ingest pipeline uses this to decide which non-device subject
        rows to fan out into ``telemetry_readings``.
        """
        stmt = select(TenantModel.telemetry_subject_kinds).where(
            TenantModel.id == tenant_id
        )
        result = await self._session.execute(stmt)
        kinds = result.scalar_one_or_none()
        if kinds is None:
            return ["device"]
        return list(kinds)
