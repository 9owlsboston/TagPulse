"""HTTP ingestion endpoint for tag read events."""

from fastapi import APIRouter, Depends

from tagpulse.api.dependencies import get_ingestion_service
from tagpulse.core.tenant_auth import Tenant, get_current_tenant
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
    return await service.ingest(tenant.id, body)


@router.post("/tag-reads/batch", status_code=201)
async def create_tag_reads_batch(
    body: list[TagReadCreate],
    tenant: Tenant = Depends(get_current_tenant),
    service: IngestionService = Depends(get_ingestion_service),
) -> dict[str, int]:
    """Ingest a batch of tag read events via HTTP push."""
    count = await service.ingest_batch(tenant.id, body)
    return {"ingested": count}
