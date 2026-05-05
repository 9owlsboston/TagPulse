#!/usr/bin/env python3
"""Device simulator — generates fake RFID tag reads for local testing.

Usage:
    python scripts/simulate_devices.py --tenant-id <UUID> --devices 5 --interval 2

Sends tag reads to the TagPulse API every N seconds from simulated RFID readers.
"""

import argparse
import math
import os
import random
import sys
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

API_URL = "http://localhost:8000"

# Optional bearer API key (admin/editor) — populated from --api-key or
# TAGPULSE_API_KEY env. Required for POST/PATCH endpoints since Sprint 12
# (a bare X-Tenant-ID header authenticates as viewer only).
_API_KEY: str | None = None


def _headers(tenant_id: str) -> dict[str, str]:
    h = {"X-Tenant-ID": tenant_id}
    if _API_KEY:
        h["Authorization"] = f"Bearer {_API_KEY}"
    return h


TAG_POOL = [f"TAG{i:04d}" for i in range(1, 51)]  # 50 unique tags

# Per-device round-robin cursor over TAG_POOL so each tag (= each bound
# asset) gets reads at a steady cadence. Re-keyed when --tags shrinks the
# pool.
_TAG_CURSOR: dict[str, int] = {}


def _next_tag(device_id: str) -> str:
    idx = _TAG_CURSOR.get(device_id, 0)
    tag = TAG_POOL[idx % len(TAG_POOL)]
    _TAG_CURSOR[device_id] = idx + 1
    return tag

# --with-gps: per-device random-walk state, anchored to a San Francisco city
# block (Bay Area). Two motion modes — see --motion in main():
#   * random  — wide-angle wander (forklift idling, pedestrian, original
#               smoke-test behaviour). Heading jitter ±15° per step.
#   * vehicle — mostly straight runs with occasional sharp turns
#               (delivery van / forklift route). Heading jitter ±3° per
#               step, plus a ~1-in-25 "turn at the corner" event.
# Both tether to a per-tag home bubble so 5 markers stay visually distinct.
_GPS_STATE: dict[str, dict[str, float]] = {}
_GPS_ANCHOR_LAT = 37.7749
_GPS_ANCHOR_LON = -122.4194
_GPS_BLOCK_RADIUS_DEG = 0.0015  # ~165 m N/S, ~130 m E/W at 37.77°
_GPS_HOME_RADIUS_DEG = 0.0004  # ~45 m wander around each device's home point
_GPS_STEP_DEG = 5.0e-5  # ~5 m per step (visible at zoom ~16)
_MOTION_MODE = "random"  # set from --motion in main()


def _gps_step(device_id: str) -> dict[str, float]:
    """Advance the device's random-walk position by one step.

    Each device is assigned a deterministic *home* point inside the block on
    first call (derived from a hash of ``device_id``) and tethers to a small
    radius around it. This keeps multiple simulated assets visually
    separated rather than collapsing into one cluster.
    """
    state = _GPS_STATE.get(device_id)
    if state is None:
        # Deterministic home offset from the device id so re-runs reproduce.
        h = hash(device_id)
        ang = (h % 360) * math.pi / 180.0
        # Push home points out toward the edge of the block on a circle so
        # they spread evenly around the anchor.
        home_offset = _GPS_BLOCK_RADIUS_DEG * 0.7
        home_lat = _GPS_ANCHOR_LAT + home_offset * math.cos(ang)
        home_lon = _GPS_ANCHOR_LON + home_offset * math.sin(ang)
        state = {
            "home_lat": home_lat,
            "home_lon": home_lon,
            "lat": home_lat,
            "lon": home_lon,
            "heading": random.uniform(0.0, 360.0),
        }
        _GPS_STATE[device_id] = state
    # Per-step heading jitter — vehicles barely deviate, pedestrians wander.
    if _MOTION_MODE == "vehicle":
        state["heading"] = (state["heading"] + random.uniform(-3.0, 3.0)) % 360.0
        # ~4% chance of a sharp turn at an "intersection".
        if random.random() < 0.04:
            state["heading"] = (
                state["heading"] + random.choice([-90.0, 90.0])
            ) % 360.0
    else:  # "random"
        state["heading"] = (state["heading"] + random.uniform(-15.0, 15.0)) % 360.0
    rad = math.radians(state["heading"])
    state["lat"] += _GPS_STEP_DEG * math.cos(rad)
    state["lon"] += _GPS_STEP_DEG * math.sin(rad)
    # Gently steer back toward home when wandering past the tether radius.
    # (Hard 180° reflection produced visible "jumps" in the marker; this
    # rotates the heading toward the home vector by up to 30° per step so
    # the trajectory curves smoothly back inside the bubble.)
    dlat = state["home_lat"] - state["lat"]
    dlon = state["home_lon"] - state["lon"]
    drift = math.hypot(dlat, dlon)
    if drift > _GPS_HOME_RADIUS_DEG:
        target_heading = math.degrees(math.atan2(dlon, dlat)) % 360.0
        diff = ((target_heading - state["heading"] + 540.0) % 360.0) - 180.0
        # Clamp the per-step turn so the motion stays smooth.
        turn = max(-30.0, min(30.0, diff))
        state["heading"] = (state["heading"] + turn) % 360.0
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
    headers = _headers(tenant_id)

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

    tag_id = _next_tag(device_id)
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
    # Key the walk by tag_id (not device_id) so each asset (bound 1:1 to a
    # tag) has its own home point on the Map — otherwise random tag→device
    # pairing causes asset positions to bounce across all device walks.
    if with_gps:
        body["location"] = _gps_step(tag_id)
    elif random.random() < 0.20:
        body["location"] = {
            "latitude": round(37.77 + random.uniform(-0.05, 0.05), 5),
            "longitude": round(-122.42 + random.uniform(-0.05, 0.05), 5),
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
        headers=_headers(tenant_id),
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
        headers=_headers(tenant_id),
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
    parser.add_argument(
        "--api-key",
        default=os.environ.get("TAGPULSE_API_KEY"),
        help="Admin/editor API key (Bearer). Required for device creation since Sprint 12. "
        "Falls back to $TAGPULSE_API_KEY.",
    )
    parser.add_argument(
        "--tags",
        type=int,
        default=50,
        help="Constrain the tag pool to TAG0001..TAG{N:04d} (default: 50). "
        "Set this to the number of bound assets so every read maps to a marker.",
    )
    parser.add_argument(
        "--motion",
        choices=("random", "vehicle"),
        default="random",
        help=(
            "GPS walk profile (with --with-gps). 'random' = wide-angle wander "
            "(forklift idling, pedestrian); 'vehicle' = mostly-straight runs "
            "with occasional 90° turns (delivery van / route)."
        ),
    )
    args = parser.parse_args()

    global _API_KEY, TAG_POOL, _MOTION_MODE
    _API_KEY = args.api_key
    _MOTION_MODE = args.motion
    if args.tags > 0 and args.tags != len(TAG_POOL):
        TAG_POOL = [f"TAG{i:04d}" for i in range(1, args.tags + 1)]
    if not _API_KEY:
        print(
            "WARNING: no --api-key (or $TAGPULSE_API_KEY) provided — "
            "device creation/ingestion will fail with 403 since Sprint 12. "
            "See docs/quickstart.md → Step 5b for how to bootstrap one."
        )

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
