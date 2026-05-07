"""Time-bucketed aggregation seam (Sprint 13b — Multi-tier Foundations).

Single place where backend-specific aggregation SQL lives. Selected once at
startup from :data:`tagpulse.core.config.settings.database_backend`.

* :class:`TimescaleMetricsRepository` uses ``time_bucket`` so the planner can
  later route the query through a continuous aggregate without changing the
  call sites.
* :class:`PostgresMetricsRepository` uses ``date_trunc`` and is intended to be
  paired with a periodic ``REFRESH MATERIALIZED VIEW`` (or ``pg_cron``) on
  the same buckets.

**Scope is intentionally tight**: only time-bucketed aggregation queries go
through this seam (estimated 4–8 methods over the platform's lifetime).
Single-row lookups, simple filters, and ``LIMIT`` queries stay on regular
repositories with plain SQL that runs identically on both backends. **Review
rule:** any new method here requires both implementations in the same PR.

Per [docs/design/storage-strategy.md §6 Q1](../../docs/design/storage-strategy.md).
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.config import settings


@dataclass(slots=True, frozen=True)
class HourlyReaderBucket:
    """One row of ``tag_reads_hourly_by_reader``."""

    bucket_start: datetime
    reader_id: uuid.UUID
    read_count: int


class MetricsRepository(ABC):
    """Backend-agnostic interface for time-bucketed aggregations.

    The contract guarantees identical *shape* of results across backends so
    callers (rules engine, dashboards, billing rollups) never have to branch
    on the active backend.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @abstractmethod
    async def tag_reads_hourly_by_reader(
        self,
        tenant_id: uuid.UUID,
        since: datetime,
        until: datetime,
    ) -> Sequence[HourlyReaderBucket]:
        """Reads-per-hour-per-reader over ``[since, until)``."""


class TimescaleMetricsRepository(MetricsRepository):
    """Uses ``time_bucket('1 hour', timestamp)`` so the planner can swap in a
    continuous aggregate transparently when one is created."""

    _SQL = text(
        """
        SELECT
            time_bucket('1 hour', "timestamp") AS bucket_start,
            reader_id,
            COUNT(*)::bigint                   AS read_count
        FROM tag_reads
        WHERE tenant_id = :tenant_id
          AND "timestamp" >= :since
          AND "timestamp" <  :until
        GROUP BY bucket_start, reader_id
        ORDER BY bucket_start, reader_id
        """
    )

    async def tag_reads_hourly_by_reader(
        self,
        tenant_id: uuid.UUID,
        since: datetime,
        until: datetime,
    ) -> Sequence[HourlyReaderBucket]:
        result = await self._session.execute(
            self._SQL, {"tenant_id": tenant_id, "since": since, "until": until}
        )
        return [HourlyReaderBucket(*row) for row in result.all()]


class PostgresMetricsRepository(MetricsRepository):
    """Plain-PostgreSQL implementation. Identical row shape to Timescale.

    Intended to be paired with a periodic ``REFRESH MATERIALIZED VIEW
    tag_reads_hourly_by_reader`` driven by ``pg_cron`` or an app-side
    scheduler — see roadmap Sprint 13b for the matview rollout plan.
    """

    _SQL = text(
        """
        SELECT
            date_trunc('hour', "timestamp") AS bucket_start,
            reader_id,
            COUNT(*)::bigint                AS read_count
        FROM tag_reads
        WHERE tenant_id = :tenant_id
          AND "timestamp" >= :since
          AND "timestamp" <  :until
        GROUP BY bucket_start, reader_id
        ORDER BY bucket_start, reader_id
        """
    )

    async def tag_reads_hourly_by_reader(
        self,
        tenant_id: uuid.UUID,
        since: datetime,
        until: datetime,
    ) -> Sequence[HourlyReaderBucket]:
        result = await self._session.execute(
            self._SQL, {"tenant_id": tenant_id, "since": since, "until": until}
        )
        return [HourlyReaderBucket(*row) for row in result.all()]


def get_metrics_repository(session: AsyncSession) -> MetricsRepository:
    """Pick the right impl based on ``settings.database_backend``."""
    if settings.database_backend == "timescale":
        return TimescaleMetricsRepository(session)
    return PostgresMetricsRepository(session)
