#!/usr/bin/env python3
"""
Minimal paho-mqtt v5 publisher for TagPulse smoke testing.

Loads defaults (broker host/port/user, tenant id, device id) and the broker
password from a sidecar env file. Search order:

    1. --env-file PATH (CLI)
    2. $TP_PAHO_EDGE_ENV (env var)
    3. ./.tp_paho_edge.env  (next to this script)
    4. ~/.tp_paho_edge.env

A starter file lives at `.tp_paho_edge.env.example`. Copy it to
`.tp_paho_edge.env`, fill in `BROKER_PASS=…`, and never commit the real one.

Usage:
    pip install 'paho-mqtt>=2.0'

    # one-shot single tag-read publish
    python3 paho_smoke_publisher.py --once

    # rich payload with location, sensors, EPC, free-form metadata
    python3 paho_smoke_publisher.py --once \\
      --lat 42.36 --lon -71.06 --accuracy 5 \\
      --temp-c 21.4 --humidity 47 --battery 88 \\
      --sensor pressure_kpa=101.3 \\
      --epc-hex 30340789AB1234567890ABCD --epc-scheme sgtin-96 \\
      --tag-data lot=L-2026-05-09 --tag-data sku=SKU-9988

    # standalone GPS update on the device's location topic
    python3 paho_smoke_publisher.py --once --topic location \\
      --lat 42.36 --lon -71.06 --accuracy 6

    # device-side event
    python3 paho_smoke_publisher.py --once --topic events \\
      --event-type heartbeat --detail uptime_s=1234

    # simulate device movement along a pre-recorded track
    #   track file: CSV with header `lat,lon[,accuracy,dwell_s]` (header optional)
    python3 paho_smoke_publisher.py --topic location \\
      --track tracks/boston-loop.csv --track-loop

    # smooth 1 Hz GPS by interpolating between sparse waypoints
    python3 paho_smoke_publisher.py --topic location \\
      --track tracks/boston-loop.csv --track-interp 1

    # v2 wire format (Sprint 46+, ADR-025) — exercise the presence dispatch:
    #   appeared (t=1, writes one tag_reads row per spec §4.4)
    python3 paho_smoke_publisher.py --once --wire v2 \\
      --epc-hex E280112233445566778899AA --sn 42
    #   snap (t=0, writes one tag_reads row per epcs[] entry)
    python3 paho_smoke_publisher.py --once --wire v2 --v2-type snap \\
      --epc-hex E280112233445566778899AA --sn 42 --lat 42.36 --lon -71.06
    #   disappeared (t=2, no tag_reads row; reconciler marks gone)
    python3 paho_smoke_publisher.py --once --wire v2 --v2-type disappeared \\
      --epc-hex E280112233445566778899AA --sn 42

Wire-format reference: docs/design/edge-device-contract.md (v1) and
docs/design/edge-wire-format-v2.md (v2).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import signal
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion
from paho.mqtt.reasoncodes import ReasonCode

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILENAME = ".tp_paho_edge.env"

# Defaults applied when neither the env file nor a CLI flag sets a value.
# Keep these as last-ditch fallbacks; real config belongs in the env file.
HARDCODED_FALLBACKS = {
    "BROKER_HOST": "localhost",
    "BROKER_PORT": "1883",
    "BROKER_USER": "tagpulse",
    "TENANT_ID": "",
    "DEVICE_ID": "",
}


# --------------------------------------------------------------------------- #
# Env-file loader
# --------------------------------------------------------------------------- #


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            out[k] = v
    return out


def _resolve_env_file(explicit: str | None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env_var = os.environ.get("TP_PAHO_EDGE_ENV")
    if env_var:
        candidates.append(Path(env_var).expanduser())
    candidates.append(SCRIPT_DIR / ENV_FILENAME)
    candidates.append(Path.home() / ENV_FILENAME)
    for p in candidates:
        if p.is_file():
            return p
    return None


def load_env(explicit: str | None) -> tuple[dict[str, str], Path | None]:
    """Merge precedence (highest first): real env vars > env file > hardcoded."""
    path = _resolve_env_file(explicit)
    file_vals = _parse_env_file(path) if path else {}
    merged = {**HARDCODED_FALLBACKS, **file_vals}
    # Real env vars win over file values (so CI / shell exports still work).
    for k in list(merged.keys()):
        if k in os.environ and os.environ[k] != "":
            merged[k] = os.environ[k]
    return merged, path


# --------------------------------------------------------------------------- #
# Payload builders (one per supported MQTT sub-topic)
# --------------------------------------------------------------------------- #


def _normalize_v2_epc(value: str | None) -> str:
    """Coerce ``value`` into a valid v2 EPC (uppercase hex, even, 8..124 chars).

    Used by the ``--wire v2`` builders below. The v2 subscriber rejects
    invalid EPCs with the ``invalid_epc`` reason per spec §6, so the
    smoke publisher mirrors :mod:`tagpulse.ingestion.wm_wire_format`
    validation here to fail loudly client-side instead of producing a
    silently-DLQ-ed payload.
    """
    epc = (value or f"E280{uuid.uuid4().hex[:20].upper()}").strip().upper()
    if len(epc) < 8 or len(epc) > 124 or len(epc) % 2 != 0:
        sys.exit(f"--epc-hex {epc!r} must be uppercase hex, 8..124 chars, even length")
    try:
        int(epc, 16)
    except ValueError:
        sys.exit(f"--epc-hex {epc!r} must be hexadecimal")
    return epc


def make_v2_tag_read_payload(args: argparse.Namespace) -> dict:
    """`tag-reads` topic in **v2 wire format** (Sprint 46 / ADR-025).

    Emits one ``t=0`` snap, ``t=1`` appeared, or ``t=2`` disappeared
    message per call, matching the discriminated union enforced by the
    backend subscriber (:mod:`tagpulse.ingestion.wm_wire_format`).
    Useful for exercising the v2 dispatch branch and the presence
    reconciler from outside the conformance harness.

    Schema reference: docs/design/edge-wire-format-v2.md §2.2.
    """
    if args.topic != "tag-reads":
        sys.exit("--wire v2 only applies to --topic tag-reads")
    epc = _normalize_v2_epc(args.epc_hex)
    ts_ms = int(time.time() * 1000)
    if args.v2_type == "disappeared":
        # Spec §2.2: t=2 carries epc only; lat/lon/an MAY be omitted.
        return {"t": 2, "sn": args.sn, "ts": ts_ms, "epc": epc}
    rssi = int(args.rssi) if args.rssi is not None else -55
    entry: dict = {
        "an": args.antenna,
        "epc": epc,
        "rssi": rssi,
        "cnt": 1,
    }
    # Sensor key omission mirrors the spec §6 explicit_null rejection rule.
    if args.temp_c is not None:
        entry["tmp"] = args.temp_c
    if args.humidity is not None:
        entry["hum"] = args.humidity
    if args.v2_type == "snap":
        return {
            "t": 0,
            "sn": args.sn,
            "ts": ts_ms,
            "lat": args.lat,
            "lon": args.lon,
            "epcs": [entry],
        }
    # v2-type appeared (default)
    appeared = {
        "t": 1,
        "sn": args.sn,
        "ts": ts_ms,
        "lat": args.lat,
        "lon": args.lon,
        **entry,
    }
    return appeared


def make_tag_read_payload(args: argparse.Namespace) -> dict:
    """`tag-reads` topic: single object, NO device_id (worker derives from topic).

    Schema: tagpulse.models.schemas.TagReadCreate
      tag_id, timestamp, signal_strength, reader_antenna,
      sensor_data (free-form dict), tag_data (free-form dict),
      location {latitude, longitude, accuracy_m?, source?},
      identity {epc, epc_hex, epc_scheme, epc_decoded, tid, user_memory_hex}
    """
    payload: dict = {
        "tag_id": args.tag_id or f"E280PAHO{int(time.time())}",
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "signal_strength": args.rssi
        if args.rssi is not None
        else round(random.uniform(-75, -45), 1),  # noqa: S311 — simulator noise, not crypto
        "reader_antenna": args.antenna,
    }

    # Optional structured location (GPS / wifi / cell)
    if args.lat is not None and args.lon is not None:
        loc: dict = {"latitude": args.lat, "longitude": args.lon, "source": args.loc_source}
        if args.accuracy is not None:
            loc["accuracy_m"] = args.accuracy
        payload["location"] = loc

    # Free-form sensor readings (temperature, humidity, battery, etc.)
    sensor: dict = {}
    if args.temp_c is not None:
        sensor["temperature_c"] = args.temp_c
    if args.humidity is not None:
        sensor["humidity_pct"] = args.humidity
    if args.battery is not None:
        sensor["battery_pct"] = args.battery
    for kv in args.sensor or []:
        k, _, v = kv.partition("=")
        sensor[k] = _coerce(v)
    if sensor:
        payload["sensor_data"] = sensor

    # Optional RFID identity sub-payload
    identity: dict = {}
    if args.epc:
        identity["epc"] = args.epc
    if args.epc_hex:
        identity["epc_hex"] = args.epc_hex
    if args.epc_scheme:
        identity["epc_scheme"] = args.epc_scheme
    if args.tid:
        identity["tid"] = args.tid
    if identity:
        payload["identity"] = identity

    # Free-form per-tag metadata
    tag_data: dict = {}
    for kv in args.tag_data or []:
        k, _, v = kv.partition("=")
        tag_data[k] = _coerce(v)
    if tag_data:
        payload["tag_data"] = tag_data

    return payload


def make_location_payload(args: argparse.Namespace) -> dict:
    """`location` topic: LocationPayload — device-level GPS update."""
    if args.lat is None or args.lon is None:
        sys.exit("--topic location requires --lat and --lon")
    body: dict = {
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latitude": args.lat,
        "longitude": args.lon,
        "source": args.loc_source,
    }
    if args.accuracy is not None:
        body["accuracy_m"] = args.accuracy
    return body


def make_event_payload(args: argparse.Namespace) -> dict:
    """`events` topic: DeviceEventPayload — free-form device-side event."""
    if not args.event_type:
        sys.exit("--topic events requires --event-type")
    details: dict = {}
    for kv in args.detail or []:
        k, _, v = kv.partition("=")
        details[k] = _coerce(v)
    return {
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event_type": args.event_type,
        "details": details or None,
    }


def _coerce(v: str):
    """Best-effort scalar coercion for k=v CLI flags (int, float, bool, str)."""
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


# --------------------------------------------------------------------------- #
# Track / movement simulation
# --------------------------------------------------------------------------- #


@dataclass
class Waypoint:
    lat: float
    lon: float
    accuracy: float | None = None
    dwell_s: float | None = None  # seconds to wait after publishing this point


def load_track(path: Path) -> list[Waypoint]:
    """Load a CSV track file.

    Columns: lat,lon[,accuracy,dwell_s]. A header row is optional; if the first
    row's first cell can't be parsed as float, it's treated as a header.
    """
    rows: list[Waypoint] = []
    with path.open() as f:
        reader = csv.reader(f)
        for _i, raw in enumerate(reader):
            if not raw or all(not c.strip() for c in raw):
                continue
            if raw[0].lstrip().startswith("#"):
                continue
            try:
                lat = float(raw[0])
            except ValueError:
                if not rows:
                    continue  # header row (possibly preceded by comments)
                raise
            lon = float(raw[1])
            accuracy = float(raw[2]) if len(raw) > 2 and raw[2].strip() else None
            dwell = float(raw[3]) if len(raw) > 3 and raw[3].strip() else None
            rows.append(Waypoint(lat=lat, lon=lon, accuracy=accuracy, dwell_s=dwell))
    if not rows:
        sys.exit(f"track file {path} contained no waypoints")
    return rows


def interpolate_track(track: list[Waypoint], hz: float) -> list[Waypoint]:
    """Linearly interpolate intermediate points between waypoints at `hz` per second.

    Per-segment duration is taken from the *origin* waypoint's `dwell_s`
    (default 1.0s). Output waypoints inherit accuracy from the origin and have
    `dwell_s = 1/hz`. Linear lat/lon is fine for smoke-test distances.
    """
    if hz <= 0:
        sys.exit("--track-interp must be > 0")
    if len(track) < 2:
        return list(track)
    step = 1.0 / hz
    out: list[Waypoint] = []
    for a, b in zip(track, track[1:], strict=False):
        seg_s = a.dwell_s if a.dwell_s and a.dwell_s > 0 else 1.0
        n = max(1, int(round(seg_s * hz)))
        for k in range(n):
            t = k / n
            out.append(
                Waypoint(
                    lat=a.lat + (b.lat - a.lat) * t,
                    lon=a.lon + (b.lon - a.lon) * t,
                    accuracy=a.accuracy,
                    dwell_s=step,
                )
            )
    out.append(
        Waypoint(lat=track[-1].lat, lon=track[-1].lon, accuracy=track[-1].accuracy, dwell_s=step)
    )
    return out


# --------------------------------------------------------------------------- #
# Paho callbacks
# --------------------------------------------------------------------------- #


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0 or (isinstance(reason_code, ReasonCode) and reason_code.value == 0):
        print(f"[connect] CONNACK ok (flags={flags})", flush=True)
    else:
        print(f"[connect] FAILED reason={reason_code}", flush=True)


def on_disconnect(client, userdata, flags, reason_code, properties=None):
    print(f"[disconnect] reason={reason_code}", flush=True)


def on_publish(client, userdata, mid, reason_code=None, properties=None):
    print(f"[publish] PUBACK mid={mid} reason={reason_code}", flush=True)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> int:
    ap = argparse.ArgumentParser(
        description="paho-mqtt v5 publisher for TagPulse smoke testing",
    )
    ap.add_argument(
        "--env-file", default=None, help=f"path to env file (default: search for {ENV_FILENAME})"
    )
    # Connection
    ap.add_argument("--host", default=None, help="override BROKER_HOST")
    ap.add_argument("--port", type=int, default=None, help="override BROKER_PORT")
    ap.add_argument("--user", default=None, help="override BROKER_USER")
    ap.add_argument("--password", default=None, help="override BROKER_PASS")
    ap.add_argument("--tenant", default=None, help="override TENANT_ID")
    ap.add_argument("--device", default=None, help="override DEVICE_ID")
    # TLS (Sprint 28 C6). Auto-enabled when --port==8883 or TLS_CA is set.
    ap.add_argument(
        "--tls-ca",
        default=None,
        help="path to CA pem for TLS broker (overrides TLS_CA env)",
    )
    ap.add_argument(
        "--insecure",
        action="store_true",
        help="skip TLS server-cert verification (debug only)",
    )
    # Topic + tag-read core
    ap.add_argument(
        "--topic",
        default="tag-reads",
        choices=["tag-reads", "location", "events"],
        help="which device sub-topic to publish on",
    )
    ap.add_argument("--tag-id", default=None, help="fixed tag id (default: E280PAHO<epoch>)")
    ap.add_argument("--rssi", type=float, default=None)
    ap.add_argument("--antenna", type=int, default=1)
    # Location (used by tag-reads.location sub-object AND by --topic location)
    ap.add_argument("--lat", type=float, default=None)
    ap.add_argument("--lon", type=float, default=None)
    ap.add_argument("--accuracy", type=float, default=None, help="location accuracy meters")
    ap.add_argument(
        "--loc-source", default="gps", choices=["gps", "wifi", "cell", "manual", "static"]
    )
    # Sensor data shortcuts + free-form
    ap.add_argument(
        "--temp-c", type=float, default=None, help="temperature in C -> sensor_data.temperature_c"
    )
    ap.add_argument(
        "--humidity", type=float, default=None, help="%% RH -> sensor_data.humidity_pct"
    )
    ap.add_argument("--battery", type=float, default=None, help="%% -> sensor_data.battery_pct")
    ap.add_argument(
        "--sensor",
        action="append",
        metavar="KEY=VAL",
        help="extra sensor_data entry, repeatable (e.g. --sensor pressure_kpa=101.3)",
    )
    # Identity (RFID EPC/TID)
    ap.add_argument("--epc", default=None)
    ap.add_argument("--epc-hex", default=None)
    ap.add_argument("--epc-scheme", default=None, help="e.g. sgtin-96")
    ap.add_argument("--tid", default=None)
    # Free-form tag_data
    ap.add_argument(
        "--tag-data", action="append", metavar="KEY=VAL", help="extra tag_data entry, repeatable"
    )
    # Events topic
    ap.add_argument("--event-type", default=None, help="required when --topic events")
    ap.add_argument(
        "--detail", action="append", metavar="KEY=VAL", help="events.details entry, repeatable"
    )
    # v2 wire format (Sprint 46+, ADR-025; --topic tag-reads only)
    ap.add_argument(
        "--wire",
        default="v1",
        choices=["v1", "v2"],
        help="v1 = legacy TagReadCreate shape (default); v2 = WM presence wire format",
    )
    ap.add_argument(
        "--v2-type",
        default="appeared",
        choices=["snap", "appeared", "disappeared"],
        help="which v2 message to emit (t=0/1/2); only used when --wire v2",
    )
    ap.add_argument(
        "--sn",
        type=int,
        default=1,
        help="v2 wire envelope sn (device serial, int); --wire v2 only",
    )
    # Loop
    ap.add_argument("--qos", type=int, default=1, choices=[0, 1, 2])
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between publishes")
    ap.add_argument("--count", type=int, default=0, help="0 = run until SIGINT")
    ap.add_argument("--once", action="store_true", help="publish exactly one message and exit")
    # Track-based movement simulation
    ap.add_argument(
        "--track",
        default=None,
        type=Path,
        help="CSV waypoint file (lat,lon[,accuracy,dwell_s]); overrides --interval/--count",
    )
    ap.add_argument("--track-loop", action="store_true", help="repeat the track until SIGINT")
    ap.add_argument(
        "--track-interp",
        type=float,
        default=0.0,
        metavar="HZ",
        help="linearly interpolate intermediate points at HZ samples/sec between waypoints",
    )
    args = ap.parse_args()

    if args.once:
        args.count = 1

    env, env_path = load_env(args.env_file)
    if env_path:
        print(f"[env] loaded {env_path}", flush=True)
    else:
        print(
            "[env] no env file found; relying on CLI flags + env vars + hardcoded fallbacks",
            flush=True,
        )

    host = args.host or env.get("BROKER_HOST") or HARDCODED_FALLBACKS["BROKER_HOST"]
    port = (
        args.port
        if args.port is not None
        else int(env.get("BROKER_PORT") or HARDCODED_FALLBACKS["BROKER_PORT"])
    )
    user = args.user or env.get("BROKER_USER") or HARDCODED_FALLBACKS["BROKER_USER"]
    password = args.password or env.get("BROKER_PASS")
    tenant = args.tenant or env.get("TENANT_ID")
    device = args.device or env.get("DEVICE_ID")

    missing = [
        name
        for name, val in (("BROKER_PASS", password), ("TENANT_ID", tenant), ("DEVICE_ID", device))
        if not val
    ]
    if missing:
        sys.exit(
            f"missing required config: {', '.join(missing)} (set in env file, env var, or CLI flag)"
        )

    topic = f"tenants/{tenant}/devices/{device}/{args.topic}"
    client_id = f"tp-paho-edge-{uuid.uuid4().hex[:8]}"

    builders: dict[str, Callable[[argparse.Namespace], dict]] = {
        "tag-reads": make_v2_tag_read_payload if args.wire == "v2" else make_tag_read_payload,
        "location": make_location_payload,
        "events": make_event_payload,
    }
    builder = builders[args.topic]

    if args.wire == "v2" and args.topic != "tag-reads":
        sys.exit("--wire v2 requires --topic tag-reads")

    track: list[Waypoint] | None = None
    if args.track is not None:
        if args.topic == "events":
            sys.exit("--track is only meaningful with --topic location or tag-reads")
        track = load_track(args.track)
        if args.track_interp > 0:
            track = interpolate_track(track, args.track_interp)
        print(
            f"[track] loaded {args.track} "
            f"({len(track)} points, interp={args.track_interp or 'off'})",
            flush=True,
        )

    client = mqtt.Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=client_id,
        protocol=mqtt.MQTTv5,
    )
    client.username_pw_set(user, password)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_publish = on_publish
    client.enable_logger()  # routes paho internals to stdlib logging at WARNING

    # MQTT v5 last-will (informational; broker has no LWT consumers wired up)
    client.will_set(
        f"tenants/{tenant}/devices/{device}/status",
        payload=json.dumps({"connection_state": "offline", "reason": "lwt"}).encode(),
        qos=1,
        retain=True,
    )

    tls_ca = args.tls_ca or env.get("TLS_CA")
    use_tls = bool(tls_ca) or port == 8883 or args.insecure
    if use_tls:
        import ssl

        if tls_ca:
            client.tls_set(ca_certs=tls_ca, cert_reqs=ssl.CERT_REQUIRED)
        else:
            client.tls_set(cert_reqs=ssl.CERT_NONE if args.insecure else ssl.CERT_REQUIRED)
        if args.insecure:
            client.tls_insecure_set(True)
        print(f"[tls] enabled ca={tls_ca or '(system)'} insecure={args.insecure}", flush=True)

    print(
        f"[main] client_id={client_id} broker={host}:{port} topic={topic} qos={args.qos}",
        flush=True,
    )
    client.connect(host, port, keepalive=60, clean_start=True)
    client.loop_start()

    stop = {"flag": False}

    def _sig(_signum, _frame):
        stop["flag"] = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    def _publish_once(wait_s: float) -> None:
        nonlocal sent
        payload = builder(args)
        body = json.dumps(payload).encode()
        info = client.publish(topic, body, qos=args.qos)
        print(f"[send #{sent + 1}] {json.dumps(payload)}", flush=True)
        if args.qos > 0:
            info.wait_for_publish(timeout=10)
        sent += 1
        slept = 0.0
        while slept < wait_s and not stop["flag"]:
            time.sleep(min(0.2, wait_s - slept))
            slept += 0.2

    sent = 0
    try:
        if track is not None:
            default_dwell = args.interval
            while not stop["flag"]:
                for wp in track:
                    if stop["flag"]:
                        break
                    args.lat = wp.lat
                    args.lon = wp.lon
                    if wp.accuracy is not None:
                        args.accuracy = wp.accuracy
                    wait_s = wp.dwell_s if wp.dwell_s is not None else default_dwell
                    _publish_once(wait_s)
                if not args.track_loop:
                    break
        else:
            while not stop["flag"]:
                _publish_once(args.interval)
                if args.count and sent >= args.count:
                    break
    finally:
        print(f"[main] sent={sent}, disconnecting", flush=True)
        client.loop_stop()
        client.disconnect()

    return 0


if __name__ == "__main__":
    sys.exit(main())
