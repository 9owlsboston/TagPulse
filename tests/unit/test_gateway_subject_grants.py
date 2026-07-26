"""Sprint 81 — per-gateway subject grants (C-6S9H): repo + admin route guards."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tagpulse.api.dependencies import get_gateway_grant_repo
from tagpulse.api.routes import gateway_grants as grants_route
from tagpulse.core.user_auth import AuthenticatedUser, get_current_user
from tagpulse.repositories.timescaledb.gateway_subject_grants import (
    TimescaleGatewaySubjectGrantRepository,
)
from tagpulse.repositories.timescaledb.session import get_session

# --------------------------------------------------------------------------- #
# Repository (fake session)
# --------------------------------------------------------------------------- #


class _Result:
    def __init__(self, rows: list[Any], rowcount: int = 0) -> None:
        self._rows = rows
        self.rowcount = rowcount

    def all(self) -> list[Any]:
        return self._rows

    def scalars(self) -> _Result:
        return self

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, rows: list[Any] | None = None, rowcount: int = 0) -> None:
        self._rows = rows or []
        self._rowcount = rowcount
        self.added: list[Any] = []

    async def execute(self, _stmt: Any) -> _Result:
        return _Result(self._rows, self._rowcount)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_repo_create_populates_response() -> None:
    repo = TimescaleGatewaySubjectGrantRepository(_FakeSession())  # type: ignore[arg-type]
    tid, gw, sid, by = uuid4(), uuid4(), uuid4(), uuid4()
    resp = await repo.create(tid, gw, "asset", sid, by)
    assert resp.tenant_id == tid
    assert resp.gateway_device_id == gw
    assert resp.subject_kind == "asset"
    assert resp.subject_id == sid
    assert resp.granted_by == by
    assert resp.granted_at is not None
    assert resp.revoked_at is None


@pytest.mark.asyncio
async def test_repo_active_subject_set_builds_tuples() -> None:
    a, b = uuid4(), uuid4()
    session = _FakeSession(rows=[("asset", a), ("device", b)])
    repo = TimescaleGatewaySubjectGrantRepository(session)  # type: ignore[arg-type]
    result = await repo.active_subject_set(uuid4(), uuid4())
    assert result == {("asset", a), ("device", b)}


@pytest.mark.asyncio
async def test_repo_revoke_returns_false_when_nothing_active() -> None:
    repo = TimescaleGatewaySubjectGrantRepository(_FakeSession(rowcount=0))  # type: ignore[arg-type]
    ok = await repo.revoke(uuid4(), uuid4(), "asset", uuid4(), None)
    assert ok is False


@pytest.mark.asyncio
async def test_repo_revoke_returns_true_on_hit() -> None:
    repo = TimescaleGatewaySubjectGrantRepository(_FakeSession(rowcount=1))  # type: ignore[arg-type]
    ok = await repo.revoke(uuid4(), uuid4(), "asset", uuid4(), None)
    assert ok is True


# --------------------------------------------------------------------------- #
# Admin route — pre-DB validation guards (DB-dependent paths are integration)
# --------------------------------------------------------------------------- #


class _FakeGrantRepo:
    async def active_subject_set(self, *_a: Any) -> set[tuple[str, UUID]]:
        return set()


def _admin_app(role: str = "admin") -> FastAPI:
    app = FastAPI()
    app.include_router(grants_route.router)

    def _user() -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=uuid4(),
            tenant_id=uuid4(),
            tenant_name="Acme",
            tenant_slug="acme",
            role=role,
        )

    async def _session() -> Any:  # sentinel — the guarded branches raise first
        yield object()

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_gateway_grant_repo] = lambda: _FakeGrantRepo()
    app.dependency_overrides[get_session] = _session
    return app


async def _post_grant(app: FastAPI, device_id: UUID, body: dict[str, Any]) -> Any:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.post(f"/admin/gateways/{device_id}/subject-grants", json=body)


@pytest.mark.asyncio
async def test_create_grant_unknown_kind_422() -> None:
    # All five Literal kinds are now supported (C-4Z66); a non-Literal kind is
    # rejected by schema validation (422) before the route runs.
    app = _admin_app()
    resp = await _post_grant(app, uuid4(), {"subject_kind": "widget", "subject_id": str(uuid4())})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_grant_self_device_redundant_422() -> None:
    app = _admin_app()
    did = uuid4()
    resp = await _post_grant(app, did, {"subject_kind": "device", "subject_id": str(did)})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_grant_non_admin_403() -> None:
    app = _admin_app(role="editor")
    resp = await _post_grant(app, uuid4(), {"subject_kind": "asset", "subject_id": str(uuid4())})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_grant_device_principal_403() -> None:
    app = _admin_app(role="device")
    resp = await _post_grant(app, uuid4(), {"subject_kind": "asset", "subject_id": str(uuid4())})
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# _assert_subject_exists — new grant kinds (C-4Z66)
# --------------------------------------------------------------------------- #


def _patch_repo(monkeypatch: pytest.MonkeyPatch, name: str, *, found: bool) -> None:
    """Replace grants_route.<name> so ``Repo(session).get(...)`` returns a stub or None."""

    class _R:
        def __init__(self, _session: Any) -> None:
            pass

        async def get(self, _tenant: UUID, _subject: UUID) -> Any:
            return object() if found else None

    monkeypatch.setattr(grants_route, name, _R)


_NEW_KINDS = [
    ("lot", "TimescaleLotRepository"),
    ("stock_item", "TimescaleStockItemRepository"),
    ("zone", "TimescaleZoneRepository"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,repo_name", _NEW_KINDS)
async def test_assert_subject_exists_new_kind_ok(
    monkeypatch: pytest.MonkeyPatch, kind: str, repo_name: str
) -> None:
    _patch_repo(monkeypatch, repo_name, found=True)
    # Does not raise when the subject exists in-tenant.
    await grants_route._assert_subject_exists(object(), uuid4(), kind, uuid4())  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,repo_name", _NEW_KINDS)
async def test_assert_subject_exists_new_kind_404(
    monkeypatch: pytest.MonkeyPatch, kind: str, repo_name: str
) -> None:
    from fastapi import HTTPException

    _patch_repo(monkeypatch, repo_name, found=False)
    with pytest.raises(HTTPException) as exc:
        await grants_route._assert_subject_exists(object(), uuid4(), kind, uuid4())  # type: ignore[arg-type]
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_assert_subject_exists_unknown_kind_422() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await grants_route._assert_subject_exists(object(), uuid4(), "widget", uuid4())  # type: ignore[arg-type]
    assert exc.value.status_code == 422
