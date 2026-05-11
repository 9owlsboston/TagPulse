"""Telemetry model API — CRUD for per-device-type metric definitions."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from tagpulse.api.dependencies import get_telemetry_model_service
from tagpulse.api.services.telemetry_model_service import (
    TelemetryModelService,
)
from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.models.schemas import (
    TelemetryModelCreate,
    TelemetryModelResponse,
    TelemetryModelUpdate,
)

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


# Sprint 28 H6 (May 2026): the Sprint 21 ``GET /telemetry-models/{device_type}``
# 410 Gone tombstone has been removed. Callers receive FastAPI's default 404
# from the un-routed path. See ADR-013 §6 and ADR-015 §6 for the deprecation
# history; the Sprint 19 301 redirect and Sprint 21 410 Gone both ran for a
# full retention window each, so no surviving clients should still hit the
# legacy path.


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


@router.patch("/{model_id}", response_model=TelemetryModelResponse)
async def update_telemetry_model(
    model_id: UUID,
    body: TelemetryModelUpdate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    service: TelemetryModelService = Depends(get_telemetry_model_service),
) -> TelemetryModelResponse:
    """Sprint 28 G1: update a telemetry model's metrics list.

    Only ``metrics`` is mutable. To change ``subject_kind`` or ``device_type``
    delete the model and POST a new one — those columns key the Sprint 18
    unique constraint and are part of the model's identity.
    """
    updated = await service.update(user.tenant_id, user.user_id, model_id, body)
    if updated is None:
        raise HTTPException(
            status_code=404, detail="Telemetry model not found"
        ) from None
    return updated
