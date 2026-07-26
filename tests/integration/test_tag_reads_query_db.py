"""Live-DB integration tests for the tag-reads query SQL (C-XSD1).

Backfills the fake-only paths flagged in docs/backlog.md: the ``asset_q``
correlated ``EXISTS`` (bound-asset-name filter — the in-memory fake can't model
the binding→asset join) and ``GET /tag-reads/facets`` distinct values.
"""

from __future__ import annotations

import pytest

from tagpulse.repositories.timescaledb.tag_reads import TimescaleTagReadRepository


@pytest.mark.asyncio
async def test_asset_q_filters_by_bound_asset_name(
    session, make_tenant, make_category, make_asset, make_device, make_binding, make_tag_read
) -> None:
    tenant = await make_tenant(session)
    cat = await make_category(session, tenant)
    device = await make_device(session, tenant, name="reader")

    truck = await make_asset(session, tenant, cat, name="Truck 42")
    plane = await make_asset(session, tenant, cat, name="Airplane 7")
    # Active bindings: tag value == the read's tag_id (one of the EXISTS forms).
    await make_binding(session, tenant, truck, binding_value="E280-TRUCK")
    await make_binding(session, tenant, plane, binding_value="E280-PLANE")
    await make_tag_read(session, tenant, device, tag_id="E280-TRUCK", epc_scheme="sgtin-96")
    await make_tag_read(session, tenant, device, tag_id="E280-PLANE", epc_scheme="sscc-96")

    repo = TimescaleTagReadRepository(session)

    # Only the read bound to "Truck 42" matches the wildcard.
    truck_reads = await repo.query(tenant, asset_q="Truck*")
    assert {r.tag_id for r in truck_reads} == {"E280-TRUCK"}

    # A wildcard matching no asset name yields nothing.
    assert await repo.query(tenant, asset_q="Submarine*") == []

    # No asset_q → both reads returned (filter is off).
    assert len(await repo.query(tenant)) == 2


@pytest.mark.asyncio
async def test_asset_q_is_tenant_scoped(
    session, make_tenant, make_category, make_asset, make_device, make_binding, make_tag_read
) -> None:
    t1 = await make_tenant(session)
    t2 = await make_tenant(session)
    cat = await make_category(session, t1)
    device = await make_device(session, t1, name="reader")
    truck = await make_asset(session, t1, cat, name="Truck 42")
    await make_binding(session, t1, truck, binding_value="E280-TRUCK")
    await make_tag_read(session, t1, device, tag_id="E280-TRUCK")

    repo = TimescaleTagReadRepository(session)
    # t2 has no reads/bindings — the EXISTS + tenant filter yields nothing.
    assert await repo.query(t2, asset_q="Truck*") == []


@pytest.mark.asyncio
async def test_facets_returns_distinct_scheme_and_antenna(
    session, make_tenant, make_device, make_tag_read
) -> None:
    tenant = await make_tenant(session)
    device = await make_device(session, tenant, name="reader")
    await make_tag_read(
        session, tenant, device, tag_id="a", epc_scheme="sgtin-96", reader_antenna=1
    )
    await make_tag_read(
        session, tenant, device, tag_id="b", epc_scheme="sgtin-96", reader_antenna=2
    )
    await make_tag_read(session, tenant, device, tag_id="c", epc_scheme="sscc-96", reader_antenna=1)

    repo = TimescaleTagReadRepository(session)
    facets = await repo.facets(tenant)
    assert facets["epc_scheme"] == ["sgtin-96", "sscc-96"]
    assert facets["reader_antenna"] == ["1", "2"]
