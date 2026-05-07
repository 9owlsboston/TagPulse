"""Sprint 15b — case/pallet containment via stock_items.parent_stock_item_id.

Verifies the schema-level wiring: ``StockItemCreate`` accepts
``parent_stock_item_id``, ``StockItemResponse`` exposes it, and ``StockItemUpdate``
can patch it.
"""

from __future__ import annotations

import uuid

from tagpulse.models.schemas import (
    StockItemCreate,
    StockItemResponse,
    StockItemUpdate,
)


def test_stock_item_create_accepts_parent() -> None:
    parent = uuid.uuid4()
    payload = StockItemCreate(
        product_id=uuid.uuid4(),
        binding_value="urn:epc:id:sgtin:0614141.812345.6789",
        parent_stock_item_id=parent,
    )
    assert payload.parent_stock_item_id == parent


def test_stock_item_create_parent_optional_default_none() -> None:
    payload = StockItemCreate(
        product_id=uuid.uuid4(),
        binding_value="urn:epc:id:sgtin:0614141.812345.6790",
    )
    assert payload.parent_stock_item_id is None


def test_stock_item_response_round_trips_parent() -> None:
    from datetime import UTC, datetime

    parent = uuid.uuid4()
    now = datetime.now(UTC)
    resp = StockItemResponse(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        product_id=uuid.uuid4(),
        lot_id=None,
        parent_stock_item_id=parent,
        binding_value="urn:epc:id:sgtin:0614141.812345.6791",
        binding_kind="epc",
        state="in_stock",
        current_zone_id=None,
        first_seen_at=now,
        last_seen_at=now,
        consumed_at=None,
    )
    assert resp.parent_stock_item_id == parent


def test_stock_item_update_accepts_parent_repatching() -> None:
    new_parent = uuid.uuid4()
    patch = StockItemUpdate(parent_stock_item_id=new_parent)
    data = patch.model_dump(exclude_unset=True)
    assert data == {"parent_stock_item_id": new_parent}


def test_stock_item_update_can_clear_parent() -> None:
    patch = StockItemUpdate(parent_stock_item_id=None)
    data = patch.model_dump(exclude_unset=True)
    assert data == {"parent_stock_item_id": None}
