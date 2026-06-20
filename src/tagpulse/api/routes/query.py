"""Query API routes — tag read search, aggregations, telemetry, device health."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from tagpulse.api.dependencies import get_query_service
from tagpulse.api.services.query_service import QueryService
from tagpulse.core.tenant_auth import Tenant, get_current_tenant
from tagpulse.models.schemas import (
    DeviceHealthSummary,
    ReadsPerHour,
    TagReadResponse,
    UniqueTagsPerWindow,
)

router = APIRouter(tags=["query"])


@router.get("/tag-reads", response_model=list[TagReadResponse])
async def query_tag_reads(
    device_id: UUID | None = Query(default=None),
    tag_id: str | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    has_location: bool | None = Query(
        default=None,
        description="If true, only return reads with a location; if false, only without.",
    ),
    epc_scheme: str | None = Query(
        default=None,
        description="Filter by decoded EPC scheme (e.g. 'sgtin-96', 'sscc-96', 'raw').",
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    tenant: Tenant = Depends(get_current_tenant),
    service: QueryService = Depends(get_query_service),
) -> list[TagReadResponse]:
    """Query tag reads with filters and pagination."""
    return await service.query_tag_reads(
        tenant.id,
        device_id=device_id,
        tag_id=tag_id,
        start=start,
        end=end,
        has_location=has_location,
        epc_scheme=epc_scheme,
        limit=limit,
        offset=offset,
    )


@router.get("/tag-reads/reads-per-hour", response_model=list[ReadsPerHour])
async def reads_per_hour(
    device_id: UUID | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    bucket_minutes: int = Query(default=60, ge=1, le=1440),
    tenant: Tenant = Depends(get_current_tenant),
    service: QueryService = Depends(get_query_service),
) -> list[ReadsPerHour]:
    """Get read counts per device per time bucket.

    ``bucket_minutes`` sets the bucket width (default 60 = hourly). Callers
    showing a narrow window can request a finer bucket so the series has real
    resolution instead of one or two hourly points.
    """
    return await service.reads_per_hour(
        tenant.id,
        device_id=device_id,
        start=start,
        end=end,
        bucket_minutes=bucket_minutes,
    )


@router.get("/tag-reads/unique-tags", response_model=list[UniqueTagsPerWindow])
async def unique_tags_per_window(
    device_id: UUID | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    window_minutes: int = Query(default=60, ge=1, le=1440),
    tenant: Tenant = Depends(get_current_tenant),
    service: QueryService = Depends(get_query_service),
) -> list[UniqueTagsPerWindow]:
    """Get unique tag counts per time window."""
    return await service.unique_tags_per_window(
        tenant.id,
        device_id=device_id,
        start=start,
        end=end,
        window_minutes=window_minutes,
    )


@router.get(
    "/telemetry/{device_id}/recent-reads",
    response_model=list[TagReadResponse],
)
async def recent_reads(
    device_id: UUID,
    limit: int = Query(default=50, ge=1, le=500),
    tenant: Tenant = Depends(get_current_tenant),
    service: QueryService = Depends(get_query_service),
) -> list[TagReadResponse]:
    """Get the most recent tag reads for a specific device."""
    return await service.recent_reads(tenant.id, device_id, limit=limit)


@router.get("/device-health", response_model=list[DeviceHealthSummary])
async def device_health(
    status: str | None = Query(default="active"),
    tenant: Tenant = Depends(get_current_tenant),
    service: QueryService = Depends(get_query_service),
) -> list[DeviceHealthSummary]:
    """Get health summaries for all devices."""
    return await service.device_health(tenant.id, status=status)


@router.get("/device-health/{device_id}", response_model=DeviceHealthSummary)
async def single_device_health(
    device_id: UUID,
    tenant: Tenant = Depends(get_current_tenant),
    service: QueryService = Depends(get_query_service),
) -> DeviceHealthSummary:
    """Get health summary for a single device."""
    result = await service.single_device_health(tenant.id, device_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Device not found") from None
    return result
