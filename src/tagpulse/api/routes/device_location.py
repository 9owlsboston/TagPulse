"""Device-self external location endpoint (Sprint 80, I-9HQA).

A gateway/device principal (``tpd_`` token, I-K6D1) stamps its **own**
``device`` position. Mirrors the gateway telemetry ingest (I-75YC): a device
may write only its own subject, and ``recorded_at`` is clock-validated. No
event is emitted in this MVE (the asset external-location event contract is
left untouched — see docs/design/external-locations-subject-kinds.md §5).
"""

from fastapi import APIRouter, Depends, HTTPException

from tagpulse.api.dependencies import get_external_location_repo
from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.ingestion.clock import check_clock_window
from tagpulse.models.schemas import ExternalLocationCreate, ExternalLocationResponse
from tagpulse.repositories.timescaledb.external_locations import (
    TimescaleExternalLocationRepository,
)

router = APIRouter(tags=["device-location"])


@router.post("/device-location", response_model=ExternalLocationResponse, status_code=201)
async def record_device_location(
    body: ExternalLocationCreate,
    user: AuthenticatedUser = require_role("device"),
    repo: TimescaleExternalLocationRepository = Depends(get_external_location_repo),
) -> ExternalLocationResponse:
    """Record the calling device's own external position (device principals only).

    The subject is fixed to ``('device', <this device>)`` — a device cannot
    stamp another subject's location. ``recorded_at`` must fall inside the
    ingest clock window (``check_clock_window``).
    """
    if user.device_id is None:
        # Defensive: a ``device`` role always carries device_id (I-K6D1), but
        # narrow the type + fail closed if that ever changes.
        raise HTTPException(status_code=403, detail="Device principal required")
    if check_clock_window(body.recorded_at) is not None:
        raise HTTPException(
            status_code=400,
            detail="recorded_at outside the acceptable clock window",
        )
    return await repo.insert_for_subject(
        user.tenant_id,
        "device",
        user.device_id,
        body,
    )
