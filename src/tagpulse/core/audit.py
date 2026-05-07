"""Audit logging service — records configuration changes."""

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import AuditLogModel

logger = logging.getLogger(__name__)


class AuditLogger:
    """Records audit trail entries for configuration mutations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log(
        self,
        tenant_id: uuid.UUID,
        action: str,
        resource_type: str,
        resource_id: uuid.UUID,
        changes: dict[str, Any] | None = None,
        *,
        user_id: uuid.UUID | None = None,
    ) -> None:
        """Record an audit log entry."""
        entry = AuditLogModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            changes=changes,
        )
        self._session.add(entry)
        logger.debug(
            "Audit: %s %s %s by user %s (tenant %s)",
            action,
            resource_type,
            resource_id,
            user_id,
            tenant_id,
        )

    async def list_logs(
        self,
        tenant_id: uuid.UUID,
        *,
        resource_type: str | None = None,
        actions: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query audit logs for a tenant.

        ``actions`` (when provided) filters to entries whose ``action`` is in
        the list — used by the UI "device security events" preset to surface
        ``device.token_rotated`` / ``device.cert_attached`` / ``device.approved``
        / ``device.rejected`` together (Sprint 16, design §7).
        """
        stmt = (
            select(AuditLogModel)
            .where(AuditLogModel.tenant_id == tenant_id)
            .order_by(AuditLogModel.created_at.desc())
        )
        if resource_type is not None:
            stmt = stmt.where(AuditLogModel.resource_type == resource_type)
        if actions:
            stmt = stmt.where(AuditLogModel.action.in_(actions))
        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return [
            {
                "id": str(row.id),
                "user_id": str(row.user_id) if row.user_id else None,
                "action": row.action,
                "resource_type": row.resource_type,
                "resource_id": str(row.resource_id),
                "changes": row.changes,
                "created_at": row.created_at.isoformat(),
            }
            for row in result.scalars()
        ]
