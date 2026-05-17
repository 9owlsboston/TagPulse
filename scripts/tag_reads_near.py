"""scripts/tag_reads_near.py — inspect tag_reads rows around a timestamp.

Read-only triage tool for "what landed in tag_reads near time T?". Runs
in-VNet via ``scripts/azd-job.sh <env> tag_reads_near.py -- [flags]`` so
it can reach the private Postgres (the laptop can't).

Examples
--------
- Show every tag-read in the 10 minutes around 14:49:55 PDT
  (= 21:49:55 UTC), default 20-row limit::

      scripts/azd-job.sh dev tag_reads_near.py -- \\
        --at 2026-05-17T21:49:55Z --window 600

- Narrow to one reader and widen the window to 30 minutes either side::

      scripts/azd-job.sh dev tag_reads_near.py -- \\
        --device-id <reader-uuid> \\
        --at 2026-05-17T21:49:55Z --before 1800 --after 1800 --limit 100

- Asymmetric window (everything in the 5 min *before* T, nothing after)::

      scripts/azd-job.sh dev tag_reads_near.py -- \\
        --at 2026-05-17T21:49:55Z --before 300 --after 0

- "What's the most recent tag-read we've got for this reader?" (defaults
  --at to now, --before to 1h, --after to 0)::

      scripts/azd-job.sh dev tag_reads_near.py -- \\
        --device-id <reader-uuid> --before 3600 --after 0

Output: one JSON object per row to stdout, plus a one-line summary to
stderr (count, earliest, latest, distinct devices/antennas). Strictly
observational — no writes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tag-reads-near")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--at",
        default=None,
        help=(
            "ISO-8601 timestamp WITH timezone (e.g. '2026-05-17T21:49:55Z' or "
            "'2026-05-17T14:49:55-07:00'). Default: now (UTC)."
        ),
    )
    p.add_argument(
        "--window",
        type=int,
        default=300,
        help=(
            "Symmetric window in seconds around --at. Default 300 (=5 min "
            "either side). Ignored if --before/--after are set."
        ),
    )
    p.add_argument(
        "--before",
        type=int,
        default=None,
        help="Seconds before --at to include (overrides --window).",
    )
    p.add_argument(
        "--after",
        type=int,
        default=None,
        help="Seconds after --at to include (overrides --window).",
    )
    p.add_argument(
        "--device-id",
        default=None,
        help="Filter to a single device UUID.",
    )
    p.add_argument(
        "--tenant-id",
        default=None,
        help="Filter to a single tenant UUID.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max rows to print, ordered by timestamp DESC (default 20).",
    )
    p.add_argument(
        "--order",
        choices=["asc", "desc"],
        default="desc",
        help="Sort order on timestamp (default desc = most recent first).",
    )
    return p.parse_args(argv)


def _resolve_anchor(args: argparse.Namespace) -> datetime:
    if args.at:
        try:
            # Python 3.11+: fromisoformat accepts 'Z' suffix and ±HH:MM offsets.
            anchor = datetime.fromisoformat(args.at)
        except ValueError as exc:
            log.error("invalid --at value %r: %s", args.at, exc)
            raise SystemExit(2) from exc
        if anchor.tzinfo is None:
            log.error("--at must include a timezone (e.g. 'Z' or '-07:00')")
            raise SystemExit(2)
        return anchor.astimezone(UTC)
    return datetime.now(UTC)


def _resolve_window(args: argparse.Namespace) -> tuple[int, int]:
    before = args.before if args.before is not None else args.window
    after = args.after if args.after is not None else args.window
    if before < 0 or after < 0:
        log.error("--before/--after/--window must be non-negative")
        raise SystemExit(2)
    return before, after


def _row_to_jsonable(row: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row._mapping.items():  # noqa: SLF001 (SQLAlchemy Row API)
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, uuid.UUID):
            out[k] = str(v)
        else:
            out[k] = v
    return out


async def _run_async(args: argparse.Namespace) -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        log.error("DATABASE_URL not set; run via scripts/azd-job.sh so the tools-job env loads")
        return 2

    anchor = _resolve_anchor(args)
    before_s, after_s = _resolve_window(args)
    start = anchor - timedelta(seconds=before_s)
    end = anchor + timedelta(seconds=after_s)

    log.info(
        "anchor=%s window=[%s, %s] (=-%ds/+%ds) limit=%d order=%s",
        anchor.isoformat(),
        start.isoformat(),
        end.isoformat(),
        before_s,
        after_s,
        args.limit,
        args.order,
    )

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    # Project a useful triage subset; full row is wide and noisy for stdout.
    sql_text = """
        SELECT
            id, tenant_id, device_id, tag_id,
            timestamp, created_at,
            signal_strength, reader_antenna,
            epc_hex, tid,
            sensor_data, tag_data,
            latitude, longitude, location_accuracy_m, location_source
        FROM tag_reads
        WHERE timestamp >= :start AND timestamp <= :end
        {device_filter}
        {tenant_filter}
        ORDER BY timestamp {order}
        LIMIT :limit
    """.format(
        device_filter="AND device_id = :device_id" if args.device_id else "",
        tenant_filter="AND tenant_id = :tenant_id" if args.tenant_id else "",
        order="DESC" if args.order == "desc" else "ASC",
    )
    params: dict[str, Any] = {"start": start, "end": end, "limit": args.limit}
    if args.device_id:
        try:
            params["device_id"] = uuid.UUID(args.device_id)
        except ValueError as exc:
            log.error("invalid --device-id %r: %s", args.device_id, exc)
            return 2
    if args.tenant_id:
        try:
            params["tenant_id"] = uuid.UUID(args.tenant_id)
        except ValueError as exc:
            log.error("invalid --tenant-id %r: %s", args.tenant_id, exc)
            return 2

    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(sql_text), params)
            rows = list(result)
    finally:
        await engine.dispose()

    for row in rows:
        print(json.dumps(_row_to_jsonable(row), indent=2, sort_keys=True, default=str))
        print("---")

    # Summary to stderr so stdout stays a clean JSON stream.
    if rows:
        timestamps = sorted(r._mapping["timestamp"] for r in rows)  # noqa: SLF001
        distinct_devices = {str(r._mapping["device_id"]) for r in rows}  # noqa: SLF001
        antennas = {r._mapping["reader_antenna"] for r in rows}  # noqa: SLF001
        log.info(
            "rows=%d earliest=%s latest=%s devices=%d antennas=%s",
            len(rows),
            timestamps[0].isoformat(),
            timestamps[-1].isoformat(),
            len(distinct_devices),
            sorted(a for a in antennas if a is not None),
        )
    else:
        log.info("rows=0 (no tag_reads in window)")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        return asyncio.run(_run_async(args))
    except KeyboardInterrupt:
        log.info("interrupted")
        return 0


if __name__ == "__main__":
    sys.exit(main())
