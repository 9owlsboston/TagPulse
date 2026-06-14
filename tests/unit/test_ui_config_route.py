"""Route-level tests for ``GET /ui-config`` (Sprint 60, ADR-032 §7 step 1).

Mirrors the ``TestClient`` + ``app.dependency_overrides[get_current_user]``
pattern from ``test_assets_route_category_ids.py``: no DB, the assertions live
on the resolved response document.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tagpulse.api.routes.ui_config import router
from tagpulse.core.user_auth import AuthenticatedUser, get_current_user


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)

    def _fake_user() -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=uuid4(),
            tenant_id=uuid4(),
            tenant_name="t",
            tenant_slug="t",
            role="viewer",
        )

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


def test_get_ui_config_returns_system_default(client: TestClient) -> None:
    response = client.get("/ui-config")
    assert response.status_code == 200
    body = response.json()
    # Increment 1: system default for every caller — empty, today's UI.
    assert body["labels"] == {}
    assert body["theme"] == {"variant": "default", "cardStyle": "default"}
    assert body["nav"] == {"hidden": [], "order": []}
    assert body["cards"] == {}
    assert body["columns"] == {}
    assert body["tables"] == {}


def test_get_ui_config_serialises_camelcase(client: TestClient) -> None:
    """The resolved document carries the ADR-032 §4 camelCase wire keys."""
    body = client.get("/ui-config").json()
    assert "cardStyle" in body["theme"]
    assert "card_style" not in body["theme"]


def test_get_ui_config_requires_auth() -> None:
    """No ``get_current_user`` override → the dependency rejects the call."""
    app = FastAPI()
    app.include_router(router)
    unauth = TestClient(app)
    response = unauth.get("/ui-config")
    assert response.status_code in {401, 403}
