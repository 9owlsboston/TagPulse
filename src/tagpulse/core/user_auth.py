"""User authentication — JWT, API key, and X-Tenant-ID (backward compat)."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.config import settings
from tagpulse.models.database import TenantModel, UserModel
from tagpulse.repositories.timescaledb.session import get_session

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="Authorization", auto_error=False)
tenant_id_header = APIKeyHeader(name="X-Tenant-ID", auto_error=False)


class AuthenticatedUser:
    """Represents the authenticated user for the current request."""

    def __init__(
        self,
        user_id: UUID | None,
        tenant_id: UUID,
        tenant_name: str,
        tenant_slug: str,
        role: str,
        email: str | None = None,
    ) -> None:
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.tenant_name = tenant_name
        self.tenant_slug = tenant_slug
        self.role = role
        self.email = email


def generate_api_key(tenant_slug: str) -> tuple[str, str, str]:
    """Generate an API key, its prefix, and its hash.

    Returns: (raw_key, prefix, sha256_hash)
    """
    random_part = secrets.token_hex(16)
    raw_key = f"tp_{tenant_slug}_{random_part}"
    prefix = raw_key[:10]
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return raw_key, prefix, key_hash


def verify_api_key(raw_key: str, stored_hash: str) -> bool:
    """Verify an API key against its stored hash."""
    return hashlib.sha256(raw_key.encode()).hexdigest() == stored_hash


def generate_device_token(tenant_slug: str) -> tuple[str, str, str]:
    """Generate a per-device Bearer token, its prefix, and its hash.

    Mirrors :func:`generate_api_key` but uses a ``tpd_`` (tag-pulse-device)
    prefix so token leak triage can distinguish user keys from device tokens
    at a glance. Per ADR-011 Phase 1.

    Returns: (raw_token, prefix, sha256_hash)
    """
    random_part = secrets.token_hex(16)
    raw_token = f"tpd_{tenant_slug}_{random_part}"
    prefix = raw_token[:10]
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    return raw_token, prefix, token_hash


def create_jwt(user: UserModel, tenant: TenantModel) -> str:
    """Create a JWT access token for a user."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "tid": str(tenant.id),
        "role": user.role,
        "email": user.email,
        "tenant_name": tenant.name,
        "tenant_slug": tenant.slug,
        "iss": "tagpulse",
        "iat": now,
        "exp": now + timedelta(seconds=settings.jwt_expiry_seconds),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_jwt(token: str) -> dict[str, Any]:
    """Decode and verify a JWT token. Raises HTTPException on failure."""
    try:
        decoded: dict[str, Any] = jwt.decode(
            token, settings.jwt_secret, algorithms=["HS256"], issuer="tagpulse"
        )
        return decoded
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token") from None


async def get_current_user(
    authorization: str | None = Security(api_key_header),
    x_tenant_id: str | None = Security(tenant_id_header),
    session: AsyncSession = Depends(get_session),
) -> AuthenticatedUser:
    """Authenticate via Bearer JWT, Bearer API key, or X-Tenant-ID header."""
    # Try Bearer token first
    if authorization and authorization.startswith("Bearer "):
        raw_token = authorization[7:]
        # JWT tokens don't start with "tp_"; API keys do
        if not raw_token.startswith("tp_"):
            # Decode JWT
            payload = decode_jwt(raw_token)
            return AuthenticatedUser(
                user_id=UUID(payload["sub"]),
                tenant_id=UUID(payload["tid"]),
                tenant_name=payload["tenant_name"],
                tenant_slug=payload["tenant_slug"],
                role=payload["role"],
                email=payload.get("email"),
            )
        # API key auth
        prefix = raw_token[:10]
        stmt = select(UserModel).where(
            UserModel.api_key_prefix == prefix,
            UserModel.status == "active",
        )
        result = await session.execute(stmt)
        # Multiple users in the same tenant share the same 10-char prefix
        # (`tp_{slug}_` is identical for them). Verify the full hash against
        # each candidate and pick the matching one.
        user: UserModel | None = None
        for candidate in result.scalars().all():
            if verify_api_key(raw_token, candidate.api_key_hash or ""):
                user = candidate
                break
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid API key")
        # Look up tenant
        tenant = await session.get(TenantModel, user.tenant_id)
        if tenant is None or tenant.status != "active":
            raise HTTPException(status_code=401, detail="Tenant inactive")
        return AuthenticatedUser(
            user_id=user.id,
            tenant_id=tenant.id,
            tenant_name=tenant.name,
            tenant_slug=tenant.slug,
            role=user.role,
            email=user.email,
        )

    # Fall back to X-Tenant-ID (backward compat — viewer role)
    if x_tenant_id:
        try:
            tid = UUID(x_tenant_id)
        except ValueError:
            raise HTTPException(status_code=401, detail="Invalid tenant ID") from None
        tenant_stmt = select(TenantModel).where(
            TenantModel.id == tid, TenantModel.status == "active"
        )
        tenant_result = await session.execute(tenant_stmt)
        tenant = tenant_result.scalar_one_or_none()
        if tenant is None:
            raise HTTPException(status_code=401, detail="Tenant not found")
        return AuthenticatedUser(
            user_id=None,
            tenant_id=tenant.id,
            tenant_name=tenant.name,
            tenant_slug=tenant.slug,
            role="viewer",
        )

    raise HTTPException(status_code=401, detail="Authentication required")


def require_role(*roles: str) -> Any:
    """FastAPI dependency that enforces role-based access."""

    async def _check(
        user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        if user.role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Requires role: {', '.join(roles)}",
            )
        return user

    return Depends(_check)
