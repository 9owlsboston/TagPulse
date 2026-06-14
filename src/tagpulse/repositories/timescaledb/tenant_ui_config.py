"""TimescaleDB repository for per-tenant Configurable-UI defaults (Sprint 60).

Backs the ADR-032 §3 *tenant* + *role* default layers, stored on the
``tenants.ui_config`` JSONB column (the tenant-JSONB precedent). The blob holds
the tenant-default leaves at the top level and the role layer under a reserved
``roles`` sub-object; splitting it into resolve layers happens in
:mod:`tagpulse.services.ui_config`, not here.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update

from tagpulse.models.database import TenantModel


class TenantUiConfigRepository:
    """Reads/writes the raw ``tenants.ui_config`` blob for one tenant."""

    def __init__(self, session: Any) -> None:
        self._session = session

    async def get(self, tenant_id: uuid.UUID) -> dict[str, Any] | None:
        """Return the tenant's raw ``ui_config`` blob, or ``None`` if unset.

        ``None`` (NULL column) is the pure-system-default state — the caller
        folds in no tenant/role layer.
        """
        row = await self._session.scalar(
            select(TenantModel.ui_config).where(TenantModel.id == tenant_id)
        )
        return dict(row) if isinstance(row, dict) else None

    async def set(self, tenant_id: uuid.UUID, ui_config: dict[str, Any] | None) -> None:
        """Replace the tenant's ``ui_config`` blob wholesale (``None`` clears).

        The route composes the new blob (preserving the sibling tenant/role
        sub-tree it isn't editing), so this is a plain whole-column write.
        """
        await self._session.execute(
            update(TenantModel).where(TenantModel.id == tenant_id).values(ui_config=ui_config)
        )
