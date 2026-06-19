"""TimescaleDB adapters for the floor-position estimation pipeline (Sprint 66).

Concrete implementations of the three ports in
:mod:`tagpulse.services.floor_position_estimator`. The pure mapping helpers
(:func:`resolve_antenna_xy`, :func:`build_floor_observations`) are extracted and
unit-tested; the thin session/SQL wrappers around them are exercised by
integration tests before the worker is enabled (it is gated **off** by default —
see ``settings.position_estimator_enabled``).

EPC→asset matching uses ``tag_reads.epc`` (the decoded EPC), matching the
canonical ``binding_kind='epc' AND tr.epc = b.binding_value`` join used by
``asset_current_location`` — *not* ``epc_hex`` (cf. the inventory gate bug in
``docs/backlog.md``).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tagpulse.core.context import tenant_context
from tagpulse.models.database import (
    AntennaModel,
    DeviceModel,
    SiteModel,
    TagReadModel,
    TenantModel,
)
from tagpulse.models.schemas import FloorPositionCreate
from tagpulse.repositories.timescaledb.asset_positions import (
    TimescaleAssetPositionRepository,
)
from tagpulse.repositories.timescaledb.assets import TimescaleAssetTagBindingRepository
from tagpulse.services.asset_fusion import AssetFusionService
from tagpulse.services.floor_position_estimator import FloorObservation
from tagpulse.services.positioning import PositionStrategy

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure mapping helpers (unit-tested)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawRead:
    """A recent floor read, pre-fusion (one row of the reads query)."""

    device_id: UUID
    port: int
    rssi: float
    epc: str
    ts: datetime


# (device_id, port) → (antenna_id, x, y) for *surveyed* antennas only.
AntennaIndex = dict[tuple[UUID, int], tuple[UUID, float, float]]


def resolve_antenna_xy(
    index: AntennaIndex, device_id: UUID, port: int
) -> tuple[UUID, float, float] | None:
    """Resolve a read's ``(device, port)`` to a surveyed antenna position.

    Exact port wins; otherwise fall back to the device's **port-0** nominal
    location (the reader spot). ``None`` when neither is surveyed.
    """
    hit = index.get((device_id, port))
    if hit is not None:
        return hit
    return index.get((device_id, 0))


def build_floor_observations(
    reads: Sequence[RawRead],
    device_site: dict[UUID, UUID],
    antenna_index: AntennaIndex,
    epc_to_asset: dict[str, UUID],
) -> list[FloorObservation]:
    """Join raw reads to asset + antenna position → estimator observations.

    Reads whose EPC resolves to no asset, whose device isn't sited, or whose
    antenna has no surveyed position are dropped.
    """
    out: list[FloorObservation] = []
    for r in reads:
        asset_id = epc_to_asset.get(r.epc)
        if asset_id is None:
            continue
        site_id = device_site.get(r.device_id)
        if site_id is None:
            continue
        resolved = resolve_antenna_xy(antenna_index, r.device_id, r.port)
        if resolved is None:
            continue
        antenna_id, x, y = resolved
        out.append(
            FloorObservation(
                site_id=site_id,
                asset_id=asset_id,
                antenna_id=antenna_id,
                x=x,
                y=y,
                rssi=r.rssi,
                cnt=1,
                ts=r.ts,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Concrete adapters
# ---------------------------------------------------------------------------


class TimescaleStrategySource:
    """Lists tenants that have opted into estimation (non-null ``position_strategy``)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def tenants_with_strategy(self) -> list[tuple[UUID, PositionStrategy]]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(TenantModel.id, TenantModel.position_strategy).where(
                        TenantModel.position_strategy.isnot(None)
                    )
                )
            ).all()
        out: list[tuple[UUID, PositionStrategy]] = []
        for tenant_id, raw in rows:
            try:
                out.append((tenant_id, PositionStrategy.model_validate(raw)))
            except ValidationError:
                logger.warning("Tenant %s has an invalid position_strategy; skipping", tenant_id)
        return out


class TimescaleObservationSource:
    """Fetches recent floor observations for a tenant (RLS-scoped per call)."""

    async def recent_observations(
        self, tenant_id: UUID, *, since: datetime
    ) -> list[FloorObservation]:
        async with tenant_context(tenant_id) as session:
            floor_site_ids = (
                (
                    await session.execute(
                        select(SiteModel.id).where(
                            SiteModel.tenant_id == tenant_id,
                            SiteModel.coord_system.isnot(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if not floor_site_ids:
                return []

            device_rows = (
                await session.execute(
                    select(DeviceModel.id, DeviceModel.site_id).where(
                        DeviceModel.tenant_id == tenant_id,
                        DeviceModel.mobility == "fixed",
                        DeviceModel.site_id.in_(floor_site_ids),
                    )
                )
            ).all()
            device_site = {d_id: s_id for d_id, s_id in device_rows}
            if not device_site:
                return []
            device_ids = list(device_site)

            antenna_index: AntennaIndex = {}
            for ant_id, dev_id, port, x, y in (
                await session.execute(
                    select(
                        AntennaModel.id,
                        AntennaModel.device_id,
                        AntennaModel.port,
                        AntennaModel.x,
                        AntennaModel.y,
                    ).where(AntennaModel.device_id.in_(device_ids))
                )
            ).all():
                if x is not None and y is not None:
                    antenna_index[(dev_id, port)] = (ant_id, float(x), float(y))
            if not antenna_index:
                return []

            read_rows = (
                await session.execute(
                    select(
                        TagReadModel.device_id,
                        TagReadModel.reader_antenna,
                        TagReadModel.signal_strength,
                        TagReadModel.epc,
                        TagReadModel.timestamp,
                    ).where(
                        TagReadModel.tenant_id == tenant_id,
                        TagReadModel.device_id.in_(device_ids),
                        TagReadModel.timestamp >= since,
                        TagReadModel.epc.isnot(None),
                        TagReadModel.signal_strength.isnot(None),
                        TagReadModel.reader_antenna.isnot(None),
                    )
                )
            ).all()
            if not read_rows:
                return []

            reads = [
                RawRead(
                    device_id=dev_id,
                    port=port,
                    rssi=float(rssi),
                    epc=epc,
                    ts=ts,
                )
                for dev_id, port, rssi, epc, ts in read_rows
            ]

            fusion = AssetFusionService(TimescaleAssetTagBindingRepository(session))
            fused = await fusion.fuse(tenant_id, [r.epc for r in reads])
            epc_to_asset: dict[str, UUID] = {}
            for fa in fused:
                for epc in fa.observed_epcs:
                    epc_to_asset[epc] = fa.asset_id

        return build_floor_observations(reads, device_site, antenna_index, epc_to_asset)


class TimescalePositionWriter:
    """Writes one computed floor fix (RLS-scoped per call), reusing Sprint 65's repo."""

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
    ) -> None:
        async with tenant_context(tenant_id) as session:
            repo = TimescaleAssetPositionRepository(session)
            await repo.insert(
                tenant_id,
                asset_id,
                recorded_at=recorded_at,
                position=FloorPositionCreate(
                    site_id=site_id,
                    x=x,
                    y=y,
                    confidence=confidence,
                    metadata=metadata,
                ),
                source="computed",
            )


__all__ = [
    "AntennaIndex",
    "RawRead",
    "TimescaleObservationSource",
    "TimescalePositionWriter",
    "TimescaleStrategySource",
    "build_floor_observations",
    "resolve_antenna_xy",
]
