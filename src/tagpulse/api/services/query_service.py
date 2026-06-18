"""Query and telemetry service — tag read queries, aggregations, device health."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from tagpulse.models.schemas import (
    DeviceHealthSummary,
    LocationDescriptor,
    ReadsPerHour,
    TagReadResponse,
    UniqueTagsPerWindow,
    ZoneResponse,
)
from tagpulse.repositories.protocols import DeviceRepository, TagReadRepository

if TYPE_CHECKING:
    from tagpulse.api.services.floor_zone_resolver import FloorRef, FloorZoneResolver

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ZoneRef:
    """Minimal zone projection used for the tag-read location descriptor."""

    id: UUID
    name: str


class ZoneReaderResolver(Protocol):
    """The slice of the zone repo the query service needs."""

    async def get_zone_for_reader(
        self, tenant_id: UUID, device_id: UUID
    ) -> ZoneResponse | None: ...


class QueryService:
    """Provides tag read queries, aggregations, and device health summaries."""

    def __init__(
        self,
        tag_read_repo: TagReadRepository,
        device_repo: DeviceRepository,
        zone_repo: "ZoneReaderResolver | None" = None,
        floor_resolver: "FloorZoneResolver | None" = None,
    ) -> None:
        self._tag_read_repo = tag_read_repo
        self._device_repo = device_repo
        # Optional: when present, fixed reads resolve their reader_bound zone for
        # the UI "Location" column. When absent (e.g. some unit tests), floor
        # reads simply get a ``kind="none"`` descriptor.
        self._zone_repo = zone_repo
        # Optional: the accurate D5 path — resolve a fixed read to a *floor* zone
        # by antenna-position point-in-polygon, preferred over reader_bound.
        self._floor_resolver = floor_resolver

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
        """Query tag reads with filters and pagination, enriched with a
        resolved ``location`` descriptor for the UI."""
        reads = await self._tag_read_repo.query(
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
        await self._attach_location_descriptors(tenant_id, reads)
        return reads

    async def _attach_location_descriptors(
        self, tenant_id: UUID, reads: list[TagReadResponse]
    ) -> None:
        """Resolve a ``location`` descriptor per read (in place).

        Geo reads (lat/lon present) are self-describing. Fixed reads resolve
        their reader-bound zone once per distinct device (cached for the page),
        so a 1000-row page does at most one zone lookup per device.
        """
        zone_cache: dict[UUID, ZoneRef | None] = {}
        for read in reads:
            if read.latitude is not None and read.longitude is not None:
                read.location = LocationDescriptor(
                    kind="geo",
                    lat=read.latitude,
                    lon=read.longitude,
                    accuracy_m=read.location_accuracy_m,
                    source=read.location_source,
                )
                continue
            # Accurate path first: antenna-position → floor polygon (D5).
            floor = await self._resolve_floor_zone(tenant_id, read)
            if floor is not None:
                read.location = LocationDescriptor(
                    kind="floor",
                    source=read.location_source,
                    zone_id=floor.id,
                    zone_name=floor.name,
                )
                continue
            zone = await self._resolve_zone(tenant_id, read.device_id, zone_cache)
            if zone is not None:
                read.location = LocationDescriptor(
                    kind="floor",
                    source=read.location_source,
                    zone_id=zone.id,
                    zone_name=zone.name,
                )
            else:
                read.location = LocationDescriptor(kind="none", source=read.location_source)

    async def _resolve_floor_zone(
        self, tenant_id: UUID, read: TagReadResponse
    ) -> "FloorRef | None":
        if self._floor_resolver is None:
            return None
        return await self._floor_resolver.resolve(tenant_id, read.device_id, read.reader_antenna)

    async def _resolve_zone(
        self,
        tenant_id: UUID,
        device_id: UUID,
        cache: "dict[UUID, ZoneRef | None]",
    ) -> "ZoneRef | None":
        if self._zone_repo is None:
            return None
        if device_id not in cache:
            zone = await self._zone_repo.get_zone_for_reader(tenant_id, device_id)
            cache[device_id] = ZoneRef(id=zone.id, name=zone.name) if zone else None
        return cache[device_id]

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
        return await self._tag_read_repo.query(tenant_id, device_id=device_id, limit=limit)

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
