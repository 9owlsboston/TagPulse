"""TimescaleDB repository for per-user UI presentation prefs (Sprint 60).

Backs the ADR-032 §3 *user* override layer. ``prefs`` is stored sparse
(subset of the §4 leaf document); resolution onto role/tenant/system happens
in :mod:`tagpulse.services.ui_config`, not here.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import UserUiPrefsModel


class UserUiPrefsRepository:
    """Persists per-user UI overrides keyed on the globally-unique ``user_id``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_user(self, user_id: uuid.UUID) -> dict[str, Any] | None:
        """Return the user's sparse override doc, or ``None`` if unset.

        ``None`` (no row) is the "reset to team default" state — the caller
        folds in no user layer and falls through to role/tenant/system.
        """
        row = await self._session.scalar(
            select(UserUiPrefsModel).where(UserUiPrefsModel.user_id == user_id)
        )
        return dict(row.prefs) if row is not None else None

    async def upsert(self, user_id: uuid.UUID, tenant_id: uuid.UUID, prefs: dict[str, Any]) -> None:
        """Insert or replace the user's override doc (whole-document upsert).

        ``prefs`` is the already-validated sparse override; the user owns their
        own layer, so a ``PUT`` replaces it wholesale (per-leaf merge happens
        across *layers* at resolve time, not within the user layer).
        """
        stmt = (
            pg_insert(UserUiPrefsModel)
            .values(user_id=user_id, tenant_id=tenant_id, prefs=prefs)
            .on_conflict_do_update(
                index_elements=[UserUiPrefsModel.user_id],
                set_={"prefs": prefs, "updated_at": func.now()},
            )
        )
        await self._session.execute(stmt)
