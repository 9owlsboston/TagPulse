"""TimescaleDB repository for gateway_subject_grants (Sprint 81, C-6S9H).

Per-gateway approved-subject-set grants. Every method takes an explicit
``tenant_id`` and filters on it (HTTP requests may not set the RLS GUC, so
tenant scoping is enforced in SQL, not only by the RLS policy). Soft-revoke via
``revoked_at``; "active" == ``revoked_at IS NULL``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import GatewaySubjectGrantModel
from tagpulse.models.schemas import GatewaySubjectGrantResponse


def _to_response(row: GatewaySubjectGrantModel) -> GatewaySubjectGrantResponse:
    return GatewaySubjectGrantResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        gateway_device_id=row.gateway_device_id,
        subject_kind=row.subject_kind,
        subject_id=row.subject_id,
        granted_by=row.granted_by,
        granted_at=row.granted_at,
        revoked_at=row.revoked_at,
    )


class TimescaleGatewaySubjectGrantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active(
        self,
        tenant_id: uuid.UUID,
        gateway_device_id: uuid.UUID,
        subject_kind: str,
        subject_id: uuid.UUID,
    ) -> GatewaySubjectGrantResponse | None:
        stmt = select(GatewaySubjectGrantModel).where(
            GatewaySubjectGrantModel.tenant_id == tenant_id,
            GatewaySubjectGrantModel.gateway_device_id == gateway_device_id,
            GatewaySubjectGrantModel.subject_kind == subject_kind,
            GatewaySubjectGrantModel.subject_id == subject_id,
            GatewaySubjectGrantModel.revoked_at.is_(None),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_response(row) if row else None

    async def create(
        self,
        tenant_id: uuid.UUID,
        gateway_device_id: uuid.UUID,
        subject_kind: str,
        subject_id: uuid.UUID,
        granted_by: uuid.UUID | None,
    ) -> GatewaySubjectGrantResponse:
        row = GatewaySubjectGrantModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            gateway_device_id=gateway_device_id,
            subject_kind=subject_kind,
            subject_id=subject_id,
            granted_by=granted_by,
            # Set explicitly (not just via server_default) so the response is
            # complete without a DB round-trip and writes are deterministic.
            granted_at=datetime.now(UTC),
        )
        self._session.add(row)
        await self._session.flush()
        return _to_response(row)

    async def list_for_gateway(
        self, tenant_id: uuid.UUID, gateway_device_id: uuid.UUID
    ) -> list[GatewaySubjectGrantResponse]:
        stmt = (
            select(GatewaySubjectGrantModel)
            .where(
                GatewaySubjectGrantModel.tenant_id == tenant_id,
                GatewaySubjectGrantModel.gateway_device_id == gateway_device_id,
                GatewaySubjectGrantModel.revoked_at.is_(None),
            )
            .order_by(GatewaySubjectGrantModel.granted_at.desc())
        )
        result = await self._session.execute(stmt)
        return [_to_response(r) for r in result.scalars()]

    async def revoke(
        self,
        tenant_id: uuid.UUID,
        gateway_device_id: uuid.UUID,
        subject_kind: str,
        subject_id: uuid.UUID,
        revoked_at: object,
    ) -> bool:
        """Soft-revoke an active grant. Returns True if a row was revoked."""
        stmt = (
            update(GatewaySubjectGrantModel)
            .where(
                GatewaySubjectGrantModel.tenant_id == tenant_id,
                GatewaySubjectGrantModel.gateway_device_id == gateway_device_id,
                GatewaySubjectGrantModel.subject_kind == subject_kind,
                GatewaySubjectGrantModel.subject_id == subject_id,
                GatewaySubjectGrantModel.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )
        result = await self._session.execute(stmt)
        return bool(cast("CursorResult[Any]", result).rowcount)

    async def active_subject_set(
        self, tenant_id: uuid.UUID, gateway_device_id: uuid.UUID
    ) -> set[tuple[str, uuid.UUID]]:
        """Return the gateway's active ``(subject_kind, subject_id)`` grants."""
        stmt = select(
            GatewaySubjectGrantModel.subject_kind,
            GatewaySubjectGrantModel.subject_id,
        ).where(
            GatewaySubjectGrantModel.tenant_id == tenant_id,
            GatewaySubjectGrantModel.gateway_device_id == gateway_device_id,
            GatewaySubjectGrantModel.revoked_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return {(kind, sid) for kind, sid in result.all()}
