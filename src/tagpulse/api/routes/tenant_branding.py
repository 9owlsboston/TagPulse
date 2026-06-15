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

# An uploaded logo is stored inline as a base64 ``data:`` URL (chore: branding
# logo upload — no blob storage). Cap the encoded length so a logo can't bloat
# the branding row (fetched on every page load). 96 KB of base64 ≈ 70 KB of
# image — generous for an SVG wordmark or a small PNG icon, tiny for the row.
_MAX_LOGO_DATA_URL_LEN = 96 * 1024
# Allowed inline image media types (kept tight — these are *logos*, not media).
_DATA_URL_RE = re.compile(r"^data:image/(png|jpeg|svg\+xml|webp|gif);base64,[A-Za-z0-9+/=\s]+$")


def _validate_logo(v: str | None) -> str | None:
    """A logo may be an ``https://`` URL (operator-hosted) or an uploaded
    base64 ``data:image/...`` URL (capped). Empty/``None`` clears it."""
    if v is None or v == "":
        return None
    if v.startswith("data:"):
        if len(v) > _MAX_LOGO_DATA_URL_LEN:
            raise ValueError(
                f"uploaded logo is too large (max {_MAX_LOGO_DATA_URL_LEN // 1024} KB encoded)"
            )
        if not _DATA_URL_RE.match(v):
            raise ValueError(
                "logo data URL must be a base64 data:image/(png|jpeg|svg+xml|webp|gif)"
            )
        return v
    if v.startswith("https://"):
        if len(v) > _MAX_LOGO_URL_LEN:
            raise ValueError(f"logo URL too long (max {_MAX_LOGO_URL_LEN} chars)")
        return v
    raise ValueError("logo must be an https:// URL or a base64 data:image/... URL")


class TenantBranding(BaseModel):
    """Per-tenant branding overrides. ``None`` on any field means
    "no override; UI uses the system default"."""

    logo_url: str | None = Field(
        default=None,
        description="Full/expanded logo: an https:// URL or an uploaded "
        "base64 data:image/... URL. Shown in the expanded sidebar header.",
    )
    logo_collapsed_url: str | None = Field(
        default=None,
        description="Collapsed-sidebar logo (square icon/mark): an https:// "
        "URL or an uploaded base64 data:image/... URL. Falls back to "
        "logo_url, then the monogram, when unset.",
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

    logo_url: str | None = Field(default=None)
    logo_collapsed_url: str | None = Field(default=None)
    display_name: str | None = Field(default=None, max_length=_MAX_DISPLAY_NAME_LEN)
    brand_color: str | None = None

    @field_validator("logo_url", "logo_collapsed_url")
    @classmethod
    def _validate_logo_field(cls, v: str | None) -> str | None:
        return _validate_logo(v)

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
    logo_collapsed_url: str | None = None
    brand_color: str | None = None


def _to_branding(row: TenantModel) -> TenantBranding:
    return TenantBranding(
        logo_url=row.logo_url,
        logo_collapsed_url=row.logo_collapsed_url,
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

    for column in ("logo_url", "logo_collapsed_url", "display_name", "brand_color"):
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
        logo_collapsed_url=row.logo_collapsed_url,
        brand_color=row.brand_color,
    )
