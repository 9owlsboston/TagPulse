"""Async database session factory for TimescaleDB/PostgreSQL.

The legacy ``settings.database_url`` is still honoured: it is the DSN of the
``shared_default`` pool inside :class:`tagpulse.core.pool_registry.PoolRegistry`.
``get_session`` resolves that pool and additionally binds the new
``db_session_var`` so non-request callers can rely on a single seam.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tagpulse.core.context import db_session_var
from tagpulse.core.pool_registry import get_pool_registry


def _shared_default() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    entry = get_pool_registry()._entries["shared_default"]  # noqa: SLF001
    return entry.engine, entry.sessionmaker


# Backwards-compat re-exports — older imports `from … import engine` /
# `async_session_factory` continue to work.
engine, async_session_factory = _shared_default()


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session, rolled back on error."""
    sessionmaker = get_pool_registry().sessionmaker_for("shared_default")
    async with sessionmaker() as session:
        token = db_session_var.set(session)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            db_session_var.reset(token)
