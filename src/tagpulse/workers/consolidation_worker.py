"""Asset-state consolidation worker (Sprint 71, ADR-034).

The server-side recompute tick (the [ADR-024] "Option C" pattern, generalised
from floor ``(x, y)`` to the whole asset state). Every ``recompute_interval_s``
it consolidates, per opted-in tenant, each asset's bound-tag reads over the
``lookback_s`` window into one fused snapshot (zone vote + environment mean) and
appends it to ``asset_state_history``. When an asset's fused ``frame`` changes
between ticks it emits a ``Topic.ASSET_CUSTODY_CHANGED`` custody event.

Gated **off** by default (``settings.consolidation_enabled``) — the DB adapters
need integration validation on a real tenant before the loop writes in
production. Pure fusion lives in :mod:`tagpulse.services.consolidation`; the
read/zone resolution in
:mod:`tagpulse.repositories.timescaledb.consolidation_source`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tagpulse.core.context import tenant_context
from tagpulse.events.protocol import Event, EventBus, Topic
from tagpulse.models.database import AssetStateHistoryModel
from tagpulse.repositories.timescaledb.consolidation_source import (
    TimescaleConsolidationReadSource,
    TimescaleFusionStrategySource,
)
from tagpulse.services.consolidation import AssetStateSnapshot, FusionStrategy, consolidate

logger = logging.getLogger(__name__)


class AssetConsolidationWorker:
    """Periodic per-asset consolidation tick → ``asset_state_history``."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        strategies: TimescaleFusionStrategySource | None = None,
        reads: TimescaleConsolidationReadSource | None = None,
        event_bus: EventBus | None = None,
        interval_s: float = 10.0,
    ) -> None:
        self._session_factory = session_factory
        self._strategies = strategies or TimescaleFusionStrategySource(session_factory)
        self._reads = reads or TimescaleConsolidationReadSource()
        self._event_bus = event_bus
        self._interval = interval_s
        self._task: asyncio.Task[None] | None = None
        # (tenant_id, asset_id) -> last fused (frame, zone_id, site_id), for
        # custody-change detection + carrying the origin facility on the event.
        self._last_state: dict[tuple[UUID, UUID], tuple[str, UUID | None, UUID | None]] = {}

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("AssetConsolidationWorker started (interval=%.0fs)", self._interval)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("AssetConsolidationWorker stopped")

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:  # pragma: no cover - defensive
                logger.exception("AssetConsolidationWorker scan failed")
            await asyncio.sleep(self._interval)

    async def run_once(self) -> int:
        """Consolidate every opted-in tenant once. Returns snapshots written."""
        now = datetime.now(UTC)
        written = 0
        for tenant_id, config in await self._strategies.tenants_with_strategy():
            written += await self._run_tenant(tenant_id, config, now)
        return written

    async def _run_tenant(self, tenant_id: UUID, config: FusionStrategy, now: datetime) -> int:
        window_start = now - timedelta(seconds=config.lookback_s)
        by_asset = await self._reads.resolved_reads(
            tenant_id, window_start=window_start, window_end=now
        )
        if not by_asset:
            return 0
        snapshots = [
            snap
            for asset_id, reads in by_asset.items()
            if (snap := consolidate(reads, asset_id=asset_id, now=now, config=config)) is not None
        ]
        if not snapshots:
            return 0
        await self._persist(tenant_id, snapshots)
        await self._emit_custody(tenant_id, snapshots)
        return len(snapshots)

    async def _persist(self, tenant_id: UUID, snapshots: list[AssetStateSnapshot]) -> None:
        async with tenant_context(tenant_id) as session:
            session.add_all(
                [
                    AssetStateHistoryModel(
                        time=snap.time,
                        tenant_id=tenant_id,
                        asset_id=snap.asset_id,
                        frame=snap.frame,
                        zone_id=snap.zone_id,
                        site_id=snap.site_id,
                        lat=snap.lat,
                        lon=snap.lon,
                        x=snap.x,
                        y=snap.y,
                        temperature_c=snap.temperature_c,
                        humidity_pct=snap.humidity_pct,
                        sample_count=snap.sample_count,
                        tag_count=snap.tag_count,
                        confidence=snap.confidence,
                    )
                    for snap in snapshots
                ]
            )
            await session.commit()

    async def _emit_custody(self, tenant_id: UUID, snapshots: list[AssetStateSnapshot]) -> None:
        for snap in snapshots:
            key = (tenant_id, snap.asset_id)
            prev = self._last_state.get(key)
            self._last_state[key] = (snap.frame, snap.zone_id, snap.site_id)
            if prev is None or prev[0] == snap.frame:
                continue  # first-seen (no spurious event) or unchanged.
            if self._event_bus is None:
                continue
            from_frame, from_zone_id, from_site_id = prev
            await self._event_bus.publish(
                Topic.ASSET_CUSTODY_CHANGED,
                Event(
                    id=uuid4(),
                    topic=Topic.ASSET_CUSTODY_CHANGED,
                    timestamp=snap.time,
                    payload={
                        "tenant_id": str(tenant_id),
                        "asset_id": str(snap.asset_id),
                        "from_frame": from_frame,
                        "to_frame": snap.frame,
                        "from_zone_id": str(from_zone_id) if from_zone_id else None,
                        "from_site_id": str(from_site_id) if from_site_id else None,
                        "zone_id": str(snap.zone_id) if snap.zone_id else None,
                        "site_id": str(snap.site_id) if snap.site_id else None,
                        "confidence": snap.confidence,
                        "timestamp": snap.time.isoformat(),
                    },
                ),
            )
