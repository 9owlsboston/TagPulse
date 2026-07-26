"""Live-DB integration tests for get_by_binding_value (Sprint 82).

Exercises the real bindings→assets JOIN + canonical (raw + upper) matching +
tenant isolation + unbound exclusion against TimescaleDB.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import update

from tagpulse.models.database import AssetTagBindingModel
from tagpulse.models.schemas import AssetTagBindingCreate
from tagpulse.repositories.timescaledb.assets import TimescaleAssetTagBindingRepository

VIN = "1HGCM82633A004352"


@pytest.mark.asyncio
async def test_resolves_by_raw_and_lowercased_scan(
    session, make_tenant, make_category, make_asset
) -> None:
    tenant = await make_tenant(session)
    cat = await make_category(session, tenant)
    asset = await make_asset(session, tenant, cat)
    repo = TimescaleAssetTagBindingRepository(session)
    await repo.create(tenant, asset, AssetTagBindingCreate(binding_value=VIN, binding_kind="vin"))

    raw = await repo.get_by_binding_value(tenant, VIN)
    assert raw is not None and raw.id == asset
    assert raw.binding_kind == "vin"
    assert raw.binding_value == VIN  # stored canonical form

    # A differently-cased scan resolves via the canonical candidate.
    lowered = await repo.get_by_binding_value(tenant, VIN.lower())
    assert lowered is not None and lowered.id == asset
    assert lowered.binding_kind == "vin"


@pytest.mark.asyncio
async def test_tenant_scoped_and_unbound_excluded(
    session, make_tenant, make_category, make_asset
) -> None:
    t1 = await make_tenant(session)
    t2 = await make_tenant(session)
    cat = await make_category(session, t1)
    asset = await make_asset(session, t1, cat)
    repo = TimescaleAssetTagBindingRepository(session)
    await repo.create(t1, asset, AssetTagBindingCreate(binding_value=VIN, binding_kind="vin"))

    # Another tenant with no such binding resolves to None.
    assert await repo.get_by_binding_value(t2, VIN) is None

    # Once unbound, the value no longer resolves.
    await session.execute(
        update(AssetTagBindingModel)
        .where(
            AssetTagBindingModel.tenant_id == t1,
            AssetTagBindingModel.binding_value == VIN,
        )
        .values(unbound_at=datetime.now(UTC))
    )
    await session.flush()
    assert await repo.get_by_binding_value(t1, VIN) is None
