#!/usr/bin/env python3
"""Clear rows from ``telemetry_quarantine`` — scoped + dry-run by default.

Quarantine has no clear/delete API (it is an operator triage surface). After a
model fix or a fan-out fix (e.g. Sprint 70 read_count exclusion), the stale
quarantine rows that the *old* behavior produced linger as historical noise.
This ops utility deletes them in a **scoped** way, run in-VNet via the
tools-job (it talks to the private Postgres):

    scripts/azd-job.sh dev clear_quarantine.py -- \
        --tenant-id <uuid> --reason unknown_metric --metric read_count --apply

Safety:
  * Always tenant-scoped (``--tenant-id`` required).
  * ``--reason`` / ``--metric`` narrow further (AND-combined). Omit for all.
  * **Dry-run by default** — prints the matching count and exits. Pass
    ``--apply`` to actually delete.

Connection mirrors ``scripts/reset_demo_tenant.py`` / ``smoke_setup.py``: the
tools-job sets ``POSTGRES_HOST`` / ``POSTGRES_PASSWORD`` and connects as the
admin role (RLS-bypassing), so the tenant filter is enforced by this script's
WHERE clause, not by RLS.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import NoReturn
from uuid import UUID

import asyncpg

DB_URL = os.environ.get(
    "TAGPULSE_SMOKE_DB_URL",
    "postgresql://tagpulse:secret@localhost:5432/tagpulse",
)


async def _connect_db() -> asyncpg.Connection:
    """Connect to Postgres — mirrors ``scripts/reset_demo_tenant.py:_connect_db()``."""
    host = os.environ.get("POSTGRES_HOST")
    password = os.environ.get("POSTGRES_PASSWORD")
    try:
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
    except Exception as exc:  # pragma: no cover - exercised via CLI
        _fail(f"failed to connect to Postgres: {type(exc).__name__}: {exc}")


def _fail(msg: str) -> NoReturn:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def _build_where(
    tenant_id: UUID, reason: str | None, metric: str | None
) -> tuple[str, list[object]]:
    clauses = ["tenant_id = $1"]
    params: list[object] = [tenant_id]
    if reason is not None:
        params.append(reason)
        clauses.append(f"reason = ${len(params)}")
    if metric is not None:
        params.append(metric)
        clauses.append(f"metric_name = ${len(params)}")
    return " AND ".join(clauses), params


async def _run(args: argparse.Namespace) -> int:
    where, params = _build_where(args.tenant_id, args.reason, args.metric)
    # `where` is built only from hardcoded column names + parameterized `$N`
    # placeholders; all values are bound params (never interpolated), so the
    # f-strings below are not an injection vector.
    count_sql = f"SELECT count(*) FROM telemetry_quarantine WHERE {where}"  # noqa: S608
    delete_sql = f"DELETE FROM telemetry_quarantine WHERE {where}"  # noqa: S608
    conn = await _connect_db()
    try:
        count = await conn.fetchval(count_sql, *params)
        scope = (
            f"tenant={args.tenant_id}"
            + (f" reason={args.reason}" if args.reason else "")
            + (f" metric={args.metric}" if args.metric else "")
        )
        print(f"  matched {count} quarantine row(s) ({scope})")
        if count == 0:
            print("  nothing to delete.")
            return 0
        if not args.apply:
            print("  DRY-RUN — pass --apply to delete.")
            return 0
        result = await conn.execute(delete_sql, *params)
        deleted = int(result.rsplit(" ", 1)[-1])
        print(f"  deleted {deleted} quarantine row(s).")
        return 0
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clear telemetry_quarantine rows (scoped, dry-run by default)."
    )
    parser.add_argument("--tenant-id", type=UUID, required=True, help="Tenant UUID (required).")
    parser.add_argument("--reason", default=None, help="Filter by reason (e.g. unknown_metric).")
    parser.add_argument("--metric", default=None, help="Filter by metric_name (e.g. read_count).")
    parser.add_argument("--apply", action="store_true", help="Actually delete (default: dry-run).")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
