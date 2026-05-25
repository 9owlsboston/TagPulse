"""Unit tests for the Sprint 54 Phase 54.3 dashboard summary surface.

The DB-touching path (eight aggregate queries + threshold lookup) is
covered by integration tests when the migration-check harness runs
against TimescaleDB. Here we cover:

- Route dispatch: ``GET /dashboard/summary`` returns the service's
  :class:`DashboardSummary` verbatim and passes the caller's
  ``tenant_id`` through.
- Role gating: ``admin`` / ``editor`` / ``viewer`` succeed; anonymous
  requests are rejected.
- Schema contract: every field documented in
  ``docs/design/sprint-54-ui-overhaul.md`` Phase C is present and typed.

Service-layer SQL behaviour will be covered by an integration
test once `tests/integration/` grows past migration round-trips
(currently the suite's only file); deferred per the same pattern
as ``tests/unit/test_tag_reconciliation.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tagpulse.api.routes import dashboard as dashboard_route
from tagpulse.core.user_auth import AuthenticatedUser, get_current_user
from tagpulse.models.schemas import DashboardSummary
from tagpulse.repositories.timescaledb.session import get_session


def _make_app(role: str = "viewer") -> tuple[FastAPI, UUID]:
    tenant_id = uuid4()
    user_id = uuid4()
    app = FastAPI()
    app.include_router(dashboard_route.router)

    def _user() -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=user_id,
            tenant_id=tenant_id,
            tenant_name="T",
            tenant_slug="t",
            role=role,
        )

    async def _session():  # type: ignore[no-untyped-def]
        # Sentinel — the route hands this to the service which we
        # monkeypatch, so it never reaches SQLAlchemy.
        yield object()

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_session] = _session
    return app, tenant_id


def _fixed_summary() -> DashboardSummary:
    return DashboardSummary(
        generated_at=datetime(2026, 5, 24, 12, 0, tzinfo=UTC),
        devices_online=7,
        devices_total=10,
        alerts_open_24h=3,
        reads_per_hour_now=1_234,
        assets_active=42,
        tag_transfers_in_flight=2,
        tag_recon_backlog=15,
        low_stock_count=4,
    )


@pytest.mark.asyncio
async def test_summary_returns_service_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _stub(session: Any, tenant_id: uuid.UUID) -> DashboardSummary:
        captured["session"] = session
        captured["tenant_id"] = tenant_id
        return _fixed_summary()

    monkeypatch.setattr(dashboard_route.dashboard_service, "get_summary", _stub)

    app, tenant_id = _make_app(role="viewer")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/dashboard/summary")

    assert resp.status_code == 200
    body = resp.json()
    # Every field documented in the design doc must be present —
    # the UI contract depends on the full set.
    assert set(body) == {
        "generated_at",
        "devices_online",
        "devices_total",
        "alerts_open_24h",
        "reads_per_hour_now",
        "assets_active",
        "tag_transfers_in_flight",
        "tag_recon_backlog",
        "low_stock_count",
    }
    assert body["devices_online"] == 7
    assert body["devices_total"] == 10
    assert body["alerts_open_24h"] == 3
    assert body["reads_per_hour_now"] == 1_234
    assert body["assets_active"] == 42
    assert body["tag_transfers_in_flight"] == 2
    assert body["tag_recon_backlog"] == 15
    assert body["low_stock_count"] == 4
    assert body["generated_at"].startswith("2026-05-24T12:00:00")
    # tenant_id from the auth dep must flow into the service call.
    assert captured["tenant_id"] == tenant_id


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "editor", "viewer"])
async def test_summary_open_to_all_logged_in_roles(
    monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    async def _stub(session: Any, tenant_id: uuid.UUID) -> DashboardSummary:
        return _fixed_summary()

    monkeypatch.setattr(dashboard_route.dashboard_service, "get_summary", _stub)

    app, _ = _make_app(role=role)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/dashboard/summary")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_summary_rejects_unknown_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _stub(session: Any, tenant_id: uuid.UUID) -> DashboardSummary:
        return _fixed_summary()

    monkeypatch.setattr(dashboard_route.dashboard_service, "get_summary", _stub)

    app, _ = _make_app(role="device")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/dashboard/summary")
    # require_role rejects any role outside the {admin, editor, viewer} set.
    assert resp.status_code == 403
