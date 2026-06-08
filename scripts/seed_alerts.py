#!/usr/bin/env python3
"""Seed a curated mix of open and resolved alerts on the demo tenant.

Per Sprint 58 design doc D4 (hybrid alert seeding):

  * **Open alerts**: triggered naturally by sending tag reads with
    deliberately alert-shaped sensor data (temperature above the demo
    high-temp rule threshold). The ingestion pipeline routes them
    through the rule evaluator just as real reads would.
  * **Resolved alerts**: written directly to the ``alerts`` hypertable
    via asyncpg with ``status='resolved'`` and ``triggered_at`` in the
    past, so the Alerts page has a meaningful history pane without
    waiting hours for naturally-firing rules to accumulate.

This split keeps the live ingest path honest (the open alerts are
authentic end-to-end alerts) while still giving the demo a populated
"resolved" history on first load.

Assumes ``smoke_setup.py --with-rules`` has already seeded at least one
threshold rule named "High temperature on RFID reader" — the standard
demo-tenant composer (``seed_demo_tenant.py``) handles that.

Usage:
    python scripts/seed_alerts.py \\
        --tenant-id <UUID> \\
        --api-key <KEY> \\
        --natural-count 4 \\
        --resolved-count 3
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import httpx

API_URL = os.environ.get("TAGPULSE_API_URL", "http://localhost:8000").rstrip("/")
DB_URL = os.environ.get(
    "TAGPULSE_SMOKE_DB_URL",
    "postgresql://tagpulse:secret@localhost:5432/tagpulse",
)

_HIGH_TEMP_RULE_NAME = "High temperature on RFID reader"
_TAG_POOL = [f"TAG{i:04d}" for i in range(1, 51)]


def _headers(tenant_id: str, api_key: str) -> dict[str, str]:
    return {
        "X-Tenant-ID": tenant_id,
        "Authorization": f"Bearer {api_key}",
    }


async def _connect_db() -> asyncpg.Connection:
    """Connect to Postgres — mirrors ``scripts/smoke_setup.py:_connect_db()``.

    Inside the tools-job, ``POSTGRES_HOST`` / ``POSTGRES_USER`` /
    ``POSTGRES_PASSWORD`` / ``POSTGRES_DB`` are wired separately by Bicep.
    Locally we fall back to ``TAGPULSE_SMOKE_DB_URL``.
    """
    host = os.environ.get("POSTGRES_HOST")
    password = os.environ.get("POSTGRES_PASSWORD")
    if host and password:
        return await asyncpg.connect(
            host=host,
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            user=os.environ.get("POSTGRES_USER", "tagpulse_admin"),
            password=password,
            database=os.environ.get("POSTGRES_DB", "tagpulse"),
            ssl="require",
        )
    return await asyncpg.connect(DB_URL)


def _fire_natural_alert_read(
    client: httpx.Client,
    tenant_id: str,
    api_key: str,
    device_id: str,
) -> bool:
    """POST one tag read with temperature above the demo threshold (30 C).

    Does **not** set ``?backfill=true`` — we *want* the rule evaluator
    to see this read and fire its action.
    """
    body: dict[str, Any] = {
        "device_id": device_id,
        "tag_id": random.choice(_TAG_POOL),
        "timestamp": datetime.now(UTC).isoformat(),
        "signal_strength": round(random.uniform(-70.0, -30.0), 1),
        "sensor_data": {
            # 35-42 C: comfortably above the 30 C demo threshold.
            "temperature": round(random.uniform(35.0, 42.0), 1),
            "humidity": round(random.uniform(40.0, 80.0), 1),
        },
    }
    resp = client.post(
        f"{API_URL}/tag-reads",
        headers=_headers(tenant_id, api_key),
        json=body,
    )
    return resp.status_code == 201


def _trigger_natural_alerts(tenant_id: str, api_key: str, count: int) -> int:
    """Fire ``count`` natural alerts by posting high-temp tag reads.

    The high-temp rule has a 60 s cooldown per (tenant, rule, device),
    so we round-robin across devices to ensure each read actually fires.
    Returns the number of reads accepted by the API.
    """
    if count <= 0:
        return 0
    with httpx.Client(timeout=15.0) as client:
        # Discover devices.
        resp = client.get(f"{API_URL}/device-registry", headers=_headers(tenant_id, api_key))
        resp.raise_for_status()
        devices = [d["id"] for d in resp.json()]
        if not devices:
            print(
                "ERROR: no devices for tenant — run simulate_devices.py first",
                file=sys.stderr,
            )
            return 0

        accepted = 0
        for i in range(count):
            device_id = devices[i % len(devices)]
            if _fire_natural_alert_read(client, tenant_id, api_key, device_id):
                accepted += 1
                print(f"  fired natural alert read on device {device_id[:8]}")
            else:
                print(
                    f"  WARN: natural alert read {i + 1} rejected",
                    file=sys.stderr,
                )
        return accepted


async def _insert_resolved_alerts(tenant_id: UUID, count: int) -> int:
    """Insert ``count`` resolved alerts directly into the ``alerts`` table.

    Each row references the demo high-temp rule (the seed-time rule
    set always includes this) and a real device on the tenant.
    Triggered/resolved at staggered past timestamps so the Alerts
    page history pane shows a meaningful spread.

    Returns the number of rows inserted (0 if no rule or no device).
    """
    if count <= 0:
        return 0
    conn = await _connect_db()
    try:
        rule_id = await conn.fetchval(
            "SELECT id FROM rules WHERE tenant_id = $1 AND name = $2 LIMIT 1",
            tenant_id,
            _HIGH_TEMP_RULE_NAME,
        )
        if rule_id is None:
            print(
                f"  WARN: rule {_HIGH_TEMP_RULE_NAME!r} not found — skipping"
                " resolved alerts (run smoke_setup with --with-rules first)",
                file=sys.stderr,
            )
            return 0

        device_rows = await conn.fetch(
            "SELECT id FROM devices WHERE tenant_id = $1 LIMIT 10",
            tenant_id,
        )
        if not device_rows:
            print(
                "  WARN: no devices for tenant — skipping resolved alerts",
                file=sys.stderr,
            )
            return 0
        device_ids = [row["id"] for row in device_rows]

        now = datetime.now(UTC)
        inserted = 0
        for i in range(count):
            # Stagger 6 h to 3 days in the past.
            hours_ago = 6 + i * 18
            triggered_at = now - timedelta(hours=hours_ago)
            device_id = device_ids[i % len(device_ids)]
            severity = random.choice(["warning", "info"])
            temp = round(random.uniform(31.0, 38.0), 1)
            await conn.execute(
                """
                INSERT INTO alerts (
                    id, tenant_id, rule_id, device_id, severity,
                    message, context, status, triggered_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, 'resolved', $8)
                """,
                uuid4(),
                tenant_id,
                rule_id,
                device_id,
                severity,
                f"Temperature {temp} C exceeded threshold (resolved)",
                f'{{"metric_name": "temperature", "metric_value": {temp},'
                f' "threshold": 30.0, "seeded": true}}',
                triggered_at,
            )
            inserted += 1
            print(f"  inserted resolved alert (triggered {hours_ago}h ago, severity={severity})")
        return inserted
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, help="Target tenant UUID")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("TAGPULSE_API_KEY"),
        help="Bearer API key (admin/editor). Default: $TAGPULSE_API_KEY",
    )
    parser.add_argument(
        "--natural-count",
        type=int,
        default=4,
        help="Number of natural high-temp alerts to fire (default: 4)",
    )
    parser.add_argument(
        "--resolved-count",
        type=int,
        default=3,
        help="Number of resolved alerts to insert directly (default: 3)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducible runs",
    )
    args = parser.parse_args()

    if not args.api_key:
        print(
            "ERROR: --api-key or TAGPULSE_API_KEY required",
            file=sys.stderr,
        )
        return 2
    try:
        tenant_uuid = UUID(args.tenant_id)
    except ValueError:
        print(f"ERROR: invalid --tenant-id {args.tenant_id!r}", file=sys.stderr)
        return 2

    if args.seed is not None:
        random.seed(args.seed)

    print(
        f"Seeding alerts → {API_URL} (tenant={args.tenant_id}, "
        f"natural={args.natural_count}, resolved={args.resolved_count})"
    )

    natural = _trigger_natural_alerts(args.tenant_id, args.api_key, args.natural_count)
    print(f"  natural reads accepted: {natural}")

    resolved = asyncio.run(_insert_resolved_alerts(tenant_uuid, args.resolved_count))
    print(f"  resolved alerts inserted: {resolved}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
