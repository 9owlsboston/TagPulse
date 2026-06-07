#!/usr/bin/env python3
"""Backfill historical tag-read events for the demo tenant.

Replays synthetic tag reads with timestamps spread across a configurable past
window via the existing ``POST /tag-reads/batch?backfill=true`` endpoint.

The ``?backfill=true`` query parameter is recognised by the ingestion service
(see ADR-031 / Sprint 58 Phase B): the read still runs the full ingest
pipeline (validation, enrichment, hypertable insert, telemetry rollups) but
rule evaluation is suppressed and ``reads/minute`` analytics counters skip
the row. This keeps the curated alert set authored by ``seed_alerts.py``
from being polluted by alerts the historical replay itself would otherwise
trigger.

Devices are discovered via ``GET /devices`` if not supplied. The script is
idempotent in the demo-tenant sense — re-running adds another window of
reads but does not double-create devices or tags.

Usage:
    python scripts/backfill_history.py \\
        --tenant-id <UUID> \\
        --api-key <KEY> \\
        --days 3 \\
        --reads 5000
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx

API_URL = os.environ.get("TAGPULSE_API_URL", "http://localhost:8000").rstrip("/")

# Stable demo tag pool — same shape as simulate_devices.TAG_POOL so the
# historical window references the same tag identifiers the live simulator
# will continue to emit afterward.
_DEFAULT_TAG_POOL = [f"TAG{i:04d}" for i in range(1, 51)]


def _headers(tenant_id: str, api_key: str) -> dict[str, str]:
    return {
        "X-Tenant-ID": tenant_id,
        "Authorization": f"Bearer {api_key}",
    }


def _discover_devices(
    client: httpx.Client, tenant_id: str, api_key: str
) -> list[str]:
    """Fetch all devices for the tenant and return their UUIDs."""
    resp = client.get(f"{API_URL}/devices", headers=_headers(tenant_id, api_key))
    resp.raise_for_status()
    devices = resp.json()
    if not devices:
        print(
            "ERROR: no devices found for tenant — run simulate_devices.py first",
            file=sys.stderr,
        )
        sys.exit(1)
    return [d["id"] for d in devices]


def _build_read(
    device_id: str, tag_id: str, timestamp: datetime
) -> dict[str, Any]:
    """Build a single TagReadCreate payload with a past timestamp."""
    sensor_data: dict[str, float] = {
        "temperature": round(random.uniform(18.0, 28.0), 1),
    }
    if random.random() < 0.3:
        sensor_data["humidity"] = round(random.uniform(30.0, 80.0), 1)
    return {
        "device_id": device_id,
        "tag_id": tag_id,
        "timestamp": timestamp.isoformat(),
        "signal_strength": round(random.uniform(-80.0, -20.0), 1),
        "sensor_data": sensor_data,
    }


def _generate_window(
    devices: list[str],
    tags: list[str],
    *,
    days: float,
    total_reads: int,
    seed: int | None,
) -> list[dict[str, Any]]:
    """Generate ``total_reads`` payloads spread across the last ``days``."""
    if seed is not None:
        random.seed(seed)
    now = datetime.now(UTC)
    window_start = now - timedelta(days=days)
    window_seconds = (now - window_start).total_seconds()
    reads: list[dict[str, Any]] = []
    for _ in range(total_reads):
        offset_seconds = random.uniform(0.0, window_seconds)
        ts = window_start + timedelta(seconds=offset_seconds)
        reads.append(
            _build_read(
                device_id=random.choice(devices),
                tag_id=random.choice(tags),
                timestamp=ts,
            )
        )
    # Sort by timestamp so hypertable inserts are mostly in-order — keeps
    # TimescaleDB chunk insertion efficient.
    reads.sort(key=lambda r: r["timestamp"])
    return reads


def _post_batches(
    client: httpx.Client,
    tenant_id: str,
    api_key: str,
    reads: list[dict[str, Any]],
    batch_size: int,
) -> tuple[int, int]:
    """POST reads in batches with backfill=true. Returns (ingested, rejected)."""
    url = f"{API_URL}/tag-reads/batch?backfill=true"
    headers = _headers(tenant_id, api_key)
    ingested_total = 0
    rejected_total = 0
    batch_count = (len(reads) + batch_size - 1) // batch_size
    for batch_idx, start in enumerate(range(0, len(reads), batch_size), start=1):
        batch = reads[start : start + batch_size]
        resp = client.post(url, headers=headers, json=batch)
        if resp.status_code != 201:
            print(
                f"  batch {batch_idx}/{batch_count}: HTTP {resp.status_code}"
                f" — {resp.text[:200]}",
                file=sys.stderr,
            )
            continue
        body = resp.json()
        ingested_total += body.get("ingested", 0)
        rejected_total += body.get("rejected", 0)
        if batch_idx % 10 == 0 or batch_idx == batch_count:
            print(
                f"  batch {batch_idx}/{batch_count}: "
                f"ingested={ingested_total} rejected={rejected_total}"
            )
    return ingested_total, rejected_total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tenant-id", required=True, help="Target tenant UUID (X-Tenant-ID)"
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("TAGPULSE_API_KEY"),
        help="Bearer API key (admin/editor). Default: $TAGPULSE_API_KEY",
    )
    parser.add_argument(
        "--days",
        type=float,
        default=3.0,
        help="Hours/days of history to replay (default: 3.0)",
    )
    parser.add_argument(
        "--reads",
        type=int,
        default=5000,
        help="Total reads to backfill across the window (default: 5000)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Reads per POST batch (default: 500)",
    )
    parser.add_argument(
        "--devices",
        nargs="*",
        default=None,
        help="Device UUIDs to round-robin (default: discover via GET /devices)",
    )
    parser.add_argument(
        "--tags",
        type=int,
        default=50,
        help="Size of the synthetic tag pool TAG0001..TAGNNNN (default: 50)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for deterministic replays (default: nondeterministic)",
    )
    args = parser.parse_args()

    if not args.api_key:
        print(
            "ERROR: --api-key or TAGPULSE_API_KEY required (admin/editor scope)",
            file=sys.stderr,
        )
        return 2

    try:
        UUID(args.tenant_id)
    except ValueError:
        print(f"ERROR: --tenant-id must be a UUID, got {args.tenant_id!r}", file=sys.stderr)
        return 2

    print(
        f"Backfilling {args.reads} reads across {args.days} day(s) → {API_URL} "
        f"(tenant={args.tenant_id})"
    )

    with httpx.Client(timeout=30.0) as client:
        devices = args.devices or _discover_devices(
            client, args.tenant_id, args.api_key
        )
        print(f"  using {len(devices)} device(s)")

        tag_pool = [f"TAG{i:04d}" for i in range(1, args.tags + 1)] or _DEFAULT_TAG_POOL
        print(f"  using tag pool of {len(tag_pool)} ({tag_pool[0]}..{tag_pool[-1]})")

        t0 = time.monotonic()
        reads = _generate_window(
            devices,
            tag_pool,
            days=args.days,
            total_reads=args.reads,
            seed=args.seed,
        )
        gen_secs = time.monotonic() - t0
        print(f"  generated {len(reads)} reads in {gen_secs:.1f}s")

        t1 = time.monotonic()
        ingested, rejected = _post_batches(
            client,
            args.tenant_id,
            args.api_key,
            reads,
            batch_size=args.batch_size,
        )
        post_secs = time.monotonic() - t1

    rate = ingested / post_secs if post_secs > 0 else 0.0
    print(
        f"Done: ingested={ingested} rejected={rejected} in {post_secs:.1f}s "
        f"({rate:.0f} reads/sec)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
