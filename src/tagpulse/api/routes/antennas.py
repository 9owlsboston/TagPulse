"""Antenna placement API (Sprint 64 / ADR-024).

Per-antenna ``(x, y, z)`` within a device's site coordinate frame. **Port 0 is
the reader's nominal location**; ports 1..N are individual radiators. Antennas
are device-scoped (``/devices/{device_id}/antennas``); tenant isolation flows
through the device.

Permissions mirror Sites & Zones: viewer+ read, admin write.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path

from tagpulse.api.dependencies import get_antenna_repo
from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.models.schemas import AntennaResponse, AntennaUpsert
from tagpulse.repositories.timescaledb.antennas import TimescaleAntennaRepository

router = APIRouter(tags=["antennas"])


@router.get("/devices/{device_id}/antennas", response_model=list[AntennaResponse])
async def list_antennas(
    device_id: UUID,
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    repo: TimescaleAntennaRepository = Depends(get_antenna_repo),
) -> list[AntennaResponse]:
    result = await repo.list_for_device(user.tenant_id, device_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return result


@router.put("/devices/{device_id}/antennas/{port}", response_model=AntennaResponse)
async def upsert_antenna(
    device_id: UUID,
    body: AntennaUpsert,
    port: int = Path(ge=0, le=255),
    user: AuthenticatedUser = require_role("admin"),
    repo: TimescaleAntennaRepository = Depends(get_antenna_repo),
) -> AntennaResponse:
    """Create or update the antenna at ``port`` (port 0 = the reader's spot)."""
    result = await repo.upsert(user.tenant_id, device_id, port, body)
    if result is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return result


@router.delete("/devices/{device_id}/antennas/{port}", status_code=204)
async def delete_antenna(
    device_id: UUID,
    port: int = Path(ge=0, le=255),
    user: AuthenticatedUser = require_role("admin"),
    repo: TimescaleAntennaRepository = Depends(get_antenna_repo),
) -> None:
    result = await repo.delete(user.tenant_id, device_id, port)
    if result is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if result is False:
        raise HTTPException(status_code=404, detail="Antenna not found")
    return None
