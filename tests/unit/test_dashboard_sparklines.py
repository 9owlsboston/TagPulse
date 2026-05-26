"""Unit tests for Sprint 57 Phase 57.6 dashboard sparklines surface.

Covers:

- Route dispatch: ``GET /dashboard/sparklines`` returns the service's
  :class:`DashboardSparklines` verbatim and forwards the caller's
  ``tenant_id`` + query params into the service call.
- Schema contract: all 9 Dashboard tile keys are present; each tile
  has the documented ``series`` + ``trend`` shape; flat tiles emit
  ``trend="flat"``.
- Role gating: ``admin`` / ``editor`` / ``viewer`` succeed; other
  roles are rejected (parity with ``/dashboard/summary``).
- Query-param validation: ``days`` outside ``[1, 30]`` and
  ``bucket_hours`` outside ``[1, 24]`` are rejected by FastAPI.
- Pure helper: :func:`_classify_trend` thresholds (+/-5%) round-trip
  to ``"up"`` / ``"down"`` / ``"flat"`` correctly.

DB-touching behaviour of the two real time-bucket queries
(``date_bin`` over ``tag_reads`` + ``alerts``) is deferred to the
integration suite, matching the pattern from
``test_dashboard_summary.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tagpulse.api.routes import dashboard as dashboard_route
from tagpulse.core.user_auth import AuthenticatedUser, get_current_user
from tagpulse.models.schemas import (
    DashboardSparklines,
    SparklinePoint,
    SparklineSeries,
)
from tagpulse.repositories.timescaledb.session import get_session
from tagpulse.services.dashboard import _classify_trend

_TILE_IDS = {
    "devices",
    "alerts-open",
    "reads-per-hour",
    "assets-active",
    "tags",
    "locations",
    "transfers-in-flight",
    "recon-backlog",
    "low-stock",
}


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
        yield object()

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_session] = _session
    return app, tenant_id


def _fixed_sparklines() -> DashboardSparklines:
    base = datetime(2026, 5, 24, 0, 0, tzinfo=UTC)
    bucket = timedelta(hours=6)
    starts = [base + bucket * i for i in range(28)]

    def _flat(v: int) -> SparklineSeries:
        return SparklineSeries(series=[SparklinePoint(t=ts, v=v) for ts in starts], trend="flat")

    reads = SparklineSeries(
        series=[SparklinePoint(t=ts, v=100 + i * 5) for i, ts in enumerate(starts)],
        trend="up",
    )
    alerts = SparklineSeries(
        series=[SparklinePoint(t=ts, v=10 - i // 7) for i, ts in enumerate(starts)],
        trend="down",
    )
    return DashboardSparklines(
        generated_at=datetime(2026, 5, 24, 12, 0, tzinfo=UTC),
        bucket_hours=6,
        days=7,
        tiles={
            "devices": _flat(10),
            "alerts-open": alerts,
            "reads-per-hour": reads,
            "assets-active": _flat(42),
            "tags": _flat(128),
            "locations": _flat(16),
            "transfers-in-flight": _flat(2),
            "recon-backlog": _flat(15),
            "low-stock": _flat(4),
        },
    )


@pytest.mark.asyncio
async def test_sparklines_returns_service_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _stub(
        session: Any,
        tenant_id: uuid.UUID,
        days: int = 7,
        bucket_hours: int = 6,
    ) -> DashboardSparklines:
        captured["session"] = session
        captured["tenant_id"] = tenant_id
        captured["days"] = days
        captured["bucket_hours"] = bucket_hours
        return _fixed_sparklines()

    monkeypatch.setattr(dashboard_route.dashboard_service, "get_sparklines", _stub)

    app, tenant_id = _make_app(role="viewer")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/dashboard/sparklines")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"generated_at", "bucket_hours", "days", "tiles"}
    assert body["bucket_hours"] == 6
    assert body["days"] == 7
    assert set(body["tiles"]) == _TILE_IDS
    # Real tile carries an "up" trend and 28 points.
    reads = body["tiles"]["reads-per-hour"]
    assert reads["trend"] == "up"
    assert len(reads["series"]) == 28
    assert {"t", "v"} == set(reads["series"][0])
    # Flat tile shape.
    devices = body["tiles"]["devices"]
    assert devices["trend"] == "flat"
    assert all(p["v"] == 10 for p in devices["series"])
    # tenant_id from auth dep flows through; defaults applied for query params.
    assert captured["tenant_id"] == tenant_id
    assert captured["days"] == 7
    assert captured["bucket_hours"] == 6


@pytest.mark.asyncio
async def test_sparklines_forwards_query_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _stub(
        session: Any,
        tenant_id: uuid.UUID,
        days: int = 7,
        bucket_hours: int = 6,
    ) -> DashboardSparklines:
        captured["days"] = days
        captured["bucket_hours"] = bucket_hours
        return _fixed_sparklines()

    monkeypatch.setattr(dashboard_route.dashboard_service, "get_sparklines", _stub)

    app, _ = _make_app(role="admin")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/dashboard/sparklines?days=14&bucket_hours=12")

    assert resp.status_code == 200
    assert captured["days"] == 14
    assert captured["bucket_hours"] == 12


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "editor", "viewer"])
async def test_sparklines_open_to_all_logged_in_roles(
    monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    async def _stub(session: Any, tenant_id: uuid.UUID, **_: Any) -> DashboardSparklines:
        return _fixed_sparklines()

    monkeypatch.setattr(dashboard_route.dashboard_service, "get_sparklines", _stub)

    app, _ = _make_app(role=role)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/dashboard/sparklines")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_sparklines_rejects_unknown_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _stub(session: Any, tenant_id: uuid.UUID, **_: Any) -> DashboardSparklines:
        return _fixed_sparklines()

    monkeypatch.setattr(dashboard_route.dashboard_service, "get_sparklines", _stub)

    app, _ = _make_app(role="device")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/dashboard/sparklines")
    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "qs",
    ["days=0", "days=31", "bucket_hours=0", "bucket_hours=25"],
)
async def test_sparklines_query_param_validation(qs: str) -> None:
    app, _ = _make_app(role="viewer")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get(f"/dashboard/sparklines?{qs}")
    assert resp.status_code == 422


def test_classify_trend_thresholds() -> None:
    # Strict upward ramp — last quarter mean > first quarter mean by > 5%.
    assert _classify_trend([1, 2, 3, 4, 5, 6, 7, 8]) == "up"
    # Strict downward ramp.
    assert _classify_trend([8, 7, 6, 5, 4, 3, 2, 1]) == "down"
    # All zeros → flat (first-quarter mean is 0, tail is 0).
    assert _classify_trend([0, 0, 0, 0]) == "flat"
    # Within +/-5% — small noise on a baseline of 100.
    assert _classify_trend([100, 101, 99, 100, 102, 98, 101, 100]) == "flat"
    # Empty list — guard returns "flat".
    assert _classify_trend([]) == "flat"
    # Zero baseline, positive tail — special-cased to "up".
    assert _classify_trend([0, 0, 0, 5, 5, 5]) == "up"
