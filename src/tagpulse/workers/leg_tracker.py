"""Asset transit-leg tracker (Sprint 72, ADR-034 Phase 2).

A **stateless** subscriber to ``Topic.ASSET_CUSTODY_CHANGED`` (emitted by the
Phase-1 consolidation worker). A *leg* is the ``geo``-frame interval between two
facility frames:

- ``facility → geo`` (departure) **opens** a leg, recording the origin facility
  (carried as ``from_zone_id``/``from_site_id`` on the event) + ``departed_at``.
- ``… → facility`` (arrival) **closes** the open leg: records the destination,
  ``arrived_at``, and the env envelope + cold-chain SLA computed from
  ``asset_state_history`` over the leg window (per the tenant's
  ``fusion_strategy.sla``).

The ``asset_legs`` table *is* the state (one open leg per asset, partial-unique),
so there is no in-memory map to hydrate — each event is a small DB op. Gated with
the rest of consolidation (``consolidation_enabled``).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tagpulse.core.context import tenant_context
from tagpulse.events.protocol import Event
from tagpulse.models.database import AssetLegModel, AssetStateHistoryModel, TenantModel
from tagpulse.services.consolidation import FusionStrategy
from tagpulse.services.legs import EnvSample, summarize_leg_env

logger = logging.getLogger(__name__)

_FACILITY_FRAMES: frozenset[str] = frozenset({"reader", "floor"})


def _parse_ts(raw: object) -> datetime:
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return datetime.now(UTC)


def _uuid_or_none(raw: object) -> UUID | None:
    return UUID(raw) if isinstance(raw, str) and raw else None


class AssetLegTracker:
    """Opens/closes ``asset_legs`` from custody events. Subscribe to ASSET_CUSTODY_CHANGED."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def on_custody_changed(self, event: Event) -> None:
        payload = event.payload
        tenant_raw = payload.get("tenant_id")
        asset_raw = payload.get("asset_id")
        if not tenant_raw or not asset_raw:
            return
        tenant_id = UUID(str(tenant_raw))
        asset_id = UUID(str(asset_raw))
        to_frame = payload.get("to_frame")
        from_frame = payload.get("from_frame")
        ts = _parse_ts(payload.get("timestamp"))
        try:
            if to_frame == "geo" and from_frame in _FACILITY_FRAMES:
                await self._open_leg(
                    tenant_id,
                    asset_id,
                    origin_zone_id=_uuid_or_none(payload.get("from_zone_id")),
                    origin_site_id=_uuid_or_none(payload.get("from_site_id")),
                    departed_at=ts,
                )
            elif to_frame in _FACILITY_FRAMES:
                await self._close_leg(
                    tenant_id,
                    asset_id,
                    dest_zone_id=_uuid_or_none(payload.get("zone_id")),
                    dest_site_id=_uuid_or_none(payload.get("site_id")),
                    arrived_at=ts,
                )
            # to_frame == 'none' (ambiguous) → neither opens nor closes.
        except Exception:  # pragma: no cover - defensive
            logger.exception("AssetLegTracker failed for asset %s", asset_id)

    async def _open_leg(
        self,
        tenant_id: UUID,
        asset_id: UUID,
        *,
        origin_zone_id: UUID | None,
        origin_site_id: UUID | None,
        departed_at: datetime,
    ) -> None:
        async with tenant_context(tenant_id) as session:
            # Safety net: close any stale open leg so the partial-unique index holds.
            await session.execute(
                text(
                    "UPDATE asset_legs SET status='closed', arrived_at=:t "
                    "WHERE tenant_id=:tn AND asset_id=:a AND status='open'"
                ),
                {"t": departed_at, "tn": str(tenant_id), "a": str(asset_id)},
            )
            session.add(
                AssetLegModel(
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    status="open",
                    origin_zone_id=origin_zone_id,
                    origin_site_id=origin_site_id,
                    departed_at=departed_at,
                )
            )
            await session.commit()

    async def _close_leg(
        self,
        tenant_id: UUID,
        asset_id: UUID,
        *,
        dest_zone_id: UUID | None,
        dest_site_id: UUID | None,
        arrived_at: datetime,
    ) -> None:
        async with tenant_context(tenant_id) as session:
            leg = (
                (
                    await session.execute(
                        select(AssetLegModel)
                        .where(
                            AssetLegModel.tenant_id == tenant_id,
                            AssetLegModel.asset_id == asset_id,
                            AssetLegModel.status == "open",
                        )
                        .order_by(AssetLegModel.departed_at.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if leg is None:
                return  # no open leg (asset started mid-journey) — nothing to close.

            rows = (
                await session.execute(
                    select(
                        AssetStateHistoryModel.time,
                        AssetStateHistoryModel.temperature_c,
                        AssetStateHistoryModel.humidity_pct,
                    )
                    .where(
                        AssetStateHistoryModel.tenant_id == tenant_id,
                        AssetStateHistoryModel.asset_id == asset_id,
                        AssetStateHistoryModel.time >= leg.departed_at,
                        AssetStateHistoryModel.time <= arrived_at,
                    )
                    .order_by(AssetStateHistoryModel.time.asc())
                )
            ).all()
            samples = [
                EnvSample(time=r.time, temperature_c=r.temperature_c, humidity_pct=r.humidity_pct)
                for r in rows
            ]
            sla = await self._tenant_sla(session, tenant_id)
            summary = summarize_leg_env(samples, sla)

            leg.status = "closed"
            leg.dest_zone_id = dest_zone_id
            leg.dest_site_id = dest_site_id
            leg.arrived_at = arrived_at
            leg.temp_min_c = summary.temp_min_c
            leg.temp_max_c = summary.temp_max_c
            leg.temp_mean_c = summary.temp_mean_c
            leg.humidity_min = summary.humidity_min
            leg.humidity_max = summary.humidity_max
            leg.excursion_s = summary.excursion_s
            leg.in_range_pct = summary.in_range_pct
            leg.sla_breached = summary.sla_breached
            await session.commit()

    @staticmethod
    async def _tenant_sla(session: AsyncSession, tenant_id: UUID):  # type: ignore[no-untyped-def]
        raw = (
            await session.execute(
                select(TenantModel.fusion_strategy).where(TenantModel.id == tenant_id)
            )
        ).scalar_one_or_none()
        if not raw:
            return None
        try:
            return FusionStrategy.model_validate(raw).sla
        except ValidationError:
            return None
