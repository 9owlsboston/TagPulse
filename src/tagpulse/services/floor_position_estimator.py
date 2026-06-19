"""Floor-position estimation pipeline — orchestration (Sprint 66, Phase 2).

Wires the pure :func:`tagpulse.services.positioning.rssi_weighted_centroid`
estimator to data via small **ports** (Protocols), so the orchestration logic is
unit-testable with fakes and the DB adapters can land (and be integration-tested)
in a follow-on slice without touching this logic.

Emit model = **Option C** (see ``docs/design/floor-position-estimation.md``):
a server-side recompute over a lookback window, keyed on **server ingest time**.
This module computes; the ``FloorPositionWorker`` drives it on a cadence.

Scope of this slice: orchestration + ports + tests only. The concrete
TimescaleDB observation source / position writer / strategy source and the worker
registration are the next slice (gated off by default until validated).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from tagpulse.services.positioning import (
    AntennaObservation,
    PositionFix,
    PositionStrategy,
    rssi_weighted_centroid,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FloorObservation:
    """One asset-on-antenna read within a floor site, ready to estimate from.

    Produced by an :class:`ObservationSource` (the EPC→asset fusion and the
    ``(device, port) → antenna (x, y)`` resolution have already happened).
    """

    site_id: UUID
    asset_id: UUID
    antenna_id: UUID
    x: float
    y: float
    rssi: float
    cnt: int
    ts: datetime


class ObservationSource(Protocol):
    """Supplies recent floor observations for a tenant within a time window."""

    async def recent_observations(
        self, tenant_id: UUID, *, since: datetime
    ) -> Sequence[FloorObservation]: ...


class PositionWriter(Protocol):
    """Persists a computed floor fix (``asset_positions`` ``source='computed'``)."""

    async def insert_computed(
        self,
        tenant_id: UUID,
        asset_id: UUID,
        *,
        site_id: UUID,
        recorded_at: datetime,
        x: float,
        y: float,
        confidence: float,
        metadata: dict[str, object] | None = None,
    ) -> None: ...


class StrategySource(Protocol):
    """Yields the tenants that have opted into estimation + their config."""

    async def tenants_with_strategy(self) -> Sequence[tuple[UUID, PositionStrategy]]: ...


class FloorPositionEstimatorService:
    """Recompute computed floor fixes for all opted-in tenants (Option C)."""

    def __init__(
        self,
        observations: ObservationSource,
        writer: PositionWriter,
        strategies: StrategySource,
    ) -> None:
        self._observations = observations
        self._writer = writer
        self._strategies = strategies

    async def run_once(self, now: datetime) -> int:
        """One recompute pass. Returns the number of computed fixes written."""
        written = 0
        for tenant_id, config in await self._strategies.tenants_with_strategy():
            written += await self._run_tenant(tenant_id, config, now)
        return written

    async def _run_tenant(self, tenant_id: UUID, config: PositionStrategy, now: datetime) -> int:
        since = now - timedelta(seconds=config.lookback_s)
        observations = await self._observations.recent_observations(tenant_id, since=since)

        # Group by (site, asset) — a fix is per asset within one floor frame.
        grouped: dict[tuple[UUID, UUID], list[FloorObservation]] = defaultdict(list)
        for obs in observations:
            grouped[(obs.site_id, obs.asset_id)].append(obs)

        written = 0
        for (site_id, asset_id), group in grouped.items():
            fix = self._estimate(group, now=now, config=config)
            if fix is None:
                continue
            await self._writer.insert_computed(
                tenant_id,
                asset_id,
                site_id=site_id,
                recorded_at=now,
                x=fix.x,
                y=fix.y,
                confidence=fix.confidence,
                metadata={
                    "strategy": config.strategy,
                    "antennas": fix.antenna_count,
                    "half_life_s": config.half_life_s,
                },
            )
            written += 1
        return written

    @staticmethod
    def _estimate(
        group: Sequence[FloorObservation], *, now: datetime, config: PositionStrategy
    ) -> PositionFix | None:
        antenna_obs = [
            AntennaObservation(
                antenna_id=o.antenna_id, x=o.x, y=o.y, rssi=o.rssi, cnt=o.cnt, ts=o.ts
            )
            for o in group
        ]
        return rssi_weighted_centroid(antenna_obs, now=now, config=config)
