"""HTTP ingestion endpoint for tag read events."""

from fastapi import APIRouter, Depends, HTTPException, Query

from tagpulse.api.dependencies import get_ingestion_service
from tagpulse.core.tenant_auth import (
    IngestAuth,
    enforce_device_ingest,
    get_ingest_auth,
)
from tagpulse.ingestion.clock import ClockRejectionError
from tagpulse.ingestion.service import IngestionService
from tagpulse.models.schemas import TagReadCreate, TagReadResponse

router = APIRouter(tags=["ingestion"])


_BACKFILL_DESCRIPTION = (
    "Sprint 58 (Q1): when true, the read still runs the full ingest pipeline "
    "(validation, enrichment, hypertable insert, telemetry rollups) but rule "
    "evaluation is suppressed and reads/minute analytics counters skip the "
    "row. Use this for replaying historical reads from the demo-tenant seed "
    "bundle so the curated alert set isn't polluted by alerts the seed step "
    "itself accidentally triggers."
)


@router.post("/tag-reads", response_model=TagReadResponse, status_code=201)
async def create_tag_read(
    body: TagReadCreate,
    backfill: bool = Query(False, description=_BACKFILL_DESCRIPTION),
    auth: IngestAuth = Depends(get_ingest_auth),
    service: IngestionService = Depends(get_ingestion_service),
) -> TagReadResponse:
    """Ingest a single tag read event via HTTP push."""
    enforce_device_ingest(auth.principal, [body.device_id], backfill=backfill)
    try:
        return await service.ingest(auth.tenant.id, body, backfill=backfill)
    except ClockRejectionError as exc:
        raise HTTPException(status_code=400, detail=exc.reason) from None


@router.post("/tag-reads/batch", status_code=201)
async def create_tag_reads_batch(
    body: list[TagReadCreate],
    backfill: bool = Query(False, description=_BACKFILL_DESCRIPTION),
    auth: IngestAuth = Depends(get_ingest_auth),
    service: IngestionService = Depends(get_ingestion_service),
) -> dict[str, int]:
    """Ingest a batch of tag read events via HTTP push.

    Returns the count of accepted and clock-rejected events; rejected events
    are dead-lettered per docs/design/edge-device-contract.md §3.5.
    """
    enforce_device_ingest(auth.principal, [r.device_id for r in body], backfill=backfill)
    ingested, rejected = await service.ingest_batch(auth.tenant.id, body, backfill=backfill)
    return {"ingested": ingested, "rejected": rejected}
