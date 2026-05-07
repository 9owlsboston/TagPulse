"""TimescaleDB implementation of the TagReadRepository protocol."""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import (
    AlertModel,
    DeadLetterEventModel,
    TagReadModel,
)
from tagpulse.models.schemas import (
    ReadsPerHour,
    TagReadCreate,
    TagReadResponse,
    UniqueTagsPerWindow,
)


class TimescaleTagReadRepository:
    """Persists tag read events to TimescaleDB hypertable."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(self, tenant_id: uuid.UUID, read: TagReadCreate) -> TagReadResponse:
        row = TagReadModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            device_id=read.device_id,
            tag_id=read.tag_id or "",
            timestamp=read.timestamp,
            signal_strength=read.signal_strength,
            sensor_data=read.sensor_data,
            latitude=read.location.latitude if read.location else None,
            longitude=read.location.longitude if read.location else None,
            location_accuracy_m=(
                read.location.accuracy_m if read.location else None
            ),
            location_source=read.location.source if read.location else None,
            epc=read.identity.epc if read.identity else None,
            epc_hex=read.identity.epc_hex if read.identity else None,
            epc_scheme=read.identity.epc_scheme if read.identity else None,
            epc_decoded=read.identity.epc_decoded if read.identity else None,
            tid=read.identity.tid if read.identity else None,
            user_memory_hex=(
                read.identity.user_memory_hex if read.identity else None
            ),
            tag_data=read.tag_data,
            reader_antenna=read.reader_antenna,
        )
        self._session.add(row)
        await self._session.flush()
        return TagReadResponse.model_validate(row)

    async def insert_batch(
        self, tenant_id: uuid.UUID, reads: list[TagReadCreate]
    ) -> list[TagReadResponse]:
        rows = [
            TagReadModel(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                device_id=r.device_id,
                tag_id=r.tag_id or "",
                timestamp=r.timestamp,
                signal_strength=r.signal_strength,
                sensor_data=r.sensor_data,
                latitude=r.location.latitude if r.location else None,
                longitude=r.location.longitude if r.location else None,
                location_accuracy_m=(
                    r.location.accuracy_m if r.location else None
                ),
                location_source=r.location.source if r.location else None,
                epc=r.identity.epc if r.identity else None,
                epc_hex=r.identity.epc_hex if r.identity else None,
                epc_scheme=r.identity.epc_scheme if r.identity else None,
                epc_decoded=r.identity.epc_decoded if r.identity else None,
                tid=r.identity.tid if r.identity else None,
                user_memory_hex=(
                    r.identity.user_memory_hex if r.identity else None
                ),
                tag_data=r.tag_data,
                reader_antenna=r.reader_antenna,
            )
            for r in reads
        ]
        self._session.add_all(rows)
        await self._session.flush()
        return [TagReadResponse.model_validate(row) for row in rows]

    async def record_rejection(
        self,
        tenant_id: uuid.UUID,
        read: TagReadCreate,
        reason: str,
    ) -> None:
        """Persist a tag read that failed an ingestion-time guard (e.g. clock window)
        as a ``dead_letter_events`` row so operators can audit drops without
        relying solely on metrics. Per docs/design/edge-device-contract.md §3.5.
        """
        ts = read.timestamp
        payload: dict[str, object] = {
            "device_id": str(read.device_id),
            "tag_id": read.tag_id,
            "timestamp": ts.isoformat() if ts else None,
            "epc": read.identity.epc if read.identity else None,
            "tid": read.identity.tid if read.identity else None,
        }
        row = DeadLetterEventModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            topic="tag_read.rejected_clock",
            payload=payload,
            error_message=reason,
            retry_count=0,
            status="rejected",
        )
        self._session.add(row)
        await self._session.flush()

    async def query(
        self,
        tenant_id: uuid.UUID,
        *,
        device_id: uuid.UUID | None = None,
        tag_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        has_location: bool | None = None,
        epc_scheme: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TagReadResponse]:
        stmt = select(TagReadModel).where(
            TagReadModel.tenant_id == tenant_id
        ).order_by(TagReadModel.timestamp.desc())
        if device_id is not None:
            stmt = stmt.where(TagReadModel.device_id == device_id)
        if tag_id is not None:
            stmt = stmt.where(TagReadModel.tag_id == tag_id)
        if start is not None:
            stmt = stmt.where(TagReadModel.timestamp >= start)
        if end is not None:
            stmt = stmt.where(TagReadModel.timestamp <= end)
        if has_location is True:
            stmt = stmt.where(TagReadModel.latitude.isnot(None))
        elif has_location is False:
            stmt = stmt.where(TagReadModel.latitude.is_(None))
        if epc_scheme is not None:
            stmt = stmt.where(TagReadModel.epc_scheme == epc_scheme)
        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return [TagReadResponse.model_validate(row) for row in result.scalars()]

    async def reads_per_hour(
        self,
        tenant_id: uuid.UUID,
        *,
        device_id: uuid.UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[ReadsPerHour]:
        bucket = func.date_trunc("hour", TagReadModel.timestamp).label("bucket")
        stmt = (
            select(
                bucket,
                TagReadModel.device_id,
                func.count().label("read_count"),
            )
            .where(TagReadModel.tenant_id == tenant_id)
            .group_by(bucket, TagReadModel.device_id)
            .order_by(bucket.desc())
        )
        if device_id is not None:
            stmt = stmt.where(TagReadModel.device_id == device_id)
        if start is not None:
            stmt = stmt.where(TagReadModel.timestamp >= start)
        if end is not None:
            stmt = stmt.where(TagReadModel.timestamp <= end)
        result = await self._session.execute(stmt)
        return [
            ReadsPerHour(bucket=row.bucket, device_id=row.device_id, read_count=row.read_count)
            for row in result
        ]

    async def unique_tags_per_window(
        self,
        tenant_id: uuid.UUID,
        *,
        device_id: uuid.UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        window_minutes: int = 60,
    ) -> list[UniqueTagsPerWindow]:
        bucket = func.date_trunc("hour", TagReadModel.timestamp).label("bucket")
        if window_minutes != 60:
            bucket = func.to_timestamp(
                func.floor(
                    func.extract("epoch", TagReadModel.timestamp) / (window_minutes * 60)
                )
                * (window_minutes * 60)
            ).label("bucket")
        stmt = (
            select(
                bucket,
                TagReadModel.device_id,
                func.count(func.distinct(TagReadModel.tag_id)).label("unique_tags"),
            )
            .where(TagReadModel.tenant_id == tenant_id)
            .group_by(bucket, TagReadModel.device_id)
            .order_by(bucket.desc())
        )
        if device_id is not None:
            stmt = stmt.where(TagReadModel.device_id == device_id)
        if start is not None:
            stmt = stmt.where(TagReadModel.timestamp >= start)
        if end is not None:
            stmt = stmt.where(TagReadModel.timestamp <= end)
        result = await self._session.execute(stmt)
        return [
            UniqueTagsPerWindow(
                bucket=row.bucket,
                device_id=row.device_id,
                unique_tags=row.unique_tags,
            )
            for row in result
        ]

    async def count_reads_since(
        self,
        tenant_id: uuid.UUID,
        device_id: uuid.UUID,
        since: datetime,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(TagReadModel)
            .where(TagReadModel.tenant_id == tenant_id)
            .where(TagReadModel.device_id == device_id)
            .where(TagReadModel.timestamp >= since)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def count_alerts_since(
        self,
        tenant_id: uuid.UUID,
        device_id: uuid.UUID,
        since: datetime,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(AlertModel)
            .where(AlertModel.tenant_id == tenant_id)
            .where(AlertModel.device_id == device_id)
            .where(AlertModel.triggered_at >= since)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()
