"""Telemetry model service — CRUD for per-device-type metric definitions."""

import logging
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.audit import AuditLogger
from tagpulse.models.database import TelemetryModelDef
from tagpulse.models.schemas import (
    TelemetryModelCreate,
    TelemetryModelResponse,
    TelemetryModelUpdate,
)

logger = logging.getLogger(__name__)


class TelemetryModelService:
    """Manages telemetry model definitions."""

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditLogger | None = None,
    ) -> None:
        self._session = session
        self._audit = audit

    async def create(
        self, tenant_id: uuid.UUID, body: TelemetryModelCreate
    ) -> TelemetryModelResponse:
        row = TelemetryModelDef(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            subject_kind=body.subject_kind,
            device_type=body.device_type,
            metrics=[m.model_dump() for m in body.metrics],
        )
        self._session.add(row)
        await self._session.flush()
        logger.info(
            "Telemetry model created: subject_kind=%s device_type=%s tenant=%s",
            body.subject_kind,
            body.device_type,
            tenant_id,
        )
        return _to_response(row)

    async def list_all(
        self, tenant_id: uuid.UUID
    ) -> list[TelemetryModelResponse]:
        stmt = (
            select(TelemetryModelDef)
            .where(TelemetryModelDef.tenant_id == tenant_id)
            .order_by(TelemetryModelDef.subject_kind, TelemetryModelDef.device_type)
        )
        result = await self._session.execute(stmt)
        return [_to_response(row) for row in result.scalars()]

    async def get_by_device_type(
        self, tenant_id: uuid.UUID, device_type: str
    ) -> TelemetryModelResponse | None:
        """Look up a device-scoped telemetry model by its device_type.

        Sprint 18 limited the lookup to ``subject_kind='device'`` rows so
        a future ``asset`` model with the same string in some other
        column can't shadow it. Sprint 19 added :meth:`get_by_subject`
        for non-device kinds; this device-only path is preserved for
        the Sprint 14 ``GET /telemetry-models/{device_type}`` route.
        """
        stmt = select(TelemetryModelDef).where(
            TelemetryModelDef.tenant_id == tenant_id,
            TelemetryModelDef.subject_kind == "device",
            TelemetryModelDef.device_type == device_type,
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _to_response(row) if row else None

    async def get_by_subject(
        self,
        tenant_id: uuid.UUID,
        subject_kind: str,
        key: str,
    ) -> TelemetryModelResponse | None:
        """Subject-scoped telemetry model lookup (Sprint 19).

        ``key`` is interpreted as ``device_type`` for
        ``subject_kind='device'`` and ignored otherwise (only one row
        per (tenant, subject_kind != 'device') is permitted by the
        Sprint 18 unique constraint, since non-device kinds do not
        sub-classify by device_type).
        """
        stmt = select(TelemetryModelDef).where(
            TelemetryModelDef.tenant_id == tenant_id,
            TelemetryModelDef.subject_kind == subject_kind,
        )
        if subject_kind == "device":
            stmt = stmt.where(TelemetryModelDef.device_type == key)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _to_response(row) if row else None

    async def delete(
        self, tenant_id: uuid.UUID, model_id: uuid.UUID
    ) -> bool:
        stmt = (
            delete(TelemetryModelDef)
            .where(
                TelemetryModelDef.id == model_id,
                TelemetryModelDef.tenant_id == tenant_id,
            )
            .returning(TelemetryModelDef.id)
        )
        result = await self._session.execute(stmt)
        deleted = result.scalar_one_or_none()
        if deleted:
            logger.info("Telemetry model deleted: id=%s", model_id)
        return deleted is not None

    async def update(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None,
        model_id: uuid.UUID,
        patch: TelemetryModelUpdate,
    ) -> TelemetryModelResponse | None:
        """Sprint 28 G1: PATCH a telemetry model's mutable fields.

        Only ``metrics`` is mutable; identity columns (``subject_kind``,
        ``device_type``) intentionally cannot change. Returns ``None`` when
        no row matches ``(tenant_id, model_id)``.
        """
        stmt = select(TelemetryModelDef).where(
            TelemetryModelDef.id == model_id,
            TelemetryModelDef.tenant_id == tenant_id,
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None

        new_metrics = [m.model_dump() for m in patch.metrics]
        row.metrics = new_metrics  # type: ignore[assignment]
        await self._session.flush()
        await self._session.refresh(row)

        if self._audit is not None:
            await self._audit.log(
                tenant_id=tenant_id,
                user_id=user_id,
                action="telemetry_model.updated",
                resource_type="telemetry_model",
                resource_id=row.id,
                changes={"metrics_count": len(new_metrics)},
            )
        logger.info(
            "Telemetry model updated: id=%s tenant=%s metrics_count=%d",
            model_id,
            tenant_id,
            len(new_metrics),
        )
        return _to_response(row)


def _to_response(row: TelemetryModelDef) -> TelemetryModelResponse:
    return TelemetryModelResponse(
        id=row.id,
        subject_kind=row.subject_kind,  # type: ignore[arg-type]
        device_type=row.device_type,
        metrics=row.metrics,  # type: ignore[arg-type]
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
