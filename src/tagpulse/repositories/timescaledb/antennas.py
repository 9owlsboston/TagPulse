"""Antenna position repository (Sprint 64 / ADR-024).

Per-antenna ``(x, y, z)`` within a device's site coordinate frame. Port 0 is
the reader's nominal location; ports 1..N are individual radiators.

The ``antennas`` table carries no ``tenant_id`` of its own — isolation flows
through the ``device_id`` FK (devices are tenant-scoped). Every method therefore
verifies device ownership before touching antenna rows. A ``None`` return means
"device not found for this tenant" (→ 404); a ``bool`` distinguishes the
antenna-port outcome.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import AntennaModel, DeviceModel
from tagpulse.models.schemas import AntennaResponse, AntennaUpsert


def _to_decimal(value: float | None) -> Decimal | None:
    # str() keeps the value exact (avoids binary float artifacts) for Numeric.
    return Decimal(str(value)) if value is not None else None


def _to_response(row: AntennaModel) -> AntennaResponse:
    return AntennaResponse(
        id=row.id,
        device_id=row.device_id,
        port=row.port,
        x=float(row.x) if row.x is not None else None,
        y=float(row.y) if row.y is not None else None,
        z=float(row.z) if row.z is not None else None,
        label=row.label,
        gain_dbi=float(row.gain_dbi) if row.gain_dbi is not None else None,
    )


class TimescaleAntennaRepository:
    """Persists per-antenna positions, scoped to tenant-owned devices."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _device_owned(self, tenant_id: uuid.UUID, device_id: uuid.UUID) -> bool:
        stmt = select(DeviceModel.id).where(
            DeviceModel.id == device_id, DeviceModel.tenant_id == tenant_id
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def list_for_device(
        self, tenant_id: uuid.UUID, device_id: uuid.UUID
    ) -> list[AntennaResponse] | None:
        """List a device's antennas ordered by port, or ``None`` if not owned."""
        if not await self._device_owned(tenant_id, device_id):
            return None
        stmt = (
            select(AntennaModel)
            .where(AntennaModel.device_id == device_id)
            .order_by(AntennaModel.port)
        )
        result = await self._session.execute(stmt)
        return [_to_response(row) for row in result.scalars()]

    async def upsert(
        self,
        tenant_id: uuid.UUID,
        device_id: uuid.UUID,
        port: int,
        payload: AntennaUpsert,
    ) -> AntennaResponse | None:
        """Create or update the antenna at ``port``; ``None`` if device not owned."""
        if not await self._device_owned(tenant_id, device_id):
            return None
        stmt = select(AntennaModel).where(
            AntennaModel.device_id == device_id, AntennaModel.port == port
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            row = AntennaModel(id=uuid.uuid4(), device_id=device_id, port=port)
            self._session.add(row)
        row.x = _to_decimal(payload.x)
        row.y = _to_decimal(payload.y)
        row.z = _to_decimal(payload.z)
        row.label = payload.label
        row.gain_dbi = _to_decimal(payload.gain_dbi)
        await self._session.flush()
        return _to_response(row)

    async def delete(self, tenant_id: uuid.UUID, device_id: uuid.UUID, port: int) -> bool | None:
        """Delete the antenna at ``port``.

        Returns ``None`` if the device is not owned (→ 404 device), ``False`` if
        the port has no antenna (→ 404 antenna), ``True`` on delete.
        """
        if not await self._device_owned(tenant_id, device_id):
            return None
        stmt = select(AntennaModel).where(
            AntennaModel.device_id == device_id, AntennaModel.port == port
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True
