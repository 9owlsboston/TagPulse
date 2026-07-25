"""Live-DB integration tests for gateway_subject_grants (Sprint 81 deferred paths).

Exercises the real partial-unique index + soft-revoke + set query against
TimescaleDB — the unit tests only used in-memory fakes. Gated by
``TAGPULSE_INTEGRATION_DB_URL`` (see conftest).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from tagpulse.repositories.timescaledb.gateway_subject_grants import (
    TimescaleGatewaySubjectGrantRepository,
)


@pytest.mark.asyncio
async def test_grant_lifecycle(session, make_tenant, make_device) -> None:
    tenant = await make_tenant(session)
    gw = await make_device(session, tenant, name="gw")
    subj = uuid.uuid4()
    repo = TimescaleGatewaySubjectGrantRepository(session)

    await repo.create(tenant, gw, "asset", subj, None)
    assert await repo.get_active(tenant, gw, "asset", subj) is not None

    assert await repo.revoke(tenant, gw, "asset", subj, datetime.now(UTC)) is True
    assert await repo.get_active(tenant, gw, "asset", subj) is None

    # Re-create the same pair after revoke: the partial-unique index
    # (WHERE revoked_at IS NULL) excludes the revoked row, so this succeeds.
    await repo.create(tenant, gw, "asset", subj, None)
    assert await repo.active_subject_set(tenant, gw) == {("asset", subj)}


@pytest.mark.asyncio
async def test_duplicate_active_grant_rejected(session, make_tenant, make_device) -> None:
    tenant = await make_tenant(session)
    gw = await make_device(session, tenant, name="gw")
    subj = uuid.uuid4()
    repo = TimescaleGatewaySubjectGrantRepository(session)

    await repo.create(tenant, gw, "asset", subj, None)
    # A second active grant for the same (tenant, gateway, kind, subject) hits
    # the partial-unique index. This is the test's LAST action — the aborted
    # transaction is discarded by the session fixture's rollback.
    with pytest.raises(IntegrityError):
        await repo.create(tenant, gw, "asset", subj, None)


@pytest.mark.asyncio
async def test_active_subject_set_tenant_and_gateway_scoped(
    session, make_tenant, make_device
) -> None:
    t1 = await make_tenant(session)
    t2 = await make_tenant(session)
    gw = await make_device(session, t1, name="gw")
    subj = uuid.uuid4()
    repo = TimescaleGatewaySubjectGrantRepository(session)

    await repo.create(t1, gw, "asset", subj, None)
    assert await repo.active_subject_set(t1, gw) == {("asset", subj)}
    # Another tenant sees none of t1's grants.
    assert await repo.active_subject_set(t2, gw) == set()
