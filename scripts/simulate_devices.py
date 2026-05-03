#!/usr/bin/env python3
"""Device simulator — generates fake RFID tag reads for local testing.

Usage:
    python scripts/simulate_devices.py --tenant-id <UUID> --devices 5 --interval 2

Sends tag reads to the TagPulse API every N seconds from simulated RFID readers.
"""

import argparse
import math
import random
import sys
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

API_URL = "http://localhost:8000"

TAG_POOL = [f"TAG{i:04d}" for i in range(1, 51)]  # 50 unique tags

# --with-gps: per-device random-walk state, anchored to a Seattle city block.
# Each device walks ~1 m per step (~1e-5 deg lat) and is steered to deliberately
# cross a small geofence polygon every few minutes — useful for exercising the
# Sprint 17a geofence eval path locally.
_GPS_STATE: dict[str, dict[str, float]] = {}
_GPS_ANCHOR_LAT = 47.6062
_GPS_ANCHOR_LON = -122.3321
_GPS_BLOCK_RADIUS_DEG = 0.0010  # ~110 m N/S, ~75 m E/W at 47.6°


def _gps_step(device_id: str) -> dict[str, float]:
    """Advance the device's random-walk position by one step."""
    state = _GPS_STATE.get(device_id)
    if state is None:
        state = {
            "lat": _GPS_ANCHOR_LAT + random.uniform(-_GPS_BLOCK_RADIUS_DEG, _GPS_BLOCK_RADIUS_DEG),
            "lon": _GPS_ANCHOR_LON + random.uniform(-_GPS_BLOCK_RADIUS_DEG, _GPS_BLOCK_RADIUS_DEG),
            "heading": random.uniform(0.0, 360.0),
        }
        _GPS_STATE[device_id] = state
    # Drift the heading slightly each step.
    state["heading"] = (state["heading"] + random.uniform(-15.0, 15.0)) % 360.0
    rad = math.radians(state["heading"])
    step = 1.5e-5  # ~1.5 m
    state["lat"] += step * math.cos(rad)
    state["lon"] += step * math.sin(rad)
    # Tether to anchor block (reflect off the edges).
    if abs(state["lat"] - _GPS_ANCHOR_LAT) > _GPS_BLOCK_RADIUS_DEG:
        state["heading"] = (state["heading"] + 180.0) % 360.0
    if abs(state["lon"] - _GPS_ANCHOR_LON) > _GPS_BLOCK_RADIUS_DEG:
        state["heading"] = (state["heading"] + 180.0) % 360.0
    return {
        "latitude": round(state["lat"], 6),
        "longitude": round(state["lon"], 6),
        "accuracy_m": round(random.uniform(2.0, 6.0), 1),
        "source": "gps",
    }


def create_devices(client: httpx.Client, tenant_id: str, count: int) -> list[dict[str, str]]:
    """Register simulated devices and return their IDs.

    Reuses existing devices with matching names to avoid duplicates on re-run.
    """
    headers = {"X-Tenant-ID": tenant_id}

    # Fetch existing devices
    existing: dict[str, str] = {}
    resp = client.get(f"{API_URL}/device-registry", headers=headers, params={"limit": 1000})
    if resp.status_code == 200:
        for d in resp.json():
            existing[d["name"]] = d["id"]

    devices = []
    for i in range(count):
        name = f"Sim-Reader-{i + 1:02d}"
        if name in existing:
            devices.append({"id": existing[name], "name": name})
            print(f"  Reusing device: {name} ({existing[name]})")
            continue
        resp = client.post(
            f"{API_URL}/device-registry",
            headers=headers,
            json={
                "name": name,
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
    *,
    with_gps: bool = False,
) -> bool:
    """Send a single simulated tag read."""
    # 5% chance of a read "failing" (device glitch)
    if random.random() < 0.05:
        return False

    tag_id = random.choice(TAG_POOL)
    signal = round(random.uniform(-80.0, -20.0), 1)
    sensor_data: dict[str, float] = {
        "temperature": round(random.uniform(18.0, 28.0), 1),
    }
    # 30% chance of humidity sensor data
    if random.random() < 0.3:
        sensor_data["humidity"] = round(random.uniform(30.0, 80.0), 1)
    # 10% chance of battery level
    if random.random() < 0.1:
        sensor_data["battery_pct"] = round(random.uniform(10.0, 100.0), 0)

    body: dict[str, object] = {
        "device_id": device_id,
        "tag_id": tag_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "signal_strength": signal,
        "sensor_data": sensor_data,
    }
    # 20% chance to attach a GPS location (mobile-reader profile), or always
    # when --with-gps is enabled (random-walk anchored block, exercises geofence eval).
    if with_gps:
        body["location"] = _gps_step(device_id)
    elif random.random() < 0.20:
        body["location"] = {
            "latitude": round(47.60 + random.uniform(-0.05, 0.05), 5),
            "longitude": round(-122.33 + random.uniform(-0.05, 0.05), 5),
            "accuracy_m": round(random.uniform(2.0, 12.0), 1),
            "source": "gps",
        }
    # 25% chance to be a sensor-tag (temperature embedded in tag_data)
    if random.random() < 0.25:
        body["tag_data"] = {
            "temperature_c": round(random.uniform(2.0, 8.0), 2),  # cold-chain band
        }
        body["identity"] = {
            # Synthetic SGTIN-96 hex; decoder will produce ("raw", {}) for most
            # but exercises the path. Real tags would have valid encodings.
            "epc_hex": f"3034{random.randrange(0, 2**80):020x}",
        }

    resp = client.post(
        f"{API_URL}/tag-reads",
        headers={"X-Tenant-ID": tenant_id},
        json=body,
    )
    return resp.status_code == 201


def send_telemetry(
    client: httpx.Client,
    tenant_id: str,
    device_id: str,
) -> bool:
    """Send a small batch of standalone telemetry readings."""
    readings = [
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "metric_name": "temperature",
            "metric_value": round(random.uniform(18.0, 28.0), 1),
            "unit": "C",
        }
    ]
    if random.random() < 0.5:
        readings.append({
            "timestamp": datetime.now(UTC).isoformat(),
            "metric_name": "battery_pct",
            "metric_value": round(random.uniform(10.0, 100.0), 0),
            "unit": "pct",
        })
    resp = client.post(
        f"{API_URL}/telemetry",
        headers={"X-Tenant-ID": tenant_id},
        json={"device_id": device_id, "readings": readings},
    )
    return resp.status_code == 201


def main() -> None:
    parser = argparse.ArgumentParser(description="TagPulse device simulator")
    parser.add_argument("--tenant-id", required=True, help="Tenant UUID")
    parser.add_argument("--devices", type=int, default=3, help="Number of simulated devices")
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between reads per device")
    parser.add_argument("--duration", type=int, default=0, help="Run for N seconds (0 = forever)")
    parser.add_argument("--seed-only", action="store_true", help="Create devices and exit")
    parser.add_argument(
        "--with-gps",
        action="store_true",
        help="Always attach a GPS location (random-walk per device) to exercise geofence eval",
    )
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
    dropped = 0
    try:
        while True:
            for device in devices:
                # 10% chance a device skips this cycle (simulates busy/offline)
                if random.random() < 0.10:
                    continue
                ok = send_tag_read(
                    client, args.tenant_id, device["id"], with_gps=args.with_gps
                )
                if ok:
                    total_reads += 1
                else:
                    dropped += 1
                # Roughly every 5th cycle, also send standalone telemetry.
                if random.random() < 0.20:
                    send_telemetry(client, args.tenant_id, device["id"])
                status = "✓" if ok else "✗"
                print(
                    f"  {status} {device['name']} → {total_reads} sent, {dropped} dropped",
                    end="\r",
                )
            # Jitter: ±30% of the base interval
            jitter = args.interval * random.uniform(0.7, 1.3)
            time.sleep(jitter)
            if args.duration > 0 and (time.monotonic() - start) > args.duration:
                break
    except KeyboardInterrupt:
        pass

    elapsed = time.monotonic() - start
    print(f"\n\nDone: {total_reads} sent, {dropped} dropped in {elapsed:.0f}s ({total_reads / max(elapsed, 1):.1f} reads/sec)")


if __name__ == "__main__":
    main()
