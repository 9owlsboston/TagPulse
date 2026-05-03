"""TimescaleDB implementation of telemetry persistence (Sprint 14)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import (
    DeviceTelemetryModel,
    TelemetryQuarantineModel,
)
from tagpulse.models.schemas import TelemetryReading, TelemetryResponse


class TimescaleTelemetryRepository:
    """Persists telemetry readings and quarantine rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_reading(
        self,
        tenant_id: UUID,
        device_id: UUID,
        reading: TelemetryReading,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> TelemetryResponse:
        merged_metadata = {**(reading.metadata or {}), **(metadata or {})}
        merged = merged_metadata if merged_metadata else None
        row = DeviceTelemetryModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            device_id=device_id,
            timestamp=reading.timestamp,
            metric_name=reading.metric_name,
            metric_value=reading.metric_value,
            unit=reading.unit,
            metadata_=merged,
        )
        self._session.add(row)
        await self._session.flush()
        return TelemetryResponse(
            id=row.id,
            device_id=row.device_id,
            timestamp=row.timestamp,
            metric_name=row.metric_name,
            metric_value=row.metric_value,
            unit=row.unit,
            metadata=row.metadata_,
        )

    async def quarantine(
        self,
        tenant_id: UUID,
        device_id: UUID,
        reading: TelemetryReading,
        reason: str,
    ) -> None:
        row = TelemetryQuarantineModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            device_id=device_id,
            metric_name=reading.metric_name,
            metric_value=reading.metric_value,
            raw_payload=reading.model_dump(mode="json"),
            reason=reason,
        )
        self._session.add(row)
        await self._session.flush()

    async def query(
        self,
        tenant_id: UUID,
        *,
        device_id: UUID | None = None,
        metric_name: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> list[TelemetryResponse]:
        stmt = (
            select(DeviceTelemetryModel)
            .where(DeviceTelemetryModel.tenant_id == tenant_id)
            .order_by(DeviceTelemetryModel.timestamp.desc())
        )
        if device_id is not None:
            stmt = stmt.where(DeviceTelemetryModel.device_id == device_id)
        if metric_name is not None:
            stmt = stmt.where(DeviceTelemetryModel.metric_name == metric_name)
        if start is not None:
            stmt = stmt.where(DeviceTelemetryModel.timestamp >= start)
        if end is not None:
            stmt = stmt.where(DeviceTelemetryModel.timestamp <= end)
        stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return [
            TelemetryResponse(
                id=r.id,
                device_id=r.device_id,
                timestamp=r.timestamp,
                metric_name=r.metric_name,
                metric_value=r.metric_value,
                unit=r.unit,
                metadata=r.metadata_,
            )
            for r in result.scalars()
        ]
