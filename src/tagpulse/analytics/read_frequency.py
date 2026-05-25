"""Read frequency analytics module — reads/min per reader, anomaly flagging."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tagpulse.analytics import AnalyticsModule
from tagpulse.events.protocol import Event, Topic
from tagpulse.models.database import AnalyticsResultModel

logger = logging.getLogger(__name__)

# Anomaly threshold: flag if current rate deviates by more than 2 standard deviations
ANOMALY_STDDEV_FACTOR = 2.0
FLUSH_INTERVAL_SECONDS = 60


class ReadFrequencyModule(AnalyticsModule):
    """Counts reads per minute per device and flags anomalies."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory
        self._counters: dict[tuple[str, str], int] = defaultdict(int)
        self._task: asyncio.Task[None] | None = None

    @property
    def name(self) -> str:
        return "read_frequency"

    @property
    def subscribed_topics(self) -> list[Topic]:
        return [Topic.TAG_READ_CREATED]

    async def on_event(self, event: Event) -> None:
        """Increment in-memory counter for the device."""
        tenant_id = event.payload.get("tenant_id", "")
        device_id = event.payload.get("device_id", "")
        if tenant_id and device_id:
            self._counters[(tenant_id, device_id)] += 1

    async def start(self) -> None:
        self._task = asyncio.create_task(self._flush_loop())
        logger.info("ReadFrequencyModule started (flush every %ds)", FLUSH_INTERVAL_SECONDS)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._flush()
        logger.info("ReadFrequencyModule stopped")

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
            await self._flush()

    async def _flush(self) -> None:
        """Flush counters to DB and compute anomaly flags."""
        if not self._counters:
            return

        snapshot = dict(self._counters)
        self._counters.clear()
        now = datetime.now(UTC)

        try:
            async with self._session_factory() as session:
                for (tenant_id_str, device_id_str), count in snapshot.items():
                    tenant_id = uuid.UUID(tenant_id_str)
                    device_id = uuid.UUID(device_id_str)

                    # Store reads_per_minute
                    session.add(
                        AnalyticsResultModel(
                            id=uuid.uuid4(),
                            tenant_id=tenant_id,
                            module_name=self.name,
                            device_id=device_id,
                            metric_name="reads_per_minute",
                            metric_value=float(count),
                            computed_at=now,
                        )
                    )

                    # Compute anomaly flag
                    is_anomaly = await _check_anomaly(
                        session, tenant_id, device_id, self.name, count
                    )
                    session.add(
                        AnalyticsResultModel(
                            id=uuid.uuid4(),
                            tenant_id=tenant_id,
                            module_name=self.name,
                            device_id=device_id,
                            metric_name="anomaly_flag",
                            metric_value=1.0 if is_anomaly else 0.0,
                            computed_at=now,
                        )
                    )

                await session.commit()
                logger.debug("ReadFrequencyModule flushed %d devices", len(snapshot))
        except (SQLAlchemyError, OSError):
            # Put data back for retry
            for key, cnt in snapshot.items():
                self._counters[key] += cnt
            logger.exception("ReadFrequencyModule flush failed")


async def _check_anomaly(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    module_name: str,
    current_rate: int,
) -> bool:
    """Check if current rate is anomalous vs. 1-hour rolling window."""
    one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
    stmt = select(
        func.avg(AnalyticsResultModel.metric_value).label("mean"),
        func.stddev(AnalyticsResultModel.metric_value).label("stddev"),
    ).where(
        AnalyticsResultModel.tenant_id == tenant_id,
        AnalyticsResultModel.device_id == device_id,
        AnalyticsResultModel.module_name == module_name,
        AnalyticsResultModel.metric_name == "reads_per_minute",
        AnalyticsResultModel.computed_at >= one_hour_ago,
    )
    result = await session.execute(stmt)
    row = result.one()
    mean: float | None = row.mean
    stddev: float | None = row.stddev

    if mean is None or stddev is None or stddev == 0:
        return False

    deviation = abs(current_rate - mean)
    return deviation > ANOMALY_STDDEV_FACTOR * stddev
