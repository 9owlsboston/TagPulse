"""Authentication routes — login endpoint with rate limiting."""

from __future__ import annotations

import logging
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.config import settings
from tagpulse.core.user_auth import create_jwt, verify_api_key
from tagpulse.models.database import TenantModel, UserModel
from tagpulse.repositories.timescaledb.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Simple in-memory rate limiter: {ip: [(timestamp, ...)]}
_login_attempts: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(client_ip: str) -> None:
    """Enforce per-IP rate limit on login attempts."""
    now = time.monotonic()
    window = 60.0  # 1 minute window
    attempts = _login_attempts[client_ip]
    # Prune old entries
    _login_attempts[client_ip] = [t for t in attempts if now - t < window]
    if len(_login_attempts[client_ip]) >= settings.login_rate_limit:
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Try again in 1 minute.",
        )
    _login_attempts[client_ip].append(now)


class LoginRequest(BaseModel):
    """Login with email and API key."""

    email: str = Field(min_length=1)
    api_key: str = Field(min_length=1)


class LoginUserInfo(BaseModel):
    """User info returned in login response."""

    id: str
    email: str
    name: str
    role: str
    tenant_id: str
    tenant_name: str


class LoginResponse(BaseModel):
    """Successful login response with JWT token."""

    access_token: str
    token_type: str = "bearer"  # noqa: S105 — OAuth2 bearer token type identifier, not a credential
    expires_in: int
    user: LoginUserInfo


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    """Exchange email + API key for a JWT access token."""
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    # Look up user by email and API key prefix
    prefix = body.api_key[:10]
    stmt = select(UserModel).where(
        UserModel.email == body.email,
        UserModel.api_key_prefix == prefix,
    )
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None or not verify_api_key(body.api_key, user.api_key_hash or ""):
        raise HTTPException(status_code=401, detail="Invalid email or API key")

    if user.status != "active":
        raise HTTPException(status_code=403, detail="User account is deactivated")

    # Look up tenant
    tenant = await session.get(TenantModel, user.tenant_id)
    if tenant is None or tenant.status != "active":
        raise HTTPException(status_code=403, detail="Tenant is inactive")

    # Create JWT
    token = create_jwt(user, tenant)

    # Update last_login
    from sqlalchemy import func

    user.last_login = func.now()
    await session.flush()

    return LoginResponse(
        access_token=token,
        expires_in=settings.jwt_expiry_seconds,
        user=LoginUserInfo(
            id=str(user.id),
            email=user.email,
            name=user.name,
            role=user.role,
            tenant_id=str(tenant.id),
            tenant_name=tenant.name,
        ),
    )
