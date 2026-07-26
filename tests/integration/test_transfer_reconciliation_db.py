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


def _epc(suffix: str) -> str:
    """A valid 24-char canonical EPC hex ending in ``suffix``.

    ``tags``/``tag_transfers``/``stock_items`` all CHECK epc_hex against
    ``^[0-9A-F]{16,128}$`` (migration 043), so test EPCs must be uppercase hex
    of that length — a short literal like ``E280AA01`` is rejected.
    """
    return "E2" + "0" * (24 - 2 - len(suffix)) + suffix


def _wild(epc: str) -> str:
    """Prefix wildcard for ``epc`` (drops the last 2 chars, appends ``*``)."""
    return epc[:-2] + "*"


async def _add_transfer(
    session,
    *,
    from_tenant: uuid.UUID,
    to_tenant: uuid.UUID,
    requested_by: uuid.UUID,
    epc_hex: str,
    status: str,
) -> None:
    # ck_tag_transfers_completed_at (migration 043): completed_at is set iff the
    # transfer is terminal (completed/failed) and NULL while requested.
    completed_at = datetime.now(UTC) if status in ("completed", "failed") else None
    session.add(
        TagTransferModel(
            request_id=uuid.uuid4(),
            from_tenant_id=from_tenant,
            to_tenant_id=to_tenant,
            epc_hex=epc_hex,
            status=status,
            requested_by=requested_by,
            completed_at=completed_at,
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
    epc_aa, epc_bb = _epc("AA01"), _epc("BB02")
    await _add_transfer(
        session,
        from_tenant=home,
        to_tenant=other,
        requested_by=user,
        epc_hex=epc_aa,
        status="requested",
    )
    await _add_transfer(
        session,
        from_tenant=home,
        to_tenant=other,
        requested_by=user,
        epc_hex=epc_bb,
        status="requested",
    )
    repo = TimescaleTagTransferRepository(session)

    # Wildcard over epc_hex (same grammar as the tag list ``q``).
    hits = await repo.list_for_tenant(home, epc_q=_wild(epc_aa))
    assert {r.epc_hex for r in hits} == {epc_aa}

    # A wildcard matching nothing yields an empty page.
    assert await repo.list_for_tenant(home, epc_q="FFFF*") == []

    # No epc_q → both rows returned (filter is off).
    assert len(await repo.list_for_tenant(home)) == 2


@pytest.mark.asyncio
async def test_transfer_statuses_multiselect(session, make_tenant, make_user) -> None:
    home = await make_tenant(session)
    other = await make_tenant(session)
    user = await make_user(session, home)
    epc1, epc2, epc3 = _epc("A001"), _epc("B002"), _epc("C003")
    for epc, status in (
        (epc1, "requested"),
        (epc2, "completed"),
        (epc3, "failed"),
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
        home, statuses=["requested", "completed"], epc_q=_wild(epc2)
    )
    assert {r.epc_hex for r in combined} == {epc2}


@pytest.mark.asyncio
async def test_transfer_list_is_tenant_scoped(session, make_tenant, make_user) -> None:
    home = await make_tenant(session)
    other = await make_tenant(session)
    stranger = await make_tenant(session)
    user = await make_user(session, home)
    epc = _epc("CAFE")
    await _add_transfer(
        session,
        from_tenant=home,
        to_tenant=other,
        requested_by=user,
        epc_hex=epc,
        status="requested",
    )
    repo = TimescaleTagTransferRepository(session)

    # A tenant that is neither the from- nor to-side sees nothing.
    assert await repo.list_for_tenant(stranger, epc_q=_wild(epc)) == []
    # The counterparty (to-side) sees it (list returns both sides).
    assert len(await repo.list_for_tenant(other, epc_q=_wild(epc))) == 1


# --------------------------------------------------------------------------- #
# Reconciliation views — q wildcard
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_registered_unread_q_filters_by_epc_hex(session, make_tenant, make_tag) -> None:
    tenant = await make_tenant(session)
    stale = datetime.now(UTC) - timedelta(days=30)
    epc_aa, epc_bb = _epc("AA01"), _epc("BB02")
    # Both live + stale → both are "registered but unread"; q narrows the set.
    await make_tag(session, tenant, epc_hex=epc_aa, status="registered", last_seen_at=None)
    await make_tag(session, tenant, epc_hex=epc_bb, status="active", last_seen_at=stale)

    rows = await tag_reconciliation.query_registered_unread(
        session, tenant, days=7, limit=100, offset=0, q=_wild(epc_aa)
    )
    assert {r.epc_hex for r in rows} == {epc_aa}

    # No q → both surface (filter off).
    all_rows = await tag_reconciliation.query_registered_unread(
        session, tenant, days=7, limit=100, offset=0
    )
    assert {r.epc_hex for r in all_rows} == {epc_aa, epc_bb}


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
    epc_aa, epc_bb = _epc("AA01"), _epc("BB02")
    # Stock item bound (epc) to a tag that is in a terminal status → the inconsistency.
    await make_stock_item(session, tenant, product, binding_value=epc_aa, binding_kind="epc")
    await make_stock_item(session, tenant, product, binding_value=epc_bb, binding_kind="epc")
    await make_tag(session, tenant, epc_hex=epc_aa, status="retired")
    await make_tag(session, tenant, epc_hex=epc_bb, status="defective")

    rows = await tag_reconciliation.query_bindings_on_retired(
        session, tenant, limit=100, offset=0, q=_wild(epc_aa)
    )
    assert {r.epc_hex for r in rows} == {epc_aa}

    all_rows = await tag_reconciliation.query_bindings_on_retired(
        session, tenant, limit=100, offset=0
    )
    assert {r.epc_hex for r in all_rows} == {epc_aa, epc_bb}
