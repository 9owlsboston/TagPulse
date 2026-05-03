"""Tests for the inventory branch of IngestionService (Sprint 15b Phase D.5).

Covers:

* SGTIN reads with a matching product create a stock_item on first sight.
* Lot inferred from ``tag_data`` via ``tag_data_mappings`` (most-specific scope wins).
* Zone transitions append a ``stock_movements`` row and emit
  ``Topic.SUBJECT_ZONE_CHANGED`` with ``subject_kind='stock_item'``.
* SGTIN reads with no matching product increment the unmapped counter and emit nothing.
* Mobile readers and missing inventory repos short-circuit cleanly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from tagpulse.events.async_bus import AsyncEventBus
from tagpulse.events.protocol import Topic
from tagpulse.ingestion.service import (
    _LAST_ZONE_BY_ASSET,
    _LAST_ZONE_BY_STOCK_ITEM,
    IngestionService,
)
from tagpulse.models.schemas import (
    DeviceResponse,
    Identity,
    LotResponse,
    ProductResponse,
    StockItemCreate,
    StockItemResponse,
    TagDataMappingResponse,
    TagReadCreate,
    TagReadResponse,
    ZoneResponse,
)

# ---- Fakes -------------------------------------------------------------


class FakeRepo:
    async def insert(self, tenant_id: UUID, read: TagReadCreate) -> TagReadResponse:  # type: ignore[no-untyped-def]
        return TagReadResponse(
            id=uuid4(),
            device_id=read.device_id,
            tag_id=read.tag_id,
            timestamp=read.timestamp,
            signal_strength=read.signal_strength,
            sensor_data=None,
            created_at=datetime.now(UTC),
        )

    async def insert_batch(self, tenant_id, reads):  # type: ignore[no-untyped-def]
        return len(reads)

    async def query(self, *a, **kw):  # type: ignore[no-untyped-def]
        return []


class FakeDeviceRepo:
    def __init__(self, mobility: str = "fixed") -> None:
        self._mobility = mobility

    async def get(self, tenant_id: UUID, device_id: UUID) -> DeviceResponse:  # type: ignore[no-untyped-def]
        return DeviceResponse(
            id=device_id,
            name="r",
            device_type="rfid_reader",
            status="active",
            metadata=None,
            configuration=None,
            firmware_version=None,
            connection_state="online",
            last_seen=None,
            mobility=self._mobility,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    async def record_last_seen(self, *a, **kw):  # type: ignore[no-untyped-def]
        return None

    async def record_connection_state(self, *a, **kw):  # type: ignore[no-untyped-def]
        return None


class FakeZoneRepo:
    def __init__(self, device_to_zone: dict[UUID, UUID | None]) -> None:
        self._map = device_to_zone

    async def get_zone_for_reader(  # type: ignore[no-untyped-def]
        self, tenant_id: UUID, device_id: UUID
    ) -> ZoneResponse | None:
        zone_id = self._map.get(device_id)
        if zone_id is None:
            return None
        return ZoneResponse(
            id=zone_id,
            tenant_id=tenant_id,
            site_id=uuid4(),
            name="Z",
            kind="reader_bound",
            fixed_reader_ids=[device_id],
            polygon_geojson=None,
            metadata=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )


class FakeProductRepo:
    def __init__(self, gtin_to_product: dict[str, ProductResponse]) -> None:
        self._map = gtin_to_product

    async def get_by_gtin(  # type: ignore[no-untyped-def]
        self, tenant_id: UUID, gtin: str
    ) -> ProductResponse | None:
        return self._map.get(gtin)


class FakeStockRepo:
    def __init__(self) -> None:
        self.items: dict[UUID, StockItemResponse] = {}
        self.observations: list[tuple[UUID, UUID | None]] = []
        self.creates: int = 0

    async def get_active_by_binding(  # type: ignore[no-untyped-def]
        self, tenant_id: UUID, binding_kind: str, binding_value: str
    ) -> StockItemResponse | None:
        for item in self.items.values():
            if (
                item.tenant_id == tenant_id
                and item.binding_kind == binding_kind
                and item.binding_value == binding_value
                and item.state not in ("consumed", "expired", "lost")
            ):
                return item
        return None

    async def create(  # type: ignore[no-untyped-def]
        self, tenant_id: UUID, payload: StockItemCreate
    ) -> StockItemResponse:
        self.creates += 1
        item = StockItemResponse(
            id=uuid4(),
            tenant_id=tenant_id,
            product_id=payload.product_id,
            lot_id=payload.lot_id,
            binding_value=payload.binding_value,
            binding_kind=payload.binding_kind,
            state="in_stock",
            current_zone_id=None,
            first_seen_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
            consumed_at=None,
            metadata=None,
        )
        self.items[item.id] = item
        return item

    async def record_observation(  # type: ignore[no-untyped-def]
        self,
        tenant_id: UUID,
        stock_item_id: UUID,
        *,
        zone_id: UUID | None,
        observed_at: datetime,
    ) -> tuple[UUID | None, UUID | None] | None:
        item = self.items.get(stock_item_id)
        if item is None:
            return None
        prev_zone = item.current_zone_id
        new = item.model_copy(
            update={"current_zone_id": zone_id, "last_seen_at": observed_at}
        )
        self.items[stock_item_id] = new
        self.observations.append((stock_item_id, zone_id))
        return prev_zone, zone_id


class FakeMovementRepo:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def insert(  # type: ignore[no-untyped-def]
        self, tenant_id, stock_item_id, **kw
    ):
        self.rows.append({"stock_item_id": stock_item_id, **kw})


class FakeMappingRepo:
    def __init__(self, rows: list[TagDataMappingResponse]) -> None:
        self._rows = rows

    async def list(  # type: ignore[no-untyped-def]
        self, tenant_id, *, scope_kind=None, scope_id=None
    ) -> list[TagDataMappingResponse]:
        out = []
        for r in self._rows:
            if scope_kind is not None and r.scope_kind != scope_kind:
                continue
            if scope_id is not None and r.scope_id != scope_id:
                continue
            if scope_id is None and r.scope_id is not None:
                continue
            out.append(r)
        return out


class FakeLotRepo:
    def __init__(self, lots: list[LotResponse]) -> None:
        self._lots = lots

    async def list_for_product(  # type: ignore[no-untyped-def]
        self, tenant_id, product_id, *,
        expiring_within_days=None, limit=100, offset=0,
    ) -> list[LotResponse]:
        return [lot for lot in self._lots if lot.product_id == product_id]


# ---- Helpers -----------------------------------------------------------


# An SGTIN-96 with company_prefix=0614141, item_ref=100734 (indicator=1,
# item=00734) and serial=42 yields GTIN-14 "10614141007346" (mod-10 check
# digit = 6). Test data is asserted against gtin14_from_decoded() in
# test_gtin14_helper to keep the two consistent.
GTIN = "10614141007346"


def _sgtin_identity(serial: str = "42") -> Identity:
    return Identity(
        epc=f"urn:epc:id:sgtin:0614141.100734.{serial}",
        epc_hex=None,
        epc_scheme="sgtin-96",
        epc_decoded={
            "scheme": "sgtin-96",
            "filter": 1,
            "company_prefix": "0614141",
            "item_ref": "100734",
            "serial": serial,
            "uri": f"urn:epc:id:sgtin:0614141.100734.{serial}",
        },
    )


def _product(tenant_id: UUID) -> ProductResponse:
    return ProductResponse(
        id=uuid4(),
        tenant_id=tenant_id,
        sku="SKU-1",
        gtin=GTIN,
        name="Widget",
        category=None,
        unit="each",
        attributes=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _read(
    device_id: UUID,
    *,
    serial: str = "42",
    tag_data: dict[str, Any] | None = None,
) -> TagReadCreate:
    return TagReadCreate(
        device_id=device_id,
        tag_id=f"urn:epc:id:sgtin:0614141.100734.{serial}",
        timestamp=datetime.now(UTC),
        signal_strength=-50,
        identity=_sgtin_identity(serial),
        tag_data=tag_data,
    )


@pytest.fixture(autouse=True)
def _clear_caches() -> Any:
    _LAST_ZONE_BY_ASSET.clear()
    _LAST_ZONE_BY_STOCK_ITEM.clear()
    yield
    _LAST_ZONE_BY_ASSET.clear()
    _LAST_ZONE_BY_STOCK_ITEM.clear()


# ---- Tests -------------------------------------------------------------


def test_gtin14_helper_matches_test_fixture() -> None:
    """Lock the GTIN-14 derivation against the test fixture."""
    from tagpulse.rfid.epc import gtin14_from_decoded

    assert (
        gtin14_from_decoded(_sgtin_identity().epc_decoded) == GTIN
    )


@pytest.mark.asyncio
async def test_unmapped_sgtin_emits_no_event() -> None:
    bus = AsyncEventBus(capacity=10)
    events: list[Any] = []
    await bus.subscribe(Topic.SUBJECT_ZONE_CHANGED, lambda e: events.append(e))
    await bus.start()
    stock = FakeStockRepo()
    svc = IngestionService(
        repo=FakeRepo(),  # type: ignore[arg-type]
        event_bus=bus,
        device_repo=FakeDeviceRepo(),  # type: ignore[arg-type]
        zone_repo=FakeZoneRepo({}),  # type: ignore[arg-type]
        product_repo=FakeProductRepo({}),  # type: ignore[arg-type]
        stock_repo=stock,  # type: ignore[arg-type]
    )
    await svc.ingest(uuid4(), _read(uuid4()))
    await bus.drain(timeout=1.0)
    assert events == []
    assert stock.creates == 0


@pytest.mark.asyncio
async def test_first_sgtin_auto_creates_stock_item() -> None:
    bus = AsyncEventBus(capacity=10)
    events: list[Any] = []
    await bus.subscribe(Topic.SUBJECT_ZONE_CHANGED, lambda e: events.append(e))
    await bus.start()
    tenant = uuid4()
    product = _product(tenant)
    stock = FakeStockRepo()
    svc = IngestionService(
        repo=FakeRepo(),  # type: ignore[arg-type]
        event_bus=bus,
        device_repo=FakeDeviceRepo(),  # type: ignore[arg-type]
        zone_repo=FakeZoneRepo({}),  # type: ignore[arg-type]
        product_repo=FakeProductRepo({GTIN: product}),  # type: ignore[arg-type]
        stock_repo=stock,  # type: ignore[arg-type]
    )
    await svc.ingest(tenant, _read(uuid4()))
    await bus.drain(timeout=1.0)
    assert stock.creates == 1
    [item] = list(stock.items.values())
    assert item.product_id == product.id
    assert item.binding_value.startswith("urn:epc:id:sgtin:")
    assert events == []  # first observation seeds the cache only


@pytest.mark.asyncio
async def test_zone_transition_records_movement_and_emits_event() -> None:
    bus = AsyncEventBus(capacity=10)
    events: list[Any] = []
    await bus.subscribe(Topic.SUBJECT_ZONE_CHANGED, lambda e: events.append(e))
    await bus.start()
    tenant = uuid4()
    product = _product(tenant)
    reader_a, reader_b = uuid4(), uuid4()
    zone_a, zone_b = uuid4(), uuid4()
    stock = FakeStockRepo()
    movements = FakeMovementRepo()
    svc = IngestionService(
        repo=FakeRepo(),  # type: ignore[arg-type]
        event_bus=bus,
        device_repo=FakeDeviceRepo(mobility="fixed"),  # type: ignore[arg-type]
        zone_repo=FakeZoneRepo({reader_a: zone_a, reader_b: zone_b}),  # type: ignore[arg-type]
        product_repo=FakeProductRepo({GTIN: product}),  # type: ignore[arg-type]
        stock_repo=stock,  # type: ignore[arg-type]
        movement_repo=movements,  # type: ignore[arg-type]
    )
    await svc.ingest(tenant, _read(reader_a))  # seed
    await svc.ingest(tenant, _read(reader_a))  # same zone -> no event
    await svc.ingest(tenant, _read(reader_b))  # transition
    await bus.drain(timeout=1.0)
    assert len(events) == 1
    payload = events[0].payload
    assert payload["subject_kind"] == "stock_item"
    assert payload["from_zone_id"] == str(zone_a)
    assert payload["to_zone_id"] == str(zone_b)
    assert len(movements.rows) == 1
    assert movements.rows[0]["movement_type"] == "transfer"
    assert movements.rows[0]["from_zone_id"] == zone_a
    assert movements.rows[0]["to_zone_id"] == zone_b


@pytest.mark.asyncio
async def test_lot_inferred_from_tag_data_via_mapping() -> None:
    bus = AsyncEventBus(capacity=10)
    await bus.start()
    tenant = uuid4()
    product = _product(tenant)
    lot = LotResponse(
        id=uuid4(),
        tenant_id=tenant,
        product_id=product.id,
        lot_code="LOT-2026-04",
        manufactured_at=None,
        expires_at=None,
        metadata=None,
        created_at=datetime.now(UTC),
    )
    mapping = TagDataMappingResponse(
        id=uuid4(),
        tenant_id=tenant,
        scope_kind="tenant",
        scope_id=None,
        semantic_field="lot",
        tag_data_key="lot_code",
        transform=None,
        created_at=datetime.now(UTC),
    )
    stock = FakeStockRepo()
    svc = IngestionService(
        repo=FakeRepo(),  # type: ignore[arg-type]
        event_bus=bus,
        device_repo=FakeDeviceRepo(),  # type: ignore[arg-type]
        zone_repo=FakeZoneRepo({}),  # type: ignore[arg-type]
        product_repo=FakeProductRepo({GTIN: product}),  # type: ignore[arg-type]
        stock_repo=stock,  # type: ignore[arg-type]
        lot_repo=FakeLotRepo([lot]),  # type: ignore[arg-type]
        tag_data_mapping_repo=FakeMappingRepo([mapping]),  # type: ignore[arg-type]
    )
    await svc.ingest(
        tenant, _read(uuid4(), tag_data={"lot_code": "LOT-2026-04"})
    )
    [item] = list(stock.items.values())
    assert item.lot_id == lot.id


@pytest.mark.asyncio
async def test_mobile_reader_skips_zone_resolution() -> None:
    bus = AsyncEventBus(capacity=10)
    events: list[Any] = []
    await bus.subscribe(Topic.SUBJECT_ZONE_CHANGED, lambda e: events.append(e))
    await bus.start()
    tenant = uuid4()
    product = _product(tenant)
    stock = FakeStockRepo()
    movements = FakeMovementRepo()
    svc = IngestionService(
        repo=FakeRepo(),  # type: ignore[arg-type]
        event_bus=bus,
        device_repo=FakeDeviceRepo(mobility="mobile"),  # type: ignore[arg-type]
        zone_repo=FakeZoneRepo({uuid4(): uuid4()}),  # type: ignore[arg-type]
        product_repo=FakeProductRepo({GTIN: product}),  # type: ignore[arg-type]
        stock_repo=stock,  # type: ignore[arg-type]
        movement_repo=movements,  # type: ignore[arg-type]
    )
    await svc.ingest(tenant, _read(uuid4()))
    await svc.ingest(tenant, _read(uuid4()))
    await bus.drain(timeout=1.0)
    # stock_item still auto-created, but no zone transition / movement.
    assert stock.creates == 1
    assert events == []
    assert movements.rows == []


@pytest.mark.asyncio
async def test_no_inventory_branch_when_repos_absent() -> None:
    """Ingestion still works when inventory repos aren't wired in."""
    bus = AsyncEventBus(capacity=10)
    await bus.start()
    svc = IngestionService(
        repo=FakeRepo(),  # type: ignore[arg-type]
        event_bus=bus,
        device_repo=FakeDeviceRepo(),  # type: ignore[arg-type]
    )
    result = await svc.ingest(uuid4(), _read(uuid4()))
    assert result.tag_id.startswith("urn:epc:id:sgtin:")
