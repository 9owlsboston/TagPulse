"""Sprint 52: ``GET /bulk-operations`` list endpoint.

Tests the route → service contract with a monkeypatched
:func:`pending_ops.list_pending` so we don't need a live Postgres.
DB-touching paths for the SELECT itself are covered by the
integration suite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tagpulse.api.routes import bulk_operations as bulk_ops_routes
from tagpulse.core.user_auth import AuthenticatedUser, get_current_user
from tagpulse.models.database import PendingBulkOperationModel
from tagpulse.repositories.timescaledb.session import get_session
from tagpulse.services import pending_bulk_operations as pending_ops


def _make_app(role: str = "admin") -> tuple[FastAPI, uuid.UUID, uuid.UUID]:
    tenant_id = uuid4()
    user_id = uuid4()
    app = FastAPI()
    app.include_router(bulk_ops_routes.router)

    def _user() -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=user_id,
            tenant_id=tenant_id,
            tenant_name="T",
            tenant_slug="t",
            role=role,
        )

    async def _session():  # type: ignore[no-untyped-def]
        yield object()

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_session] = _session
    return app, tenant_id, user_id


def _make_row(
    *,
    tenant_id: uuid.UUID,
    operation: str = "tags.import",
    status: str = "pending",
) -> PendingBulkOperationModel:
    now = datetime.now(UTC)
    return PendingBulkOperationModel(
        id=uuid4(),
        tenant_id=tenant_id,
        operation=operation,
        status=status,
        requested_by=uuid4(),
        decided_by=None,
        content_hash="hash-" + operation,
        row_count=10,
        sample=["AAA", "BBB"],
        payload=b"epc_hex\nAAA\nBBB\n",
        request_id=None,
        created_at=now,
        decided_at=None,
        executed_at=None,
        expires_at=now + timedelta(hours=24),
    )


@pytest.mark.asyncio
async def test_list_default_returns_all_for_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    app, tenant_id, _ = _make_app(role="admin")
    rows = [
        _make_row(tenant_id=tenant_id),
        _make_row(tenant_id=tenant_id, operation="tags.bulk_patch", status="approved"),
    ]

    async def _stub(
        session: Any,
        tid: uuid.UUID,
        *,
        status: str | None,
        operation: str | None,
        limit: int,
        offset: int,
    ) -> list[PendingBulkOperationModel]:
        captured.update(
            tenant_id=tid, status=status, operation=operation, limit=limit, offset=offset
        )
        return rows

    monkeypatch.setattr(pending_ops, "list_pending", _stub)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/bulk-operations")

    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 2
    assert {b["operation"] for b in body} == {"tags.import", "tags.bulk_patch"}
    # payload bytes never leave the process.
    assert all("payload" not in b for b in body)
    assert captured == {
        "tenant_id": tenant_id,
        "status": None,
        "operation": None,
        "limit": 100,
        "offset": 0,
    }


@pytest.mark.asyncio
async def test_list_forwards_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    app, tenant_id, _ = _make_app(role="admin")

    async def _stub(session: Any, tid: uuid.UUID, **kwargs: Any) -> list[PendingBulkOperationModel]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(pending_ops, "list_pending", _stub)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(
            "/bulk-operations",
            params={
                "status": "pending",
                "operation": "tags.import",
                "limit": 25,
                "offset": 50,
            },
        )

    assert r.status_code == 200
    assert captured["status"] == "pending"
    assert captured["operation"] == "tags.import"
    assert captured["limit"] == 25
    assert captured["offset"] == 50


@pytest.mark.asyncio
async def test_list_rejects_invalid_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, _ = _make_app(role="admin")

    async def _stub(*a: Any, **kw: Any) -> list[PendingBulkOperationModel]:
        return []

    monkeypatch.setattr(pending_ops, "list_pending", _stub)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/bulk-operations", params={"status": "bogus"})

    assert r.status_code == 422


@pytest.mark.asyncio
async def test_list_forbidden_for_viewer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, _ = _make_app(role="viewer")

    async def _stub(*a: Any, **kw: Any) -> list[PendingBulkOperationModel]:
        return []

    monkeypatch.setattr(pending_ops, "list_pending", _stub)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/bulk-operations")

    assert r.status_code == 403


@pytest.mark.asyncio
async def test_list_editor_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    app, _, _ = _make_app(role="editor")

    async def _stub(*a: Any, **kw: Any) -> list[PendingBulkOperationModel]:
        return []

    monkeypatch.setattr(pending_ops, "list_pending", _stub)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/bulk-operations")

    assert r.status_code == 200
    assert r.json() == []
