"""Unit tests for InventoryService (Sprint 15b)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from tagpulse.api.services.inventory_service import (
    InventoryService,
    ProductNotFoundError,
    StockItemLedgerError,
)
from tagpulse.models.schemas import (
    LotCreate,
    LotResponse,
    ProductCreate,
    ProductResponse,
    ProductUpdate,
    StockItemCreate,
    StockItemResponse,
    StockItemUpdate,
    StockLevelRow,
    StockMovementResponse,
    TagDataMappingCreate,
    TagDataMappingResponse,
)


def _product(tenant_id: UUID, **kw: Any) -> ProductResponse:
    base: dict[str, Any] = dict(
        id=uuid4(),
        tenant_id=tenant_id,
        sku="SKU-1",
        gtin=None,
        name="Widget",
        category=None,
        unit="each",
        attributes=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    base.update(kw)
    return ProductResponse(**base)


def _stock(tenant_id: UUID, product_id: UUID, **kw: Any) -> StockItemResponse:
    base: dict[str, Any] = dict(
        id=uuid4(),
        tenant_id=tenant_id,
        product_id=product_id,
        lot_id=None,
        binding_value="urn:epc:sgtin:1",
        binding_kind="epc",
        state="in_stock",
        current_zone_id=None,
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
        consumed_at=None,
        metadata=None,
    )
    base.update(kw)
    return StockItemResponse(**base)


class _FakeProductRepo:
    def __init__(self) -> None:
        self.products: dict[UUID, ProductResponse] = {}

    async def create(self, tenant_id: UUID, payload: ProductCreate) -> ProductResponse:
        p = _product(tenant_id, sku=payload.sku, name=payload.name, gtin=payload.gtin)
        self.products[p.id] = p
        return p

    async def get(self, tenant_id: UUID, product_id: UUID) -> ProductResponse | None:
        p = self.products.get(product_id)
        return p if p and p.tenant_id == tenant_id else None

    async def list(  # type: ignore[no-untyped-def]
        self, tenant_id, *, category=None, q=None, limit=100, offset=0
    ):
        return list(self.products.values())

    async def update(self, tenant_id, product_id, patch):  # type: ignore[no-untyped-def]
        p = self.products.get(product_id)
        if p is None:
            return None
        data = patch.model_dump(exclude_unset=True)
        new = p.model_copy(update=data)
        self.products[product_id] = new
        return new

    async def delete(self, tenant_id: UUID, product_id: UUID) -> bool:
        return self.products.pop(product_id, None) is not None


class _FakeLotRepo:
    def __init__(self) -> None:
        self.lots: list[LotResponse] = []

    async def create(self, tenant_id, product_id, payload: LotCreate):  # type: ignore[no-untyped-def]
        lot = LotResponse(
            id=uuid4(),
            tenant_id=tenant_id,
            product_id=product_id,
            lot_code=payload.lot_code,
            manufactured_at=payload.manufactured_at,
            expires_at=payload.expires_at,
            metadata=payload.metadata,
            created_at=datetime.now(UTC),
        )
        self.lots.append(lot)
        return lot

    async def list_for_product(  # type: ignore[no-untyped-def]
        self,
        tenant_id,
        product_id,
        *,
        expiring_within_days=None,
        limit=100,
        offset=0,
    ):
        return [lot for lot in self.lots if lot.product_id == product_id]

    async def update(self, tenant_id, lot_id, patch):  # type: ignore[no-untyped-def]
        return None


class _FakeStockRepo:
    def __init__(self) -> None:
        self.items: dict[UUID, StockItemResponse] = {}
        self.deleted: list[UUID] = []

    async def create(self, tenant_id, payload: StockItemCreate):  # type: ignore[no-untyped-def]
        item = _stock(
            tenant_id,
            payload.product_id,
            lot_id=payload.lot_id,
            binding_value=payload.binding_value,
            binding_kind=payload.binding_kind,
        )
        self.items[item.id] = item
        return item

    async def get(self, tenant_id, stock_item_id):  # type: ignore[no-untyped-def]
        return self.items.get(stock_item_id)

    async def get_active_by_binding(self, tenant_id, binding_kind, binding_value):  # type: ignore[no-untyped-def]
        return next(
            (
                i
                for i in self.items.values()
                if i.binding_kind == binding_kind
                and i.binding_value == binding_value
                and i.state not in ("consumed", "expired", "lost")
            ),
            None,
        )

    async def list(self, tenant_id, **kw):  # type: ignore[no-untyped-def]
        return list(self.items.values())

    async def update(self, tenant_id, stock_item_id, patch: StockItemUpdate):  # type: ignore[no-untyped-def]
        item = self.items.get(stock_item_id)
        if item is None:
            return None
        data = patch.model_dump(exclude_unset=True)
        if data.get("state") == "consumed":
            data["consumed_at"] = datetime.now(UTC)
        new = item.model_copy(update=data)
        self.items[stock_item_id] = new
        return new

    async def stock_levels(self, tenant_id, *, product_id=None, zone_id=None):  # type: ignore[no-untyped-def]
        # Aggregate in-memory.
        buckets: dict[tuple[UUID, UUID | None, UUID | None], int] = {}
        for i in self.items.values():
            if i.state != "in_stock":
                continue
            if product_id and i.product_id != product_id:
                continue
            if zone_id and i.current_zone_id != zone_id:
                continue
            key = (i.product_id, i.lot_id, i.current_zone_id)
            buckets[key] = buckets.get(key, 0) + 1
        return [
            StockLevelRow(product_id=p, lot_id=lot, zone_id=z, quantity=q)
            for (p, lot, z), q in buckets.items()
        ]

    async def delete(self, tenant_id, stock_item_id, *, force=False):  # type: ignore[no-untyped-def]
        item = self.items.get(stock_item_id)
        if item is None:
            return False
        if item.state == "in_stock" and not force:
            raise ValueError(
                "Cannot delete an in_stock item; use ?force=true or change state first"
            )
        del self.items[stock_item_id]
        self.deleted.append(stock_item_id)
        return True


class _FakeMovementRepo:
    def __init__(self) -> None:
        self.rows: list[StockMovementResponse] = []

    async def insert(self, tenant_id, stock_item_id, **kw):  # type: ignore[no-untyped-def]
        row = StockMovementResponse(
            id=uuid4(),
            tenant_id=tenant_id,
            stock_item_id=stock_item_id,
            from_zone_id=kw.get("from_zone_id"),
            to_zone_id=kw.get("to_zone_id"),
            movement_type=kw["movement_type"],
            quantity=kw.get("quantity", 1),
            device_id=kw.get("device_id"),
            occurred_at=kw["occurred_at"],
        )
        self.rows.append(row)
        return row

    async def list(self, tenant_id, **kw):  # type: ignore[no-untyped-def]
        return list(self.rows)

    async def count_for_stock_item(self, tenant_id, stock_item_id):  # type: ignore[no-untyped-def]
        return sum(1 for r in self.rows if r.stock_item_id == stock_item_id)


class _FakeMappingRepo:
    def __init__(self) -> None:
        self.rows: list[TagDataMappingResponse] = []

    async def create(self, tenant_id, payload: TagDataMappingCreate):  # type: ignore[no-untyped-def]
        row = TagDataMappingResponse(
            id=uuid4(),
            tenant_id=tenant_id,
            scope_kind=payload.scope_kind,
            scope_id=payload.scope_id,
            semantic_field=payload.semantic_field,
            tag_data_key=payload.tag_data_key,
            transform=payload.transform,
            created_at=datetime.now(UTC),
        )
        self.rows.append(row)
        return row

    async def list(self, tenant_id, **kw):  # type: ignore[no-untyped-def]
        return list(self.rows)

    async def delete(self, tenant_id, mapping_id):  # type: ignore[no-untyped-def]
        before = len(self.rows)
        self.rows = [r for r in self.rows if r.id != mapping_id]
        return len(self.rows) < before


class _FakeAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def log(
        self,
        tenant_id,
        action,
        resource_type,
        resource_id,  # type: ignore[no-untyped-def]
        changes=None,
        *,
        user_id=None,
    ):
        self.entries.append({"action": action, "resource_id": resource_id, "changes": changes})


def _svc() -> tuple[
    InventoryService,
    _FakeProductRepo,
    _FakeLotRepo,
    _FakeStockRepo,
    _FakeMovementRepo,
    _FakeMappingRepo,
    _FakeAudit,
]:
    p, lots, s, m, mp, a = (
        _FakeProductRepo(),
        _FakeLotRepo(),
        _FakeStockRepo(),
        _FakeMovementRepo(),
        _FakeMappingRepo(),
        _FakeAudit(),
    )
    svc = InventoryService(
        product_repo=p,  # type: ignore[arg-type]
        lot_repo=lots,  # type: ignore[arg-type]
        stock_repo=s,  # type: ignore[arg-type]
        movement_repo=m,  # type: ignore[arg-type]
        mapping_repo=mp,  # type: ignore[arg-type]
        audit=a,  # type: ignore[arg-type]
    )
    return svc, p, lots, s, m, mp, a


@pytest.mark.asyncio
async def test_create_product_audits() -> None:
    svc, _, _, _, _, _, audit = _svc()
    product = await svc.create_product(
        uuid4(),
        uuid4(),
        ProductCreate(sku="A1", name="Widget", gtin="00012345"),
    )
    assert product.sku == "A1"
    assert audit.entries[-1]["action"] == "product.created"


@pytest.mark.asyncio
async def test_update_missing_product_no_audit() -> None:
    svc, _, _, _, _, _, audit = _svc()
    out = await svc.update_product(uuid4(), uuid4(), uuid4(), ProductUpdate(name="x"))
    assert out is None
    assert audit.entries == []


@pytest.mark.asyncio
async def test_create_lot_requires_product() -> None:
    svc, _, _, _, _, _, _ = _svc()
    with pytest.raises(ProductNotFoundError):
        await svc.create_lot(
            uuid4(),
            uuid4(),
            uuid4(),
            LotCreate(lot_code="L1"),
        )


@pytest.mark.asyncio
async def test_create_lot_with_expiry() -> None:
    svc, products, _, _, _, _, audit = _svc()
    tenant = uuid4()
    p = _product(tenant)
    products.products[p.id] = p
    expires = datetime.now(UTC) + timedelta(days=30)
    lot = await svc.create_lot(tenant, uuid4(), p.id, LotCreate(lot_code="L42", expires_at=expires))
    assert lot.lot_code == "L42"
    assert lot.expires_at == expires
    assert audit.entries[-1]["action"] == "lot.created"


@pytest.mark.asyncio
async def test_create_stock_item_requires_product() -> None:
    svc, _, _, _, _, _, _ = _svc()
    with pytest.raises(ProductNotFoundError):
        await svc.create_stock_item(
            uuid4(),
            uuid4(),
            StockItemCreate(product_id=uuid4(), binding_value="urn:epc:sgtin:1"),
        )


@pytest.mark.asyncio
async def test_consume_stock_item_sets_state_and_audits() -> None:
    svc, products, _, stock, _, _, audit = _svc()
    tenant = uuid4()
    p = _product(tenant)
    products.products[p.id] = p
    item = await svc.create_stock_item(
        tenant,
        uuid4(),
        StockItemCreate(product_id=p.id, binding_value="urn:epc:sgtin:42"),
    )
    audit.entries.clear()
    out = await svc.update_stock_item(tenant, uuid4(), item.id, StockItemUpdate(state="consumed"))
    assert out is not None
    assert out.state == "consumed"
    assert out.consumed_at is not None
    assert audit.entries[-1]["action"] == "stock_item.updated"
    assert audit.entries[-1]["changes"] == {"state": "consumed"}


@pytest.mark.asyncio
async def test_stock_levels_aggregates_in_stock_only() -> None:
    svc, products, _, stock_repo, _, _, _ = _svc()
    tenant = uuid4()
    p = _product(tenant)
    products.products[p.id] = p
    zone = uuid4()
    # Three items, two in_stock, one consumed.
    for i, state in enumerate(["in_stock", "in_stock", "consumed"]):
        item = _stock(
            tenant,
            p.id,
            binding_value=f"urn:epc:sgtin:{i}",
            current_zone_id=zone if state == "in_stock" else None,
            state=state,
        )
        stock_repo.items[item.id] = item

    levels = list(await svc.stock_levels(tenant, product_id=p.id))
    assert len(levels) == 1
    assert levels[0].quantity == 2
    assert levels[0].zone_id == zone


@pytest.mark.asyncio
async def test_create_tag_data_mapping_audits() -> None:
    svc, _, _, _, _, _, audit = _svc()
    out = await svc.create_tag_data_mapping(
        uuid4(),
        uuid4(),
        TagDataMappingCreate(
            scope_kind="tenant",
            scope_id=None,
            semantic_field="lot",
            tag_data_key="L",
        ),
    )
    assert out.semantic_field == "lot"
    assert audit.entries[-1]["action"] == "tag_data_mapping.created"


@pytest.mark.asyncio
async def test_delete_tag_data_mapping_audits_only_when_deleted() -> None:
    svc, _, _, _, _, mappings, audit = _svc()
    out = await svc.create_tag_data_mapping(
        uuid4(),
        uuid4(),
        TagDataMappingCreate(scope_kind="tenant", semantic_field="lot", tag_data_key="L"),
    )
    audit.entries.clear()
    assert await svc.delete_tag_data_mapping(uuid4(), uuid4(), out.id) is True
    assert audit.entries[-1]["action"] == "tag_data_mapping.deleted"
    audit.entries.clear()
    assert await svc.delete_tag_data_mapping(uuid4(), uuid4(), uuid4()) is False
    assert audit.entries == []


# -- Sprint 59 (§59.6): force-delete must never orphan the movement ledger --


async def _make_stock_item(svc: InventoryService, products: Any, tenant: UUID) -> StockItemResponse:
    p = _product(tenant)
    products.products[p.id] = p
    return await svc.create_stock_item(
        tenant,
        uuid4(),
        StockItemCreate(product_id=p.id, binding_value="urn:epc:sgtin:59"),
    )


@pytest.mark.asyncio
async def test_force_delete_moved_item_raises_ledger_error() -> None:
    """A unit with movement history cannot be hard-deleted even with force=True;
    the service raises StockItemLedgerError (route maps to a structured 409)
    instead of letting the RESTRICT FK explode into a 500."""
    svc, products, _, stock, movements, _, audit = _svc()
    tenant = uuid4()
    item = await _make_stock_item(svc, products, tenant)
    await movements.insert(
        tenant,
        item.id,
        from_zone_id=None,
        to_zone_id=uuid4(),
        movement_type="move",
        device_id=None,
        occurred_at=datetime.now(UTC),
    )
    audit.entries.clear()

    with pytest.raises(StockItemLedgerError) as excinfo:
        await svc.delete_stock_item(tenant, uuid4(), item.id, force=True)

    assert excinfo.value.movement_count == 1
    assert excinfo.value.stock_item_id == item.id
    # The item survives and nothing was audited as a deletion.
    assert item.id in stock.items
    assert stock.deleted == []
    assert audit.entries == []


@pytest.mark.asyncio
async def test_ledger_guard_applies_even_to_consumed_item() -> None:
    """The ledger guard is independent of state: a consumed (already retired)
    item with movements still cannot be hard-deleted."""
    svc, products, _, stock, movements, _, _ = _svc()
    tenant = uuid4()
    item = await _make_stock_item(svc, products, tenant)
    stock.items[item.id] = stock.items[item.id].model_copy(update={"state": "consumed"})
    await movements.insert(
        tenant,
        item.id,
        from_zone_id=None,
        to_zone_id=uuid4(),
        movement_type="move",
        device_id=None,
        occurred_at=datetime.now(UTC),
    )

    with pytest.raises(StockItemLedgerError):
        await svc.delete_stock_item(tenant, uuid4(), item.id, force=True)
    assert item.id in stock.items


@pytest.mark.asyncio
async def test_force_delete_unmoved_item_succeeds_and_audits() -> None:
    """force=True bypasses the *state* guard for a never-moved in_stock unit."""
    svc, products, _, stock, _, _, audit = _svc()
    tenant = uuid4()
    item = await _make_stock_item(svc, products, tenant)
    audit.entries.clear()

    deleted = await svc.delete_stock_item(tenant, uuid4(), item.id, force=True)

    assert deleted is True
    assert item.id not in stock.items
    assert audit.entries[-1]["action"] == "stock_item.deleted"


@pytest.mark.asyncio
async def test_delete_in_stock_without_force_blocked_by_state_guard() -> None:
    """Without force, an in_stock unit (no movements) is still blocked, but by
    the repo state guard (plain ValueError), not the ledger guard."""
    svc, products, _, stock, _, _, _ = _svc()
    tenant = uuid4()
    item = await _make_stock_item(svc, products, tenant)

    with pytest.raises(ValueError) as excinfo:
        await svc.delete_stock_item(tenant, uuid4(), item.id, force=False)

    assert not isinstance(excinfo.value, StockItemLedgerError)
    assert item.id in stock.items


@pytest.mark.asyncio
async def test_delete_missing_item_returns_false() -> None:
    svc, _, _, _, _, _, audit = _svc()
    deleted = await svc.delete_stock_item(uuid4(), uuid4(), uuid4(), force=True)
    assert deleted is False
    assert audit.entries == []
