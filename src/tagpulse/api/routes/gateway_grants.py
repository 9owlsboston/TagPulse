"""Admin management of per-gateway subject grants (Sprint 81, C-6S9H).

An admin authorizes a gateway ``device`` to relay telemetry for a specific
``(subject_kind, subject_id)``. The telemetry-ingest guard (I-75YC) then allows
the gateway's own device subject **plus** its active grants.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.api.dependencies import get_gateway_grant_repo
from tagpulse.core.audit import AuditLogger
from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.models.schemas import (
    GatewaySubjectGrantCreate,
    GatewaySubjectGrantResponse,
)
from tagpulse.repositories.timescaledb.assets import TimescaleAssetRepository
from tagpulse.repositories.timescaledb.devices import TimescaleDeviceRepository
from tagpulse.repositories.timescaledb.gateway_subject_grants import (
    TimescaleGatewaySubjectGrantRepository,
)
from tagpulse.repositories.timescaledb.session import get_session

router = APIRouter(prefix="/admin", tags=["admin"])

# MVE grant subject kinds — each has an in-tenant existence check below. Other
# kinds (lot/stock_item/zone) are rejected 422 until their checks are wired.
_SUPPORTED_KINDS = ("asset", "device")


async def _assert_subject_exists(
    session: AsyncSession, tenant_id: UUID, subject_kind: str, subject_id: UUID
) -> None:
    if subject_kind == "asset":
        if await TimescaleAssetRepository(session).get(tenant_id, subject_id) is None:
            raise HTTPException(status_code=404, detail="Subject asset not found")
    elif subject_kind == "device":
        if await TimescaleDeviceRepository(session).get(tenant_id, subject_id) is None:
            raise HTTPException(status_code=404, detail="Subject device not found")
    else:
        raise HTTPException(
            status_code=422,
            detail=f"subject_kind '{subject_kind}' not supported for grants yet",
        )


@router.post(
    "/gateways/{device_id}/subject-grants",
    response_model=GatewaySubjectGrantResponse,
    status_code=201,
)
async def create_gateway_grant(
    device_id: UUID,
    body: GatewaySubjectGrantCreate,
    user: AuthenticatedUser = require_role("admin"),
    grants: TimescaleGatewaySubjectGrantRepository = Depends(get_gateway_grant_repo),
    session: AsyncSession = Depends(get_session),
) -> GatewaySubjectGrantResponse:
    """Grant a gateway device relay authority for a subject (admin only)."""
    if body.subject_kind not in _SUPPORTED_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"subject_kind '{body.subject_kind}' not supported for grants yet",
        )
    if body.subject_kind == "device" and body.subject_id == device_id:
        raise HTTPException(
            status_code=422,
            detail="a gateway is always allowed its own device subject; no grant needed",
        )
    # Gateway device must exist in-tenant.
    if await TimescaleDeviceRepository(session).get(user.tenant_id, device_id) is None:
        raise HTTPException(status_code=404, detail="Gateway device not found")
    # Granted subject must exist in-tenant (no orphan/bogus grants).
    await _assert_subject_exists(session, user.tenant_id, body.subject_kind, body.subject_id)

    existing = await grants.get_active(
        user.tenant_id, device_id, body.subject_kind, body.subject_id
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="an active grant already exists")

    grant = await grants.create(
        user.tenant_id, device_id, body.subject_kind, body.subject_id, user.user_id
    )
    await AuditLogger(session).log(
        user.tenant_id,
        action="gateway_subject_grant.created",
        resource_type="device",
        resource_id=device_id,
        changes={"subject_kind": body.subject_kind, "subject_id": str(body.subject_id)},
        user_id=user.user_id,
    )
    return grant


@router.get(
    "/gateways/{device_id}/subject-grants",
    response_model=list[GatewaySubjectGrantResponse],
)
async def list_gateway_grants(
    device_id: UUID,
    user: AuthenticatedUser = require_role("admin"),
    grants: TimescaleGatewaySubjectGrantRepository = Depends(get_gateway_grant_repo),
) -> list[GatewaySubjectGrantResponse]:
    """List a gateway's active subject grants (admin only)."""
    return await grants.list_for_gateway(user.tenant_id, device_id)


@router.delete(
    "/gateways/{device_id}/subject-grants/{subject_kind}/{subject_id}",
    status_code=204,
)
async def revoke_gateway_grant(
    device_id: UUID,
    subject_kind: str,
    subject_id: UUID,
    user: AuthenticatedUser = require_role("admin"),
    grants: TimescaleGatewaySubjectGrantRepository = Depends(get_gateway_grant_repo),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Revoke a gateway's active subject grant (admin only)."""
    revoked = await grants.revoke(
        user.tenant_id, device_id, subject_kind, subject_id, datetime.now(UTC)
    )
    if not revoked:
        raise HTTPException(status_code=404, detail="active grant not found")
    await AuditLogger(session).log(
        user.tenant_id,
        action="gateway_subject_grant.revoked",
        resource_type="device",
        resource_id=device_id,
        changes={"subject_kind": subject_kind, "subject_id": str(subject_id)},
        user_id=user.user_id,
    )
