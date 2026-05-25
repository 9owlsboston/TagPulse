"""Per-request context plumbing (Sprint 13b — Multi-tier Foundations).

Single seam used by both request-scoped code (FastAPI middleware / `Depends`)
and non-request code (background workers, scripts, scheduled jobs):

* ``db_session_var`` — the currently-bound :class:`AsyncSession`.
* ``current_tenant_var`` — the currently-bound tenant id (``None`` outside any
  tenant scope; cross-tenant admin code must pass tenant ids explicitly to
  :class:`tagpulse.repositories.admin.AdminRepository`).
* :func:`tenant_context` — async context manager that binds both vars from a
  pool resolved via :class:`tagpulse.core.pool_registry.PoolRegistry`.

Per [docs/design/storage-strategy.md §6 Q2](../../docs/design/storage-strategy.md).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

db_session_var: ContextVar[AsyncSession | None] = ContextVar("tagpulse_db_session", default=None)
current_tenant_var: ContextVar[uuid.UUID | None] = ContextVar(
    "tagpulse_current_tenant", default=None
)


def get_bound_session() -> AsyncSession:
    """Return the session bound to the current context, or raise.

    Background workers and admin code that intentionally do not bind a tenant
    can still call this once they have entered :func:`tenant_context` (or any
    other binding scope set by middleware).
    """
    session = db_session_var.get()
    if session is None:  # pragma: no cover — defensive
        raise RuntimeError(
            "No async session bound. Call inside `tenant_context()` or behind "
            "the FastAPI session dependency."
        )
    return session


@asynccontextmanager
async def tenant_context(
    tenant_id: uuid.UUID,
    *,
    pool_key: str = "shared_default",
) -> AsyncIterator[AsyncSession]:
    """Bind a session + tenant id for non-request code.

    Resolves the pool from :class:`PoolRegistry`, opens a session, sets the
    ``app.current_tenant_id`` GUC for RLS, and binds both contextvars. On exit
    the bindings are reset and the session is closed (commit on success, roll
    back on exception).
    """
    # Local import — avoids a hard import cycle between context and registry,
    # which itself reads settings.
    from tagpulse.core.pool_registry import get_pool_registry

    registry = get_pool_registry()
    sessionmaker = registry.sessionmaker_for(pool_key)

    session_token = None
    tenant_token = None
    async with sessionmaker() as session:
        try:
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            session_token = db_session_var.set(session)
            tenant_token = current_tenant_var.set(tenant_id)
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            if tenant_token is not None:
                current_tenant_var.reset(tenant_token)
            if session_token is not None:
                db_session_var.reset(session_token)
