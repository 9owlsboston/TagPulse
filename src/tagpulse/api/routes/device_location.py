"""Device-self / gateway-relay external location endpoint (Sprint 80 I-9HQA, Sprint 84 C-4Z66).

A gateway/device principal (``tpd_`` token, I-K6D1) stamps its **own** ``device`` position, or —
when it holds an active admin grant (C-6S9H) — relays a position for a granted subject (C-4Z66).
Mirrors the gateway telemetry ingest guard (I-75YC): own subject *or* a granted subject, and
``recorded_at`` is clock-validated. No event is emitted in this MVE (the asset external-location
event contract is left untouched — see docs/design/external-locations-subject-kinds.md §5).
"""

from fastapi import APIRouter, Depends, HTTPException

from tagpulse.api.dependencies import get_external_location_repo, get_gateway_grant_repo
from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.ingestion.clock import check_clock_window
from tagpulse.models.schemas import DeviceLocationCreate, ExternalLocationResponse
from tagpulse.repositories.timescaledb.external_locations import (
    TimescaleExternalLocationRepository,
)
from tagpulse.repositories.timescaledb.gateway_subject_grants import (
    TimescaleGatewaySubjectGrantRepository,
)

router = APIRouter(tags=["device-location"])


@router.post("/device-location", response_model=ExternalLocationResponse, status_code=201)
async def record_device_location(
    body: DeviceLocationCreate,
    user: AuthenticatedUser = require_role("device"),
    repo: TimescaleExternalLocationRepository = Depends(get_external_location_repo),
    grants: TimescaleGatewaySubjectGrantRepository = Depends(get_gateway_grant_repo),
) -> ExternalLocationResponse:
    """Record a device's own external position, or relay one for a granted subject.

    Device principals only. With no target subject the row is the calling device's own
    (``('device', device_id)``, unchanged). With a target ``(subject_kind, subject_id)`` the write
    is allowed iff it is the own device subject **or** an active admin grant authorizes it (C-4Z66),
    exactly mirroring the telemetry-ingest guard (I-75YC). ``recorded_at`` must fall inside the
    ingest clock window.
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

    # Resolve the target subject. None/None → self; else the requested pair.
    subject_kind = body.subject_kind or "device"
    subject_id = body.subject_id or user.device_id
    is_relay = not (subject_kind == "device" and subject_id == user.device_id)

    if is_relay:
        # Own-or-granted (identical rule to _enforce_device_telemetry). The grant set is keyed
        # only by the authenticated device_id — a device cannot relay by spoofing another gateway.
        grant_set = await grants.active_subject_set(user.tenant_id, user.device_id)
        if (subject_kind, subject_id) not in grant_set:
            raise HTTPException(
                status_code=403,
                detail="Device principals may only record locations for their own device "
                "subject or a granted subject",
            )
        # Server-controlled relay provenance (external_locations has no relaying-device column);
        # overwrite any client-supplied key so it can't be spoofed.
        body = body.model_copy(
            update={
                "metadata": {**(body.metadata or {}), "relayed_by_device_id": str(user.device_id)}
            }
        )

    # Asset reads filter external_locations on asset_id (not subject_id), so an asset target must
    # populate asset_id to stay visible to the asset location APIs/views (mirrors the asset shim).
    asset_id = subject_id if subject_kind == "asset" else None
    return await repo.insert_for_subject(
        user.tenant_id,
        subject_kind,
        subject_id,
        body,
        asset_id=asset_id,
    )
