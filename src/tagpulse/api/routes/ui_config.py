"""Configurable UI config endpoints (Sprint 60, ADR-032).

Increment 1 (ADR-032 §7 step 1) shipped the server-resolved ``GET /ui-config``
over system defaults only. Increment 2 (step 2) added the **user override**
layer (``PUT /ui-config/me``). Increment 3 (step 3) adds the admin-set
**tenant + role default** layers (``PUT /ui-config/tenant`` and
``PUT /ui-config/role/{role}``), so ``GET`` now folds the full four-layer
merge — System → Tenant → Role → User — server-side, and the UI consumes the
resolved document directly, ignoring any leaf it doesn't recognise
(ADR-032 §6.4).

Storage (ADR-032 §3): the tenant + role layers live on ``tenants.ui_config``
(tenant-default leaves at the top level, the role layer under a reserved
``roles`` sub-object); the user layer lives on ``user_ui_prefs``.

"Reset to team default" (ADR-032 §2): a user ``PUT``s ``/me`` with an empty
body ``{}`` → their override clears and they fall back to role/tenant/system.
The same empty-body convention resets a tenant or role layer.

The ``locked`` leaf-pin (ADR-032 §2) stays deferred to a later increment.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.audit import AuditLogger
from tagpulse.core.user_auth import AuthenticatedUser, get_current_user, require_role
from tagpulse.repositories.timescaledb.session import get_session
from tagpulse.repositories.timescaledb.tenant_ui_config import TenantUiConfigRepository
from tagpulse.repositories.timescaledb.user_ui_prefs import UserUiPrefsRepository
from tagpulse.services.ui_config import (
    ROLES_KEY,
    UiConfig,
    resolve_ui_config,
    tenant_role_layers,
    validate_ui_config_override,
)

router = APIRouter(prefix="/ui-config", tags=["ui-config"])

# The roles a tenant may carry a default layer for (matches the user role set).
_CONFIGURABLE_ROLES = ("admin", "editor", "viewer")


async def _resolve_for_user(user: AuthenticatedUser, session: AsyncSession) -> UiConfig:
    """Resolve the effective config for ``user`` across all four layers.

    Folds System → Tenant → Role → User (ADR-032 §2). The tenant + role layers
    come from ``tenants.ui_config`` (split for the caller's role); the user
    layer from ``user_ui_prefs``. A caller with no ``user_id`` (the X-Tenant-ID
    backward-compat path) contributes no user layer; a user with no stored row
    is the "reset to team default" state — both still inherit tenant/role.
    """
    stored_tenant = await TenantUiConfigRepository(session).get(user.tenant_id)
    overrides: list[dict[str, Any]] = tenant_role_layers(stored_tenant, user.role)
    if user.user_id is not None:
        stored_user = await UserUiPrefsRepository(session).get_for_user(user.user_id)
        if stored_user:
            overrides.append(stored_user)
    return resolve_ui_config(overrides)


@router.get("", response_model=UiConfig)
async def get_ui_config(
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UiConfig:
    """Return the presentation config resolved for the calling viewer.

    Folds System → Tenant → Role → User server-side so the UI never
    reconstructs the merge.
    """
    return await _resolve_for_user(user, session)


@router.put("/me", response_model=UiConfig)
async def put_ui_config_me(
    body: dict[str, Any],
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UiConfig:
    """Upsert the caller's UI override and return the freshly resolved config.

    The body is a **sparse** subset of the ADR-032 §4 document; unknown or
    ill-typed keys are rejected (422). An empty body ``{}`` clears the
    override ("reset to team default"). Requires a real user identity — the
    X-Tenant-ID backward-compat path has no user to attach prefs to.
    """
    if user.user_id is None:
        raise HTTPException(
            status_code=403,
            detail="A user identity is required to save UI preferences",
        )
    try:
        override = validate_ui_config_override(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    await UserUiPrefsRepository(session).upsert(user.user_id, user.tenant_id, override)
    return await _resolve_for_user(user, session)


@router.put("/tenant", response_model=UiConfig)
async def put_ui_config_tenant(
    body: dict[str, Any],
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> UiConfig:
    """Set the tenant-default presentation layer (admin only).

    The body is a **sparse** subset of the ADR-032 §4 document; unknown or
    ill-typed keys are rejected (422). It replaces the tenant-default leaves
    wholesale while preserving the per-role layer (managed via
    ``PUT /ui-config/role/{role}``). An empty body ``{}`` clears the
    tenant-default leaves. Audited. Returns the caller's freshly resolved
    config.
    """
    try:
        override = validate_ui_config_override(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    repo = TenantUiConfigRepository(session)
    stored = await repo.get(user.tenant_id) or {}
    new_blob: dict[str, Any] = dict(override)
    roles = stored.get(ROLES_KEY)
    if roles:
        new_blob[ROLES_KEY] = roles
    await repo.set(user.tenant_id, new_blob or None)
    await AuditLogger(session=session).log(
        user.tenant_id,
        "ui_config.tenant.update",
        "tenant",
        user.tenant_id,
        changes={"ui_config_tenant": override},
        user_id=user.user_id,
    )
    return await _resolve_for_user(user, session)


@router.put("/role/{role}", response_model=UiConfig)
async def put_ui_config_role(
    role: str,
    body: dict[str, Any],
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> UiConfig:
    """Set a per-role default presentation layer (admin only).

    ``role`` must be one of the known roles (``admin`` / ``editor`` /
    ``viewer``); an unknown role is rejected (422). The body is a **sparse**
    subset of the ADR-032 §4 document, rejecting unknown/ill-typed keys (422).
    It replaces that role's layer wholesale; an empty body ``{}`` removes the
    role layer (reset). The tenant-default leaves and other roles are
    untouched. Audited. Returns the caller's freshly resolved config.
    """
    if role not in _CONFIGURABLE_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"role must be one of {list(_CONFIGURABLE_ROLES)}; got {role!r}",
        )
    try:
        override = validate_ui_config_override(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    repo = TenantUiConfigRepository(session)
    stored = dict(await repo.get(user.tenant_id) or {})
    roles: dict[str, Any] = dict(stored.get(ROLES_KEY) or {})
    if override:
        roles[role] = override
    else:
        roles.pop(role, None)
    if roles:
        stored[ROLES_KEY] = roles
    else:
        stored.pop(ROLES_KEY, None)
    await repo.set(user.tenant_id, stored or None)
    await AuditLogger(session=session).log(
        user.tenant_id,
        "ui_config.role.update",
        "tenant",
        user.tenant_id,
        changes={"role": role, "ui_config_role": override},
        user_id=user.user_id,
    )
    return await _resolve_for_user(user, session)
