"""Repository protocols — technology-agnostic contracts for data access."""

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from tagpulse.models.schemas import (
    DeviceCreate,
    DeviceResponse,
    DeviceUpdate,
    LatestTelemetryEntry,
    ReadsPerHour,
    TagReadCreate,
    TagReadResponse,
    TelemetryAggregateBucket,
    TelemetryQuarantineResponse,
    TelemetryReading,
    TelemetryReadingResponse,
    TelemetryResponse,
    UniqueTagsPerWindow,
)


class TagReadRepository(Protocol):
    """Contract for tag read persistence."""

    async def insert(self, tenant_id: UUID, read: TagReadCreate) -> TagReadResponse: ...

    async def insert_batch(
        self, tenant_id: UUID, reads: list[TagReadCreate]
    ) -> list[TagReadResponse]: ...

    async def record_rejection(
        self,
        tenant_id: UUID,
        read: TagReadCreate,
        reason: str,
    ) -> None: ...

    async def query(
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
    ) -> list[TagReadResponse]: ...

    async def reads_per_hour(
        self,
        tenant_id: UUID,
        *,
        device_id: UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        bucket_minutes: int = 60,
    ) -> list[ReadsPerHour]: ...

    async def unique_tags_per_window(
        self,
        tenant_id: UUID,
        *,
        device_id: UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        window_minutes: int = 60,
    ) -> list[UniqueTagsPerWindow]: ...

    async def count_reads_since(
        self,
        tenant_id: UUID,
        device_id: UUID,
        since: datetime,
    ) -> int: ...

    async def count_alerts_since(
        self,
        tenant_id: UUID,
        device_id: UUID,
        since: datetime,
    ) -> int: ...


class DeviceRepository(Protocol):
    """Contract for device registry persistence."""

    async def create(self, tenant_id: UUID, device: DeviceCreate) -> DeviceResponse: ...

    async def get(self, tenant_id: UUID, device_id: UUID) -> DeviceResponse | None: ...

    async def list(
        self,
        tenant_id: UUID,
        *,
        status: str | None = None,
        device_type: str | None = None,
        labels: dict[str, list[str]] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DeviceResponse]: ...

    async def update(
        self, tenant_id: UUID, device_id: UUID, patch: DeviceUpdate
    ) -> DeviceResponse | None: ...

    async def decommission(self, tenant_id: UUID, device_id: UUID) -> DeviceResponse | None: ...

    async def update_status(
        self,
        tenant_id: UUID,
        device_id: UUID,
        *,
        connection_state: str,
        firmware_version: str | None = None,
    ) -> DeviceResponse | None: ...

    async def record_last_seen(
        self, tenant_id: UUID, device_id: UUID, seen_at: datetime
    ) -> None: ...

    async def record_connection_state(
        self, tenant_id: UUID, device_id: UUID, connection_state: str
    ) -> None: ...


class TelemetryReadingsRepository(Protocol):
    """Subject-scoped telemetry persistence (Sprint 18 schema).

    Sprint 21 (ADR-015 §6) folded the Sprint 14 device-shaped surface
    (``insert_reading`` / ``query`` / ``quarantine`` / ``list_quarantine``)
    into this protocol after the legacy ``TelemetryRepository`` was
    removed. The subject-aware methods (``insert`` / ``query_by_subject`` /
    ``latest_per_metric`` / ``aggregate``) target the
    ``telemetry_readings`` hypertable directly.
    """

    # -- Sprint 14 device-shaped surface --

    async def insert_reading(
        self,
        tenant_id: UUID,
        device_id: UUID,
        reading: TelemetryReading,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> TelemetryResponse: ...

    async def quarantine(
        self,
        tenant_id: UUID,
        device_id: UUID,
        reading: TelemetryReading,
        reason: str,
    ) -> None: ...

    async def query(
        self,
        tenant_id: UUID,
        *,
        device_id: UUID | None = None,
        metric_name: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> list[TelemetryResponse]: ...

    async def list_quarantine(
        self,
        tenant_id: UUID,
        *,
        device_id: UUID | None = None,
        reason: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TelemetryQuarantineResponse]: ...

    # -- Subject-aware surface (Sprint 18+) --

    async def insert(
        self,
        *,
        tenant_id: UUID,
        subject_kind: str,
        subject_id: UUID,
        timestamp: datetime,
        metric_name: str,
        metric_value: float,
        device_id: UUID | None = None,
        unit: str | None = None,
        source: str = "device",
        metadata: dict[str, Any] | None = None,
    ) -> TelemetryReadingResponse: ...

    async def query_by_subject(
        self,
        *,
        tenant_id: UUID,
        subject_kind: str,
        subject_id: UUID,
        metric_name: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> list[TelemetryReadingResponse]: ...

    async def latest_per_metric(
        self,
        *,
        tenant_id: UUID,
        subject_kind: str,
        subject_id: UUID,
        limit: int = 5,
    ) -> list[LatestTelemetryEntry]: ...

    async def aggregate(
        self,
        *,
        tenant_id: UUID,
        subject_kind: str,
        subject_id: UUID,
        metric_name: str,
        bucket_seconds: int,
        start: datetime,
        end: datetime,
    ) -> list[TelemetryAggregateBucket]: ...
