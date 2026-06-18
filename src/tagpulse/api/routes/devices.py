"""CRUD API routes for the device registry."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.api.dependencies import get_device_service
from tagpulse.api.label_filter import LabelFilterError, parse_label_filter
from tagpulse.api.services.device_service import DeviceNotFoundError, DeviceService
from tagpulse.core.audit import AuditLogger
from tagpulse.core.otel_metrics import (
    device_cert_attachments_counter,
    device_token_rotations_counter,
)
from tagpulse.core.user_auth import AuthenticatedUser, generate_device_token, require_role
from tagpulse.models.database import DeviceModel, TenantModel
from tagpulse.models.schemas import DeviceCreate, DeviceResponse, DeviceUpdate
from tagpulse.repositories.timescaledb.session import get_session

router = APIRouter(prefix="/device-registry", tags=["device-registry"])


@router.post("", response_model=DeviceResponse, status_code=201)
async def register_device(
    body: DeviceCreate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    service: DeviceService = Depends(get_device_service),
) -> DeviceResponse:
    """Register a new device (reader)."""
    try:
        return await service.register(user.tenant_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("", response_model=list[DeviceResponse])
async def list_devices(
    request: Request,
    status: str | None = Query(default=None),
    device_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: DeviceService = Depends(get_device_service),
) -> list[DeviceResponse]:
    """List devices with optional filters."""
    try:
        labels = parse_label_filter(request.query_params)
    except LabelFilterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return await service.list_devices(
        user.tenant_id,
        status=status,
        device_type=device_type,
        labels=labels,
        limit=limit,
        offset=offset,
    )


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(
    device_id: UUID,
    user: AuthenticatedUser = require_role("admin", "editor", "viewer"),
    service: DeviceService = Depends(get_device_service),
) -> DeviceResponse:
    """Get a single device by ID."""
    try:
        return await service.get(user.tenant_id, device_id)
    except DeviceNotFoundError:
        raise HTTPException(status_code=404, detail="Device not found") from None


@router.patch("/{device_id}", response_model=DeviceResponse)
async def update_device(
    device_id: UUID,
    body: DeviceUpdate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    service: DeviceService = Depends(get_device_service),
) -> DeviceResponse:
    """Update device fields (partial update)."""
    try:
        return await service.update(user.tenant_id, device_id, body)
    except DeviceNotFoundError:
        raise HTTPException(status_code=404, detail="Device not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/{device_id}/decommission", response_model=DeviceResponse)
async def decommission_device(
    device_id: UUID,
    user: AuthenticatedUser = require_role("admin"),
    service: DeviceService = Depends(get_device_service),
) -> DeviceResponse:
    """Decommission a device — sets status to 'decommissioned'."""
    try:
        return await service.decommission(user.tenant_id, device_id)
    except DeviceNotFoundError:
        raise HTTPException(status_code=404, detail="Device not found") from None


class DeviceTokenResponse(BaseModel):
    """One-time device-token reveal — never re-readable after this response."""

    device_id: UUID
    token: str
    prefix: str
    rotated_at: datetime


@router.post("/{device_id}/rotate-token", response_model=DeviceTokenResponse)
async def rotate_device_token(
    device_id: UUID,
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> DeviceTokenResponse:
    """Rotate a device's Bearer token (admin only).

    Plaintext token is returned **once** — backend stores only its SHA-256
    hash, immediately invalidating any prior token. Audit-logged and metered
    per ADR-011 Phase 1 / docs/design/edge-device-contract.md §5.
    """
    stmt = select(DeviceModel).where(
        DeviceModel.id == device_id,
        DeviceModel.tenant_id == user.tenant_id,
    )
    result = await session.execute(stmt)
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found") from None

    tenant = await session.get(TenantModel, user.tenant_id)
    if tenant is None:  # pragma: no cover — defensive
        raise HTTPException(status_code=500, detail="Tenant not found") from None

    prior_prefix = device.token_prefix
    raw_token, prefix, token_hash = generate_device_token(tenant.slug)
    rotated_at = datetime.now(UTC)
    device.token_hash = token_hash
    device.token_prefix = prefix
    device.token_rotated_at = rotated_at

    audit = AuditLogger(session)
    await audit.log(
        user.tenant_id,
        action="device.token_rotated",
        resource_type="device",
        resource_id=device_id,
        changes={"prior_prefix": prior_prefix, "new_prefix": prefix},
        user_id=user.user_id,
    )
    await session.flush()

    device_token_rotations_counter.add(1, {"tenant_id": str(user.tenant_id)})

    return DeviceTokenResponse(
        device_id=device_id,
        token=raw_token,
        prefix=prefix,
        rotated_at=rotated_at,
    )


# -- Sprint 17b: device certificate attachment (ADR-012 Phase 2) --


class DeviceCertAttach(BaseModel):
    """Admin payload for attaching a device certificate.

    Plaintext PEM is **not** stored — only its SHA-256 thumbprint plus the
    parsed subject. The MQTT broker (Mosquitto with EXTERNAL auth) holds the
    PKI; the backend uses the thumbprint to map an authenticated cert back
    to a device row.
    """

    cert_pem: str = Field(min_length=1, max_length=16_384)


class DeviceCertResponse(BaseModel):
    device_id: UUID
    thumbprint: str
    subject: str | None
    attached_at: datetime


@router.post("/{device_id}/cert", response_model=DeviceCertResponse)
async def attach_device_cert(
    device_id: UUID,
    body: DeviceCertAttach = Body(...),
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> DeviceCertResponse:
    """Attach a client certificate to a device (admin only).

    Stores SHA-256 thumbprint + subject. The actual PEM lives in the MQTT
    broker's CA store, never in the application database.
    """
    # Lazy import to keep cryptography off the hot path for deployments that
    # don't enable mTLS.
    import hashlib

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(
            status_code=501,
            detail="cryptography package required for cert attachment",
        ) from exc

    try:
        cert = x509.load_pem_x509_certificate(body.cert_pem.encode("utf-8"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid PEM: {exc}") from exc

    der = cert.public_bytes(serialization.Encoding.DER)
    thumbprint = hashlib.sha256(der).hexdigest()
    subject = cert.subject.rfc4514_string() if cert.subject else None

    stmt = select(DeviceModel).where(
        DeviceModel.id == device_id,
        DeviceModel.tenant_id == user.tenant_id,
    )
    device = (await session.execute(stmt)).scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found") from None

    prior_thumbprint = device.cert_thumbprint
    device.cert_thumbprint = thumbprint
    device.cert_subject = subject
    attached_at = datetime.now(UTC)

    audit = AuditLogger(session)
    await audit.log(
        user.tenant_id,
        action="device.cert_attached",
        resource_type="device",
        resource_id=device_id,
        changes={
            "prior_thumbprint": prior_thumbprint,
            "new_thumbprint": thumbprint,
            "subject": subject,
        },
        user_id=user.user_id,
    )
    try:
        await session.flush()
    except Exception as exc:  # uniqueness collision — same cert, different device
        raise HTTPException(
            status_code=409,
            detail="cert thumbprint already attached to another device",
        ) from exc
    device_cert_attachments_counter.add(1, {"tenant_id": str(user.tenant_id)})

    return DeviceCertResponse(
        device_id=device_id,
        thumbprint=thumbprint,
        subject=subject,
        attached_at=attached_at,
    )
