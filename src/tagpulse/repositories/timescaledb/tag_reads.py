"""TimescaleDB implementation of the TagReadRepository protocol."""

import uuid
from datetime import datetime

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.api.filters import LIKE_ESCAPE, wildcard_to_ilike
from tagpulse.models.database import (
    AlertModel,
    AssetModel,
    AssetTagBindingModel,
    DeadLetterEventModel,
    TagReadModel,
)
from tagpulse.models.schemas import (
    ReadsPerHour,
    TagReadCreate,
    TagReadResponse,
    UniqueTagsPerWindow,
)

# Sprint 76: whitelist of server-sortable Tag Reads columns. Anything outside
# this map is rejected (avoids arbitrary ORDER BY injection from the UI).
TAG_READ_SORT_COLUMNS = {
    "timestamp": TagReadModel.timestamp,
    "signal_strength": TagReadModel.signal_strength,
    "reader_antenna": TagReadModel.reader_antenna,
}


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
            location_accuracy_m=(read.location.accuracy_m if read.location else None),
            location_source=read.location.source if read.location else None,
            epc=read.identity.epc if read.identity else None,
            epc_hex=read.identity.epc_hex if read.identity else None,
            epc_scheme=read.identity.epc_scheme if read.identity else None,
            epc_decoded=read.identity.epc_decoded if read.identity else None,
            tid=read.identity.tid if read.identity else None,
            user_memory_hex=(read.identity.user_memory_hex if read.identity else None),
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
                location_accuracy_m=(r.location.accuracy_m if r.location else None),
                location_source=r.location.source if r.location else None,
                epc=r.identity.epc if r.identity else None,
                epc_hex=r.identity.epc_hex if r.identity else None,
                epc_scheme=r.identity.epc_scheme if r.identity else None,
                epc_decoded=r.identity.epc_decoded if r.identity else None,
                tid=r.identity.tid if r.identity else None,
                user_memory_hex=(r.identity.user_memory_hex if r.identity else None),
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
            source="tag_read_rejected",
        )
        self._session.add(row)
        await self._session.flush()

    async def query(
        self,
        tenant_id: uuid.UUID,
        *,
        device_id: uuid.UUID | None = None,
        tag_id: str | None = None,
        tag_q: str | None = None,
        epc_q: str | None = None,
        asset_q: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        has_location: bool | None = None,
        epc_scheme: str | None = None,
        epc_schemes: list[str] | None = None,
        reader_antennas: list[int] | None = None,
        sort: str | None = None,
        order: str = "desc",
        limit: int = 100,
        offset: int = 0,
    ) -> list[TagReadResponse]:
        stmt = select(TagReadModel).where(TagReadModel.tenant_id == tenant_id)
        if device_id is not None:
            stmt = stmt.where(TagReadModel.device_id == device_id)
        if tag_id is not None:
            stmt = stmt.where(TagReadModel.tag_id == tag_id)
        tag_like = wildcard_to_ilike(tag_q)
        if tag_like is not None:
            # Sprint 70: wildcard search over ``tag_id`` (the EPC), bare term =
            # substring, anchored when a ``*``/``?`` is present, case-insensitive.
            stmt = stmt.where(TagReadModel.tag_id.ilike(tag_like, escape=LIKE_ESCAPE))
        epc_like = wildcard_to_ilike(epc_q)
        if epc_like is not None:
            # Sprint 75: identifier wildcard search across the EPC family
            # (``tag_id``/``epc``/``epc_hex``/``tid``) via OR; same grammar as
            # ``tag_q``. A read matches if any identifier column matches.
            stmt = stmt.where(
                or_(
                    TagReadModel.tag_id.ilike(epc_like, escape=LIKE_ESCAPE),
                    TagReadModel.epc.ilike(epc_like, escape=LIKE_ESCAPE),
                    TagReadModel.epc_hex.ilike(epc_like, escape=LIKE_ESCAPE),
                    TagReadModel.tid.ilike(epc_like, escape=LIKE_ESCAPE),
                )
            )
        asset_like = wildcard_to_ilike(asset_q)
        if asset_like is not None:
            # Sprint 76: filter by the *bound asset name*. A read matches if
            # there is an active binding (``unbound_at IS NULL``) whose
            # ``binding_value`` equals any of the read's tag forms and whose
            # asset name matches the wildcard. Correlated EXISTS — keeps the
            # tag-reads hypertable scan single-pass.
            stmt = stmt.where(
                exists(
                    select(1)
                    .select_from(AssetTagBindingModel)
                    .join(AssetModel, AssetModel.id == AssetTagBindingModel.asset_id)
                    .where(
                        AssetTagBindingModel.tenant_id == tenant_id,
                        AssetTagBindingModel.unbound_at.is_(None),
                        AssetModel.name.ilike(asset_like, escape=LIKE_ESCAPE),
                        AssetTagBindingModel.binding_value.in_(
                            [
                                TagReadModel.tag_id,
                                TagReadModel.epc,
                                TagReadModel.epc_hex,
                                TagReadModel.tid,
                            ]
                        ),
                    )
                )
            )
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
        if epc_schemes:
            # Sprint 76: multi-select scheme (the column checkbox list, backed by
            # ``GET /tag-reads/facets``). Combines with single ``epc_scheme`` via AND.
            stmt = stmt.where(TagReadModel.epc_scheme.in_(epc_schemes))
        if reader_antennas:
            stmt = stmt.where(TagReadModel.reader_antenna.in_(reader_antennas))
        # Sprint 76: server-side sort over a whitelist; default timestamp desc.
        sort_col = TAG_READ_SORT_COLUMNS.get(sort or "timestamp")
        if sort_col is None:
            raise ValueError(f"unsortable column: {sort!r}")
        stmt = stmt.order_by(sort_col.asc() if order == "asc" else sort_col.desc())
        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return [TagReadResponse.model_validate(row) for row in result.scalars()]

    async def facets(self, tenant_id: uuid.UUID) -> dict[str, list[str]]:
        """Sprint 76 — distinct low-cardinality values for Tag Reads checkbox
        filters (``epc_scheme``, ``reader_antenna``). Bounded by a LIMIT; these
        columns are inherently small-set."""
        scheme_stmt = (
            select(TagReadModel.epc_scheme)
            .where(
                TagReadModel.tenant_id == tenant_id,
                TagReadModel.epc_scheme.isnot(None),
            )
            .distinct()
            .limit(100)
        )
        antenna_stmt = (
            select(TagReadModel.reader_antenna)
            .where(
                TagReadModel.tenant_id == tenant_id,
                TagReadModel.reader_antenna.isnot(None),
            )
            .distinct()
            .limit(100)
        )
        schemes = sorted(str(v) for v in (await self._session.execute(scheme_stmt)).scalars())
        antennas = sorted(
            int(v) for v in (await self._session.execute(antenna_stmt)).scalars() if v is not None
        )
        return {"epc_scheme": schemes, "reader_antenna": [str(a) for a in antennas]}

    async def reads_per_hour(
        self,
        tenant_id: uuid.UUID,
        *,
        device_id: uuid.UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        bucket_minutes: int = 60,
    ) -> list[ReadsPerHour]:
        bucket = func.date_trunc("hour", TagReadModel.timestamp).label("bucket")
        if bucket_minutes != 60:
            bucket = func.to_timestamp(
                func.floor(func.extract("epoch", TagReadModel.timestamp) / (bucket_minutes * 60))
                * (bucket_minutes * 60)
            ).label("bucket")
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
                func.floor(func.extract("epoch", TagReadModel.timestamp) / (window_minutes * 60))
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
