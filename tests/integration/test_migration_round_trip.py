"""Sprint 19 carry-over: alembic round-trip CI harness.

Validates the full migration chain (currently ``001`` -> ``031``) by:

1. Running ``alembic upgrade head`` against an empty database.
2. Running ``alembic downgrade -1`` from head.
3. Running ``alembic upgrade head`` again to restore.

Step 2 is the regression catcher — many migrations historically forgot
to symmetrically drop a constraint or column (Sprint 18's audit found
two such gaps). Without this harness, a broken downgrade only surfaces
during a production rollback, which is the worst possible time.

The test is skipped when ``TAGPULSE_INTEGRATION_DB_URL`` is not set so
``make test`` (unit tests only) stays hermetic. CI runs it via
``make migration-check`` after spinning up a TimescaleDB container.

Per docs/roadmap.md Sprint 19, this is the deferred Sprint 18 item.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_URL_ENV = "TAGPULSE_INTEGRATION_DB_URL"


pytestmark = pytest.mark.skipif(
    DB_URL_ENV not in os.environ,
    reason=(
        f"{DB_URL_ENV} not set — set to a TimescaleDB connection string "
        "(e.g. postgresql+asyncpg://tagpulse:tagpulse@localhost:5432/test) "
        "and ensure the database is empty."
    ),
)


def _alembic(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Invoke alembic with ``DATABASE_URL`` from the integration env var."""
    env = dict(os.environ)
    env["DATABASE_URL"] = os.environ[DB_URL_ENV]
    return subprocess.run(  # noqa: S603 — args are constants in this module
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_alembic_upgrade_downgrade_upgrade_round_trip() -> None:
    """Full chain must upgrade, downgrade one step, then upgrade again."""
    up = _alembic(["upgrade", "head"])
    assert up.returncode == 0, f"initial upgrade failed:\nstdout={up.stdout}\nstderr={up.stderr}"

    down = _alembic(["downgrade", "-1"])
    assert down.returncode == 0, f"downgrade -1 failed:\nstdout={down.stdout}\nstderr={down.stderr}"

    up2 = _alembic(["upgrade", "head"])
    assert up2.returncode == 0, f"re-upgrade failed:\nstdout={up2.stdout}\nstderr={up2.stderr}"
