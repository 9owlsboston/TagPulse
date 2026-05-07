"""Sprint 22 A7: startup migration-version assertion.

Compares the ``alembic_version`` row in the database against the head
revision discovered from the local Alembic script directory. Used by:

* ``/health/ready`` — degraded if drift detected (always runs).
* ``lifespan`` startup — refuses to boot when
  ``settings.strict_migration_check`` is ``True`` (default in
  staging/production via the ``Settings`` validator).

The expected head is computed once at import time so the runtime cost
is a single ``SELECT version_num FROM alembic_version`` per call.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class MigrationVersionMismatch(RuntimeError):  # noqa: N818
    """Raised at startup when DB schema doesn't match the code's head."""


def _alembic_ini_path() -> Path:
    """Locate alembic.ini relative to the package or working dir."""
    # Package install: alembic.ini is shipped at repo root, two parents
    # up from src/tagpulse/core/migration_check.py.
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent.parent.parent / "alembic.ini",
        Path.cwd() / "alembic.ini",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("alembic.ini not found in expected locations")


@lru_cache(maxsize=1)
def expected_head_revision() -> str:
    """Discover the head revision from the local migrations directory."""
    cfg = Config(str(_alembic_ini_path()))
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    if head is None:
        raise RuntimeError("Alembic script directory has no head revision")
    return head


async def fetch_db_revision(
    session_factory: Callable[[], AsyncSession],
) -> str | None:
    """Read the current ``alembic_version`` row, or ``None`` if missing."""
    async with session_factory() as session:
        result = await session.execute(
            text("SELECT version_num FROM alembic_version LIMIT 1")
        )
        row = result.scalar_one_or_none()
    if row is None:
        return None
    return str(row)


async def assert_migration_head(
    session_factory: Callable[[], AsyncSession],
) -> None:
    """Raise ``MigrationVersionMismatch`` if DB != code head."""
    expected = expected_head_revision()
    actual = await fetch_db_revision(session_factory)
    if actual is None:
        raise MigrationVersionMismatch(
            f"alembic_version table is empty; expected head={expected!r}. "
            "Run `alembic upgrade head` before starting the API."
        )
    if actual != expected:
        raise MigrationVersionMismatch(
            f"Schema drift: DB revision={actual!r} but code head={expected!r}. "
            "Run `alembic upgrade head` (or `alembic downgrade` if rolling back) "
            "before starting the API."
        )
    logger.info("migration_check ok: revision=%s", actual)
