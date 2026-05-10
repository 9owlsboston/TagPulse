"""scripts/mqtt_canary.py — Sprint 28 C2 in-VNet MQTT canary.

Run via ``scripts/azd-job.sh <env> mqtt_canary.py`` so the publisher
reaches the broker over the private VNet (the broker has no public
listener post-Sprint 23-B).

Behaviour:
1. Resolves broker host/port from env vars set by the tools-job
   (``MQTT_BROKER_HOST``, ``MQTT_BROKER_PORT``, ``MQTT_BROKER_USERNAME``,
   ``MQTT_BROKER_PASSWORD`` — all already wired by
   ``deploy/azure/bicep/modules/mqtt.bicep`` for the worker).
2. Publishes one synthetic tag-read on
   ``tenants/{TENANT_ID}/devices/{DEVICE_ID}/tag-reads`` with a
   recognisable ``tag_id="CANARY-<run-id>"``.
3. Polls Postgres for the row to appear in ``tag_reads`` within
   ``--timeout-seconds`` (default 30s) — proves broker → subscriber →
   ingestion → DB end-to-end.
4. Exits 0 on success, 1 on canary not seen in time, 2 on setup error.

Used by:
- The Sprint 28 D2 alert "MQTT subscriber canary failing" (run on a
  5-minute schedule).
- The Sprint 28 C4 outage runbook for ad-hoc verification after a
  Mosquitto restart.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from datetime import UTC, datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mqtt-canary")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--tenant-id",
        default=os.environ.get("CANARY_TENANT_ID"),
        help="Tenant UUID to publish under (default: $CANARY_TENANT_ID).",
    )
    p.add_argument(
        "--device-id",
        default=os.environ.get("CANARY_DEVICE_ID"),
        help="Device UUID to publish for (default: $CANARY_DEVICE_ID).",
    )
    p.add_argument(
        "--timeout-seconds",
        type=int,
        default=30,
        help="How long to wait for the canary row to appear in tag_reads (default: 30).",
    )
    return p.parse_args(argv)


async def _publish(host: str, port: int, username: str, password: str, topic: str, body: str) -> None:
    import aiomqtt

    log.info("connecting to mqtt %s:%d as %s", host, port, username)
    async with aiomqtt.Client(
        hostname=host, port=port, username=username, password=password
    ) as client:
        await client.publish(topic, payload=body.encode("utf-8"), qos=1)
    log.info("published canary to %s", topic)


async def _wait_for_row(database_url: str, tenant_id: uuid.UUID, tag_id: str, timeout_s: int) -> bool:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(database_url, pool_pre_ping=True)
    deadline = asyncio.get_event_loop().time() + timeout_s
    poll_interval = 1.0
    sql = text(
        "SELECT 1 FROM tag_reads WHERE tenant_id = :tid AND tag_id = :tag LIMIT 1"
    )
    while asyncio.get_event_loop().time() < deadline:
        async with engine.begin() as conn:
            row = (await conn.execute(sql, {"tid": tenant_id, "tag": tag_id})).first()
        if row is not None:
            log.info("canary row visible in tag_reads (tag_id=%s)", tag_id)
            return True
        await asyncio.sleep(poll_interval)
    log.error("canary row NOT visible after %ds (tag_id=%s)", timeout_s, tag_id)
    return False


async def _run_async(args: argparse.Namespace) -> int:
    host = os.environ.get("MQTT_BROKER_HOST")
    port = int(os.environ.get("MQTT_BROKER_PORT", "1883"))
    username = os.environ.get("MQTT_BROKER_USERNAME", "")
    password = os.environ.get("MQTT_BROKER_PASSWORD", "")
    database_url = os.environ.get("DATABASE_URL")
    if not host or not database_url or not args.tenant_id or not args.device_id:
        log.error(
            "missing required config: host=%s db=%s tenant=%s device=%s",
            bool(host),
            bool(database_url),
            args.tenant_id,
            args.device_id,
        )
        return 2

    try:
        tenant_uuid = uuid.UUID(args.tenant_id)
        device_uuid = uuid.UUID(args.device_id)
    except ValueError as exc:
        log.error("invalid UUID: %s", exc)
        return 2

    run_id = uuid.uuid4().hex[:8]
    tag_id = f"CANARY-{run_id}"
    topic = f"tenants/{tenant_uuid}/devices/{device_uuid}/tag-reads"
    body = (
        '{"tag_id":"' + tag_id + '","timestamp":"'
        + datetime.now(UTC).isoformat() + '"}'
    )

    try:
        await _publish(host, port, username, password, topic, body)
    except Exception:
        log.exception("publish failed")
        return 1

    ok = await _wait_for_row(database_url, tenant_uuid, tag_id, args.timeout_seconds)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    return asyncio.run(_run_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
