#!/usr/bin/env python3
"""Run the real floor-position pipeline against a dev tenant and report accuracy.

DB-direct (no API) — designed to run inside the deployed tools-job via
``scripts/azd-job.sh dev validate_floor_positioning.py -- --tenant-slug demo-wm-dc``.
It exercises the **production** code path (``TimescaleObservationSource`` →
``rssi_weighted_centroid`` → ``TimescalePositionWriter``) for one tenant, then
queries the ``computed`` rows back and compares them to each asset's
ground-truth ``placed_x``/``placed_y`` (written into ``assets.metadata`` by
``simulate_floor_positioning.py --emit``).

Seed first (from a laptop, against the public dev API)::

    export TAGPULSE_API_KEY=$(scripts/azd-kv-get.sh dev tagpulse-demo-wm-dc-admin-key | tail -1)
    python scripts/simulate_floor_positioning.py --emit --api-url <dev-api> \\
        --tenant-id 241d9b81-59da-5fb7-8f78-f58200978566   # = uuid5(demo-wm-dc)

Tenant resolution: ``--tenant-id`` wins; else ``--tenant-slug`` →
``uuid5(NAMESPACE_DNS, "<slug>.tagpulse.local")`` (the demo-composer convention,
so ``demo-wm-dc`` = SuperMart). A large ``lookback`` / ``half_life`` is used so
the (now slightly old) seeded reads still contribute and decay cancels out —
this measures *position accuracy*, not freshness.

Modes (combine freely; default ``--run``):
  --run            estimate + write computed rows + report estimated-vs-placed.
  --set-strategy   write ``tenants.position_strategy`` so the live worker runs
                   for this tenant when ``POSITION_ESTIMATOR_ENABLED`` is on.
  --clean          delete the ``floorval`` seed rows (site/readers/assets/reads).
"""

from __future__ import annotations

import argparse
import asyncio
import math
import uuid
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text

from tagpulse.core.context import tenant_context
from tagpulse.repositories.timescaledb.floor_position_source import (
    TimescaleObservationSource,
    TimescalePositionWriter,
)
from tagpulse.services.floor_position_estimator import FloorPositionEstimatorService
from tagpulse.services.positioning import PositionStrategy

SITE_NAME = "Floor-Positioning Demo"


def _resolve_tenant(args: argparse.Namespace) -> UUID:
    if args.tenant_id:
        return UUID(args.tenant_id)
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"{args.tenant_slug}.tagpulse.local")


class _OneTenant:
    """A StrategySource that yields exactly one (tenant, config) pair."""

    def __init__(self, tenant_id: UUID, config: PositionStrategy) -> None:
        self._items = [(tenant_id, config)]

    async def tenants_with_strategy(self) -> list[tuple[UUID, PositionStrategy]]:
        return self._items


async def _run(tenant_id: UUID, config: PositionStrategy) -> int:
    """Run the real orchestration for one tenant; returns fixes written."""
    service = FloorPositionEstimatorService(
        observations=TimescaleObservationSource(),
        writer=TimescalePositionWriter(),
        strategies=_OneTenant(tenant_id, config),
    )
    return await service.run_once(datetime.now(UTC))


async def _report(tenant_id: UUID) -> None:
    """Compare the latest computed fix per asset to its placed ground truth."""
    async with tenant_context(tenant_id) as session:
        placed_rows = (
            await session.execute(
                text(
                    "SELECT id, name, metadata FROM assets "
                    "WHERE tenant_id = :t AND name LIKE 'asset-%' ORDER BY name"
                ),
                {"t": str(tenant_id)},
            )
        ).all()
        computed_rows = (
            await session.execute(
                text(
                    "SELECT DISTINCT ON (asset_id) asset_id, x, y, confidence "
                    "FROM asset_positions WHERE tenant_id = :t AND source = 'computed' "
                    "ORDER BY asset_id, time DESC"
                ),
                {"t": str(tenant_id)},
            )
        ).all()

    computed = {r.asset_id: (float(r.x), float(r.y), float(r.confidence)) for r in computed_rows}
    print(f"{'asset':<12} {'placed (x,y)':<18} {'computed (x,y)':<18} {'err_m':>8} {'conf':>5}")
    errors: list[float] = []
    for row in placed_rows:
        md = row.metadata or {}
        px, py = md.get("placed_x"), md.get("placed_y")
        placed = f"({px}, {py})" if px is not None else "(no ground truth)"
        fix = computed.get(row.id)
        if fix is None or px is None:
            print(f"{row.name:<12} {placed:<18} {'(no fix)':<18} {'-':>8} {'-':>5}")
            continue
        cx, cy, conf = fix
        err = math.hypot(cx - float(px), cy - float(py))
        errors.append(err)
        print(f"{row.name:<12} {placed:<18} {f'({cx:.1f}, {cy:.1f})':<18} {err:>8.2f} {conf:>5.2f}")

    if errors:
        rmse = math.sqrt(sum(e * e for e in errors) / len(errors))
        print(
            f"\ncomputed fixes={len(errors)}/{len(placed_rows)}  "
            f"mean_err={sum(errors) / len(errors):.2f}m  RMSE={rmse:.2f}m  max={max(errors):.2f}m"
        )
    else:
        print("\nNo computed fixes matched placed assets — seed first, or widen --lookback-s.")


async def _set_strategy(tenant_id: UUID, config: PositionStrategy) -> None:
    async with tenant_context(tenant_id) as session:
        await session.execute(
            text("UPDATE tenants SET position_strategy = CAST(:s AS jsonb) WHERE id = :t"),
            {"s": config.model_dump_json(), "t": str(tenant_id)},
        )
    print(f"position_strategy set for {tenant_id}: {config.model_dump_json()}")


async def _clean(tenant_id: UUID) -> None:
    async with tenant_context(tenant_id) as session:
        t = {"t": str(tenant_id)}
        # FK-safe order; tag_reads / asset_positions are hypertables (no FKs in).
        for stmt in (
            "DELETE FROM asset_positions WHERE tenant_id = :t AND asset_id IN "
            "(SELECT id FROM assets WHERE tenant_id = :t AND name LIKE 'asset-%')",
            "DELETE FROM tag_reads WHERE tenant_id = :t AND device_id IN "
            "(SELECT id FROM devices WHERE tenant_id = :t AND name LIKE 'reader-%')",
            "DELETE FROM asset_tag_bindings WHERE tenant_id = :t AND asset_id IN "
            "(SELECT id FROM assets WHERE tenant_id = :t AND name LIKE 'asset-%')",
            "DELETE FROM antennas WHERE device_id IN "
            "(SELECT id FROM devices WHERE tenant_id = :t AND name LIKE 'reader-%')",
            "DELETE FROM assets WHERE tenant_id = :t AND name LIKE 'asset-%'",
            "DELETE FROM devices WHERE tenant_id = :t AND name LIKE 'reader-%'",
            "DELETE FROM sites WHERE tenant_id = :t AND name = :site",
        ):
            await session.execute(text(stmt), {**t, "site": SITE_NAME})
    print(f"cleaned floorval seed rows for {tenant_id}")


async def _main(args: argparse.Namespace) -> int:
    tenant_id = _resolve_tenant(args)
    config = PositionStrategy(
        half_life_s=args.half_life_s,
        recompute_interval_s=args.recompute_interval_s,
        lookback_s=args.lookback_s,
        min_antennas=args.min_antennas,
        rssi_floor_dbm=args.rssi_floor_dbm,
    )
    print(f"tenant={tenant_id}  config={config.model_dump_json()}")

    if args.clean:
        await _clean(tenant_id)
        return 0
    if args.set_strategy:
        await _set_strategy(tenant_id, config)
    if args.run or not args.set_strategy:
        written = await _run(tenant_id, config)
        print(f"\nestimator wrote {written} computed fix(es); reporting accuracy:\n")
        await _report(tenant_id)
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--tenant-id")
    g.add_argument("--tenant-slug", default="demo-wm-dc")
    p.add_argument("--run", action="store_true", help="Estimate + report (default).")
    p.add_argument("--set-strategy", action="store_true", help="Persist tenants.position_strategy.")
    p.add_argument("--clean", action="store_true", help="Delete the floorval seed rows.")
    p.add_argument("--half-life-s", type=float, default=1.0e9)
    p.add_argument("--recompute-interval-s", type=float, default=3.0)
    p.add_argument(
        "--lookback-s", type=float, default=86400.0, help="Wide, so old seed reads count."
    )
    p.add_argument("--min-antennas", type=int, default=1)
    p.add_argument("--rssi-floor-dbm", type=float, default=-127.0)
    return p.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(_parse_args())))
