"""Configurable UI config endpoint (Sprint 60, ADR-032).

Increment 1 (ADR-032 §7 step 1): a single server-resolved ``GET /ui-config``
over system defaults only — no tenant/role/user persistence yet. The server
performs the four-layer merge (currently just the system default) so the UI
never reconstructs it and can consume the resolved document directly,
ignoring any leaf it doesn't recognise (ADR-032 §6.4).

Later increments add ``PUT /ui-config/me`` (user overrides) and
``PUT /ui-config/{tenant,role/{role}}`` (admin-gated defaults); they only feed
override layers into :func:`resolve_ui_config`, leaving this contract fixed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from tagpulse.core.user_auth import AuthenticatedUser, get_current_user
from tagpulse.services.ui_config import UiConfig, resolve_ui_config

router = APIRouter(prefix="/ui-config", tags=["ui-config"])


@router.get("", response_model=UiConfig)
async def get_ui_config(
    user: AuthenticatedUser = Depends(get_current_user),
) -> UiConfig:
    """Return the presentation config resolved for the calling viewer.

    Increment 1 resolves the system default only, so every authenticated
    caller gets the same document; the ``user`` dependency is here so later
    increments can fold this viewer's role/user override layers without an
    endpoint signature change.
    """
    return resolve_ui_config()
