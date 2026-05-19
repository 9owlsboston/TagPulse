"""Sprint 42: ``GET /assets`` accepts repeated ``?category_ids=`` for
multi-category filtering and still honours legacy ``?category_id=``.

These tests pin the route → service contract: which kwargs reach
``AssetService.list_assets`` for each query-string shape. They use the
same ``TestClient`` + stub-service pattern as
``test_assets_route_asset_type_removed.py`` so we don't need a real DB
or fixture asset rows — the assertions live entirely on the captured
service-call kwargs.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tagpulse.api.dependencies import get_asset_service
from tagpulse.api.routes.assets import router
from tagpulse.core.user_auth import AuthenticatedUser, get_current_user


class _StubAssetService:
    """Stub that captures the ``list_assets`` kwargs so tests can pin the
    route → service contract."""

    def __init__(self) -> None:
        self.last_kwargs: dict[str, Any] | None = None

    async def list_assets(self, tenant_id: UUID, **kwargs: Any) -> list[object]:
        self.last_kwargs = {"tenant_id": tenant_id, **kwargs}
        return []


@pytest.fixture
def stub() -> _StubAssetService:
    return _StubAssetService()


@pytest.fixture
def client(stub: _StubAssetService) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/v1")

    def _fake_user() -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=None,
            tenant_id=uuid4(),
            tenant_name="t",
            tenant_slug="t",
            role="viewer",
        )

    app.dependency_overrides[get_current_user] = _fake_user
    app.dependency_overrides[get_asset_service] = lambda: stub
    return TestClient(app)


def test_no_category_filter(client: TestClient, stub: _StubAssetService) -> None:
    response = client.get("/v1/assets")
    assert response.status_code == 200
    assert stub.last_kwargs is not None
    assert stub.last_kwargs["category_id"] is None
    assert stub.last_kwargs["category_ids"] is None


def test_legacy_singular_category_id(
    client: TestClient, stub: _StubAssetService
) -> None:
    """``?category_id=<uuid>`` still flows through (backwards compat)."""
    cid = uuid4()
    response = client.get("/v1/assets", params={"category_id": str(cid)})
    assert response.status_code == 200
    assert stub.last_kwargs is not None
    assert stub.last_kwargs["category_id"] == cid
    assert stub.last_kwargs["category_ids"] is None


def test_plural_category_ids_single_value(
    client: TestClient, stub: _StubAssetService
) -> None:
    """``?category_ids=<uuid>`` (one value) becomes a one-element list."""
    cid = uuid4()
    response = client.get("/v1/assets", params={"category_ids": str(cid)})
    assert response.status_code == 200
    assert stub.last_kwargs is not None
    assert stub.last_kwargs["category_id"] is None
    assert stub.last_kwargs["category_ids"] == [cid]


def test_plural_category_ids_multiple_values(
    client: TestClient, stub: _StubAssetService
) -> None:
    """``?category_ids=A&category_ids=B`` => ``[A, B]``."""
    a, b = uuid4(), uuid4()
    response = client.get(
        "/v1/assets",
        params=[("category_ids", str(a)), ("category_ids", str(b))],
    )
    assert response.status_code == 200
    assert stub.last_kwargs is not None
    assert stub.last_kwargs["category_ids"] == [a, b]


def test_invalid_uuid_in_category_ids_returns_422(
    client: TestClient, stub: _StubAssetService
) -> None:
    """FastAPI's ``list[UUID]`` validator rejects malformed ids."""
    response = client.get(
        "/v1/assets",
        params=[("category_ids", "not-a-uuid")],
    )
    assert response.status_code == 422
    assert stub.last_kwargs is None  # never reached the service


def test_both_singular_and_plural_accepted(
    client: TestClient, stub: _StubAssetService
) -> None:
    """When both are supplied the route forwards both; the service layer
    is responsible for the union semantics. This test only pins that the
    route doesn't 400 the combination."""
    a, b = uuid4(), uuid4()
    response = client.get(
        "/v1/assets",
        params=[("category_id", str(a)), ("category_ids", str(b))],
    )
    assert response.status_code == 200
    assert stub.last_kwargs is not None
    assert stub.last_kwargs["category_id"] == a
    assert stub.last_kwargs["category_ids"] == [b]
