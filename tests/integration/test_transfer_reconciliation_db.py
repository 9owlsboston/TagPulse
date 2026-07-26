"""Live-DB integration tests for the Transfers + Reconciliation wildcard filters (C-4PAD).

Backfills the last fake/contract-only SQL paths from the Sprint 77 filter audit
(docs/backlog.md): the tag-transfers ``epc_q`` wildcard + ``statuses`` multi-select
(:meth:`TimescaleTagTransferRepository.list_for_tenant`), and the ``q`` wildcard on
each of the three tag-reconciliation views. The in-memory fakes model neither the
``ILIKE`` wildcard grammar nor the cross-table reconciliation joins, so these need a
real TimescaleDB.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tagpulse.models.database import TagTransferModel
from tagpulse.repositories.timescaledb.tags import TimescaleTagTransferRepository
from tagpulse.services import tag_reconciliation


async def _add_transfer(
    session,
    *,
    from_tenant: uuid.UUID,
    to_tenant: uuid.UUID,
    requested_by: uuid.UUID,
    epc_hex: str,
    status: str,
) -> None:
    session.add(
        TagTransferModel(
            request_id=uuid.uuid4(),
            from_tenant_id=from_tenant,
            to_tenant_id=to_tenant,
            epc_hex=epc_hex,
            status=status,
            requested_by=requested_by,
        )
    )
    await session.flush()


# --------------------------------------------------------------------------- #
# Tag transfers — epc_q wildcard + statuses multi-select
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_transfer_epc_q_wildcard_filters_by_epc_hex(session, make_tenant, make_user) -> None:
    home = await make_tenant(session)
    other = await make_tenant(session)
    user = await make_user(session, home)
    await _add_transfer(
        session,
        from_tenant=home,
        to_tenant=other,
        requested_by=user,
        epc_hex="E280AA01",
        status="requested",
    )
    await _add_transfer(
        session,
        from_tenant=home,
        to_tenant=other,
        requested_by=user,
        epc_hex="E280BB02",
        status="requested",
    )
    repo = TimescaleTagTransferRepository(session)

    # Wildcard over epc_hex (same grammar as the tag list ``q``).
    hits = await repo.list_for_tenant(home, epc_q="E280AA*")
    assert {r.epc_hex for r in hits} == {"E280AA01"}

    # A wildcard matching nothing yields an empty page.
    assert await repo.list_for_tenant(home, epc_q="ZZZZ*") == []

    # No epc_q → both rows returned (filter is off).
    assert len(await repo.list_for_tenant(home)) == 2


@pytest.mark.asyncio
async def test_transfer_statuses_multiselect(session, make_tenant, make_user) -> None:
    home = await make_tenant(session)
    other = await make_tenant(session)
    user = await make_user(session, home)
    for epc, status in (
        ("E2800001", "requested"),
        ("E2800002", "completed"),
        ("E2800003", "failed"),
    ):
        await _add_transfer(
            session,
            from_tenant=home,
            to_tenant=other,
            requested_by=user,
            epc_hex=epc,
            status=status,
        )
    repo = TimescaleTagTransferRepository(session)

    # Multi-select status (the column checkbox list → ``status IN (...)``).
    two = await repo.list_for_tenant(home, statuses=["requested", "failed"])
    assert {r.status for r in two} == {"requested", "failed"}
    assert len(two) == 2

    # epc_q AND statuses compose (both predicates apply).
    combined = await repo.list_for_tenant(
        home, statuses=["requested", "completed"], epc_q="E2800002*"
    )
    assert {r.epc_hex for r in combined} == {"E2800002"}


@pytest.mark.asyncio
async def test_transfer_list_is_tenant_scoped(session, make_tenant, make_user) -> None:
    home = await make_tenant(session)
    other = await make_tenant(session)
    stranger = await make_tenant(session)
    user = await make_user(session, home)
    await _add_transfer(
        session,
        from_tenant=home,
        to_tenant=other,
        requested_by=user,
        epc_hex="E280CAFE",
        status="requested",
    )
    repo = TimescaleTagTransferRepository(session)

    # A tenant that is neither the from- nor to-side sees nothing.
    assert await repo.list_for_tenant(stranger, epc_q="E280CAFE*") == []
    # The counterparty (to-side) sees it (list returns both sides).
    assert len(await repo.list_for_tenant(other, epc_q="E280CAFE*")) == 1


# --------------------------------------------------------------------------- #
# Reconciliation views — q wildcard
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_registered_unread_q_filters_by_epc_hex(session, make_tenant, make_tag) -> None:
    tenant = await make_tenant(session)
    stale = datetime.now(UTC) - timedelta(days=30)
    # Both live + stale → both are "registered but unread"; q narrows the set.
    await make_tag(session, tenant, epc_hex="E280AA01", status="registered", last_seen_at=None)
    await make_tag(session, tenant, epc_hex="E280BB02", status="active", last_seen_at=stale)

    rows = await tag_reconciliation.query_registered_unread(
        session, tenant, days=7, limit=100, offset=0, q="E280AA*"
    )
    assert {r.epc_hex for r in rows} == {"E280AA01"}

    # No q → both surface (filter off).
    all_rows = await tag_reconciliation.query_registered_unread(
        session, tenant, days=7, limit=100, offset=0
    )
    assert {r.epc_hex for r in all_rows} == {"E280AA01", "E280BB02"}


@pytest.mark.asyncio
async def test_unregistered_reading_q_filters_by_tag_id(
    session, make_tenant, make_device, make_tag_read
) -> None:
    tenant = await make_tenant(session)
    device = await make_device(session, tenant, name="reader")
    # tag_known=False is what the registrar sets for edge EPCs absent from the registry.
    await make_tag_read(session, tenant, device, tag_id="EPC-UNK-AA", tag_known=False)
    await make_tag_read(session, tenant, device, tag_id="EPC-UNK-BB", tag_known=False)
    # A known read must never appear regardless of q.
    await make_tag_read(session, tenant, device, tag_id="EPC-KNOWN", tag_known=True)

    rows = await tag_reconciliation.query_unregistered_reading(
        session, tenant, days=7, limit=100, offset=0, q="EPC-UNK-AA*"
    )
    assert {r.tag_id for r in rows} == {"EPC-UNK-AA"}

    all_rows = await tag_reconciliation.query_unregistered_reading(
        session, tenant, days=7, limit=100, offset=0
    )
    assert {r.tag_id for r in all_rows} == {"EPC-UNK-AA", "EPC-UNK-BB"}


@pytest.mark.asyncio
async def test_bindings_on_retired_q_filters_by_binding_value(
    session, make_tenant, make_product, make_stock_item, make_tag
) -> None:
    tenant = await make_tenant(session)
    product = await make_product(session, tenant)
    # Stock item bound (epc) to a tag that is in a terminal status → the inconsistency.
    await make_stock_item(session, tenant, product, binding_value="E280AA01", binding_kind="epc")
    await make_stock_item(session, tenant, product, binding_value="E280BB02", binding_kind="epc")
    await make_tag(session, tenant, epc_hex="E280AA01", status="retired")
    await make_tag(session, tenant, epc_hex="E280BB02", status="defective")

    rows = await tag_reconciliation.query_bindings_on_retired(
        session, tenant, limit=100, offset=0, q="E280AA*"
    )
    assert {r.epc_hex for r in rows} == {"E280AA01"}

    all_rows = await tag_reconciliation.query_bindings_on_retired(
        session, tenant, limit=100, offset=0
    )
    assert {r.epc_hex for r in all_rows} == {"E280AA01", "E280BB02"}
