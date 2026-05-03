"""HTTP ingestion endpoint for telemetry readings (Sprint 14)."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from tagpulse.api.dependencies import get_telemetry_service
from tagpulse.api.services.telemetry_service import TelemetryService
from tagpulse.core.tenant_auth import Tenant, get_current_tenant
from tagpulse.models.schemas import (
    TelemetryBatch,
    TelemetryQuarantineResponse,
    TelemetryResponse,
)

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


@router.get(
    "/telemetry/quarantine", response_model=list[TelemetryQuarantineResponse]
)
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
