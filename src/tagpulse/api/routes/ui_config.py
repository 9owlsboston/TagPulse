"""Configurable UI config endpoints (Sprint 60, ADR-032).

Increment 1 (ADR-032 §7 step 1) shipped the server-resolved ``GET /ui-config``
over system defaults only. Increment 2 (step 2) adds the **user override
layer**: ``GET`` now folds the caller's ``user_ui_prefs`` row in as the top
merge layer, and ``PUT /ui-config/me`` upserts it. The server performs the
four-layer merge (today: System → User; tenant/role land in increment 3) so
the UI consumes the resolved document directly, ignoring any leaf it doesn't
recognise (ADR-032 §6.4).

"Reset to team default" (ADR-032 §2) is ``PUT /ui-config/me`` with an empty
body ``{}`` → an empty override that falls through to the layers below. A
later increment may add a dedicated ``DELETE`` once tenant/role defaults exist
to fall back *to*; today an empty override and no row resolve identically (the
system default).

Later increments add ``PUT /ui-config/{tenant,role/{role}}`` (admin-gated
defaults); they only feed override layers into :func:`resolve_ui_config`,
leaving this contract fixed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.user_auth import AuthenticatedUser, get_current_user
from tagpulse.repositories.timescaledb.session import get_session
from tagpulse.repositories.timescaledb.user_ui_prefs import UserUiPrefsRepository
from tagpulse.services.ui_config import (
    UiConfig,
    resolve_ui_config,
    validate_ui_config_override,
)

router = APIRouter(prefix="/ui-config", tags=["ui-config"])


async def _resolve_for_user(user: AuthenticatedUser, session: AsyncSession) -> UiConfig:
    """Resolve the effective config for ``user``, folding their override layer.

    A caller with no ``user_id`` (the X-Tenant-ID backward-compat path) has no
    per-user layer, so they get the system default. A user with no stored row
    is the "reset to team default" state — also no user layer.
    """
    overrides: list[dict[str, Any]] = []
    if user.user_id is not None:
        stored = await UserUiPrefsRepository(session).get_for_user(user.user_id)
        if stored:
            overrides.append(stored)
    return resolve_ui_config(overrides)


@router.get("", response_model=UiConfig)
async def get_ui_config(
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UiConfig:
    """Return the presentation config resolved for the calling viewer.

    Folds System → User (tenant/role layers land in increment 3). The merge is
    done server-side so the UI never reconstructs it.
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
    return resolve_ui_config([override] if override else [])
