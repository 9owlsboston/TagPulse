"""Unit tests for Phase C: carrier semantics + external positions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from tagpulse.api.services.asset_service import (
    AssetNotFoundError,
    AssetService,
)
from tagpulse.events.async_bus import AsyncEventBus
from tagpulse.events.protocol import Topic
from tagpulse.models.schemas import (
    AssetResponse,
    ExternalLocationCreate,
    ExternalLocationResponse,
)


def _asset(tenant_id: UUID, **overrides: Any) -> AssetResponse:
    base: dict[str, Any] = dict(
        id=uuid4(),
        tenant_id=tenant_id,
        external_ref=None,
        name="A",
        status="active",
        parent_asset_id=None,
        category_id=uuid4(),
        metadata=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    base.update(overrides)
    return AssetResponse(**base)


class _FakeAssetRepo:
    def __init__(self) -> None:
        self.assets: dict[UUID, AssetResponse] = {}
        self.descendants: list[tuple[AssetResponse, int]] = []

    async def get(self, tenant_id: UUID, asset_id: UUID) -> AssetResponse | None:
        a = self.assets.get(asset_id)
        if a is None or a.tenant_id != tenant_id:
            return None
        return a

    async def set_parent(  # type: ignore[no-untyped-def]
        self, tenant_id, asset_id, parent_asset_id
    ):
        a = self.assets.get(asset_id)
        if a is None or a.tenant_id != tenant_id:
            return None
        prior = a.parent_asset_id
        updated = _asset(
            tenant_id,
            id=a.id,
            name=a.name,
            parent_asset_id=parent_asset_id,
        )
        self.assets[asset_id] = updated
        return updated, prior

    async def get_descendants(  # type: ignore[no-untyped-def]
        self, tenant_id, asset_id
    ):
        return self.descendants


class _FakeBindingRepo:
    pass


class _FakeExternalRepo:
    def __init__(self) -> None:
        self.rows: list[ExternalLocationResponse] = []

    async def insert(  # type: ignore[no-untyped-def]
        self, tenant_id, asset_id, payload: ExternalLocationCreate
    ):
        row = ExternalLocationResponse(
            id=uuid4(),
            tenant_id=tenant_id,
            asset_id=asset_id,
            recorded_at=payload.recorded_at,
            latitude=payload.latitude,
            longitude=payload.longitude,
            source=payload.source,
            accuracy_meters=payload.accuracy_meters,
            speed_kph=payload.speed_kph,
            heading_deg=payload.heading_deg,
            metadata=payload.metadata,
        )
        self.rows.append(row)
        return row

    async def list_for_asset(  # type: ignore[no-untyped-def]
        self, tenant_id, asset_id, *, limit=100, offset=0
    ):
        return list(self.rows)


class _FakeAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def log(  # type: ignore[no-untyped-def]
        self,
        tenant_id,
        action,
        resource_type,
        resource_id,
        changes=None,
        *,
        user_id=None,
    ):
        self.entries.append({"action": action, "resource_id": resource_id, "changes": changes})


@pytest.fixture
async def bus() -> AsyncEventBus:
    b = AsyncEventBus(capacity=20)
    await b.start()
    return b


def _build(
    bus: AsyncEventBus,
) -> tuple[AssetService, _FakeAssetRepo, _FakeExternalRepo, _FakeAudit]:
    a = _FakeAssetRepo()
    ext = _FakeExternalRepo()
    audit = _FakeAudit()
    svc = AssetService(
        asset_repo=a,  # type: ignore[arg-type]
        binding_repo=_FakeBindingRepo(),  # type: ignore[arg-type]
        audit=audit,  # type: ignore[arg-type]
        external_location_repo=ext,  # type: ignore[arg-type]
        event_bus=bus,
    )
    return svc, a, ext, audit


# ---- Carrier load / unload ----


@pytest.mark.asyncio
async def test_load_attaches_and_emits_event(bus: AsyncEventBus) -> None:
    svc, repo, _, audit = _build(bus)
    tenant = uuid4()
    truck = _asset(tenant)
    pallet = _asset(tenant)
    repo.assets[truck.id] = truck
    repo.assets[pallet.id] = pallet
    events: list[Any] = []
    await bus.subscribe(Topic.ASSET_LOADED, lambda e: events.append(e))

    out = await svc.load_onto_carrier(tenant, uuid4(), pallet.id, truck.id)
    await bus.drain(timeout=1.0)

    assert out.parent_asset_id == truck.id
    assert audit.entries[-1]["action"] == "asset.loaded"
    assert len(events) == 1
    assert events[0].payload["asset_id"] == str(pallet.id)
    assert events[0].payload["parent_asset_id"] == str(truck.id)


@pytest.mark.asyncio
async def test_load_idempotent(bus: AsyncEventBus) -> None:
    svc, repo, _, audit = _build(bus)
    tenant = uuid4()
    truck = _asset(tenant)
    pallet = _asset(tenant, parent_asset_id=truck.id)
    repo.assets[truck.id] = truck
    repo.assets[pallet.id] = pallet
    events: list[Any] = []
    await bus.subscribe(Topic.ASSET_LOADED, lambda e: events.append(e))

    await svc.load_onto_carrier(tenant, uuid4(), pallet.id, truck.id)
    await bus.drain(timeout=1.0)

    assert events == []
    assert audit.entries == []


@pytest.mark.asyncio
async def test_load_self_parent_raises(bus: AsyncEventBus) -> None:
    svc, repo, _, _ = _build(bus)
    tenant = uuid4()
    a = _asset(tenant)
    repo.assets[a.id] = a
    with pytest.raises(ValueError):
        await svc.load_onto_carrier(tenant, uuid4(), a.id, a.id)


@pytest.mark.asyncio
async def test_load_missing_parent_raises(bus: AsyncEventBus) -> None:
    svc, repo, _, _ = _build(bus)
    tenant = uuid4()
    pallet = _asset(tenant)
    repo.assets[pallet.id] = pallet
    with pytest.raises(AssetNotFoundError):
        await svc.load_onto_carrier(tenant, uuid4(), pallet.id, uuid4())


@pytest.mark.asyncio
async def test_unload_emits_event_with_prior_parent(bus: AsyncEventBus) -> None:
    svc, repo, _, audit = _build(bus)
    tenant = uuid4()
    truck_id = uuid4()
    pallet = _asset(tenant, parent_asset_id=truck_id)
    repo.assets[pallet.id] = pallet
    events: list[Any] = []
    await bus.subscribe(Topic.ASSET_UNLOADED, lambda e: events.append(e))

    out = await svc.unload_from_carrier(tenant, uuid4(), pallet.id)
    await bus.drain(timeout=1.0)

    assert out.parent_asset_id is None
    assert events[0].payload["prior_parent_asset_id"] == str(truck_id)
    assert audit.entries[-1]["action"] == "asset.unloaded"


@pytest.mark.asyncio
async def test_unload_idempotent(bus: AsyncEventBus) -> None:
    svc, repo, _, audit = _build(bus)
    tenant = uuid4()
    pallet = _asset(tenant)  # already unloaded (parent=None)
    repo.assets[pallet.id] = pallet
    events: list[Any] = []
    await bus.subscribe(Topic.ASSET_UNLOADED, lambda e: events.append(e))

    await svc.unload_from_carrier(tenant, uuid4(), pallet.id)
    await bus.drain(timeout=1.0)

    assert events == []
    assert audit.entries == []


# ---- Manifest ----


@pytest.mark.asyncio
async def test_get_manifest_builds_tree(bus: AsyncEventBus) -> None:
    svc, repo, _, _ = _build(bus)
    tenant = uuid4()
    truck = _asset(tenant)
    pallet1 = _asset(tenant, parent_asset_id=truck.id, name="P1")
    pallet2 = _asset(tenant, parent_asset_id=truck.id, name="P2")
    case1 = _asset(tenant, parent_asset_id=pallet1.id, name="C1")
    repo.assets[truck.id] = truck
    repo.descendants = [(pallet1, 1), (pallet2, 1), (case1, 2)]

    manifest = await svc.get_manifest(tenant, truck.id)

    assert manifest.asset_id == truck.id
    assert len(manifest.children) == 2
    p1 = next(c for c in manifest.children if c.name == "P1")
    assert len(p1.children) == 1
    assert p1.children[0].name == "C1"


@pytest.mark.asyncio
async def test_get_manifest_missing_root(bus: AsyncEventBus) -> None:
    svc, _, _, _ = _build(bus)
    with pytest.raises(AssetNotFoundError):
        await svc.get_manifest(uuid4(), uuid4())


# ---- External positions ----


@pytest.mark.asyncio
async def test_record_external_position_emits_event_and_audits(
    bus: AsyncEventBus,
) -> None:
    svc, repo, ext, audit = _build(bus)
    tenant = uuid4()
    asset = _asset(tenant)
    repo.assets[asset.id] = asset
    events: list[Any] = []
    await bus.subscribe(Topic.EXTERNAL_LOCATION_RECORDED, lambda e: events.append(e))

    payload = ExternalLocationCreate(
        latitude=42.36,
        longitude=-71.06,
        recorded_at=datetime.now(UTC),
        source="samsara",
        speed_kph=88.0,
    )
    out = await svc.record_external_position(tenant, uuid4(), asset.id, payload)
    await bus.drain(timeout=1.0)

    assert out.source == "samsara"
    assert len(ext.rows) == 1
    assert events[0].payload["source"] == "samsara"
    assert audit.entries[-1]["action"] == "asset.external_position_recorded"


@pytest.mark.asyncio
async def test_record_external_position_missing_asset(bus: AsyncEventBus) -> None:
    svc, _, _, _ = _build(bus)
    payload = ExternalLocationCreate(
        latitude=0,
        longitude=0,
        recorded_at=datetime.now(UTC),
        source="x",
    )
    with pytest.raises(AssetNotFoundError):
        await svc.record_external_position(uuid4(), uuid4(), uuid4(), payload)


def test_external_position_validates_lat_lon() -> None:
    with pytest.raises(ValueError):
        ExternalLocationCreate(latitude=91, longitude=0, recorded_at=datetime.now(UTC), source="x")
    with pytest.raises(ValueError):
        ExternalLocationCreate(latitude=0, longitude=181, recorded_at=datetime.now(UTC), source="x")
