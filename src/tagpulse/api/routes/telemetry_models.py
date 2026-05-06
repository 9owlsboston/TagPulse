"""Telemetry model API — CRUD for per-device-type metric definitions."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from tagpulse.api.dependencies import get_telemetry_model_service
from tagpulse.api.services.telemetry_model_service import (
    TelemetryModelService,
)
from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.models.schemas import TelemetryModelCreate, TelemetryModelResponse

router = APIRouter(prefix="/telemetry-models", tags=["telemetry-models"])


@router.post("", response_model=TelemetryModelResponse, status_code=201)
async def create_telemetry_model(
    body: TelemetryModelCreate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    service: TelemetryModelService = Depends(get_telemetry_model_service),
) -> TelemetryModelResponse:
    """Define the telemetry schema for a device type."""
    return await service.create(user.tenant_id, body)


@router.get("", response_model=list[TelemetryModelResponse])
async def list_telemetry_models(
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: TelemetryModelService = Depends(get_telemetry_model_service),
) -> list[TelemetryModelResponse]:
    """List all telemetry model definitions."""
    return await service.list_all(user.tenant_id)


@router.get("/{subject_kind}/{key}", response_model=TelemetryModelResponse)
async def get_telemetry_model_by_subject(
    subject_kind: str,
    key: str,
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: TelemetryModelService = Depends(get_telemetry_model_service),
) -> TelemetryModelResponse:
    """Sprint 19 subject-scoped telemetry-model lookup.

    For ``subject_kind='device'`` ``key`` is the device_type;
    for non-device kinds the only model permitted per tenant is
    addressed with any non-empty ``key`` (typically the same string as
    ``subject_kind`` so URLs remain self-describing).
    """
    if subject_kind not in {"device", "asset", "lot", "stock_item", "zone"}:
        raise HTTPException(status_code=404, detail="Unknown subject_kind") from None
    result = await service.get_by_subject(user.tenant_id, subject_kind, key)
    if result is None:
        raise HTTPException(
            status_code=404, detail="Telemetry model not found"
        ) from None
    return result


@router.get("/{device_type}", response_model=None, deprecated=True)
async def get_telemetry_model_legacy(
    device_type: str,
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
) -> None:
    """Removed in Sprint 21 (ADR-015 §6).

    The Sprint 19 301 redirect to ``/telemetry-models/device/{device_type}``
    has been removed after one full retention cycle. Callers must address
    the subject-scoped path directly. Returns 410 Gone with a Location-style
    hint so any forgotten clients still get a clear migration message.
    """
    raise HTTPException(
        status_code=410,
        detail=(
            "GET /telemetry-models/{device_type} was removed in Sprint 21. "
            f"Use GET /telemetry-models/device/{device_type} instead."
        ),
    )


@router.delete("/{model_id}", status_code=204)
async def delete_telemetry_model(
    model_id: UUID,
    user: AuthenticatedUser = require_role("admin"),
    service: TelemetryModelService = Depends(get_telemetry_model_service),
) -> None:
    """Delete a telemetry model definition."""
    deleted = await service.delete(user.tenant_id, model_id)
    if not deleted:
        raise HTTPException(
            status_code=404, detail="Telemetry model not found"
        ) from None
