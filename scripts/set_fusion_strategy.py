#!/usr/bin/env python3
"""Opt a tenant in/out of asset-state consolidation by setting ``tenants.fusion_strategy``.

DB-direct (no API) — designed to run inside the deployed tools-job::

    scripts/azd-job.sh dev set_fusion_strategy.py -- --tenant-slug demo-wm-dc --set
    scripts/azd-job.sh dev set_fusion_strategy.py -- --tenant-slug demo-wm-dc --show
    scripts/azd-job.sh dev set_fusion_strategy.py -- --tenant-slug demo-wm-dc --clear

Writes the per-tenant :class:`tagpulse.services.consolidation.FusionStrategy`
JSONB the consolidation worker reads (Sprint 71, ADR-034). The worker only
consolidates tenants whose ``fusion_strategy`` is non-NULL, so this is the opt-in
switch — pair it with ``CONSOLIDATION_ENABLED=true`` on the worker container. No
API surface exists for this config yet; this is the operability stopgap.

Tenant resolution: ``--tenant-id`` wins; else ``--tenant-slug`` →
``uuid5(NAMESPACE_DNS, "<slug>.tagpulse.local")`` (the demo-composer convention,
so ``demo-wm-dc`` = SuperMart Distribution Center).
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from uuid import UUID

from sqlalchemy import text

from tagpulse.core.context import tenant_context
from tagpulse.services.consolidation import FusionStrategy


def _resolve_tenant(args: argparse.Namespace) -> UUID:
    if args.tenant_id:
        return UUID(args.tenant_id)
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"{args.tenant_slug}.tagpulse.local")


async def _show(tenant_id: UUID) -> None:
    async with tenant_context(tenant_id) as session:
        row = (
            await session.execute(
                text("SELECT fusion_strategy FROM tenants WHERE id = :t"),
                {"t": str(tenant_id)},
            )
        ).first()
    if row is None:
        print(f"tenant {tenant_id} not found")
        return
    print(f"fusion_strategy for {tenant_id}: {row[0]}")


async def _set(tenant_id: UUID, config: FusionStrategy) -> None:
    async with tenant_context(tenant_id) as session:
        await session.execute(
            text("UPDATE tenants SET fusion_strategy = CAST(:s AS jsonb) WHERE id = :t"),
            {"s": config.model_dump_json(), "t": str(tenant_id)},
        )
        await session.commit()
    print(f"fusion_strategy set for {tenant_id}: {config.model_dump_json()}")


async def _clear(tenant_id: UUID) -> None:
    async with tenant_context(tenant_id) as session:
        await session.execute(
            text("UPDATE tenants SET fusion_strategy = NULL WHERE id = :t"),
            {"t": str(tenant_id)},
        )
        await session.commit()
    print(f"fusion_strategy cleared for {tenant_id}")


async def _main(args: argparse.Namespace) -> int:
    tenant_id = _resolve_tenant(args)
    if args.clear:
        await _clear(tenant_id)
    elif args.set:
        config = FusionStrategy(
            half_life_s=args.half_life_s,
            recompute_interval_s=args.recompute_interval_s,
            lookback_s=args.lookback_s,
            rssi_floor_dbm=args.rssi_floor_dbm,
            min_reads=args.min_reads,
        )
        await _set(tenant_id, config)
    await _show(tenant_id)
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--tenant-id", help="Tenant UUID (wins over --tenant-slug).")
    g.add_argument("--tenant-slug", default="demo-wm-dc", help="Tenant slug.")
    p.add_argument("--set", action="store_true", help="Set fusion_strategy (opt in).")
    p.add_argument("--clear", action="store_true", help="Clear fusion_strategy (opt out).")
    p.add_argument("--half-life-s", type=float, default=5.0, dest="half_life_s")
    p.add_argument(
        "--recompute-interval-s", type=float, default=10.0, dest="recompute_interval_s"
    )
    p.add_argument("--lookback-s", type=float, default=60.0, dest="lookback_s")
    p.add_argument(
        "--rssi-floor-dbm", type=float, default=None, dest="rssi_floor_dbm"
    )
    p.add_argument("--min-reads", type=int, default=1, dest="min_reads")
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(_parse_args())))
