"""HTTP ingestion endpoint for telemetry readings (Sprint 14, extended Sprint 19)."""

import logging
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from tagpulse.api.dependencies import (
    get_event_bus,
    get_telemetry_readings_repo,
    get_telemetry_service,
)
from tagpulse.api.services.telemetry_service import TelemetryService
from tagpulse.core.tenant_auth import Tenant, get_current_tenant
from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.events.protocol import Event, EventBus, Topic
from tagpulse.models.schemas import (
    TelemetryAggregateBucket,
    TelemetryBatch,
    TelemetryQuarantineResponse,
    TelemetryReadingResponse,
    TelemetryReadingsBatch,
    TelemetryResponse,
)
from tagpulse.repositories.timescaledb.telemetry import (
    TimescaleTelemetryReadingsRepository,
)

logger = logging.getLogger(__name__)

SubjectKindParam = Literal["device", "asset", "lot", "stock_item", "zone"]

router = APIRouter(tags=["telemetry"])


@router.post("/telemetry", status_code=201)
async def ingest_telemetry(
    body: TelemetryBatch,
    tenant: Tenant = Depends(get_current_tenant),
    service: TelemetryService = Depends(get_telemetry_service),
) -> dict[str, int]:
    """Ingest a batch of telemetry readings via HTTP push."""
    return await service.ingest_batch(tenant.id, body)


@router.get("/telemetry", response_model=list[TelemetryResponse])
async def list_telemetry(
    device_id: UUID | None = Query(default=None),
    metric_name: str | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    tenant: Tenant = Depends(get_current_tenant),
    service: TelemetryService = Depends(get_telemetry_service),
) -> list[TelemetryResponse]:
    """Query persisted telemetry readings with filters."""
    return await service.query(
        tenant.id,
        device_id=device_id,
        metric_name=metric_name,
        start=start,
        end=end,
        limit=limit,
    )


@router.get("/telemetry/quarantine", response_model=list[TelemetryQuarantineResponse])
async def list_telemetry_quarantine(
    device_id: UUID | None = Query(default=None),
    reason: str | None = Query(
        default=None,
        description="Filter by quarantine reason "
        "(unknown_metric, out_of_range, unit_mismatch, stale_timestamp).",
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    tenant: Tenant = Depends(get_current_tenant),
    service: TelemetryService = Depends(get_telemetry_service),
) -> list[TelemetryQuarantineResponse]:
    """List quarantined telemetry readings for the current tenant."""
    return await service.list_quarantine(
        tenant.id,
        device_id=device_id,
        reason=reason,
        limit=limit,
        offset=offset,
    )


# -- Sprint 19: subject-scoped telemetry surface --


@router.get("/telemetry/readings", response_model=list[TelemetryReadingResponse])
async def list_telemetry_readings(
    subject_kind: SubjectKindParam = Query(...),
    subject_id: UUID = Query(...),
    metric_name: str | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    tenant: Tenant = Depends(get_current_tenant),
    repo: TimescaleTelemetryReadingsRepository = Depends(get_telemetry_readings_repo),
) -> list[TelemetryReadingResponse]:
    """Subject-scoped telemetry query (Sprint 19).

    Returns rows from the new ``telemetry_readings`` hypertable. The
    legacy device-only ``GET /telemetry`` endpoint stays as-is for the
    Sprint 14 contract; this endpoint is the multi-subject successor.
    """
    return await repo.query_by_subject(
        tenant_id=tenant.id,
        subject_kind=subject_kind,
        subject_id=subject_id,
        metric_name=metric_name,
        start=start,
        end=end,
        limit=limit,
    )


@router.get(
    "/telemetry/aggregates",
    response_model=list[TelemetryAggregateBucket],
)
async def list_telemetry_aggregates(
    subject_kind: SubjectKindParam = Query(...),
    subject_id: UUID = Query(...),
    metric_name: str = Query(...),
    bucket_seconds: int = Query(
        ...,
        ge=1,
        le=86_400,
        description=(
            "Bucket width in seconds. 60 / 3600 hit the continuous "
            "aggregates; other values are computed live via "
            "time_bucket() over the raw hypertable."
        ),
    ),
    start: datetime = Query(...),
    end: datetime = Query(...),
    tenant: Tenant = Depends(get_current_tenant),
    repo: TimescaleTelemetryReadingsRepository = Depends(get_telemetry_readings_repo),
) -> list[TelemetryAggregateBucket]:
    """Time-bucketed avg/min/max/count for a single subject + metric."""
    if end <= start:
        raise HTTPException(status_code=400, detail="end must be after start")
    return await repo.aggregate(
        tenant_id=tenant.id,
        subject_kind=subject_kind,
        subject_id=subject_id,
        metric_name=metric_name,
        bucket_seconds=bucket_seconds,
        start=start,
        end=end,
    )


@router.post(
    "/telemetry/readings/ingest",
    response_model=list[TelemetryReadingResponse],
    status_code=201,
)
async def ingest_telemetry_readings(
    body: TelemetryReadingsBatch,
    user: AuthenticatedUser = require_role("admin", "editor"),
    repo: TimescaleTelemetryReadingsRepository = Depends(get_telemetry_readings_repo),
    event_bus: EventBus = Depends(get_event_bus),
) -> list[TelemetryReadingResponse]:
    """Direct subject-scoped telemetry write (admin/editor only).

    For external systems publishing pre-resolved subject readings
    (e.g. a TMS pushing per-asset GPS speed). Bypasses the tag-borne
    fan-out path. Source defaults to ``"external"`` per the schema.
    Each persisted row is published as ``Topic.TELEMETRY_RECORDED`` so
    the Sprint 20 ``telemetry.threshold`` rule path fires here too.
    """
    written: list[TelemetryReadingResponse] = []
    for reading in body.readings:
        row = await repo.insert(
            tenant_id=user.tenant_id,
            subject_kind=reading.subject_kind,
            subject_id=reading.subject_id,
            timestamp=reading.timestamp,
            metric_name=reading.metric_name,
            metric_value=reading.metric_value,
            device_id=reading.device_id,
            unit=reading.unit,
            source=reading.source,
            metadata=reading.metadata,
        )
        written.append(row)
        try:
            await event_bus.publish(
                Topic.TELEMETRY_RECORDED,
                Event(
                    id=row.id,
                    topic=Topic.TELEMETRY_RECORDED,
                    timestamp=row.timestamp,
                    payload={
                        "tenant_id": str(user.tenant_id),
                        "subject_kind": reading.subject_kind,
                        "subject_id": str(reading.subject_id),
                        "metric_name": reading.metric_name,
                        "metric_value": reading.metric_value,
                        "unit": reading.unit,
                        "device_id": (str(reading.device_id) if reading.device_id else None),
                        "source": reading.source,
                        "timestamp": row.timestamp.isoformat(),
                    },
                ),
            )
        except Exception:  # noqa: BLE001 — best-effort, row is already durable
            logger.exception(
                "telemetry.recorded publish failed for %s/%s metric %s",
                reading.subject_kind,
                reading.subject_id,
                reading.metric_name,
            )
    return written
