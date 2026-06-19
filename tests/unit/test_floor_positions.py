"""Unit tests for Sprint 65 Phase 1 — BYO precomputed floor positions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from tagpulse.api.services.asset_service import (
    AssetNotFoundError,
    AssetPositionSiteError,
    AssetService,
)
from tagpulse.models.schemas import (
    AssetResponse,
    FloorPositionCreate,
    FloorPositionResponse,
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

    async def get(self, tenant_id: UUID, asset_id: UUID) -> AssetResponse | None:
        a = self.assets.get(asset_id)
        if a is None or a.tenant_id != tenant_id:
            return None
        return a


class _FakeSiteRepo:
    def __init__(self) -> None:
        # (tenant_id, site_id) pairs that exist
        self.sites: set[tuple[UUID, UUID]] = set()

    async def get(self, tenant_id: UUID, site_id: UUID) -> object | None:
        return object() if (tenant_id, site_id) in self.sites else None


class _FakePositionRepo:
    def __init__(self) -> None:
        self.rows: list[FloorPositionResponse] = []

    async def insert(  # type: ignore[no-untyped-def]
        self, tenant_id, asset_id, *, recorded_at, position: FloorPositionCreate, source
    ) -> FloorPositionResponse:
        row = FloorPositionResponse(
            id=uuid4(),
            tenant_id=tenant_id,
            asset_id=asset_id,
            site_id=position.site_id,
            recorded_at=recorded_at,
            x=position.x,
            y=position.y,
            z=position.z,
            confidence=position.confidence,
            source=source,
            metadata=position.metadata,
        )
        self.rows.append(row)
        return row

    async def list_floor_path(  # type: ignore[no-untyped-def]
        self, tenant_id, asset_id, *, since=None, until=None, source=None, limit=500
    ) -> list[FloorPositionResponse]:
        rows = [
            r
            for r in self.rows
            if r.tenant_id == tenant_id
            and r.asset_id == asset_id
            and (since is None or r.recorded_at >= since)
            and (until is None or r.recorded_at <= until)
            and (source is None or r.source == source)
        ]
        rows.sort(key=lambda r: r.recorded_at)
        return rows[:limit]


class _FakeAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def log(  # type: ignore[no-untyped-def]
        self, tenant_id, action, resource_type, resource_id, changes=None, *, user_id=None
    ) -> None:
        self.entries.append({"action": action, "resource_id": resource_id, "changes": changes})


def _build() -> tuple[AssetService, _FakeAssetRepo, _FakeSiteRepo, _FakePositionRepo, _FakeAudit]:
    assets = _FakeAssetRepo()
    sites = _FakeSiteRepo()
    positions = _FakePositionRepo()
    audit = _FakeAudit()
    svc = AssetService(
        asset_repo=assets,  # type: ignore[arg-type]
        binding_repo=object(),  # type: ignore[arg-type]
        audit=audit,  # type: ignore[arg-type]
        position_repo=positions,  # type: ignore[arg-type]
        site_repo=sites,  # type: ignore[arg-type]
    )
    return svc, assets, sites, positions, audit


@pytest.mark.asyncio
async def test_record_floor_position_happy_path() -> None:
    svc, assets, sites, positions, audit = _build()
    tenant = uuid4()
    asset = _asset(tenant)
    site_id = uuid4()
    assets.assets[asset.id] = asset
    sites.sites.add((tenant, site_id))

    out = await svc.record_floor_position(
        tenant,
        uuid4(),
        asset.id,
        FloorPositionCreate(site_id=site_id, x=142.5, y=88.0, z=1.2, confidence=0.82),
    )

    assert out.source == "precomputed"
    assert (out.x, out.y, out.z) == (142.5, 88.0, 1.2)
    assert out.site_id == site_id
    assert len(positions.rows) == 1
    assert audit.entries[-1]["action"] == "asset.floor_position_recorded"
    assert audit.entries[-1]["changes"]["site_id"] == str(site_id)


@pytest.mark.asyncio
async def test_record_floor_position_defaults_recorded_at_to_now() -> None:
    svc, assets, sites, _positions, _audit = _build()
    tenant = uuid4()
    asset = _asset(tenant)
    site_id = uuid4()
    assets.assets[asset.id] = asset
    sites.sites.add((tenant, site_id))

    before = datetime.now(UTC)
    out = await svc.record_floor_position(
        tenant, None, asset.id, FloorPositionCreate(site_id=site_id, x=1.0, y=2.0, confidence=0.5)
    )
    after = datetime.now(UTC)

    assert before <= out.recorded_at <= after


@pytest.mark.asyncio
async def test_record_floor_position_missing_asset() -> None:
    svc, _assets, sites, _positions, _audit = _build()
    tenant = uuid4()
    site_id = uuid4()
    sites.sites.add((tenant, site_id))

    with pytest.raises(AssetNotFoundError):
        await svc.record_floor_position(
            tenant,
            None,
            uuid4(),
            FloorPositionCreate(site_id=site_id, x=1.0, y=2.0, confidence=0.5),
        )


@pytest.mark.asyncio
async def test_record_floor_position_foreign_site_rejected() -> None:
    svc, assets, _sites, _positions, _audit = _build()
    tenant = uuid4()
    asset = _asset(tenant)
    assets.assets[asset.id] = asset

    # site_id not registered for this tenant
    with pytest.raises(AssetPositionSiteError):
        await svc.record_floor_position(
            tenant,
            None,
            asset.id,
            FloorPositionCreate(site_id=uuid4(), x=1.0, y=2.0, confidence=0.5),
        )


@pytest.mark.asyncio
async def test_list_floor_path_ascending_and_source_filter() -> None:
    svc, assets, sites, positions, _audit = _build()
    tenant = uuid4()
    asset = _asset(tenant)
    site_id = uuid4()
    assets.assets[asset.id] = asset
    sites.sites.add((tenant, site_id))

    t0 = datetime(2026, 6, 19, 12, 0, 0, tzinfo=UTC)
    # Insert out of order; expect ascending back.
    for i in (2, 0, 1):
        await svc.record_floor_position(
            tenant,
            None,
            asset.id,
            FloorPositionCreate(
                site_id=site_id,
                x=float(i),
                y=0.0,
                confidence=0.5,
                recorded_at=t0 + timedelta(minutes=i),
            ),
        )

    path = await svc.list_floor_path(tenant, asset.id, source="precomputed")
    assert [p.x for p in path] == [0.0, 1.0, 2.0]

    # No rows for a different source.
    assert await svc.list_floor_path(tenant, asset.id, source="computed") == []


def test_floor_position_create_rejects_bad_confidence() -> None:
    with pytest.raises(ValidationError):
        FloorPositionCreate(site_id=uuid4(), x=1.0, y=2.0, confidence=1.5)


def test_floor_position_create_rejects_nan_coordinate() -> None:
    with pytest.raises(ValidationError):
        FloorPositionCreate(site_id=uuid4(), x=float("nan"), y=2.0, confidence=0.5)
