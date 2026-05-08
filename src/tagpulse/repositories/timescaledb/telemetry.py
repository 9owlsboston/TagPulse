"""TimescaleDB implementation of telemetry persistence.

Sprint 14 introduced ``TimescaleTelemetryRepository`` over the
``device_telemetry`` hypertable. Sprint 18 (see ADR-013 and
``docs/design/subject-scoped-telemetry.md``) renamed that table to
``telemetry_readings_legacy_device`` and re-pointed all writes at a new
subject-scoped ``telemetry_readings`` hypertable.

Sprint 21 (ADR-015 §6) closes the deprecation window: the legacy
back-compat view + hypertable are dropped by migration 032 and the
old wrapper class is removed. Only :class:`TimescaleTelemetryReadingsRepository`
remains. It exposes both the subject-aware surface (``insert`` /
``query_by_subject`` / ``latest_per_metric`` / ``aggregate``) and a
device-shaped Sprint 14 surface (``insert_reading`` / ``query`` /
``quarantine`` / ``list_quarantine``) that ``TelemetryService`` uses
to keep the ``/devices/{id}/telemetry`` HTTP and MQTT contracts
byte-for-byte stable.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import (
    TelemetryQuarantineModel,
    TelemetryReadingModel,
)
from tagpulse.models.schemas import (
    LatestTelemetryEntry,
    TelemetryAggregateBucket,
    TelemetryQuarantineResponse,
    TelemetryReading,
    TelemetryReadingResponse,
    TelemetryResponse,
)


class TimescaleTelemetryReadingsRepository:
    """Subject-scoped telemetry persistence.

    Sprint 18 introduced this as the subject-aware successor to
    ``TimescaleTelemetryRepository``. Sprint 21 collapsed the legacy
    device-scoped wrapper into this class: the same instance now
    handles both the multi-subject ingest path
    (:meth:`insert` / :meth:`query_by_subject` / :meth:`latest_per_metric`
    / :meth:`aggregate`) and the Sprint 14 device-shaped surface
    (:meth:`insert_reading` / :meth:`query` / :meth:`quarantine` /
    :meth:`list_quarantine`) used by ``TelemetryService``.

    A single tag-read with on-tag temperature fans out to one row per
    resolved subject (device, asset, lot, stock_item, …) by calling
    :meth:`insert` once per subject — the rows share ``device_id`` so
    device-scoped queries keep working.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- Sprint 14 device-shaped surface (consumed by TelemetryService) --

    async def insert_reading(
        self,
        tenant_id: UUID,
        device_id: UUID,
        reading: TelemetryReading,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> TelemetryResponse:
        """Insert a device-scoped reading (subject_kind='device').

        Preserves the Sprint 14 ``TelemetryRepository`` contract so the
        ingest service can stay device-shaped while writing to the
        subject-scoped hypertable underneath.
        """
        merged_metadata = {**(reading.metadata or {}), **(metadata or {})}
        merged = merged_metadata if merged_metadata else None
        row = TelemetryReadingModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            subject_kind="device",
            subject_id=device_id,
            device_id=device_id,
            timestamp=reading.timestamp,
            metric_name=reading.metric_name,
            metric_value=reading.metric_value,
            unit=reading.unit,
            source="device",
            metadata_=merged,
        )
        self._session.add(row)
        await self._session.flush()
        return TelemetryResponse(
            id=row.id,
            device_id=device_id,
            timestamp=row.timestamp,
            metric_name=row.metric_name,
            metric_value=row.metric_value,
            unit=row.unit,
            metadata=row.metadata_,
        )

    async def quarantine(
        self,
        tenant_id: UUID,
        device_id: UUID,
        reading: TelemetryReading,
        reason: str,
    ) -> None:
        row = TelemetryQuarantineModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            device_id=device_id,
            metric_name=reading.metric_name,
            metric_value=reading.metric_value,
            raw_payload=reading.model_dump(mode="json"),
            reason=reason,
            subject_kind="device",
            subject_id=device_id,
        )
        self._session.add(row)
        await self._session.flush()

    async def query(
        self,
        tenant_id: UUID,
        *,
        device_id: UUID | None = None,
        metric_name: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> list[TelemetryResponse]:
        stmt = (
            select(TelemetryReadingModel)
            .where(
                TelemetryReadingModel.tenant_id == tenant_id,
                TelemetryReadingModel.subject_kind == "device",
            )
            .order_by(TelemetryReadingModel.timestamp.desc())
        )
        if device_id is not None:
            stmt = stmt.where(TelemetryReadingModel.device_id == device_id)
        if metric_name is not None:
            stmt = stmt.where(TelemetryReadingModel.metric_name == metric_name)
        if start is not None:
            stmt = stmt.where(TelemetryReadingModel.timestamp >= start)
        if end is not None:
            stmt = stmt.where(TelemetryReadingModel.timestamp <= end)
        stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return [
            TelemetryResponse(
                id=r.id,
                device_id=r.device_id,  # type: ignore[arg-type]
                timestamp=r.timestamp,
                metric_name=r.metric_name,
                metric_value=r.metric_value,
                unit=r.unit,
                metadata=r.metadata_,
            )
            for r in result.scalars()
        ]

    async def list_quarantine(
        self,
        tenant_id: UUID,
        *,
        device_id: UUID | None = None,
        reason: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TelemetryQuarantineResponse]:
        stmt = (
            select(TelemetryQuarantineModel)
            .where(TelemetryQuarantineModel.tenant_id == tenant_id)
            .order_by(TelemetryQuarantineModel.received_at.desc())
        )
        if device_id is not None:
            stmt = stmt.where(TelemetryQuarantineModel.device_id == device_id)
        if reason is not None:
            stmt = stmt.where(TelemetryQuarantineModel.reason == reason)
        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return [TelemetryQuarantineResponse.model_validate(row) for row in result.scalars()]

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
    ) -> TelemetryReadingResponse:
        row = TelemetryReadingModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            subject_kind=subject_kind,
            subject_id=subject_id,
            device_id=device_id,
            timestamp=timestamp,
            metric_name=metric_name,
            metric_value=metric_value,
            unit=unit,
            source=source,
            metadata_=metadata,
        )
        self._session.add(row)
        await self._session.flush()
        return TelemetryReadingResponse(
            id=row.id,
            subject_kind=row.subject_kind,  # type: ignore[arg-type]
            subject_id=row.subject_id,
            device_id=row.device_id,
            timestamp=row.timestamp,
            metric_name=row.metric_name,
            metric_value=row.metric_value,
            unit=row.unit,
            source=row.source,  # type: ignore[arg-type]
            metadata=row.metadata_,
        )

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
    ) -> list[TelemetryReadingResponse]:
        stmt = (
            select(TelemetryReadingModel)
            .where(
                TelemetryReadingModel.tenant_id == tenant_id,
                TelemetryReadingModel.subject_kind == subject_kind,
                TelemetryReadingModel.subject_id == subject_id,
            )
            .order_by(TelemetryReadingModel.timestamp.desc())
        )
        if metric_name is not None:
            stmt = stmt.where(TelemetryReadingModel.metric_name == metric_name)
        if start is not None:
            stmt = stmt.where(TelemetryReadingModel.timestamp >= start)
        if end is not None:
            stmt = stmt.where(TelemetryReadingModel.timestamp <= end)
        stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return [
            TelemetryReadingResponse(
                id=r.id,
                subject_kind=r.subject_kind,  # type: ignore[arg-type]
                subject_id=r.subject_id,
                device_id=r.device_id,
                timestamp=r.timestamp,
                metric_name=r.metric_name,
                metric_value=r.metric_value,
                unit=r.unit,
                source=r.source,  # type: ignore[arg-type]
                metadata=r.metadata_,
            )
            for r in result.scalars()
        ]

    async def latest_per_metric(
        self,
        *,
        tenant_id: UUID,
        subject_kind: str,
        subject_id: UUID,
        limit: int = 5,
    ) -> list[LatestTelemetryEntry]:
        """Return the newest reading per metric for a subject.

        Used to populate the ``latest_telemetry`` block embedded on
        ``GET /assets/{id}`` and ``GET /lots/{id}``. Bounded by
        ``limit`` distinct metric names so a runaway tag emitting
        thousands of metric keys cannot bloat a single GET response.
        """
        # PostgreSQL DISTINCT ON: pick the row with the largest
        # ``timestamp`` per ``metric_name``. The outer ORDER BY +
        # LIMIT bounds the result by recency of the latest sample.
        stmt = text(
            """
            SELECT metric_name, metric_value, unit, timestamp, source
            FROM (
                SELECT DISTINCT ON (metric_name)
                    metric_name, metric_value, unit, timestamp, source
                FROM telemetry_readings
                WHERE tenant_id = :tenant_id
                  AND subject_kind = :subject_kind
                  AND subject_id = :subject_id
                ORDER BY metric_name, timestamp DESC
            ) latest
            ORDER BY timestamp DESC
            LIMIT :limit
            """
        )
        result = await self._session.execute(
            stmt,
            {
                "tenant_id": tenant_id,
                "subject_kind": subject_kind,
                "subject_id": subject_id,
                "limit": limit,
            },
        )
        return [
            LatestTelemetryEntry(
                metric_name=row.metric_name,
                metric_value=float(row.metric_value),
                unit=row.unit,
                timestamp=row.timestamp,
                source=row.source,
            )
            for row in result
        ]

    # Process-wide cache for cagg availability — see _caggs_available().
    # ``None`` = unknown, will be probed on first aggregate() call.
    _caggs_available_cache: bool | None = None

    async def _caggs_available(self) -> bool:
        """Return True if ``cagg_telemetry_1m`` and ``_1h`` exist.

        Continuous aggregates are a TSL-licensed TimescaleDB feature
        and are unavailable on Azure Database for PostgreSQL Flexible
        Server (Apache-2 edition only). When migration 031 detects the
        Apache license it skips the cagg DDL, so the views never get
        created in cloud deployments. We probe ``pg_class`` once per
        process and cache the result on the class — the cagg story is
        a deploy-time decision, not a per-request one.
        """
        cls = type(self)
        if cls._caggs_available_cache is None:
            row = await self._session.execute(
                text(
                    "SELECT count(*) FROM pg_class "
                    "WHERE relname IN ('cagg_telemetry_1m', 'cagg_telemetry_1h')"
                )
            )
            available = (row.scalar_one() or 0) >= 2
            cls._caggs_available_cache = available
            return available
        return cls._caggs_available_cache

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
    ) -> list[TelemetryAggregateBucket]:
        """Return time-bucketed avg/min/max/count for a single metric.

        Routes to the ``cagg_telemetry_1m`` continuous aggregate when
        ``bucket_seconds == 60``, ``cagg_telemetry_1h`` when
        ``bucket_seconds == 3600``, and falls back to a live
        ``time_bucket`` over the raw hypertable for other intervals
        (or for any bucket width when the caggs are not provisioned —
        e.g. on Azure Flex's Apache-2 TimescaleDB edition).
        """
        caggs_ok = await self._caggs_available()
        if caggs_ok and bucket_seconds == 60:
            # Read-only continuous aggregate; identifier is fixed.
            stmt = text(
                """
                SELECT bucket, avg_value, min_value, max_value, sample_count
                FROM cagg_telemetry_1m
                WHERE tenant_id = :tenant_id
                  AND subject_kind = :subject_kind
                  AND subject_id = :subject_id
                  AND metric_name = :metric_name
                  AND bucket BETWEEN :start AND :end
                ORDER BY bucket ASC
                """  # noqa: S608 -- fixed identifier, all values bound
            )
            params: dict[str, Any] = {
                "tenant_id": tenant_id,
                "subject_kind": subject_kind,
                "subject_id": subject_id,
                "metric_name": metric_name,
                "start": start,
                "end": end,
            }
        elif caggs_ok and bucket_seconds == 3600:
            stmt = text(
                """
                SELECT bucket, avg_value, min_value, max_value, sample_count
                FROM cagg_telemetry_1h
                WHERE tenant_id = :tenant_id
                  AND subject_kind = :subject_kind
                  AND subject_id = :subject_id
                  AND metric_name = :metric_name
                  AND bucket BETWEEN :start AND :end
                ORDER BY bucket ASC
                """  # noqa: S608 -- fixed identifier, all values bound
            )
            params = {
                "tenant_id": tenant_id,
                "subject_kind": subject_kind,
                "subject_id": subject_id,
                "metric_name": metric_name,
                "start": start,
                "end": end,
            }
        else:
            stmt = text(
                """
                SELECT
                    time_bucket(make_interval(secs => :secs), timestamp)
                        AS bucket,
                    avg(metric_value)   AS avg_value,
                    min(metric_value)   AS min_value,
                    max(metric_value)   AS max_value,
                    count(*)            AS sample_count
                FROM telemetry_readings
                WHERE tenant_id = :tenant_id
                  AND subject_kind = :subject_kind
                  AND subject_id = :subject_id
                  AND metric_name = :metric_name
                  AND timestamp BETWEEN :start AND :end
                GROUP BY bucket
                ORDER BY bucket ASC
                """
            )
            params = {
                "secs": bucket_seconds,
                "tenant_id": tenant_id,
                "subject_kind": subject_kind,
                "subject_id": subject_id,
                "metric_name": metric_name,
                "start": start,
                "end": end,
            }

        result = await self._session.execute(stmt, params)
        return [
            TelemetryAggregateBucket(
                subject_kind=subject_kind,  # type: ignore[arg-type]
                subject_id=subject_id,
                metric_name=metric_name,
                bucket=row.bucket,
                avg_value=float(row.avg_value),
                min_value=float(row.min_value),
                max_value=float(row.max_value),
                sample_count=int(row.sample_count),
            )
            for row in result
        ]
