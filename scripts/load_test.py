#!/usr/bin/env python3
"""Load test harness — stress test TagPulse ingestion pipeline.

Usage:
    # 10 concurrent workers, 1000 total reads, as fast as possible
    python scripts/load_test.py --tenant-id <UUID> --workers 10 --total 1000

    # Sustained rate: 500 reads/sec for 60 seconds
    python scripts/load_test.py --tenant-id <UUID> --workers 20 --rps 500 --duration 60

    # Ramp up: start at 10 rps, increase by 50 every 10s, up to 500
    python scripts/load_test.py --tenant-id <UUID> --workers 20 --rps 10 --ramp 50 --ramp-step 10 --rps-max 500
"""

import argparse
import asyncio
import os
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

import httpx

# Optional bearer API key (admin/editor) — populated from --api-key or
# TAGPULSE_API_KEY env. Required for device creation since Sprint 12.
_API_KEY: str | None = None


def _headers(tenant_id: str, *, json: bool = False) -> dict[str, str]:
    h = {"X-Tenant-ID": tenant_id}
    if json:
        h["Content-Type"] = "application/json"
    if _API_KEY:
        h["Authorization"] = f"Bearer {_API_KEY}"
    return h

API_URL = "http://localhost:8000"
TAG_POOL = [f"TAG{i:04d}" for i in range(1, 201)]  # 200 unique tags


@dataclass
class Stats:
    """Collects request-level metrics."""

    success: int = 0
    failed: int = 0
    latencies: list[float] = field(default_factory=list)
    status_codes: dict[int, int] = field(default_factory=dict)
    errors: dict[str, int] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def record(self, status: int, latency: float) -> None:
        async with self._lock:
            self.latencies.append(latency)
            self.status_codes[status] = self.status_codes.get(status, 0) + 1
            if 200 <= status < 300:
                self.success += 1
            else:
                self.failed += 1

    async def record_error(self, error: str) -> None:
        async with self._lock:
            self.failed += 1
            self.errors[error] = self.errors.get(error, 0) + 1

    def summary(self, elapsed: float) -> str:
        total = self.success + self.failed
        rps = total / max(elapsed, 0.001)
        lines = [
            f"\n{'=' * 60}",
            f"  Load Test Results",
            f"{'=' * 60}",
            f"  Duration:      {elapsed:.1f}s",
            f"  Total:         {total:,}",
            f"  Success:       {self.success:,}",
            f"  Failed:        {self.failed:,}",
            f"  Throughput:    {rps:.1f} req/s",
        ]
        if self.latencies:
            lat = sorted(self.latencies)
            lines += [
                f"  Latency p50:   {lat[len(lat) // 2] * 1000:.1f}ms",
                f"  Latency p95:   {lat[int(len(lat) * 0.95)] * 1000:.1f}ms",
                f"  Latency p99:   {lat[int(len(lat) * 0.99)] * 1000:.1f}ms",
                f"  Latency max:   {lat[-1] * 1000:.1f}ms",
            ]
        if self.status_codes:
            lines.append(f"  Status codes:  {dict(sorted(self.status_codes.items()))}")
        if self.errors:
            lines.append(f"  Errors:        {dict(self.errors)}")
        lines.append(f"{'=' * 60}")
        return "\n".join(lines)


def make_payload(device_ids: list[str]) -> dict:
    """Generate a random tag read payload."""
    return {
        "device_id": random.choice(device_ids),
        "tag_id": random.choice(TAG_POOL),
        "timestamp": datetime.now(UTC).isoformat(),
        "signal_strength": round(random.uniform(-80.0, -20.0), 1),
        "sensor_data": {
            "temperature": round(random.uniform(18.0, 28.0), 1),
        },
    }


async def seed_devices(
    client: httpx.AsyncClient, tenant_id: str, count: int
) -> list[str]:
    """Create devices and return their IDs."""
    headers = _headers(tenant_id)
    # Fetch existing
    resp = await client.get(
        f"{API_URL}/device-registry", headers=headers, params={"limit": 1000}
    )
    existing = {d["name"]: d["id"] for d in resp.json()} if resp.status_code == 200 else {}

    device_ids: list[str] = []
    for i in range(count):
        name = f"LoadTest-{i + 1:03d}"
        if name in existing:
            device_ids.append(existing[name])
            continue
        resp = await client.post(
            f"{API_URL}/device-registry",
            headers=headers,
            json={"name": name, "device_type": "rfid_reader",
                  "metadata": {"load_test": True}},
        )
        if resp.status_code == 201:
            device_ids.append(resp.json()["id"])
    return device_ids


async def worker(
    client: httpx.AsyncClient,
    tenant_id: str,
    device_ids: list[str],
    stats: Stats,
    stop: asyncio.Event,
    rate_limiter: asyncio.Semaphore | None,
    total_counter: list[int] | None,
    total_limit: int,
) -> None:
    """Send tag reads until stopped or total limit reached."""
    headers = _headers(tenant_id, json=True)
    while not stop.is_set():
        if total_counter is not None:
            if total_counter[0] >= total_limit:
                return
            total_counter[0] += 1

        if rate_limiter:
            await rate_limiter.acquire()

        payload = make_payload(device_ids)
        t0 = time.monotonic()
        try:
            resp = await client.post(
                f"{API_URL}/tag-reads", headers=headers, json=payload
            )
            latency = time.monotonic() - t0
            await stats.record(resp.status_code, latency)
        except httpx.ConnectError:
            await stats.record_error("connect_error")
        except httpx.ReadTimeout:
            await stats.record_error("read_timeout")
        except Exception as e:
            await stats.record_error(type(e).__name__)


async def rate_ticker(
    sem: asyncio.Semaphore,
    stop: asyncio.Event,
    rps: int,
    ramp: int,
    ramp_step: int,
    rps_max: int,
) -> None:
    """Release semaphore tokens at the target rate, with optional ramp-up."""
    current_rps = rps
    interval = 1.0 / current_rps
    last_ramp = time.monotonic()

    while not stop.is_set():
        sem.release()
        await asyncio.sleep(interval)

        # Ramp up
        if ramp > 0 and (time.monotonic() - last_ramp) >= ramp_step:
            current_rps = min(current_rps + ramp, rps_max)
            interval = 1.0 / current_rps
            last_ramp = time.monotonic()
            print(f"  ↑ Ramped to {current_rps} rps")


async def progress_reporter(
    stats: Stats, stop: asyncio.Event, start: float
) -> None:
    """Print progress every 5 seconds."""
    while not stop.is_set():
        await asyncio.sleep(5)
        elapsed = time.monotonic() - start
        total = stats.success + stats.failed
        rps = total / max(elapsed, 0.001)
        print(
            f"  [{elapsed:.0f}s] {total:,} reqs | {rps:.0f} rps | "
            f"{stats.success:,} ok / {stats.failed:,} err"
        )


async def run_load_test(args: argparse.Namespace) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Health check
        try:
            resp = await client.get(f"{API_URL}/health")
            if resp.status_code != 200:
                print(f"API not healthy: {resp.status_code}")
                sys.exit(1)
        except httpx.ConnectError:
            print(f"Cannot connect to {API_URL}. Is the backend running?")
            sys.exit(1)

        # Seed devices
        print(f"\nSeeding {args.devices} load-test devices...")
        device_ids = await seed_devices(client, args.tenant_id, args.devices)
        if not device_ids:
            print("No devices available.")
            sys.exit(1)
        print(f"  {len(device_ids)} devices ready.\n")

        stats = Stats()
        stop = asyncio.Event()
        start = time.monotonic()

        # Rate limiter (if --rps set)
        rate_limiter: asyncio.Semaphore | None = None
        ticker_task = None
        if args.rps > 0:
            rate_limiter = asyncio.Semaphore(0)
            ticker_task = asyncio.create_task(
                rate_ticker(rate_limiter, stop, args.rps, args.ramp,
                            args.ramp_step, args.rps_max or args.rps * 10)
            )

        # Total counter (if --total set)
        total_counter: list[int] | None = None
        if args.total > 0:
            total_counter = [0]

        mode = f"{args.rps} rps" if args.rps > 0 else "max throughput"
        target = f"{args.total:,} reqs" if args.total > 0 else f"{args.duration}s"
        print(f"=== Load Test: {args.workers} workers, {mode}, target {target} ===\n")

        # Start workers
        workers = [
            asyncio.create_task(
                worker(client, args.tenant_id, device_ids, stats, stop,
                       rate_limiter, total_counter, args.total)
            )
            for _ in range(args.workers)
        ]
        reporter = asyncio.create_task(progress_reporter(stats, stop, start))

        # Wait for completion
        if args.total > 0:
            await asyncio.gather(*workers)
            stop.set()
        else:
            await asyncio.sleep(args.duration)
            stop.set()
            for w in workers:
                w.cancel()

        reporter.cancel()
        if ticker_task:
            ticker_task.cancel()

        elapsed = time.monotonic() - start
        print(stats.summary(elapsed))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TagPulse load test harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Burst: 1000 reads as fast as possible with 10 workers
  python scripts/load_test.py --tenant-id <UUID> -w 10 --total 1000

  # Sustained: 200 req/s for 60 seconds
  python scripts/load_test.py --tenant-id <UUID> -w 20 --rps 200 --duration 60

  # Ramp: start at 50 rps, add 50 every 10s, cap at 500
  python scripts/load_test.py --tenant-id <UUID> -w 30 --rps 50 --ramp 50 --ramp-step 10 --rps-max 500 --duration 120
""",
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant UUID")
    parser.add_argument("-w", "--workers", type=int, default=10,
                        help="Concurrent workers (default: 10)")
    parser.add_argument("--devices", type=int, default=20,
                        help="Number of load-test devices (default: 20)")
    parser.add_argument("--total", type=int, default=0,
                        help="Total requests to send (0 = use --duration)")
    parser.add_argument("--duration", type=int, default=30,
                        help="Test duration in seconds (default: 30)")
    parser.add_argument("--rps", type=int, default=0,
                        help="Target requests/sec (0 = max throughput)")
    parser.add_argument("--ramp", type=int, default=0,
                        help="Increase rps by this amount each --ramp-step")
    parser.add_argument("--ramp-step", type=int, default=10,
                        help="Seconds between ramp increases (default: 10)")
    parser.add_argument("--rps-max", type=int, default=0,
                        help="Max rps when ramping (default: 10x initial)")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("TAGPULSE_API_KEY"),
        help="Admin/editor API key (Bearer). Falls back to $TAGPULSE_API_KEY.",
    )
    args = parser.parse_args()

    global _API_KEY
    _API_KEY = args.api_key
    if not _API_KEY:
        print(
            "WARNING: no --api-key (or $TAGPULSE_API_KEY) provided — "
            "device creation/ingestion will fail with 403. "
            "See docs/quickstart.md → Step 5b for how to bootstrap one."
        )

    if args.total == 0 and args.duration == 0:
        parser.error("Specify --total or --duration")

    asyncio.run(run_load_test(args))


if __name__ == "__main__":
    main()
