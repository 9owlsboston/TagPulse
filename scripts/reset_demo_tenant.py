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
from typing import NoReturn
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
RECIPIENT_TENANT_ID = uuid.uuid5(uuid.NAMESPACE_DNS, f"{RECIPIENT_SLUG}.tagpulse.local")

_LOCAL_DB_HINTS = ("localhost", "127.0.0.1", "tagpulse-pg", "host.docker.internal")


async def _connect_db() -> asyncpg.Connection:
    """Connect to Postgres — mirrors ``scripts/smoke_setup.py:_connect_db()``."""
    host = os.environ.get("POSTGRES_HOST")
    password = os.environ.get("POSTGRES_PASSWORD")
    using_host_vars = bool(host and password)
    try:
        if using_host_vars:
            return await asyncpg.connect(
                host=host,
                port=int(os.environ.get("POSTGRES_PORT", "5432")),
                user=os.environ.get("POSTGRES_USER", "tagpulse_admin"),
                password=password,
                database=os.environ.get("POSTGRES_DB", "tagpulse"),
                ssl="require",
            )
        return await asyncpg.connect(DB_URL)
    except Exception as exc:  # pragma: no cover - exercised via CLI
        _fail_connect_with_guidance(exc, using_host_vars=using_host_vars)


def _fail_connect_with_guidance(exc: Exception, *, using_host_vars: bool) -> NoReturn:
    """Exit with a concise, actionable DB-connectivity error message."""
    if using_host_vars:
        target = (
            f"host={os.environ.get('POSTGRES_HOST', '<unset>')} "
            f"port={os.environ.get('POSTGRES_PORT', '5432')} "
            f"db={os.environ.get('POSTGRES_DB', 'tagpulse')}"
        )
    else:
        target = os.environ.get("TAGPULSE_SMOKE_DB_URL", DB_URL)

    print("ERROR: failed to connect to Postgres for demo reset.", file=sys.stderr)
    print(f"  target: {target}", file=sys.stderr)
    print(f"  cause:  {type(exc).__name__}: {exc}", file=sys.stderr)
    print(file=sys.stderr)
    print("Try one of these:", file=sys.stderr)
    print("  1) Start local DB + migrations: docker compose up -d db migrations", file=sys.stderr)
    print(
        "  2) If DB is already running elsewhere, set TAGPULSE_SMOKE_DB_URL to that DSN",
        file=sys.stderr,
    )
    print("  3) Verify connectivity: pg_isready -h localhost -p 5432 -U tagpulse", file=sys.stderr)
    sys.exit(2)


def _looks_local(connection_target: str) -> bool:
    return any(hint in connection_target for hint in _LOCAL_DB_HINTS)


async def _list_tenant_scoped_tables(conn: asyncpg.Connection) -> list[tuple[str, str]]:
    """Return every public-schema base table with an FK to ``tenants.id``.

    Returns a list of ``(table_name, column_name)`` tuples. We discover
    via ``pg_catalog`` FK metadata rather than column-name matching so
    tables that use non-standard column names (``from_tenant_id``,
    ``to_tenant_id`` on ``tag_transfers``) are still covered. Views are
    excluded — FKs can only target base tables anyway, but we filter
    explicitly for clarity.
    """
    rows = await conn.fetch(
        """
        SELECT
            cl.relname AS table_name,
            att.attname AS column_name
        FROM pg_constraint AS con
        JOIN pg_class AS cl ON cl.oid = con.conrelid
        JOIN pg_namespace AS ns ON ns.oid = cl.relnamespace
        JOIN pg_class AS ref_cl ON ref_cl.oid = con.confrelid
        JOIN pg_namespace AS ref_ns ON ref_ns.oid = ref_cl.relnamespace
        JOIN pg_attribute AS att
          ON att.attrelid = con.conrelid
         AND att.attnum = ANY(con.conkey)
        WHERE con.contype = 'f'
          AND ns.nspname = 'public'
          AND ref_ns.nspname = 'public'
          AND ref_cl.relname = 'tenants'
          AND cl.relkind = 'r'
          AND cl.relname <> 'tenants'
        ORDER BY cl.relname, att.attname
        """
    )
    return [(row["table_name"], row["column_name"]) for row in rows]


async def _reset(tenant_ids: list[UUID]) -> dict[str, int]:
    """Delete all rows for ``tenant_ids`` across every tenant-scoped table.

    Uses an iterative retry loop so cross-table FK chains (e.g.
    ``tag_transfers.requested_by -> users.id``) resolve themselves without
    requiring us to hand-maintain a topological order. Each pass deletes
    from tables that don't yet fail with a FK violation; surviving tables
    are retried on the next pass. We bail out when a pass makes no
    progress, surfacing the remaining errors.
    """
    conn = await _connect_db()
    deleted: dict[str, int] = {}
    try:
        targets = await _list_tenant_scoped_tables(conn)
        unique_tables = sorted({table for table, _ in targets})
        print(f"  found {len(unique_tables)} tenant-scoped tables (FK columns: {len(targets)})")

        pending = list(targets)
        last_errors: dict[str, str] = {}
        # Bounded loop: at most one pass per FK is sufficient to resolve
        # any acyclic FK chain. ``+1`` guarantees at least one pass even
        # when ``pending`` is empty.
        for _ in range(len(pending) + 1):
            if not pending:
                break
            still_pending: list[tuple[str, str]] = []
            last_errors = {}
            made_progress = False
            for table, column in pending:
                key = f"{table}.{column}"
                try:
                    result = await conn.execute(
                        f'DELETE FROM "{table}" WHERE "{column}" = ANY($1::uuid[])',
                        tenant_ids,
                    )
                    # asyncpg returns "DELETE <n>"
                    count = int(result.rsplit(" ", 1)[-1])
                    deleted[key] = deleted.get(key, 0) + count
                    made_progress = True
                    if count > 0:
                        print(f"  deleted {count:>6d} from {key}")
                except (asyncpg.PostgresError, ValueError) as exc:
                    last_errors[key] = str(exc)
                    still_pending.append((table, column))
            pending = still_pending
            if not made_progress:
                break

        for key, msg in last_errors.items():
            print(f"  WARN: DELETE FROM {key} failed: {msg}", file=sys.stderr)

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
