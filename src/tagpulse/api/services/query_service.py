"""Query and telemetry service — tag read queries, aggregations, device health."""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from tagpulse.models.schemas import (
    DeviceHealthSummary,
    ReadsPerHour,
    TagReadResponse,
    UniqueTagsPerWindow,
)
from tagpulse.repositories.protocols import DeviceRepository, TagReadRepository

logger = logging.getLogger(__name__)


class QueryService:
    """Provides tag read queries, aggregations, and device health summaries."""

    def __init__(
        self,
        tag_read_repo: TagReadRepository,
        device_repo: DeviceRepository,
    ) -> None:
        self._tag_read_repo = tag_read_repo
        self._device_repo = device_repo

    async def query_tag_reads(
        self,
        tenant_id: UUID,
        *,
        device_id: UUID | None = None,
        tag_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        has_location: bool | None = None,
        epc_scheme: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TagReadResponse]:
        """Query tag reads with filters and pagination."""
        return await self._tag_read_repo.query(
            tenant_id,
            device_id=device_id,
            tag_id=tag_id,
            start=start,
            end=end,
            has_location=has_location,
            epc_scheme=epc_scheme,
            limit=limit,
            offset=offset,
        )

    async def reads_per_hour(
        self,
        tenant_id: UUID,
        *,
        device_id: UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[ReadsPerHour]:
        """Get read counts per device per hour."""
        return await self._tag_read_repo.reads_per_hour(
            tenant_id, device_id=device_id, start=start, end=end
        )

    async def unique_tags_per_window(
        self,
        tenant_id: UUID,
        *,
        device_id: UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        window_minutes: int = 60,
    ) -> list[UniqueTagsPerWindow]:
        """Get unique tag counts per time window."""
        return await self._tag_read_repo.unique_tags_per_window(
            tenant_id,
            device_id=device_id,
            start=start,
            end=end,
            window_minutes=window_minutes,
        )

    async def recent_reads(
        self,
        tenant_id: UUID,
        device_id: UUID,
        *,
        limit: int = 50,
    ) -> list[TagReadResponse]:
        """Get the most recent reads for a specific device."""
        return await self._tag_read_repo.query(
            tenant_id, device_id=device_id, limit=limit
        )

    async def device_health(
        self,
        tenant_id: UUID,
        *,
        status: str | None = "active",
    ) -> list[DeviceHealthSummary]:
        """Build health summaries for devices."""
        devices = await self._device_repo.list(tenant_id, status=status, limit=1000)
        one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
        summaries: list[DeviceHealthSummary] = []
        for device in devices:
            reads_last_hour = await self._tag_read_repo.count_reads_since(
                tenant_id, device.id, one_hour_ago
            )
            alerts_last_hour = await self._tag_read_repo.count_alerts_since(
                tenant_id, device.id, one_hour_ago
            )
            total = reads_last_hour + alerts_last_hour
            error_rate = round(alerts_last_hour / total, 4) if total > 0 else 0.0
            summaries.append(
                DeviceHealthSummary(
                    device_id=device.id,
                    name=device.name,
                    status=device.status,
                    connection_state=device.connection_state,
                    last_seen=device.last_seen,
                    reads_last_hour=reads_last_hour,
                    error_rate=error_rate,
                )
            )
        return summaries

    async def single_device_health(
        self,
        tenant_id: UUID,
        device_id: UUID,
    ) -> DeviceHealthSummary | None:
        """Build health summary for a single device."""
        device = await self._device_repo.get(tenant_id, device_id)
        if device is None:
            return None
        one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
        reads_last_hour = await self._tag_read_repo.count_reads_since(
            tenant_id, device_id, one_hour_ago
        )
        alerts_last_hour = await self._tag_read_repo.count_alerts_since(
            tenant_id, device_id, one_hour_ago
        )
        total = reads_last_hour + alerts_last_hour
        error_rate = round(alerts_last_hour / total, 4) if total > 0 else 0.0
        return DeviceHealthSummary(
            device_id=device.id,
            name=device.name,
            status=device.status,
            connection_state=device.connection_state,
            last_seen=device.last_seen,
            reads_last_hour=reads_last_hour,
            error_rate=error_rate,
        )
