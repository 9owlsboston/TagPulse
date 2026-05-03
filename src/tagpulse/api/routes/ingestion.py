"""HTTP ingestion endpoint for tag read events."""

from fastapi import APIRouter, Depends, HTTPException

from tagpulse.api.dependencies import get_ingestion_service
from tagpulse.core.tenant_auth import Tenant, get_current_tenant
from tagpulse.ingestion.clock import ClockRejectionError
from tagpulse.ingestion.service import IngestionService
from tagpulse.models.schemas import TagReadCreate, TagReadResponse

router = APIRouter(tags=["ingestion"])


@router.post("/tag-reads", response_model=TagReadResponse, status_code=201)
async def create_tag_read(
    body: TagReadCreate,
    tenant: Tenant = Depends(get_current_tenant),
    service: IngestionService = Depends(get_ingestion_service),
) -> TagReadResponse:
    """Ingest a single tag read event via HTTP push."""
    try:
        return await service.ingest(tenant.id, body)
    except ClockRejectionError as exc:
        raise HTTPException(status_code=400, detail=exc.reason) from None


@router.post("/tag-reads/batch", status_code=201)
async def create_tag_reads_batch(
    body: list[TagReadCreate],
    tenant: Tenant = Depends(get_current_tenant),
    service: IngestionService = Depends(get_ingestion_service),
) -> dict[str, int]:
    """Ingest a batch of tag read events via HTTP push.

    Returns the count of accepted and clock-rejected events; rejected events
    are dead-lettered per docs/design/edge-device-contract.md §3.5.
    """
    ingested, rejected = await service.ingest_batch(tenant.id, body)
    return {"ingested": ingested, "rejected": rejected}
