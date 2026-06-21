"""Unit tests for AssetService asset-state read methods (Sprint 71, ADR-034)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from tagpulse.api.services.asset_service import AssetService
from tagpulse.models.schemas import AssetStateResponse

NOW = datetime(2026, 6, 20, 12, 0, 0, tzinfo=UTC)


class _FakeAssetRepo:
    async def get(self, tenant_id: UUID, asset_id: UUID):  # type: ignore[no-untyped-def]
        return None


class _FakeStateRepo:
    def __init__(self, latest: AssetStateResponse | None, history: list[AssetStateResponse]):
        self._latest = latest
        self._history = history
        self.calls: list[tuple] = []

    async def latest(self, tenant_id: UUID, asset_id: UUID) -> AssetStateResponse | None:
        self.calls.append(("latest", tenant_id, asset_id))
        return self._latest

    async def history(self, tenant_id, asset_id, *, since=None, limit=200):  # type: ignore[no-untyped-def]
        self.calls.append(("history", tenant_id, asset_id, since, limit))
        return self._history


def _snap(asset_id: UUID, frame: str = "reader") -> AssetStateResponse:
    return AssetStateResponse(
        asset_id=asset_id,
        time=NOW,
        frame=frame,
        temperature_c=4.0,
        humidity_pct=60.0,
        sample_count=3,
        tag_count=2,
        confidence=0.8,
    )


def _service(state_repo) -> AssetService:  # type: ignore[no-untyped-def]
    return AssetService(
        asset_repo=_FakeAssetRepo(),  # type: ignore[arg-type]
        binding_repo=object(),  # type: ignore[arg-type]
        audit=object(),  # type: ignore[arg-type]
        asset_state_repo=state_repo,
    )


@pytest.mark.asyncio
async def test_get_asset_state_returns_latest() -> None:
    tenant, asset = uuid4(), uuid4()
    snap = _snap(asset)
    svc = _service(_FakeStateRepo(latest=snap, history=[]))
    out = await svc.get_asset_state(tenant, asset)
    assert out is snap


@pytest.mark.asyncio
async def test_get_asset_state_none_without_repo() -> None:
    tenant, asset = uuid4(), uuid4()
    svc = _service(None)
    assert await svc.get_asset_state(tenant, asset) is None
    assert await svc.get_asset_state_history(tenant, asset) == []


@pytest.mark.asyncio
async def test_get_asset_state_history_passes_params() -> None:
    tenant, asset = uuid4(), uuid4()
    repo = _FakeStateRepo(latest=None, history=[_snap(asset), _snap(asset, "geo")])
    svc = _service(repo)
    out = await svc.get_asset_state_history(tenant, asset, since=NOW, limit=50)
    assert len(out) == 2
    assert repo.calls[-1] == ("history", tenant, asset, NOW, 50)
