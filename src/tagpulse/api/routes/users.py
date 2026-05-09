"""User management API routes."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.user_auth import (
    AuthenticatedUser,
    generate_api_key,
    require_role,
)
from tagpulse.models.database import TenantModel, UserModel
from tagpulse.models.user_schemas import (
    ApiKeyResponse,
    UserCreate,
    UserResponse,
    UserUpdate,
)
from tagpulse.repositories.timescaledb.session import get_session

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserResponse, status_code=201)
async def create_user(
    body: UserCreate,
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> UserResponse:
    """Create a new user (admin only)."""
    row = UserModel(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        email=body.email,
        name=body.name,
        role=body.role,
    )
    session.add(row)
    await session.flush()
    return _to_response(row)


@router.get("", response_model=list[UserResponse])
async def list_users(
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> list[UserResponse]:
    """List all users in the tenant (admin only)."""
    stmt = (
        select(UserModel)
        .where(UserModel.tenant_id == user.tenant_id)
        .order_by(UserModel.created_at.desc())
    )
    result = await session.execute(stmt)
    return [_to_response(row) for row in result.scalars()]


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> UserResponse:
    """Update a user's role or status (admin only)."""
    stmt = select(UserModel).where(UserModel.id == user_id, UserModel.tenant_id == user.tenant_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found") from None
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    await session.flush()
    return _to_response(row)


@router.post("/{user_id}/api-key", response_model=ApiKeyResponse)
async def generate_user_api_key(
    user_id: uuid.UUID,
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> ApiKeyResponse:
    """Generate an API key for a user (admin only). Key is returned once."""
    stmt = select(UserModel).where(UserModel.id == user_id, UserModel.tenant_id == user.tenant_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found") from None

    # Look up tenant slug
    tenant = await session.get(TenantModel, user.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=500, detail="Tenant not found") from None

    raw_key, prefix, key_hash = generate_api_key(tenant.slug)
    row.api_key_hash = key_hash
    row.api_key_prefix = prefix
    row.api_key_created_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
    await session.flush()
    return ApiKeyResponse(api_key=raw_key, prefix=prefix)


@router.delete("/{user_id}/api-key", status_code=204)
async def revoke_api_key(
    user_id: uuid.UUID,
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Revoke a user's API key (admin only)."""
    stmt = select(UserModel).where(UserModel.id == user_id, UserModel.tenant_id == user.tenant_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found") from None
    row.api_key_hash = None
    row.api_key_prefix = None
    row.api_key_created_at = None
    await session.flush()


def _to_response(row: UserModel) -> UserResponse:
    return UserResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        email=row.email,
        name=row.name,
        role=row.role,
        status=row.status,
        api_key_prefix=row.api_key_prefix,
        created_at=row.created_at,
        last_login=row.last_login,
    )
