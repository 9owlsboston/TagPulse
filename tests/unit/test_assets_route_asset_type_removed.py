"""Sprint 41 Phase H regression: ``?asset_type=`` returns HTTP 400.

The ``asset_type`` query parameter was removed when the column was
dropped (migration ``041_drop_assets_asset_type``). For one release the
route surfaces an explicit 400 with a migration hint that points
clients at ``?category_id=``. This test pins that behaviour so we can
notice if somebody quietly drops the guard before Sprint 42, *and* so
the hint string never accidentally regresses to a generic 422 from
FastAPI's "unknown query param? doesn't matter" default.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tagpulse.api.dependencies import get_asset_service
from tagpulse.api.routes.assets import router
from tagpulse.core.user_auth import AuthenticatedUser, get_current_user


class _StubAssetService:
    """Minimal stand-in — list_assets must succeed when *no* asset_type
    is supplied so we can also assert the happy path stays 200."""

    async def list_assets(self, *args: object, **kwargs: object) -> list[object]:
        return []


@pytest.fixture
def client() -> TestClient:
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
    app.dependency_overrides[get_asset_service] = lambda: _StubAssetService()
    return TestClient(app)


def test_asset_type_query_returns_400_with_migration_hint(
    client: TestClient,
) -> None:
    """Sentinel value — any presence of the key trips the guard."""
    response = client.get("/v1/assets", params={"asset_type": "pallet"})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "asset_type" in detail
    assert "category_id" in detail
    assert "Sprint 41" in detail


def test_asset_type_empty_value_still_returns_400(client: TestClient) -> None:
    """Membership test is on the key only, not the value."""
    response = client.get("/v1/assets?asset_type=")
    assert response.status_code == 400


def test_list_assets_without_asset_type_still_works(client: TestClient) -> None:
    """Sanity check that the guard doesn't fire on unrelated requests."""
    response = client.get("/v1/assets")
    assert response.status_code == 200
    assert response.json() == []
