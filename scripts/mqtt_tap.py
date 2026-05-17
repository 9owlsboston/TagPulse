"""scripts/mqtt_tap.py — read-only MQTT subscriber for in-VNet inspection.

Run via ``scripts/azd-job.sh <env> mqtt_tap.py -- [flags]`` so the
subscriber reaches the broker over the private VNet (the broker has no
public listener post-Sprint 23-B). Pairs with ``scripts/mqtt_canary.py``
(publisher) and ``scripts/azd-logs.sh`` (worker-log tail).

Behaviour
---------
1. Resolves broker host/port from the same env vars the worker uses
   (``MQTT_BROKER_HOST``, ``MQTT_BROKER_PORT``, ``MQTT_BROKER_USERNAME``,
   ``MQTT_BROKER_PASSWORD`` — wired by
   ``deploy/azure/bicep/modules/mqtt.bicep`` for the tools-job too).
2. Subscribes to one or more topic filters (default mirrors the worker:
   ``tenants/+/devices/+/+`` + ``tenants/+/subjects/+/+/telemetry``).
3. Prints one line per received message with the raw payload bytes
   (JSON pretty-printed when valid, escaped str otherwise).
4. Exits cleanly after ``--duration`` seconds OR ``--max-messages``,
   whichever comes first. Exits 0 on a normal stop, 2 on setup error.

Examples
--------
- Tap everything the worker sees, for 60s::

      scripts/azd-job.sh dev mqtt_tap.py

- Tap just one reader's tag-reads for 5 minutes::

      scripts/azd-job.sh dev mqtt_tap.py -- \\
        --device-id <reader-uuid> --duration 300

- Tap one tenant's full bus, stop after 100 messages::

      scripts/azd-job.sh dev mqtt_tap.py -- \\
        --tenant-id <tenant-uuid> --max-messages 100

This script does NOT write to the database or modify any state; it is
strictly a wire-level observer for triage. Use it to confirm what a
device is actually sending us when the parsed ``tag_reads`` rows look
wrong (or empty).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import UTC, datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mqtt-tap")

# Mirror the worker's filters from src/tagpulse/ingestion/mqtt_subscriber.py
# so the operator sees exactly what the subscriber loop sees by default.
DEFAULT_DEVICE_FILTER = "tenants/+/devices/+/+"
DEFAULT_SUBJECT_FILTER = "tenants/+/subjects/+/+/telemetry"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--topic",
        action="append",
        default=None,
        help=(
            "MQTT topic filter to subscribe to. May be repeated. "
            "Default: the two filters the worker uses "
            f"('{DEFAULT_DEVICE_FILTER}' + '{DEFAULT_SUBJECT_FILTER}')."
        ),
    )
    p.add_argument(
        "--tenant-id",
        default=None,
        help="Shortcut: narrow default filters to a single tenant UUID.",
    )
    p.add_argument(
        "--device-id",
        default=None,
        help=(
            "Shortcut: narrow default filters to a single device UUID. "
            "Subscribes to 'tenants/+/devices/{id}/+' (all sub-topics for that device). "
            "Combine with --tenant-id to fully scope."
        ),
    )
    p.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Wall-clock seconds to listen before exiting (default: 60).",
    )
    p.add_argument(
        "--max-messages",
        type=int,
        default=0,
        help="Stop after N messages (default: 0 = unlimited within --duration).",
    )
    p.add_argument(
        "--no-pretty",
        action="store_true",
        help="Skip JSON pretty-printing; print payloads as one line each.",
    )
    p.add_argument(
        "--show-bytes",
        action="store_true",
        help="Also print payload byte length and a hex dump of the first 64 bytes.",
    )
    return p.parse_args(argv)


def _resolve_filters(args: argparse.Namespace) -> list[str]:
    if args.topic:
        return list(args.topic)
    tenant_part = args.tenant_id if args.tenant_id else "+"
    device_part = args.device_id if args.device_id else "+"
    filters = [f"tenants/{tenant_part}/devices/{device_part}/+"]
    # Only include the subject filter when not narrowed by device-id
    # (subject topics live under .../subjects/..., not .../devices/...).
    if not args.device_id:
        subject_tenant = args.tenant_id if args.tenant_id else "+"
        filters.append(f"tenants/{subject_tenant}/subjects/+/+/telemetry")
    return filters


def _format_payload(raw: bytes, *, pretty: bool, show_bytes: bool) -> str:
    parts: list[str] = []
    if show_bytes:
        head = raw[:64].hex()
        parts.append(f"bytes={len(raw)} head_hex={head}")
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        parts.append(f"payload=<binary {len(raw)} bytes>")
        return " ".join(parts)
    if pretty:
        try:
            obj = json.loads(decoded)
            parts.append("payload=" + json.dumps(obj, indent=2, sort_keys=True))
            return " ".join(parts)
        except (json.JSONDecodeError, ValueError):
            pass
    parts.append("payload=" + decoded)
    return " ".join(parts)


async def _run_async(args: argparse.Namespace) -> int:
    host = os.environ.get("MQTT_BROKER_HOST")
    port = int(os.environ.get("MQTT_BROKER_PORT", "1883"))
    username = os.environ.get("MQTT_BROKER_USERNAME", "")
    password = os.environ.get("MQTT_BROKER_PASSWORD", "")
    if not host:
        log.error("MQTT_BROKER_HOST not set; run via scripts/azd-job.sh so the tools-job env loads")
        return 2

    import aiomqtt

    filters = _resolve_filters(args)
    log.info("connecting to mqtt %s:%d as %s", host, port, username or "(no user)")
    log.info("subscribing to %d filter(s): %s", len(filters), ", ".join(filters))
    log.info(
        "stopping after %ds or %s messages",
        args.duration,
        args.max_messages if args.max_messages > 0 else "unlimited",
    )

    received = 0
    deadline = asyncio.get_event_loop().time() + args.duration

    async with aiomqtt.Client(
        hostname=host,
        port=port,
        username=username or None,
        password=password or None,
    ) as client:
        for f in filters:
            await client.subscribe(f)

        async def _consume() -> None:
            nonlocal received
            async for message in client.messages:
                ts = datetime.now(UTC).isoformat(timespec="milliseconds")
                payload = _format_payload(
                    bytes(message.payload)
                    if isinstance(message.payload, bytes | bytearray | memoryview)
                    else str(message.payload).encode(),
                    pretty=not args.no_pretty,
                    show_bytes=args.show_bytes,
                )
                # One-line header followed by the payload block — easy to grep.
                print(f"{ts} topic={message.topic.value} qos={message.qos} retain={message.retain}")
                print(payload)
                print("---")
                sys.stdout.flush()
                received += 1
                if args.max_messages and received >= args.max_messages:
                    return

        try:
            remaining = max(0.0, deadline - asyncio.get_event_loop().time())
            await asyncio.wait_for(_consume(), timeout=remaining)
        except TimeoutError:
            pass

    log.info("done — received %d message(s)", received)
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
