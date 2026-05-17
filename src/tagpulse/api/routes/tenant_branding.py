"""Tenant branding endpoints (Sprint 33 QW6).

Exposes per-tenant branding overrides (logo, display name, primary
brand colour) so the admin UI can re-skin TagPulse without redeploying
or forking the UI bundle.

Scoped per the QW6 row in
[docs/design/reference-design-remediation.md §3.3][scope-lock]; the
endpoint shapes here are the source of truth and the doc was updated
to match.

[scope-lock]: ../../../docs/design/reference-design-remediation.md

Three endpoints:

- ``GET /tenant/branding``     — authenticated, any role; returns the
                                 calling tenant's branding overrides.
- ``PATCH /tenant/branding``   — admin only; updates the three columns
                                 atomically. PATCH semantics (only
                                 fields explicitly provided change).
                                 ``null`` clears an override.
- ``GET /branding/{slug}``     — **unauthenticated**; returns only the
                                 three branding fields plus the
                                 tenant's display name for the login
                                 page to skin itself before the user
                                 has credentials. Returns 404 if the
                                 slug is unknown (does not distinguish
                                 from "exists but no branding set").

Validation is intentionally format-only (HTTPS URL, length, hex
``#RRGGBB``). Deeper checks (HEAD content-length ≤ 2 MiB, image MIME)
are deferred per the scope-lock plan — they require an outbound HTTP
call from the API and are better enforced at the operator's
upload/CDN tier.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.audit import AuditLogger
from tagpulse.core.tenant_auth import Tenant, get_current_tenant
from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.models.database import TenantModel
from tagpulse.repositories.timescaledb.session import get_session

router = APIRouter(tags=["tenant"])

_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_MAX_LOGO_URL_LEN = 2048
_MAX_DISPLAY_NAME_LEN = 255


class TenantBranding(BaseModel):
    """Per-tenant branding overrides. ``None`` on any field means
    "no override; UI uses the system default"."""

    logo_url: str | None = Field(
        default=None,
        max_length=_MAX_LOGO_URL_LEN,
        description="HTTPS URL to the logo image hosted by the operator.",
    )
    display_name: str | None = Field(
        default=None,
        max_length=_MAX_DISPLAY_NAME_LEN,
        description="Friendly name shown in the Sider/login in place of tenants.name.",
    )
    brand_color: str | None = Field(
        default=None,
        description="Primary brand colour as #RRGGBB hex.",
    )


class TenantBrandingUpdate(BaseModel):
    """Admin payload. PATCH semantics: missing fields keep their
    current value, explicit ``null`` clears the override."""

    logo_url: str | None = Field(default=None, max_length=_MAX_LOGO_URL_LEN)
    display_name: str | None = Field(default=None, max_length=_MAX_DISPLAY_NAME_LEN)
    brand_color: str | None = None

    @field_validator("logo_url")
    @classmethod
    def _validate_logo_url(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not v.startswith("https://"):
            raise ValueError("logo_url must be an https:// URL")
        return v

    @field_validator("display_name")
    @classmethod
    def _validate_display_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        return stripped or None

    @field_validator("brand_color")
    @classmethod
    def _validate_brand_color(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not _HEX_COLOR_RE.match(v):
            raise ValueError("brand_color must match ^#[0-9A-Fa-f]{6}$ (e.g. #14B8A6)")
        return v


class PublicBranding(BaseModel):
    """Login-page-facing branding payload. Includes ``name`` so the
    login UI can fall back gracefully when ``display_name`` is unset."""

    slug: str
    name: str
    display_name: str | None = None
    logo_url: str | None = None
    brand_color: str | None = None


def _to_branding(row: TenantModel) -> TenantBranding:
    return TenantBranding(
        logo_url=row.logo_url,
        display_name=row.display_name,
        brand_color=row.brand_color,
    )


@router.get("/tenant/branding", response_model=TenantBranding)
async def get_tenant_branding(
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> TenantBranding:
    """Return the calling tenant's branding overrides (any role)."""
    row = await session.scalar(select(TenantModel).where(TenantModel.id == tenant.id))
    if row is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return _to_branding(row)


@router.patch("/tenant/branding", response_model=TenantBranding)
async def update_tenant_branding(
    body: TenantBrandingUpdate,
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> TenantBranding:
    """Update tenant branding (admin only). PATCH semantics; audited.

    Only fields **present** in the request body are written. An
    explicit ``null`` clears that field's override.
    """
    row = await session.scalar(select(TenantModel).where(TenantModel.id == user.tenant_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    provided = body.model_dump(exclude_unset=True)
    changes: dict[str, dict[str, str | None]] = {}

    for column in ("logo_url", "display_name", "brand_color"):
        if column not in provided:
            continue
        old_value: str | None = getattr(row, column)
        new_value: str | None = provided[column]
        if old_value != new_value:
            setattr(row, column, new_value)
            changes[column] = {"from": old_value, "to": new_value}

    if changes:
        await session.flush()
        await AuditLogger(session=session).log(
            user.tenant_id,
            "tenant.branding.update",
            "tenant",
            user.tenant_id,
            changes=changes,
            user_id=user.user_id,
        )
    return _to_branding(row)


@router.get("/branding/{slug}", response_model=PublicBranding)
async def get_public_branding(
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> PublicBranding:
    """Public branding lookup for the login page (no auth).

    Returns the tenant's display name + logo URL + brand colour so the
    login UI can skin itself before the user has credentials. 404 if
    the slug is unknown.
    """
    row = await session.scalar(select(TenantModel).where(TenantModel.slug == slug))
    if row is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return PublicBranding(
        slug=row.slug,
        name=row.name,
        display_name=row.display_name,
        logo_url=row.logo_url,
        brand_color=row.brand_color,
    )
