"""HTTP ingestion endpoint for telemetry readings (Sprint 14)."""

from fastapi import APIRouter, Depends

from tagpulse.api.dependencies import get_telemetry_service
from tagpulse.api.services.telemetry_service import TelemetryService
from tagpulse.core.tenant_auth import Tenant, get_current_tenant
from tagpulse.models.schemas import TelemetryBatch

router = APIRouter(tags=["telemetry"])


@router.post("/telemetry", status_code=201)
async def ingest_telemetry(
    body: TelemetryBatch,
    tenant: Tenant = Depends(get_current_tenant),
    service: TelemetryService = Depends(get_telemetry_service),
) -> dict[str, int]:
    """Ingest a batch of telemetry readings via HTTP push."""
    return await service.ingest_batch(tenant.id, body)
