"""Route-level tests for the ``/ui-config`` endpoints (Sprint 60, ADR-032 §7).

Increment 1: ``GET /ui-config`` over system defaults. Increment 2: the
``user_ui_prefs`` override layer folded into ``GET`` plus ``PUT /ui-config/me``.
Increment 3: the ``tenants.ui_config`` tenant + role default layers folded into
``GET`` plus admin-gated ``PUT /ui-config/tenant`` and ``PUT /ui-config/role/{role}``.

Mirrors the ``TestClient`` + ``dependency_overrides`` pattern from the other
route unit tests: no real DB — ``get_session`` is overridden to a sentinel, the
two repositories are monkeypatched onto in-memory dicts, and ``AuditLogger.log``
is stubbed so the admin writes don't touch the DB.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tagpulse.api.routes import ui_config as route
from tagpulse.api.routes.ui_config import router
from tagpulse.core.user_auth import AuthenticatedUser, get_current_user
from tagpulse.repositories.timescaledb.session import get_session


@dataclass
class _Ctx:
    client: TestClient
    user_id: UUID | None
    tenant_id: UUID
    user_store: dict[UUID, dict[str, Any]]
    tenant_store: dict[UUID, dict[str, Any] | None] = field(default_factory=dict)


def _build(
    monkeypatch: pytest.MonkeyPatch,
    *,
    user_id: UUID | None,
    role: str = "viewer",
    user_store: dict[UUID, dict[str, Any]] | None = None,
    tenant_store: dict[UUID, dict[str, Any] | None] | None = None,
) -> _Ctx:
    app = FastAPI()
    app.include_router(router)
    tenant_id = uuid4()
    users = user_store if user_store is not None else {}
    tenants = tenant_store if tenant_store is not None else {}

    def _fake_user() -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=user_id,
            tenant_id=tenant_id,
            tenant_name="t",
            tenant_slug="t",
            role=role,
        )

    async def _session():  # type: ignore[no-untyped-def]
        yield object()

    async def _user_get(self: object, uid: UUID) -> dict[str, Any] | None:
        return users.get(uid)

    async def _user_upsert(self: object, uid: UUID, tid: UUID, prefs: dict[str, Any]) -> None:
        users[uid] = prefs

    async def _tenant_get(self: object, tid: UUID) -> dict[str, Any] | None:
        return tenants.get(tid)

    async def _tenant_set(self: object, tid: UUID, blob: dict[str, Any] | None) -> None:
        tenants[tid] = blob

    async def _audit_log(self: object, *args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(route.UserUiPrefsRepository, "get_for_user", _user_get)
    monkeypatch.setattr(route.UserUiPrefsRepository, "upsert", _user_upsert)
    monkeypatch.setattr(route.TenantUiConfigRepository, "get", _tenant_get)
    monkeypatch.setattr(route.TenantUiConfigRepository, "set", _tenant_set)
    monkeypatch.setattr(route.AuditLogger, "log", _audit_log)
    app.dependency_overrides[get_current_user] = _fake_user
    app.dependency_overrides[get_session] = _session
    return _Ctx(TestClient(app), user_id, tenant_id, users, tenants)


# ---------------------------------------------------------------------------
# GET /ui-config — system default + user layer
# ---------------------------------------------------------------------------


def test_get_ui_config_returns_system_default(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _build(monkeypatch, user_id=uuid4())
    response = ctx.client.get("/ui-config")
    assert response.status_code == 200
    body = response.json()
    # No stored layers → system default (today's UI). ``labels`` carries the
    # canonical term catalogue; every other leaf is empty.
    assert body["labels"]["device"] == "Device"
    assert body["labels"]["telemetry"] == "Telemetry"
    assert body["theme"] == {"variant": "default", "cardStyle": "default"}
    assert body["nav"] == {"hidden": [], "order": [], "placement": {}}
    assert body["cards"] == {}
    assert body["columns"] == {}
    assert body["tables"] == {}


def test_get_ui_config_serialises_camelcase(monkeypatch: pytest.MonkeyPatch) -> None:
    """The resolved document carries the ADR-032 §4 camelCase wire keys."""
    ctx = _build(monkeypatch, user_id=uuid4())
    body = ctx.client.get("/ui-config").json()
    assert "cardStyle" in body["theme"]
    assert "card_style" not in body["theme"]


def test_get_ui_config_folds_stored_user_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stored ``user_ui_prefs`` row is folded onto the system default."""
    uid = uuid4()
    users: dict[UUID, dict[str, Any]] = {
        uid: {"theme": {"cardStyle": "sparkline"}, "columns": {"assets": {"hidden": ["metadata"]}}}
    }
    ctx = _build(monkeypatch, user_id=uid, user_store=users)
    body = ctx.client.get("/ui-config").json()
    assert body["theme"] == {"variant": "default", "cardStyle": "sparkline"}
    assert body["columns"]["assets"]["hidden"] == ["metadata"]
    # Untouched leaves still fall through to the system default.
    assert body["nav"] == {"hidden": [], "order": [], "placement": {}}


def test_get_ui_config_no_user_identity_is_system_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The X-Tenant-ID path (``user_id=None``) has no per-user layer."""
    ctx = _build(monkeypatch, user_id=None)
    body = ctx.client.get("/ui-config").json()
    assert body["theme"]["cardStyle"] == "default"


def test_get_ui_config_requires_auth() -> None:
    """No ``get_current_user`` override → the dependency rejects the call."""
    app = FastAPI()
    app.include_router(router)
    unauth = TestClient(app)
    response = unauth.get("/ui-config")
    assert response.status_code in {401, 403}


# ---------------------------------------------------------------------------
# GET /ui-config — tenant + role layers (increment 3)
# ---------------------------------------------------------------------------


def test_get_folds_tenant_default_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tenant-default leaves (top level of ``ui_config``) fold beneath the user."""
    ctx = _build(monkeypatch, user_id=uuid4())
    ctx.tenant_store[ctx.tenant_id] = {"labels": {"device": "Reader"}}
    body = ctx.client.get("/ui-config").json()
    assert body["labels"]["device"] == "Reader"
    # untouched terms still fall through to the canonical default
    assert body["labels"]["telemetry"] == "Telemetry"


def test_get_folds_role_default_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The caller's role layer (``ui_config.roles[role]``) folds over the tenant."""
    ctx = _build(monkeypatch, user_id=uuid4(), role="viewer")
    ctx.tenant_store[ctx.tenant_id] = {
        "theme": {"variant": "operator"},
        "roles": {
            "viewer": {"columns": {"assets": {"advanced": ["tid"]}}},
            "editor": {"theme": {"variant": "power"}},
        },
    }
    body = ctx.client.get("/ui-config").json()
    # tenant default applies...
    assert body["theme"]["variant"] == "operator"
    # ...the viewer role layer applies...
    assert body["columns"]["assets"]["advanced"] == ["tid"]
    # ...and the *editor* role layer does not leak to a viewer.
    assert body["theme"]["variant"] != "power"


def test_get_precedence_tenant_then_role_then_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """Last writer wins per leaf: tenant → role → user (ADR-032 §2)."""
    uid = uuid4()
    users: dict[UUID, dict[str, Any]] = {uid: {"theme": {"variant": "power"}}}
    ctx = _build(monkeypatch, user_id=uid, role="viewer", user_store=users)
    ctx.tenant_store[ctx.tenant_id] = {
        "theme": {"variant": "operator", "cardStyle": "default"},
        "roles": {"viewer": {"theme": {"cardStyle": "sparkline"}}},
    }
    body = ctx.client.get("/ui-config").json()
    assert body["theme"]["variant"] == "power"  # user wins over tenant
    assert body["theme"]["cardStyle"] == "sparkline"  # role wins, user untouched it


# ---------------------------------------------------------------------------
# PUT /ui-config/me
# ---------------------------------------------------------------------------


def test_put_ui_config_me_upserts_and_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    uid = uuid4()
    ctx = _build(monkeypatch, user_id=uid)
    resp = ctx.client.put("/ui-config/me", json={"theme": {"cardStyle": "sparkline"}})
    assert resp.status_code == 200
    assert resp.json()["theme"]["cardStyle"] == "sparkline"
    # The sparse override (only the set key) was persisted.
    assert ctx.user_store[uid] == {"theme": {"cardStyle": "sparkline"}}
    # A follow-up GET reflects it.
    assert ctx.client.get("/ui-config").json()["theme"]["cardStyle"] == "sparkline"


def test_put_ui_config_me_rejects_unknown_key(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _build(monkeypatch, user_id=uuid4())
    resp = ctx.client.put("/ui-config/me", json={"bogus": True})
    assert resp.status_code == 422


def test_put_ui_config_me_rejects_unknown_theme_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The theme leaf is a curated catalogue — an unregistered variant is a
    422, not a silently-stored value (ADR-032 §7 step 5)."""
    ctx = _build(monkeypatch, user_id=uuid4())
    resp = ctx.client.put("/ui-config/me", json={"theme": {"variant": "rainbow"}})
    assert resp.status_code == 422


def test_put_ui_config_me_rejects_bad_leaf_type(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _build(monkeypatch, user_id=uuid4())
    resp = ctx.client.put(
        "/ui-config/me",
        json={"tables": {"assets": {"defaultSort": {"key": "name", "dir": "up"}}}},
    )
    assert resp.status_code == 422


def test_put_ui_config_me_empty_body_resets(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty body clears the override — resolves to the layers below."""
    uid = uuid4()
    users: dict[UUID, dict[str, Any]] = {uid: {"theme": {"cardStyle": "sparkline"}}}
    ctx = _build(monkeypatch, user_id=uid, user_store=users)
    resp = ctx.client.put("/ui-config/me", json={})
    assert resp.status_code == 200
    assert resp.json()["theme"]["cardStyle"] == "default"
    assert ctx.user_store[uid] == {}


def test_put_ui_config_me_requires_user_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """An API-key-only / X-Tenant-ID caller has no user to attach prefs to."""
    ctx = _build(monkeypatch, user_id=None)
    resp = ctx.client.put("/ui-config/me", json={"theme": {"cardStyle": "sparkline"}})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /ui-config/tenant (admin)
# ---------------------------------------------------------------------------


def test_put_tenant_sets_default_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _build(monkeypatch, user_id=uuid4(), role="admin")
    resp = ctx.client.put("/ui-config/tenant", json={"labels": {"device": "Reader"}})
    assert resp.status_code == 200
    # resolved doc reflects the skin; stored blob stays sparse.
    assert resp.json()["labels"]["device"] == "Reader"
    assert ctx.tenant_store[ctx.tenant_id] == {"labels": {"device": "Reader"}}


def test_put_tenant_preserves_role_subtree(monkeypatch: pytest.MonkeyPatch) -> None:
    """Editing the tenant-default leaves leaves the per-role layer intact."""
    ctx = _build(monkeypatch, user_id=uuid4(), role="admin")
    ctx.tenant_store[ctx.tenant_id] = {"roles": {"viewer": {"theme": {"variant": "operator"}}}}
    resp = ctx.client.put("/ui-config/tenant", json={"labels": {"device": "Reader"}})
    assert resp.status_code == 200
    assert ctx.tenant_store[ctx.tenant_id] == {
        "labels": {"device": "Reader"},
        "roles": {"viewer": {"theme": {"variant": "operator"}}},
    }


def test_put_tenant_empty_body_clears_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _build(monkeypatch, user_id=uuid4(), role="admin")
    ctx.tenant_store[ctx.tenant_id] = {
        "labels": {"device": "Reader"},
        "roles": {"viewer": {"theme": {"variant": "operator"}}},
    }
    resp = ctx.client.put("/ui-config/tenant", json={})
    assert resp.status_code == 200
    # tenant-default leaves gone, role layer preserved.
    assert ctx.tenant_store[ctx.tenant_id] == {
        "roles": {"viewer": {"theme": {"variant": "operator"}}}
    }


def test_put_tenant_requires_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _build(monkeypatch, user_id=uuid4(), role="viewer")
    resp = ctx.client.put("/ui-config/tenant", json={"labels": {"device": "Reader"}})
    assert resp.status_code == 403


def test_put_tenant_rejects_unknown_key(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _build(monkeypatch, user_id=uuid4(), role="admin")
    resp = ctx.client.put("/ui-config/tenant", json={"bogus": 1})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PUT /ui-config/role/{role} (admin)
# ---------------------------------------------------------------------------


def test_put_role_sets_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _build(monkeypatch, user_id=uuid4(), role="admin")
    resp = ctx.client.put(
        "/ui-config/role/viewer", json={"columns": {"assets": {"advanced": ["tid"]}}}
    )
    assert resp.status_code == 200
    assert ctx.tenant_store[ctx.tenant_id] == {
        "roles": {"viewer": {"columns": {"assets": {"advanced": ["tid"]}}}}
    }


def test_put_role_empty_body_removes_that_role(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _build(monkeypatch, user_id=uuid4(), role="admin")
    ctx.tenant_store[ctx.tenant_id] = {
        "labels": {"device": "Reader"},
        "roles": {
            "viewer": {"theme": {"variant": "operator"}},
            "editor": {"theme": {"variant": "power"}},
        },
    }
    resp = ctx.client.put("/ui-config/role/viewer", json={})
    assert resp.status_code == 200
    # viewer removed, editor + tenant defaults preserved.
    assert ctx.tenant_store[ctx.tenant_id] == {
        "labels": {"device": "Reader"},
        "roles": {"editor": {"theme": {"variant": "power"}}},
    }


def test_put_role_unknown_role_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _build(monkeypatch, user_id=uuid4(), role="admin")
    resp = ctx.client.put("/ui-config/role/superuser", json={"labels": {"device": "Reader"}})
    assert resp.status_code == 422


def test_put_role_requires_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _build(monkeypatch, user_id=uuid4(), role="editor")
    resp = ctx.client.put("/ui-config/role/viewer", json={"labels": {"device": "Reader"}})
    assert resp.status_code == 403


def test_put_role_rejects_unknown_key(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _build(monkeypatch, user_id=uuid4(), role="admin")
    resp = ctx.client.put("/ui-config/role/viewer", json={"bogus": 1})
    assert resp.status_code == 422
