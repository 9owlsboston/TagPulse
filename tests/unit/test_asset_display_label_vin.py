"""Sprint 82 — asset display_label + VIN binding lookup (I-P923)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tagpulse.api.dependencies import get_asset_service
from tagpulse.api.routes.assets import router
from tagpulse.api.services.asset_service import AssetService
from tagpulse.core.user_auth import AuthenticatedUser, get_current_user
from tagpulse.models.schemas import (
    AssetCreate,
    AssetResponse,
    AssetTagBindingCreate,
    AssetTagBindingResponse,
)
from tagpulse.repositories.timescaledb.assets import (
    TimescaleAssetTagBindingRepository,
    _binding_candidates,
)


def _asset_response(display_label: str | None = None) -> AssetResponse:
    now = datetime.now(UTC)
    return AssetResponse(
        id=uuid4(),
        tenant_id=uuid4(),
        external_ref=None,
        name="Truck 42",
        display_label=display_label,
        status="active",
        parent_asset_id=None,
        category_id=uuid4(),
        metadata=None,
        created_at=now,
        updated_at=now,
    )


# --------------------------------------------------------------------------- #
# Schema + candidate helper
# --------------------------------------------------------------------------- #


def test_asset_schemas_carry_display_label() -> None:
    assert (
        AssetCreate(name="T", category_id=uuid4(), display_label="ABC-123").display_label
        == "ABC-123"
    )
    assert _asset_response("ABC-123").display_label == "ABC-123"
    assert _asset_response().display_label is None


def test_binding_candidates_includes_canonical() -> None:
    assert _binding_candidates("1hgcm82633a004352") == {
        "1hgcm82633a004352",
        "1HGCM82633A004352",
    }


# --------------------------------------------------------------------------- #
# Service: VIN canonicalization on bind
# --------------------------------------------------------------------------- #


class _FakeBindingRepo:
    def __init__(self) -> None:
        self.created: list[AssetTagBindingCreate] = []

    async def create(
        self, tenant_id: UUID, asset_id: UUID, payload: AssetTagBindingCreate
    ) -> AssetTagBindingResponse:
        self.created.append(payload)
        return AssetTagBindingResponse(
            id=uuid4(),
            tenant_id=tenant_id,
            asset_id=asset_id,
            binding_value=payload.binding_value,
            binding_kind=payload.binding_kind,
            bound_at=datetime.now(UTC),
            unbound_at=None,
            metadata=payload.metadata,
        )


class _FakeAudit:
    async def log(self, *_a: Any, **_k: Any) -> None:
        return None


def _svc(binding_repo: _FakeBindingRepo) -> AssetService:
    return AssetService(
        asset_repo=object(),  # type: ignore[arg-type]
        binding_repo=binding_repo,  # type: ignore[arg-type]
        audit=_FakeAudit(),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_bind_vin_canonicalizes_value() -> None:
    repo = _FakeBindingRepo()
    await _svc(repo).bind_tag(
        uuid4(),
        uuid4(),
        uuid4(),
        AssetTagBindingCreate(binding_value=" 1hgcm82633a004352 ", binding_kind="vin"),
    )
    assert repo.created[0].binding_value == "1HGCM82633A004352"


@pytest.mark.asyncio
async def test_bind_epc_value_not_canonicalized() -> None:
    repo = _FakeBindingRepo()
    await _svc(repo).bind_tag(
        uuid4(),
        uuid4(),
        uuid4(),
        AssetTagBindingCreate(binding_value="abc123", binding_kind="epc"),
    )
    assert repo.created[0].binding_value == "abc123"


# --------------------------------------------------------------------------- #
# Repo: get_by_binding_value
# --------------------------------------------------------------------------- #


class _ScalarResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def scalars(self) -> _ScalarResult:
        return self

    def first(self) -> Any:
        return self._row


class _FakeSession:
    def __init__(self, row: Any) -> None:
        self._row = row

    async def execute(self, _stmt: Any) -> _ScalarResult:
        return _ScalarResult(self._row)


def _asset_row() -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        external_ref=None,
        name="Truck 42",
        display_label="ABC-123",
        status="active",
        parent_asset_id=None,
        category_id=uuid4(),
        metadata_=None,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_get_by_binding_value_maps_asset_with_display_label() -> None:
    repo = TimescaleAssetTagBindingRepository(_FakeSession(_asset_row()))  # type: ignore[arg-type]
    resp = await repo.get_by_binding_value(uuid4(), "1HGCM82633A004352")
    assert resp is not None
    assert resp.display_label == "ABC-123"


@pytest.mark.asyncio
async def test_get_by_binding_value_none_when_unmatched() -> None:
    repo = TimescaleAssetTagBindingRepository(_FakeSession(None))  # type: ignore[arg-type]
    assert await repo.get_by_binding_value(uuid4(), "NOPE") is None


# --------------------------------------------------------------------------- #
# Route: GET /assets/by-binding (declared before /{asset_id})
# --------------------------------------------------------------------------- #


class _StubAssetService:
    def __init__(self, asset: AssetResponse | None) -> None:
        self._asset = asset
        self.looked_up: str | None = None

    async def get_asset_by_binding_value(self, _tenant: UUID, value: str) -> AssetResponse | None:
        self.looked_up = value
        return self._asset


def _client(stub: _StubAssetService) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id=uuid4(), tenant_id=uuid4(), tenant_name="t", tenant_slug="t", role="viewer"
    )
    app.dependency_overrides[get_asset_service] = lambda: stub
    return TestClient(app)


def test_route_by_binding_returns_asset() -> None:
    asset = _asset_response("ABC-123")
    stub = _StubAssetService(asset)
    resp = _client(stub).get("/v1/assets/by-binding", params={"value": "1HGCM82633A004352"})
    assert resp.status_code == 200
    assert resp.json()["display_label"] == "ABC-123"
    # Route ordering: reached the by-binding handler, not get_asset({asset_id}).
    assert stub.looked_up == "1HGCM82633A004352"


def test_route_by_binding_404_when_no_asset() -> None:
    resp = _client(_StubAssetService(None)).get("/v1/assets/by-binding", params={"value": "NOPE"})
    assert resp.status_code == 404
