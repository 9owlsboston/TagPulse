"""UsageMeter — in-process buffered counter for tenant usage metering."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from datetime import UTC, date, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tagpulse.models.database import TenantQuota, TenantUsageDetail

logger = logging.getLogger(__name__)


class QuotaResult(StrEnum):
    ALLOWED = "allowed"
    THROTTLED = "throttled"
    REJECTED = "rejected"


class UsageMeter:
    """Buffers usage counts in memory and flushes to DB periodically."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        flush_interval: float = 60.0,
    ) -> None:
        self._session_factory = session_factory
        self._flush_interval = flush_interval
        self._buffer: dict[tuple[UUID, str, str], int] = defaultdict(int)
        self._quota_cache: dict[tuple[UUID, str], TenantQuota | None] = {}
        self._task: asyncio.Task[None] | None = None

    def record(self, tenant_id: UUID, dimension: str, unit: str, count: int = 1) -> None:
        """Buffer a usage increment (non-async, fast)."""
        key = (tenant_id, dimension, unit)
        self._buffer[key] += count

    async def record_snapshot(
        self,
        tenant_id: UUID,
        dimension: str,
        unit: str,
        value: int,
    ) -> None:
        """Record a gauge-style snapshot — replaces today's value, doesn't sum.

        Used by periodic workers (e.g. ``stock_items_active``) where the
        recorded number reflects current state, not a delta. Writes through
        immediately rather than buffering, since snapshots are infrequent
        (typically once/day) and must not be merged with the additive buffer.
        """
        try:
            async with self._session_factory() as session:
                today = datetime(
                    date.today().year,
                    date.today().month,
                    date.today().day,
                    tzinfo=UTC,
                )
                stmt = text("""
                    INSERT INTO tenant_usage_detail
                        (tenant_id, usage_date, dimension, quantity, unit)
                    VALUES (:tid, :dt, :dim, :qty, :unit)
                    ON CONFLICT (tenant_id, usage_date, dimension)
                    DO UPDATE SET quantity = :qty, unit = :unit
                """)
                await session.execute(
                    stmt,
                    {
                        "tid": tenant_id,
                        "dt": today,
                        "dim": dimension,
                        "qty": value,
                        "unit": unit,
                    },
                )
                await session.commit()
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "UsageMeter.record_snapshot failed (tenant=%s dim=%s)",
                tenant_id,
                dimension,
            )

    async def check_quota(
        self, tenant_id: UUID, dimension: str, session: AsyncSession
    ) -> QuotaResult:
        """Check if tenant has exceeded quota for a dimension."""
        cache_key = (tenant_id, dimension)
        if cache_key not in self._quota_cache:
            stmt = select(TenantQuota).where(
                TenantQuota.tenant_id == tenant_id,
                TenantQuota.dimension == dimension,
            )
            result = await session.execute(stmt)
            self._quota_cache[cache_key] = result.scalar_one_or_none()

        quota = self._quota_cache[cache_key]
        if quota is None:
            return QuotaResult.ALLOWED

        # Get current usage from buffer + DB
        today = date.today()
        buffered = self._buffer.get((tenant_id, dimension, ""), 0)
        usage_stmt = select(TenantUsageDetail.quantity).where(
            TenantUsageDetail.tenant_id == tenant_id,
            TenantUsageDetail.dimension == dimension,
            TenantUsageDetail.usage_date
            == datetime(today.year, today.month, today.day, tzinfo=UTC),
        )
        usage_result = await session.execute(usage_stmt)
        db_quantity: int = usage_result.scalar_one_or_none() or 0
        total = db_quantity + buffered

        if total >= quota.max_quantity:
            if quota.action_on_exceed == "reject":
                return QuotaResult.REJECTED
            return QuotaResult.THROTTLED

        return QuotaResult.ALLOWED

    async def start(self) -> None:
        """Start periodic flush task."""
        self._task = asyncio.create_task(self._flush_loop())
        logger.info("UsageMeter started (flush interval: %.0fs)", self._flush_interval)

    async def stop(self) -> None:
        """Stop flush task and do a final flush."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._flush()
        logger.info("UsageMeter stopped")

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval)
            await self._flush()

    async def _flush(self) -> None:
        """Flush buffered counts to tenant_usage_detail table."""
        if not self._buffer:
            return

        snapshot = dict(self._buffer)
        self._buffer.clear()

        try:
            async with self._session_factory() as session:
                today = datetime(
                    date.today().year, date.today().month, date.today().day, tzinfo=UTC
                )
                for (tenant_id, dimension, unit), quantity in snapshot.items():
                    stmt = text("""
                        INSERT INTO tenant_usage_detail
                            (tenant_id, usage_date, dimension, quantity, unit)
                        VALUES (:tid, :dt, :dim, :qty, :unit)
                        ON CONFLICT (tenant_id, usage_date, dimension)
                        DO UPDATE SET quantity = tenant_usage_detail.quantity + :qty
                    """)
                    await session.execute(
                        stmt,
                        {
                            "tid": tenant_id,
                            "dt": today,
                            "dim": dimension,
                            "qty": quantity,
                            "unit": unit,
                        },
                    )
                await session.commit()
            logger.debug("UsageMeter flushed %d dimensions", len(snapshot))
        except Exception:
            # Put unflushed data back
            for key, qty in snapshot.items():
                self._buffer[key] += qty
            logger.exception("UsageMeter flush failed, data buffered for retry")
