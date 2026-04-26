"""Telemetry model service — CRUD for per-device-type metric definitions."""

import logging
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import TelemetryModelDef
from tagpulse.models.schemas import TelemetryModelCreate, TelemetryModelResponse

logger = logging.getLogger(__name__)


class TelemetryModelService:
    """Manages telemetry model definitions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, tenant_id: uuid.UUID, body: TelemetryModelCreate
    ) -> TelemetryModelResponse:
        row = TelemetryModelDef(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            device_type=body.device_type,
            metrics=[m.model_dump() for m in body.metrics],
        )
        self._session.add(row)
        await self._session.flush()
        logger.info(
            "Telemetry model created: device_type=%s tenant=%s",
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
            .order_by(TelemetryModelDef.device_type)
        )
        result = await self._session.execute(stmt)
        return [_to_response(row) for row in result.scalars()]

    async def get_by_device_type(
        self, tenant_id: uuid.UUID, device_type: str
    ) -> TelemetryModelResponse | None:
        stmt = select(TelemetryModelDef).where(
            TelemetryModelDef.tenant_id == tenant_id,
            TelemetryModelDef.device_type == device_type,
        )
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


def _to_response(row: TelemetryModelDef) -> TelemetryModelResponse:
    return TelemetryModelResponse(
        id=row.id,
        device_type=row.device_type,
        metrics=row.metrics,  # type: ignore[arg-type]
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
