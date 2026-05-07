"""Benchmark ``PostgresMetricsRepository`` to find the PG-mode scaling ceiling.

Sprint 13b deliverable per
[docs/design/storage-strategy.md §6 Q1](../docs/design/storage-strategy.md).

Seeds an isolated ``bench_tag_reads`` table at multiple fleet sizes (devices x
24 h x reads/hour) and times the ``tag_reads_hourly_by_reader`` aggregation
against both the cold table and a refreshed materialized view (the v1 PG-mode
strategy). Reports p50/p95/p99 in milliseconds.

Usage::

    DATABASE_URL=postgresql+asyncpg://tagpulse:secret@localhost:5432/tagpulse \\
        python scripts/benchmark_pg_metrics.py \\
            --fleets 100,500,1000,2000,5000 \\
            --reads-per-hour 60 \\
            --iterations 20

The script is idempotent: it drops and recreates ``bench_tag_reads`` /
``bench_tag_reads_hourly`` on every run, so it is safe to point at the dev DB.
It does **not** touch any production tables.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

DEFAULT_FLEETS = "100,500,1000,2000,5000"
DEFAULT_REGISTERS = 4  # antennas/readers per device
DEFAULT_READS_PER_HOUR = 60
DEFAULT_ITERATIONS = 20
DEFAULT_HOURS = 24


@dataclass(slots=True)
class BenchResult:
    fleet_size: int
    rows: int
    cold_p50_ms: float
    cold_p95_ms: float
    cold_p99_ms: float
    matview_refresh_ms: float
    matview_p50_ms: float
    matview_p95_ms: float
    matview_p99_ms: float


async def _seed(
    session: AsyncSession, fleet_size: int, hours: int, reads_per_hour: int
) -> tuple[int, uuid.UUID]:
    tenant_id = uuid.uuid4()
    rows_per_device = hours * reads_per_hour
    total_rows = fleet_size * rows_per_device

    await session.execute(text("DROP MATERIALIZED VIEW IF EXISTS bench_tag_reads_hourly"))
    await session.execute(text("DROP TABLE IF EXISTS bench_tag_reads"))
    await session.execute(
        text(
            """
            CREATE TABLE bench_tag_reads (
                tenant_id  UUID        NOT NULL,
                reader_id  UUID        NOT NULL,
                "timestamp" TIMESTAMPTZ NOT NULL
            )
            """
        )
    )

    # generate_series in a single statement keeps the seed under a few seconds
    # even for 5k devices x 24h x 60 reads/h = 7.2M rows.
    await session.execute(
        text(
            """
            INSERT INTO bench_tag_reads (tenant_id, reader_id, "timestamp")
            SELECT
                CAST(:tenant_id AS uuid),
                d.reader_id,
                NOW() - (random() * INTERVAL '1 hour' * :hours)
            FROM (
                SELECT gen_random_uuid() AS reader_id
                FROM generate_series(1, :fleet_size)
            ) d
            CROSS JOIN generate_series(1, :rows_per_device)
            """
        ),
        {
            "tenant_id": str(tenant_id),
            "fleet_size": fleet_size,
            "hours": hours,
            "rows_per_device": rows_per_device,
        },
    )

    await session.execute(
        text(
            "CREATE INDEX bench_tag_reads_tenant_ts_idx "
            'ON bench_tag_reads (tenant_id, "timestamp" DESC)'
        )
    )
    await session.execute(text("ANALYZE bench_tag_reads"))
    await session.commit()
    return total_rows, tenant_id


_COLD_SQL = text(
    """
    SELECT
        date_trunc('hour', "timestamp") AS bucket_start,
        reader_id,
        COUNT(*)::bigint                AS read_count
    FROM bench_tag_reads
    WHERE tenant_id = :tenant_id
      AND "timestamp" >= NOW() - INTERVAL '1 hour' * :hours
    GROUP BY bucket_start, reader_id
    ORDER BY bucket_start, reader_id
    """
)


async def _time_query(
    session: AsyncSession,
    sql: Any,
    params: dict[str, object],
    iterations: int,
) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        result = await session.execute(sql, params)
        result.all()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return samples


async def _build_matview(session: AsyncSession, tenant_id: uuid.UUID, hours: int) -> float:
    await session.execute(text("DROP MATERIALIZED VIEW IF EXISTS bench_tag_reads_hourly"))
    t0 = time.perf_counter()
    await session.execute(
        text(
            """
            CREATE MATERIALIZED VIEW bench_tag_reads_hourly AS
            SELECT
                tenant_id,
                date_trunc('hour', "timestamp") AS bucket_start,
                reader_id,
                COUNT(*)::bigint                AS read_count
            FROM bench_tag_reads
            GROUP BY tenant_id, bucket_start, reader_id
            """
        )
    )
    await session.execute(
        text(
            "CREATE INDEX bench_tag_reads_hourly_tenant_ts_idx "
            "ON bench_tag_reads_hourly (tenant_id, bucket_start)"
        )
    )
    await session.commit()
    return (time.perf_counter() - t0) * 1000.0


_MATVIEW_SQL = text(
    """
    SELECT bucket_start, reader_id, read_count
    FROM bench_tag_reads_hourly
    WHERE tenant_id = :tenant_id
      AND bucket_start >= NOW() - INTERVAL '1 hour' * :hours
    ORDER BY bucket_start, reader_id
    """
)


def _pct(samples: list[float], q: float) -> float:
    return statistics.quantiles(samples, n=100)[int(q) - 1] if len(samples) >= 2 else samples[0]


async def _run_one(
    engine: AsyncEngine,
    fleet_size: int,
    hours: int,
    reads_per_hour: int,
    iterations: int,
) -> BenchResult:
    async with AsyncSession(engine) as session:
        total_rows, tenant_id = await _seed(session, fleet_size, hours, reads_per_hour)
        params = {"tenant_id": str(tenant_id), "hours": hours}

        cold = await _time_query(session, _COLD_SQL, params, iterations)
        refresh_ms = await _build_matview(session, tenant_id, hours)
        warm = await _time_query(session, _MATVIEW_SQL, params, iterations)

    return BenchResult(
        fleet_size=fleet_size,
        rows=total_rows,
        cold_p50_ms=statistics.median(cold),
        cold_p95_ms=_pct(cold, 95),
        cold_p99_ms=_pct(cold, 99),
        matview_refresh_ms=refresh_ms,
        matview_p50_ms=statistics.median(warm),
        matview_p95_ms=_pct(warm, 95),
        matview_p99_ms=_pct(warm, 99),
    )


def _format_table(results: list[BenchResult]) -> str:
    header = (
        "| Fleet (devices) | Rows | Cold p50 / p95 / p99 (ms) "
        "| Matview refresh (ms) | Matview p50 / p95 / p99 (ms) |"
    )
    sep = "|---:|---:|---|---:|---|"
    lines = [header, sep]
    for r in results:
        lines.append(
            f"| {r.fleet_size:,} | {r.rows:,} "
            f"| {r.cold_p50_ms:.1f} / {r.cold_p95_ms:.1f} / {r.cold_p99_ms:.1f} "
            f"| {r.matview_refresh_ms:.0f} "
            f"| {r.matview_p50_ms:.1f} / {r.matview_p95_ms:.1f} / {r.matview_p99_ms:.1f} |"
        )
    return "\n".join(lines)


async def _amain() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fleets", default=DEFAULT_FLEETS)
    parser.add_argument("--hours", type=int, default=DEFAULT_HOURS)
    parser.add_argument("--reads-per-hour", type=int, default=DEFAULT_READS_PER_HOUR)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument(
        "--database-url",
        default=os.environ.get(
            "DATABASE_URL",
            "postgresql+asyncpg://tagpulse:secret@localhost:5432/tagpulse",
        ),
    )
    args = parser.parse_args()

    fleets = [int(x) for x in args.fleets.split(",") if x.strip()]
    engine = create_async_engine(args.database_url, pool_pre_ping=True)

    print(f"# benchmark_pg_metrics — {len(fleets)} fleet sizes, {args.iterations} iters each")
    print(f"#   hours={args.hours} reads_per_hour={args.reads_per_hour}")
    print()

    results: list[BenchResult] = []
    for fleet in fleets:
        print(f"... fleet={fleet}", flush=True)
        results.append(
            await _run_one(engine, fleet, args.hours, args.reads_per_hour, args.iterations)
        )

    print()
    print(_format_table(results))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_amain())
