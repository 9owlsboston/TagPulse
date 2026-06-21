"""TimescaleDB source for the asset-state consolidation worker (Sprint 71, ADR-034).

Materialises one asset's bound-tag reads over the look-back window and resolves
each read to a ``(frame, zone)`` + carries its sensor payload, producing the
:class:`tagpulse.services.consolidation.ResolvedRead` rows the pure
:func:`tagpulse.services.consolidation.consolidate` core fuses.

Layering mirrors :mod:`tagpulse.repositories.timescaledb.floor_position_source`:

- :func:`build_resolved_reads` — **pure**. Raw rows + zones → resolved reads
  grouped by asset. Reuses the established reader-bound / geofence zone matcher
  from :mod:`tagpulse.signaling.overlapping_zones` so resolution stays consistent
  with the attribution engine. Unit-tested from a hand-built fixture.
- :class:`TimescaleFusionStrategySource` / :class:`TimescaleConsolidationReadSource`
  — thin session/SQL wrappers exercised by integration tests before the worker is
  enabled (gated **off** by default — ``settings.consolidation_enabled``).

EPC→asset matching uses the ADR-033 dual-form join (``tr.epc`` OR ``tr.epc_hex``),
matching every live read→binding surface.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tagpulse.core.context import tenant_context
from tagpulse.models.database import TenantModel, ZoneModel
from tagpulse.services.consolidation import Frame, FusionStrategy, ResolvedRead
from tagpulse.signaling.isolated_zones import ZoneCandidate
from tagpulse.signaling.overlapping_zones import (
    AttributionRead,
    _attributable_zones_for_read,
)

logger = logging.getLogger(__name__)

__all__ = [
    "RawConsolidationRead",
    "TimescaleConsolidationReadSource",
    "TimescaleFusionStrategySource",
    "build_resolved_reads",
]


@dataclass(frozen=True)
class RawConsolidationRead:
    """One ``tag_reads`` ⋈ ``asset_tag_bindings`` row, pre-resolution."""

    asset_id: UUID
    tag_key: str
    reader_id: UUID
    ts: datetime
    rssi: float | None
    lat: float | None
    lon: float | None
    sensor_data: dict[str, Any] | None


def _num(sensor_data: dict[str, Any] | None, key: str) -> float | None:
    """Read a numeric ``key`` from ``sensor_data`` (``None`` if absent/non-numeric)."""
    if not sensor_data:
        return None
    v = sensor_data.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _read_count(sensor_data: dict[str, Any] | None) -> int:
    """Positive integer ``read_count`` from ``sensor_data`` (default 1)."""
    v = _num(sensor_data, "read_count")
    if v is None:
        return 1
    n = int(v)
    return n if n >= 1 else 1


def _resolve_one(
    raw: RawConsolidationRead,
    zones: Sequence[ZoneCandidate],
    site_by_zone: dict[UUID, UUID],
) -> ResolvedRead:
    """Resolve one raw read to a single ``(frame, zone)`` + carry its sensors.

    Reader-bound zone match wins (frame ``reader``); else a geofence the GPS fix
    falls inside (frame ``geo`` + that zone); else a bare GPS fix is ``geo`` with
    no zone ("in transit"); else ``none``. One resolved read per raw read so the
    environment mean never double-counts a single read.
    """
    matched = _attributable_zones_for_read(
        AttributionRead(
            asset_id=raw.asset_id,
            reader_id=raw.reader_id,
            timestamp=raw.ts,
            signal_strength=raw.rssi,
            latitude=raw.lat,
            longitude=raw.lon,
        ),
        zones,
    )
    frame: Frame = "none"
    zone_id: UUID | None = None
    site_id: UUID | None = None
    if matched:
        # Prefer a reader-bound match; fall back to the oldest geofence.
        reader_zones = [z for z in matched if z.kind == "reader_bound"]
        chosen = reader_zones[0] if reader_zones else matched[0]
        zone_id = chosen.id
        site_id = site_by_zone.get(chosen.id)
        frame = "reader" if chosen.kind == "reader_bound" else "geo"
    elif raw.lat is not None and raw.lon is not None:
        frame = "geo"  # GPS fix outside every geofence — in transit.

    return ResolvedRead(
        asset_id=raw.asset_id,
        tag_key=raw.tag_key,
        ts=raw.ts,
        read_count=_read_count(raw.sensor_data),
        rssi=raw.rssi,
        frame=frame,
        zone_id=zone_id,
        site_id=site_id,
        lat=raw.lat,
        lon=raw.lon,
        temperature_c=_num(raw.sensor_data, "temperature_c"),
        humidity_pct=_num(raw.sensor_data, "humidity_pct"),
    )


def build_resolved_reads(
    raws: Sequence[RawConsolidationRead],
    zones: Sequence[ZoneCandidate],
    site_by_zone: dict[UUID, UUID],
) -> dict[UUID, list[ResolvedRead]]:
    """Resolve raw reads and group them by ``asset_id``."""
    out: dict[UUID, list[ResolvedRead]] = {}
    for raw in raws:
        out.setdefault(raw.asset_id, []).append(_resolve_one(raw, zones, site_by_zone))
    return out


class TimescaleFusionStrategySource:
    """Lists tenants opted into consolidation (non-null ``fusion_strategy``)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def tenants_with_strategy(self) -> list[tuple[UUID, FusionStrategy]]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(TenantModel.id, TenantModel.fusion_strategy).where(
                        TenantModel.fusion_strategy.isnot(None)
                    )
                )
            ).all()
        out: list[tuple[UUID, FusionStrategy]] = []
        for tenant_id, raw in rows:
            try:
                out.append((tenant_id, FusionStrategy.model_validate(raw)))
            except ValidationError:
                logger.warning("Tenant %s has an invalid fusion_strategy; skipping", tenant_id)
        return out


_READS_SQL = text(
    """
    SELECT
        b.asset_id              AS asset_id,
        b.binding_value         AS tag_key,
        tr.device_id            AS reader_id,
        tr."timestamp"          AS ts,
        tr.signal_strength      AS rssi,
        tr.latitude             AS lat,
        tr.longitude            AS lon,
        tr.sensor_data          AS sensor_data
    FROM tag_reads tr
    JOIN asset_tag_bindings b
      ON b.tenant_id = tr.tenant_id
     AND b.unbound_at IS NULL
     AND (
            (b.binding_kind = 'epc'
             AND (tr.epc = b.binding_value OR tr.epc_hex = b.binding_value)) OR
            (b.binding_kind = 'tid'    AND tr.tid    = b.binding_value) OR
            (b.binding_kind = 'device' AND tr.tag_id = b.binding_value)
         )
    WHERE tr.tenant_id = :tenant_id
      AND tr."timestamp" >= :window_start
      AND tr."timestamp" <= :window_end
    """
)


class TimescaleConsolidationReadSource:
    """Loads resolved reads per asset for a tenant (RLS-scoped per call)."""

    async def resolved_reads(
        self, tenant_id: UUID, *, window_start: datetime, window_end: datetime
    ) -> dict[UUID, list[ResolvedRead]]:
        async with tenant_context(tenant_id) as session:
            raws = await self._load_reads(session, tenant_id, window_start, window_end)
            if not raws:
                return {}
            zones, site_by_zone = await self._load_zones(session, tenant_id)
        return build_resolved_reads(raws, zones, site_by_zone)

    async def _load_reads(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        window_start: datetime,
        window_end: datetime,
    ) -> list[RawConsolidationRead]:
        result = await session.execute(
            _READS_SQL,
            {"tenant_id": tenant_id, "window_start": window_start, "window_end": window_end},
        )
        return [
            RawConsolidationRead(
                asset_id=row.asset_id,
                tag_key=row.tag_key,
                reader_id=row.reader_id,
                ts=row.ts,
                rssi=row.rssi,
                lat=row.lat,
                lon=row.lon,
                sensor_data=row.sensor_data,
            )
            for row in result.all()
        ]

    async def _load_zones(
        self, session: AsyncSession, tenant_id: UUID
    ) -> tuple[list[ZoneCandidate], dict[UUID, UUID]]:
        stmt = (
            select(ZoneModel)
            .where(
                ZoneModel.tenant_id == tenant_id,
                ZoneModel.kind.in_(("reader_bound", "geofence")),
            )
            .order_by(ZoneModel.created_at.asc())
        )
        result = await session.execute(stmt)
        zones: list[ZoneCandidate] = []
        site_by_zone: dict[UUID, UUID] = {}
        for row in result.scalars():
            zones.append(
                ZoneCandidate(
                    id=row.id,
                    kind=row.kind,
                    created_at=row.created_at,
                    fixed_reader_ids=(
                        tuple(str(r) for r in row.fixed_reader_ids)
                        if row.fixed_reader_ids
                        else None
                    ),
                    polygon_geojson=row.polygon_geojson,
                    bbox_min_lat=row.bbox_min_lat,
                    bbox_max_lat=row.bbox_max_lat,
                    bbox_min_lon=row.bbox_min_lon,
                    bbox_max_lon=row.bbox_max_lon,
                )
            )
            site_by_zone[row.id] = row.site_id
        return zones, site_by_zone
