"""Route-level tests for the ``/ui-config`` endpoints (Sprint 60, ADR-032 §7).

Increment 1: ``GET /ui-config`` over system defaults. Increment 2: the
``user_ui_prefs`` override layer folded into ``GET`` plus ``PUT /ui-config/me``.

Mirrors the ``TestClient`` + ``dependency_overrides`` pattern from the other
route unit tests: no real DB — ``get_session`` is overridden to a sentinel and
the ``UserUiPrefsRepository`` methods are monkeypatched onto an in-memory dict.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tagpulse.api.routes import ui_config as route
from tagpulse.api.routes.ui_config import router
from tagpulse.core.user_auth import AuthenticatedUser, get_current_user
from tagpulse.repositories.timescaledb.session import get_session


def _build_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    user_id: UUID | None,
    store: dict[UUID, dict[str, Any]],
) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    tenant_id = uuid4()

    def _fake_user() -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=user_id,
            tenant_id=tenant_id,
            tenant_name="t",
            tenant_slug="t",
            role="viewer",
        )

    async def _session():  # type: ignore[no-untyped-def]
        yield object()

    async def _get_for_user(self: object, uid: UUID) -> dict[str, Any] | None:
        return store.get(uid)

    async def _upsert(self: object, uid: UUID, tid: UUID, prefs: dict[str, Any]) -> None:
        store[uid] = prefs

    monkeypatch.setattr(route.UserUiPrefsRepository, "get_for_user", _get_for_user)
    monkeypatch.setattr(route.UserUiPrefsRepository, "upsert", _upsert)
    app.dependency_overrides[get_current_user] = _fake_user
    app.dependency_overrides[get_session] = _session
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /ui-config
# ---------------------------------------------------------------------------


def test_get_ui_config_returns_system_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build_app(monkeypatch, user_id=uuid4(), store={})
    response = client.get("/ui-config")
    assert response.status_code == 200
    body = response.json()
    # No stored override → system default (today's UI, empty).
    assert body["labels"] == {}
    assert body["theme"] == {"variant": "default", "cardStyle": "default"}
    assert body["nav"] == {"hidden": [], "order": []}
    assert body["cards"] == {}
    assert body["columns"] == {}
    assert body["tables"] == {}


def test_get_ui_config_serialises_camelcase(monkeypatch: pytest.MonkeyPatch) -> None:
    """The resolved document carries the ADR-032 §4 camelCase wire keys."""
    client = _build_app(monkeypatch, user_id=uuid4(), store={})
    body = client.get("/ui-config").json()
    assert "cardStyle" in body["theme"]
    assert "card_style" not in body["theme"]


def test_get_ui_config_folds_stored_user_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stored ``user_ui_prefs`` row is folded onto the system default."""
    uid = uuid4()
    store: dict[UUID, dict[str, Any]] = {
        uid: {"theme": {"cardStyle": "sparkline"}, "columns": {"assets": {"hidden": ["metadata"]}}}
    }
    client = _build_app(monkeypatch, user_id=uid, store=store)
    body = client.get("/ui-config").json()
    assert body["theme"] == {"variant": "default", "cardStyle": "sparkline"}
    assert body["columns"]["assets"]["hidden"] == ["metadata"]
    # Untouched leaves still fall through to the system default.
    assert body["nav"] == {"hidden": [], "order": []}


def test_get_ui_config_no_user_identity_is_system_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The X-Tenant-ID path (``user_id=None``) has no per-user layer."""
    store: dict[UUID, dict[str, Any]] = {}
    client = _build_app(monkeypatch, user_id=None, store=store)
    body = client.get("/ui-config").json()
    assert body["theme"]["cardStyle"] == "default"


def test_get_ui_config_requires_auth() -> None:
    """No ``get_current_user`` override → the dependency rejects the call."""
    app = FastAPI()
    app.include_router(router)
    unauth = TestClient(app)
    response = unauth.get("/ui-config")
    assert response.status_code in {401, 403}


# ---------------------------------------------------------------------------
# PUT /ui-config/me
# ---------------------------------------------------------------------------


def test_put_ui_config_me_upserts_and_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    uid = uuid4()
    store: dict[UUID, dict[str, Any]] = {}
    client = _build_app(monkeypatch, user_id=uid, store=store)

    resp = client.put("/ui-config/me", json={"theme": {"cardStyle": "sparkline"}})
    assert resp.status_code == 200
    # Response is the freshly-resolved document with the override folded.
    assert resp.json()["theme"]["cardStyle"] == "sparkline"
    # The sparse override (only the set key) was persisted.
    assert store[uid] == {"theme": {"cardStyle": "sparkline"}}
    # A follow-up GET reflects it.
    assert client.get("/ui-config").json()["theme"]["cardStyle"] == "sparkline"


def test_put_ui_config_me_rejects_unknown_key(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build_app(monkeypatch, user_id=uuid4(), store={})
    resp = client.put("/ui-config/me", json={"bogus": True})
    assert resp.status_code == 422


def test_put_ui_config_me_rejects_bad_leaf_type(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build_app(monkeypatch, user_id=uuid4(), store={})
    resp = client.put(
        "/ui-config/me",
        json={"tables": {"assets": {"defaultSort": {"key": "name", "dir": "up"}}}},
    )
    assert resp.status_code == 422


def test_put_ui_config_me_empty_body_resets(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty body clears the override — resolves to the system default."""
    uid = uuid4()
    store: dict[UUID, dict[str, Any]] = {uid: {"theme": {"cardStyle": "sparkline"}}}
    client = _build_app(monkeypatch, user_id=uid, store=store)
    resp = client.put("/ui-config/me", json={})
    assert resp.status_code == 200
    assert resp.json()["theme"]["cardStyle"] == "default"
    assert store[uid] == {}


def test_put_ui_config_me_requires_user_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """An API-key-only / X-Tenant-ID caller has no user to attach prefs to."""
    client = _build_app(monkeypatch, user_id=None, store={})
    resp = client.put("/ui-config/me", json={"theme": {"cardStyle": "sparkline"}})
    assert resp.status_code == 403
