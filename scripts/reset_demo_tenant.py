#!/usr/bin/env python3
"""Reset the demo tenant — delete all rows for the demo + recipient tenants.

Per Sprint 58 design doc Phase B deliverable #1: ``make demo-tenant-reset``
returns the local DB to a "no demo tenant" state so re-running
``make demo-tenant`` produces a fresh build rather than appending.

Approach:
  1. Resolve demo + recipient tenant UUIDs deterministically (uuid5 on
     the canonical slug).
  2. Discover every table with a ``tenant_id`` column via the Postgres
     ``information_schema`` — this future-proofs the reset against
     migrations that add new tenant-scoped tables.
  3. ``DELETE FROM <table> WHERE tenant_id = ANY($1)`` for each table.
  4. ``DELETE FROM tenants WHERE id = ANY($1)``.

The script is idempotent: missing rows are a no-op. It is destructive,
so it explicitly refuses to run against any DB that doesn't smell like
local dev unless ``DEMO_RESET_FORCE=1`` is set.

Usage:
    python scripts/reset_demo_tenant.py
    DEMO_RESET_FORCE=1 python scripts/reset_demo_tenant.py    # bypass guard
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from uuid import UUID

import asyncpg

DB_URL = os.environ.get(
    "TAGPULSE_SMOKE_DB_URL",
    "postgresql://tagpulse:secret@localhost:5432/tagpulse",
)

# Must match scripts/seed_demo_tenant.py + scripts/seed_transfer.py.
DEMO_TENANT_SLUG = "demo-wm-dc"
DEMO_TENANT_ID = uuid.uuid5(uuid.NAMESPACE_DNS, f"{DEMO_TENANT_SLUG}.tagpulse.local")
RECIPIENT_SLUG = "demo-wm-recipient"
RECIPIENT_TENANT_ID = uuid.uuid5(
    uuid.NAMESPACE_DNS, f"{RECIPIENT_SLUG}.tagpulse.local"
)

_LOCAL_DB_HINTS = ("localhost", "127.0.0.1", "tagpulse-pg", "host.docker.internal")


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


def _looks_local(connection_target: str) -> bool:
    return any(hint in connection_target for hint in _LOCAL_DB_HINTS)


async def _list_tenant_scoped_tables(conn: asyncpg.Connection) -> list[str]:
    """Return every public-schema table with a ``tenant_id`` column."""
    rows = await conn.fetch(
        """
        SELECT table_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND column_name = 'tenant_id'
        ORDER BY table_name
        """
    )
    return [row["table_name"] for row in rows]


async def _reset(tenant_ids: list[UUID]) -> dict[str, int]:
    """Delete all rows for ``tenant_ids`` across every tenant-scoped table."""
    conn = await _connect_db()
    deleted: dict[str, int] = {}
    try:
        tables = await _list_tenant_scoped_tables(conn)
        print(f"  found {len(tables)} tenant-scoped tables")

        # First pass: clear child tables. We use DELETE (not TRUNCATE) so
        # ON DELETE constraints on other FKs still fire correctly.
        for table in tables:
            if table == "tenants":  # delete the parent last
                continue
            try:
                result = await conn.execute(
                    f'DELETE FROM "{table}" WHERE tenant_id = ANY($1::uuid[])',
                    tenant_ids,
                )
                # asyncpg returns "DELETE <n>"
                count = int(result.rsplit(" ", 1)[-1])
                deleted[table] = count
                if count > 0:
                    print(f"  deleted {count:>6d} from {table}")
            except (asyncpg.PostgresError, ValueError) as exc:
                print(
                    f"  WARN: DELETE FROM {table} failed: {exc}",
                    file=sys.stderr,
                )

        # Final pass: delete the tenant rows themselves.
        result = await conn.execute(
            "DELETE FROM tenants WHERE id = ANY($1::uuid[])",
            tenant_ids,
        )
        tenant_count = int(result.rsplit(" ", 1)[-1])
        deleted["tenants"] = tenant_count
        print(f"  deleted {tenant_count} tenant row(s)")
        return deleted
    finally:
        await conn.close()


def _safety_check(force: bool) -> None:
    """Refuse to run against anything that doesn't smell like local dev."""
    if force:
        print("  DEMO_RESET_FORCE=1 — safety check bypassed")
        return
    host = os.environ.get("POSTGRES_HOST", "")
    db_url = os.environ.get("TAGPULSE_SMOKE_DB_URL", DB_URL)
    target = f"{host} {db_url}"
    if not _looks_local(target):
        print(
            "ERROR: refusing to reset — target DB does not look local."
            f" (host={host!r}, db_url={db_url!r}). Set DEMO_RESET_FORCE=1"
            " to override.",
            file=sys.stderr,
        )
        sys.exit(2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-recipient",
        action="store_true",
        default=True,
        help="Also delete the recipient tenant (default: true)",
    )
    parser.add_argument(
        "--source-only",
        action="store_true",
        help="Delete only the demo source tenant (keep the recipient)",
    )
    args = parser.parse_args()

    force = os.environ.get("DEMO_RESET_FORCE") == "1"
    _safety_check(force)

    tenant_ids = [DEMO_TENANT_ID]
    if args.include_recipient and not args.source_only:
        tenant_ids.append(RECIPIENT_TENANT_ID)

    print(f"Resetting tenants: {[str(t) for t in tenant_ids]}")
    deleted = asyncio.run(_reset(tenant_ids))
    total = sum(deleted.values())
    print(f"Done. Deleted {total} rows across {len(deleted)} tables.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
