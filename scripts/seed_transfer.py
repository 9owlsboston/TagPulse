#!/usr/bin/env python3
"""Seed one in-flight cross-tenant tag transfer on the demo tenant.

Per Sprint 58 design doc D7: the demo tenant ships with exactly one
in-flight transfer so the Tag Transfers page is non-empty on first load.

Workflow:
  1. Ensure a recipient tenant exists (``demo-wm-recipient``). Created
     deterministically via direct DB upsert if missing — never deleted
     by the recipient itself, so subsequent runs reuse it.
  2. Pick up to ``--epc-count`` active tags owned by the source tenant
     and POST them to ``/tag-transfers`` with ``to_tenant_slug`` set to
     the recipient.

Idempotent: if the source tenant already has at least one transfer in
``status='requested'``, the script is a no-op. The recipient tenant is
upserted (no row duplication).

Usage:
    python scripts/seed_transfer.py \\
        --tenant-id <UUID> \\
        --api-key <KEY> \\
        --epc-count 3
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg
import httpx

API_URL = os.environ.get("TAGPULSE_API_URL", "http://localhost:8000").rstrip("/")
DB_URL = os.environ.get(
    "TAGPULSE_SMOKE_DB_URL",
    "postgresql://tagpulse:secret@localhost:5432/tagpulse",
)

# Deterministic recipient tenant identity — uuid5 keeps re-runs converging
# to the same row across operators / machines / CI.
DEFAULT_RECIPIENT_SLUG = "demo-wm-recipient"
DEFAULT_RECIPIENT_NAME = "WM Receiving Hub"
DEFAULT_RECIPIENT_ID = uuid.uuid5(uuid.NAMESPACE_DNS, f"{DEFAULT_RECIPIENT_SLUG}.tagpulse.local")

# Backfill / live-ingest reads run through HTTP and are processed by the
# tag-registrar worker on a tick interval (currently a few seconds). The
# composer invokes us immediately after backfill returns, so there is a
# narrow window where no tags have yet flipped from ``status='registered'``
# to ``status='active'``. Poll for up to this many seconds rather than
# failing the seed run on a benign race. The default is intentionally
# generous to absorb occasional queue backlog on first startup.
_WORKER_PROMOTION_TIMEOUT_S = 120.0
_WORKER_PROMOTION_POLL_INTERVAL_S = 1.0


def _headers(tenant_id: str, api_key: str) -> dict[str, str]:
    return {
        "X-Tenant-ID": tenant_id,
        "Authorization": f"Bearer {api_key}",
    }


async def _connect_db() -> asyncpg.Connection:
    """Connect to Postgres — mirrors ``scripts/smoke_setup.py:_connect_db()``."""
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


async def _ensure_recipient_tenant(recipient_id: UUID, slug: str, name: str) -> None:
    """Upsert the recipient tenant — same shape as smoke_setup.upsert_tenant()."""
    conn = await _connect_db()
    try:
        await conn.execute(
            """
            INSERT INTO tenants (id, name, slug, plan, status, tracking_modes)
            VALUES ($1, $2, $3, 'standard', 'active', '["asset"]'::jsonb)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                slug = EXCLUDED.slug,
                status = 'active'
            """,
            recipient_id,
            name,
            slug,
        )
    finally:
        await conn.close()


async def _existing_inflight_transfer_count(source_tenant_id: UUID) -> int:
    """Count transfers from ``source_tenant_id`` in status='requested'."""
    conn = await _connect_db()
    try:
        row = await conn.fetchval(
            "SELECT COUNT(*) FROM tag_transfers WHERE from_tenant_id = $1 AND status = 'requested'",
            source_tenant_id,
        )
        return int(row or 0)
    finally:
        await conn.close()


async def _pick_active_epcs(source_tenant_id: UUID, count: int) -> list[str]:
    """Return up to ``count`` active EPC hex values owned by the tenant."""
    conn = await _connect_db()
    try:
        rows = await conn.fetch(
            "SELECT epc_hex FROM tags"
            " WHERE tenant_id = $1 AND status = 'active'"
            " ORDER BY first_seen_at NULLS LAST"
            " LIMIT $2",
            source_tenant_id,
            count,
        )
        return [row["epc_hex"] for row in rows]
    finally:
        await conn.close()


async def _wait_for_active_epcs(
    source_tenant_id: UUID,
    count: int,
    *,
    timeout_s: float = _WORKER_PROMOTION_TIMEOUT_S,
    poll_interval_s: float = _WORKER_PROMOTION_POLL_INTERVAL_S,
) -> list[str]:
    """Poll for ``count`` active EPCs, waiting up to ``timeout_s`` seconds.

    Mitigates the worker-promotion race that surfaced in the Sprint 58 audit:
    backfilled reads ingest synchronously but the registrar worker only
    promotes ``status='registered'`` to ``status='active'`` on its next tick,
    so an immediate post-backfill pick can return zero rows on a freshly
    seeded tenant. Returns whatever the picker has on the last attempt
    (possibly empty) once the budget expires — callers decide how to
    handle short returns.
    """
    deadline = time.monotonic() + max(timeout_s, 0.0)
    epcs: list[str] = []
    while True:
        epcs = await _pick_active_epcs(source_tenant_id, count)
        if len(epcs) >= count or time.monotonic() >= deadline:
            return epcs
        await asyncio.sleep(poll_interval_s)


def _post_transfer(
    tenant_id: str, api_key: str, recipient_slug: str, epcs: list[str]
) -> dict[str, Any] | None:
    with httpx.Client(timeout=15.0) as client:
        resp = client.post(
            f"{API_URL}/tag-transfers",
            headers=_headers(tenant_id, api_key),
            json={"to_tenant_slug": recipient_slug, "epcs": epcs},
        )
        if resp.status_code != 201:
            print(
                f"  POST /tag-transfers failed: {resp.status_code} {resp.text}",
                file=sys.stderr,
            )
            return None
        rows = resp.json()
        return {"request_id": rows[0]["request_id"], "count": len(rows)}


def _bootstrap_transferable_epcs(
    tenant_id: UUID,
    api_key: str,
    *,
    epc_count: int,
) -> bool:
    """Create demo tag rows + emit reads so registrar can promote to active.

    The transfer API requires EPCs in status ``active``. On a fresh local
    stack the demo flow may have no tag-registry rows yet (only ``tag_id``
    strings like ``TAG0001``), so we seed deterministic EPC-hex tags and
    submit one matching read per EPC to trigger ``registered -> active``.
    """
    headers = _headers(str(tenant_id), api_key)
    with httpx.Client(timeout=15.0) as client:
        devices = client.get(f"{API_URL}/device-registry", headers=headers, params={"limit": 1})
        if devices.status_code != 200:
            print(
                f"  WARN: cannot list devices for transfer bootstrap: "
                f"{devices.status_code} {devices.text}",
                file=sys.stderr,
            )
            return False
        payload = devices.json()
        if not payload:
            print(
                "  WARN: cannot bootstrap transfer tags (no devices found for tenant)",
                file=sys.stderr,
            )
            return False
        device_id = payload[0]["id"]

        seeded = 0
        base = 0xE20000172211000000000000
        for i in range(1, epc_count + 1):
            epc_hex = f"{base + i:024X}"
            create_tag = client.post(
                f"{API_URL}/tags",
                headers=headers,
                json={
                    "epc_hex": epc_hex,
                    "source": "api",
                    "metadata": {"seed": "seed_transfer"},
                },
            )
            if create_tag.status_code not in (201, 409):
                print(
                    f"  WARN: failed to ensure tag {epc_hex}: "
                    f"{create_tag.status_code} {create_tag.text}",
                    file=sys.stderr,
                )
                continue

            ingest = client.post(
                f"{API_URL}/tag-reads",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "device_id": device_id,
                    "tag_id": epc_hex,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "identity": {"epc": epc_hex, "epc_hex": epc_hex},
                },
            )
            if ingest.status_code != 201:
                print(
                    f"  WARN: failed to ingest bootstrap read for {epc_hex}: "
                    f"{ingest.status_code} {ingest.text}",
                    file=sys.stderr,
                )
                continue
            seeded += 1

        if seeded:
            print(f"  bootstrapped {seeded} EPC read(s) for transfer seeding")
            return True
        return False


async def _seed_one_transfer(
    source_tenant_id: UUID,
    api_key: str,
    *,
    recipient_id: UUID,
    recipient_slug: str,
    recipient_name: str,
    epc_count: int,
    wait_timeout_s: float,
) -> bool:
    """Seed exactly one in-flight transfer if none already exists.

    Returns True on success or no-op (idempotent), False on error.
    """
    inflight = await _existing_inflight_transfer_count(source_tenant_id)
    if inflight > 0:
        print(
            f"  source tenant already has {inflight} in-flight transfer(s); skipping (idempotent)"
        )
        return True

    await _ensure_recipient_tenant(recipient_id, recipient_slug, recipient_name)
    print(f"  recipient tenant: {recipient_slug} ({recipient_id}) ensured")

    epcs = await _wait_for_active_epcs(
        source_tenant_id,
        epc_count,
        timeout_s=wait_timeout_s,
    )
    if not epcs:
        print("  no active EPC tags found yet; bootstrapping transfer-ready EPCs")
        bootstrapped = _bootstrap_transferable_epcs(
            source_tenant_id,
            api_key,
            epc_count=epc_count,
        )
        if bootstrapped:
            epcs = await _wait_for_active_epcs(
                source_tenant_id,
                epc_count,
                timeout_s=wait_timeout_s,
            )

    if not epcs:
        print(
            "  WARN: no active tags on source tenant after waiting"
            f" {wait_timeout_s:.0f}s for the tag-registrar worker."
            " Re-run `make demo-tenant` once the worker container is healthy,"
            " or run simulate_devices.py with live reads to flush the queue.",
            file=sys.stderr,
        )
        return False
    if len(epcs) < epc_count:
        print(
            f"  NOTE: only {len(epcs)}/{epc_count} active EPC(s) available;"
            " proceeding with what we have (worker may still be catching up)."
        )

    result = _post_transfer(str(source_tenant_id), api_key, recipient_slug, epcs)
    if result is None:
        return False
    print(
        f"  created transfer request {result['request_id']}"
        f" with {result['count']} EPC(s) to {recipient_slug}"
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, help="Source tenant UUID")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("TAGPULSE_API_KEY"),
        help="Bearer API key (admin scope). Default: $TAGPULSE_API_KEY",
    )
    parser.add_argument(
        "--epc-count",
        type=int,
        default=3,
        help="Number of EPCs to include in the transfer (default: 3)",
    )
    parser.add_argument(
        "--recipient-slug",
        default=DEFAULT_RECIPIENT_SLUG,
        help=f"Recipient tenant slug (default: {DEFAULT_RECIPIENT_SLUG})",
    )
    parser.add_argument(
        "--recipient-name",
        default=DEFAULT_RECIPIENT_NAME,
        help=f"Recipient tenant display name (default: {DEFAULT_RECIPIENT_NAME!r})",
    )
    parser.add_argument(
        "--wait-timeout-s",
        type=float,
        default=_WORKER_PROMOTION_TIMEOUT_S,
        help=(
            "Seconds to wait for active tags before giving up "
            f"(default: {_WORKER_PROMOTION_TIMEOUT_S:.0f})"
        ),
    )
    args = parser.parse_args()

    if not args.api_key:
        print(
            "ERROR: --api-key or TAGPULSE_API_KEY required (admin)",
            file=sys.stderr,
        )
        return 2
    try:
        source_uuid = UUID(args.tenant_id)
    except ValueError:
        print(f"ERROR: invalid --tenant-id {args.tenant_id!r}", file=sys.stderr)
        return 2

    # Recipient UUID derived deterministically from its slug so the row is
    # the same across runs.
    recipient_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"{args.recipient_slug}.tagpulse.local")

    print(
        f"Seeding in-flight transfer → {API_URL}"
        f" (source={source_uuid}, recipient={args.recipient_slug})"
    )
    ok = asyncio.run(
        _seed_one_transfer(
            source_uuid,
            args.api_key,
            recipient_id=recipient_id,
            recipient_slug=args.recipient_slug,
            recipient_name=args.recipient_name,
            epc_count=args.epc_count,
            wait_timeout_s=args.wait_timeout_s,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
