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


@router.get("/{device_type}", response_model=TelemetryModelResponse)
async def get_telemetry_model(
    device_type: str,
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: TelemetryModelService = Depends(get_telemetry_model_service),
) -> TelemetryModelResponse:
    """Get telemetry model definition for a device type."""
    result = await service.get_by_device_type(user.tenant_id, device_type)
    if result is None:
        raise HTTPException(
            status_code=404, detail="Telemetry model not found"
        ) from None
    return result


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
