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
from tagpulse.services.consolidation import FusionStrategy, SlaConfig


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


async def _load(tenant_id: UUID) -> FusionStrategy | None:
    """Read + parse the tenant's current fusion_strategy (``None`` if unset/invalid)."""
    async with tenant_context(tenant_id) as session:
        raw = (
            await session.execute(
                text("SELECT fusion_strategy FROM tenants WHERE id = :t"),
                {"t": str(tenant_id)},
            )
        ).scalar_one_or_none()
    if not raw:
        return None
    try:
        return FusionStrategy.model_validate(raw)
    except ValueError:
        return None


def _pick(provided, current):  # type: ignore[no-untyped-def]
    return provided if provided is not None else current


def _merge(base: FusionStrategy, args: argparse.Namespace) -> FusionStrategy:
    """Merge only the explicitly-provided flags onto ``base`` (read-modify-write).

    Omitted knobs keep their existing value, so a partial ``--set`` never clobbers
    the rest of the config (e.g. setting ``--half-life-s`` keeps the SLA).
    """
    sla = base.sla
    if any(
        v is not None
        for v in (
            args.sla_temp_min_c,
            args.sla_temp_max_c,
            args.sla_humidity_max,
            args.sla_excursion_tolerance_s,
        )
    ):
        bs = base.sla or SlaConfig()
        sla = SlaConfig(
            temp_min_c=_pick(args.sla_temp_min_c, bs.temp_min_c),
            temp_max_c=_pick(args.sla_temp_max_c, bs.temp_max_c),
            humidity_max=_pick(args.sla_humidity_max, bs.humidity_max),
            excursion_tolerance_s=_pick(args.sla_excursion_tolerance_s, bs.excursion_tolerance_s),
        )
    return FusionStrategy(
        half_life_s=_pick(args.half_life_s, base.half_life_s),
        recompute_interval_s=_pick(args.recompute_interval_s, base.recompute_interval_s),
        lookback_s=_pick(args.lookback_s, base.lookback_s),
        rssi_floor_dbm=_pick(args.rssi_floor_dbm, base.rssi_floor_dbm),
        min_reads=_pick(args.min_reads, base.min_reads),
        sla=sla,
    )


async def _main(args: argparse.Namespace) -> int:
    tenant_id = _resolve_tenant(args)
    if args.clear:
        await _clear(tenant_id)
    elif args.set:
        # Merge onto the existing config so a partial --set keeps untouched knobs
        # (e.g. the SLA). No existing config -> start from FusionStrategy() defaults.
        base = await _load(tenant_id) or FusionStrategy()
        await _set(tenant_id, _merge(base, args))
    await _show(tenant_id)
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--tenant-id", help="Tenant UUID (wins over --tenant-slug).")
    g.add_argument("--tenant-slug", default="demo-wm-dc", help="Tenant slug.")
    p.add_argument("--set", action="store_true", help="Set fusion_strategy (opt in).")
    p.add_argument("--clear", action="store_true", help="Clear fusion_strategy (opt out).")
    # Knob defaults are None sentinels so a partial --set merges onto the existing
    # config (omitted knobs keep their current value; defaults apply only when no
    # config exists yet).
    p.add_argument("--half-life-s", type=float, default=None, dest="half_life_s")
    p.add_argument(
        "--recompute-interval-s", type=float, default=None, dest="recompute_interval_s"
    )
    p.add_argument("--lookback-s", type=float, default=None, dest="lookback_s")
    p.add_argument("--rssi-floor-dbm", type=float, default=None, dest="rssi_floor_dbm")
    p.add_argument("--min-reads", type=int, default=None, dest="min_reads")
    # -- Sprint 72: optional cold-chain SLA block (omit all bounds to leave SLA
    # unchanged; legs then record the envelope only). --
    p.add_argument("--sla-temp-min-c", type=float, default=None, dest="sla_temp_min_c")
    p.add_argument("--sla-temp-max-c", type=float, default=None, dest="sla_temp_max_c")
    p.add_argument("--sla-humidity-max", type=float, default=None, dest="sla_humidity_max")
    p.add_argument(
        "--sla-excursion-tolerance-s",
        type=int,
        default=None,
        dest="sla_excursion_tolerance_s",
    )
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(_parse_args())))
