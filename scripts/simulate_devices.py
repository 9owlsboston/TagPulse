#!/usr/bin/env python3
"""Device simulator — generates fake RFID tag reads for local testing.

Usage:
    python scripts/simulate_devices.py --tenant-id <UUID> --devices 5 --interval 2

Sends tag reads to the TagPulse API every N seconds from simulated RFID readers.
"""

import argparse
import random
import sys
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

API_URL = "http://localhost:8000"

TAG_POOL = [f"TAG{i:04d}" for i in range(1, 51)]  # 50 unique tags


def create_devices(client: httpx.Client, tenant_id: str, count: int) -> list[dict[str, str]]:
    """Register simulated devices and return their IDs."""
    devices = []
    for i in range(count):
        resp = client.post(
            f"{API_URL}/device-registry",
            headers={"X-Tenant-ID": tenant_id},
            json={
                "name": f"Sim-Reader-{i + 1:02d}",
                "device_type": "rfid_reader",
                "metadata": {"location": f"zone-{chr(65 + i % 6)}", "simulated": True},
            },
        )
        if resp.status_code == 201:
            device = resp.json()
            devices.append({"id": device["id"], "name": device["name"]})
            print(f"  Created device: {device['name']} ({device['id']})")
        else:
            print(f"  Failed to create device {i + 1}: {resp.status_code} {resp.text}")
    return devices


def send_tag_read(
    client: httpx.Client,
    tenant_id: str,
    device_id: str,
) -> bool:
    """Send a single simulated tag read."""
    tag_id = random.choice(TAG_POOL)
    signal = round(random.uniform(-80.0, -20.0), 1)
    resp = client.post(
        f"{API_URL}/tag-reads",
        headers={"X-Tenant-ID": tenant_id},
        json={
            "device_id": device_id,
            "tag_id": tag_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "signal_strength": signal,
            "sensor_data": {"temperature": round(random.uniform(18.0, 28.0), 1)},
        },
    )
    return resp.status_code == 201


def main() -> None:
    parser = argparse.ArgumentParser(description="TagPulse device simulator")
    parser.add_argument("--tenant-id", required=True, help="Tenant UUID")
    parser.add_argument("--devices", type=int, default=3, help="Number of simulated devices")
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between reads per device")
    parser.add_argument("--duration", type=int, default=0, help="Run for N seconds (0 = forever)")
    parser.add_argument("--seed-only", action="store_true", help="Create devices and exit")
    args = parser.parse_args()

    client = httpx.Client(timeout=10.0)

    # Verify API is running
    try:
        resp = client.get(f"{API_URL}/health")
        if resp.status_code != 200:
            print(f"API not healthy: {resp.status_code}")
            sys.exit(1)
    except httpx.ConnectError:
        print(f"Cannot connect to API at {API_URL}. Is the backend running?")
        sys.exit(1)

    print(f"\n=== TagPulse Device Simulator ===")
    print(f"Tenant: {args.tenant_id}")
    print(f"Devices: {args.devices}")
    print(f"Interval: {args.interval}s\n")

    # Create devices
    print("Creating simulated devices...")
    devices = create_devices(client, args.tenant_id, args.devices)
    if not devices:
        print("No devices created. Check tenant ID and API.")
        sys.exit(1)
    print(f"\n{len(devices)} devices ready.\n")

    if args.seed_only:
        print("Seed-only mode. Exiting.")
        return

    # Generate tag reads
    print("Sending tag reads (Ctrl+C to stop)...\n")
    start = time.monotonic()
    total_reads = 0
    try:
        while True:
            for device in devices:
                ok = send_tag_read(client, args.tenant_id, device["id"])
                total_reads += 1
                status = "✓" if ok else "✗"
                print(
                    f"  {status} {device['name']} → {total_reads} reads sent",
                    end="\r",
                )
            time.sleep(args.interval)
            if args.duration > 0 and (time.monotonic() - start) > args.duration:
                break
    except KeyboardInterrupt:
        pass

    elapsed = time.monotonic() - start
    print(f"\n\nDone: {total_reads} reads in {elapsed:.0f}s ({total_reads / max(elapsed, 1):.1f} reads/sec)")


if __name__ == "__main__":
    main()
