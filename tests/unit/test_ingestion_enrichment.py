"""Tests for ingestion's asset/zone enrichment hook (Sprint 15 Phase B.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from tagpulse.events.async_bus import AsyncEventBus
from tagpulse.events.protocol import Topic
from tagpulse.ingestion.service import _LAST_ZONE_BY_ASSET, IngestionService
from tagpulse.models.schemas import (
    AssetTagBindingResponse,
    DeviceResponse,
    Identity,
    TagReadCreate,
    TagReadResponse,
    ZoneResponse,
)

# ---- Fakes ----


class FakeRepo:
    async def insert(  # type: ignore[no-untyped-def]
        self, tenant_id, read
    ) -> TagReadResponse:
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


class FakeBindingRepo:
    def __init__(self, value_to_asset: dict[str, UUID] | None = None) -> None:
        self._map = value_to_asset or {}

    async def get_active_by_value(  # type: ignore[no-untyped-def]
        self, tenant_id, binding_value
    ) -> AssetTagBindingResponse | None:
        if binding_value not in self._map:
            return None
        return AssetTagBindingResponse(
            id=uuid4(),
            tenant_id=tenant_id,
            asset_id=self._map[binding_value],
            binding_value=binding_value,
            binding_kind="epc",
            bound_at=datetime.now(UTC),
            unbound_at=None,
            metadata=None,
        )


class FakeZoneRepo:
    def __init__(self, device_to_zone: dict[UUID, UUID | None]) -> None:
        self._map = device_to_zone

    async def get_zone_for_reader(  # type: ignore[no-untyped-def]
        self, tenant_id, device_id
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


class FakeDeviceRepo:
    def __init__(self, mobility: str = "fixed") -> None:
        self._mobility = mobility

    async def get(  # type: ignore[no-untyped-def]
        self, tenant_id, device_id
    ) -> DeviceResponse:
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


def _read(device_id: UUID, *, epc: str | None = None) -> TagReadCreate:
    return TagReadCreate(
        device_id=device_id,
        tag_id=epc or "tag-fallback",
        timestamp=datetime.now(UTC),
        signal_strength=-50,
        identity=Identity(epc=epc) if epc else None,
    )


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    _LAST_ZONE_BY_ASSET.clear()
    yield
    _LAST_ZONE_BY_ASSET.clear()


# ---- Tests ----


@pytest.mark.asyncio
async def test_no_binding_increments_counter_no_event() -> None:
    bus = AsyncEventBus(capacity=10)
    events: list[Any] = []
    await bus.subscribe(Topic.SUBJECT_ZONE_CHANGED, lambda e: events.append(e))
    await bus.start()
    svc = IngestionService(
        repo=FakeRepo(),  # type: ignore[arg-type]
        event_bus=bus,
        device_repo=FakeDeviceRepo(),  # type: ignore[arg-type]
        binding_repo=FakeBindingRepo(),  # type: ignore[arg-type]
        zone_repo=FakeZoneRepo({}),  # type: ignore[arg-type]
    )
    await svc.ingest(uuid4(), _read(uuid4(), epc="urn:epc:foo"))
    await bus.drain(timeout=1.0)
    assert events == []


@pytest.mark.asyncio
async def test_zone_transition_emits_event() -> None:
    bus = AsyncEventBus(capacity=10)
    events: list[Any] = []
    await bus.subscribe(Topic.SUBJECT_ZONE_CHANGED, lambda e: events.append(e))
    await bus.start()
    asset_id = uuid4()
    reader_a = uuid4()
    reader_b = uuid4()
    zone_a = uuid4()
    zone_b = uuid4()
    epc = "urn:epc:asset-1"
    svc = IngestionService(
        repo=FakeRepo(),  # type: ignore[arg-type]
        event_bus=bus,
        device_repo=FakeDeviceRepo(mobility="fixed"),  # type: ignore[arg-type]
        binding_repo=FakeBindingRepo({epc: asset_id}),  # type: ignore[arg-type]
        zone_repo=FakeZoneRepo({reader_a: zone_a, reader_b: zone_b}),  # type: ignore[arg-type]
    )
    tenant = uuid4()
    # First read seeds the cache (no event).
    await svc.ingest(tenant, _read(reader_a, epc=epc))
    # Same zone again: no event.
    await svc.ingest(tenant, _read(reader_a, epc=epc))
    # Different zone: one event.
    await svc.ingest(tenant, _read(reader_b, epc=epc))
    await bus.drain(timeout=1.0)
    assert len(events) == 1
    payload = events[0].payload
    assert payload["subject_kind"] == "asset"
    assert payload["subject_id"] == str(asset_id)
    assert payload["from_zone_id"] == str(zone_a)
    assert payload["to_zone_id"] == str(zone_b)


@pytest.mark.asyncio
async def test_mobile_reader_skips_zone_lookup() -> None:
    bus = AsyncEventBus(capacity=10)
    events: list[Any] = []
    await bus.subscribe(Topic.SUBJECT_ZONE_CHANGED, lambda e: events.append(e))
    await bus.start()
    asset_id = uuid4()
    reader = uuid4()
    epc = "urn:epc:cargo-1"
    svc = IngestionService(
        repo=FakeRepo(),  # type: ignore[arg-type]
        event_bus=bus,
        device_repo=FakeDeviceRepo(mobility="mobile"),  # type: ignore[arg-type]
        binding_repo=FakeBindingRepo({epc: asset_id}),  # type: ignore[arg-type]
        zone_repo=FakeZoneRepo({reader: uuid4()}),  # type: ignore[arg-type]
    )
    await svc.ingest(uuid4(), _read(reader, epc=epc))
    await svc.ingest(uuid4(), _read(reader, epc=epc))
    await bus.drain(timeout=1.0)
    assert events == []


@pytest.mark.asyncio
async def test_no_enrichment_when_binding_repo_absent() -> None:
    """Backward compat: ingestion still works when bindings aren't wired in."""
    bus = AsyncEventBus(capacity=10)
    await bus.start()
    svc = IngestionService(
        repo=FakeRepo(),  # type: ignore[arg-type]
        event_bus=bus,
        device_repo=FakeDeviceRepo(),  # type: ignore[arg-type]
    )
    result = await svc.ingest(uuid4(), _read(uuid4(), epc="x"))
    assert result.tag_id == "x"
