"""Sprint 49 Phase B1: ``?asset_type=`` is silently ignored.

The Sprint 41 Phase H 400 migration-hint guard was a one-release
courtesy after the column drop. Sprint 49 removes the guard \u2014 the
column has been gone since migration ``041_drop_assets_asset_type``,
no UI surface still sends the param, and the deprecation window has
long expired. This test pins the new behaviour (unknown param is
ignored, route returns 200) so a future refactor can't accidentally
reinstate the 400.
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


def test_asset_type_query_is_silently_ignored(client: TestClient) -> None:
    """Unknown query param \u2192 200, no filter applied."""
    response = client.get("/v1/assets", params={"asset_type": "pallet"})
    assert response.status_code == 200
    assert response.json() == []


def test_asset_type_empty_value_is_silently_ignored(client: TestClient) -> None:
    response = client.get("/v1/assets?asset_type=")
    assert response.status_code == 200


def test_list_assets_without_asset_type_still_works(client: TestClient) -> None:
    response = client.get("/v1/assets")
    assert response.status_code == 200
    assert response.json() == []
