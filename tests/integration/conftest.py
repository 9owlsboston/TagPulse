"""Live-DB integration harness (C-6RTX).

Provides an async session bound to a real TimescaleDB (schema built once via
``alembic upgrade head``) plus factory fixtures for the minimal prerequisite
rows. Every test rolls back at teardown, so flushed writes are visible in-test
but never persist; each test also mints a fresh ``tenant_id`` for isolation.

Skipped entirely unless ``TAGPULSE_INTEGRATION_DB_URL`` is set (same gate as the
migration round-trip), so ``make test`` stays hermetic. The ``integration-test``
CI job sets it to a ``timescale/timescaledb`` service container.

Engine notes: a fresh ``create_async_engine(..., poolclass=NullPool)`` is built
inside each test's event loop (``asyncio_mode=auto`` gives each test its own
loop), avoiding "Future attached to a different loop" from a shared pooled
engine.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tagpulse.models.database import (
    AssetModel,
    CategoryModel,
    DeviceModel,
    TenantModel,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_URL_ENV = "TAGPULSE_INTEGRATION_DB_URL"


@pytest.fixture(scope="session")
def _migrated_db() -> str:
    """Run ``alembic upgrade head`` once against the integration DB."""
    url = os.environ.get(DB_URL_ENV)
    if not url:
        pytest.skip(f"{DB_URL_ENV} not set — live-DB integration tests skipped")
    env = {**os.environ, "DATABASE_URL": url}
    res = subprocess.run(  # noqa: S603 — args are constants
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        pytest.fail(f"alembic upgrade head failed:\n{res.stdout}\n{res.stderr}")
    return url


@pytest_asyncio.fixture
async def session(_migrated_db: str) -> AsyncIterator[AsyncSession]:
    """A rolled-back-at-teardown async session on a fresh NullPool engine."""
    engine = create_async_engine(_migrated_db, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        try:
            yield s
        finally:
            await s.rollback()
    await engine.dispose()


# --------------------------------------------------------------------------- #
# Factories (callables that take the session; return the new row's id)
# --------------------------------------------------------------------------- #


@pytest.fixture
def make_tenant() -> Callable[..., Awaitable[uuid.UUID]]:
    async def _make(s: AsyncSession, *, slug: str | None = None) -> uuid.UUID:
        row = TenantModel(name="Test Tenant", slug=slug or f"t-{uuid.uuid4().hex[:12]}")
        s.add(row)
        await s.flush()
        return row.id

    return _make


@pytest.fixture
def make_category() -> Callable[..., Awaitable[uuid.UUID]]:
    async def _make(
        s: AsyncSession, tenant_id: uuid.UUID, *, category_type: str = "object"
    ) -> uuid.UUID:
        # category_type is CHECK-constrained (migration 037) to
        # ('liquid_container','reference_tag','rti_container','object').
        row = CategoryModel(tenant_id=tenant_id, name="Test Category", category_type=category_type)
        s.add(row)
        await s.flush()
        return row.id

    return _make


@pytest.fixture
def make_device() -> Callable[..., Awaitable[uuid.UUID]]:
    async def _make(s: AsyncSession, tenant_id: uuid.UUID, *, name: str = "Gateway") -> uuid.UUID:
        row = DeviceModel(tenant_id=tenant_id, name=name)
        s.add(row)
        await s.flush()
        return row.id

    return _make


@pytest.fixture
def make_asset() -> Callable[..., Awaitable[uuid.UUID]]:
    async def _make(
        s: AsyncSession, tenant_id: uuid.UUID, category_id: uuid.UUID, *, name: str = "Truck 42"
    ) -> uuid.UUID:
        row = AssetModel(tenant_id=tenant_id, name=name, category_id=category_id)
        s.add(row)
        await s.flush()
        return row.id

    return _make
