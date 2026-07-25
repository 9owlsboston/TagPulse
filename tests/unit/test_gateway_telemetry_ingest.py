"""Sprint 79 — gateway/device telemetry ingest (I-75YC).

`POST /telemetry/readings/ingest` now accepts device principals, restricted to
their own device subject + clock-validated, with source/device_id coerced.
Admin/editor behavior is unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tagpulse.api.dependencies import (
    get_event_bus,
    get_gateway_grant_repo,
    get_telemetry_readings_repo,
)
from tagpulse.api.routes import telemetry as telemetry_route
from tagpulse.core.user_auth import AuthenticatedUser, get_current_user
from tagpulse.models.schemas import TelemetryReadingResponse


class _FakeGrantRepo:
    """Returns a configurable active grant set; empty by default."""

    def __init__(self, grants: set[tuple[str, UUID]] | None = None) -> None:
        self._grants = grants or set()

    async def active_subject_set(self, _tenant: UUID, _gw: UUID) -> set[tuple[str, UUID]]:
        return self._grants


class _FakeRepo:
    def __init__(self) -> None:
        self.inserts: list[dict[str, Any]] = []

    async def insert(self, **kwargs: Any) -> TelemetryReadingResponse:
        self.inserts.append(kwargs)
        return TelemetryReadingResponse(
            id=uuid4(),
            subject_kind=kwargs["subject_kind"],
            subject_id=kwargs["subject_id"],
            device_id=kwargs.get("device_id"),
            timestamp=kwargs["timestamp"],
            metric_name=kwargs["metric_name"],
            metric_value=kwargs["metric_value"],
            unit=kwargs.get("unit"),
            source=kwargs["source"],
            metadata=kwargs.get("metadata"),
        )


class _FakeBus:
    async def publish(self, *_a: Any, **_k: Any) -> None:
        return None


def _app(
    principal: AuthenticatedUser,
    repo: _FakeRepo,
    grants: set[tuple[str, UUID]] | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(telemetry_route.router)
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_telemetry_readings_repo] = lambda: repo
    app.dependency_overrides[get_gateway_grant_repo] = lambda: _FakeGrantRepo(grants)
    app.dependency_overrides[get_event_bus] = lambda: _FakeBus()
    return app


def _device(device_id: UUID, tenant_id: UUID) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=None,
        tenant_id=tenant_id,
        tenant_name="Acme",
        tenant_slug="acme",
        role="device",
        device_id=device_id,
    )


def _human(role: str, tenant_id: UUID) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid4(),
        tenant_id=tenant_id,
        tenant_name="Acme",
        tenant_slug="acme",
        role=role,
    )


def _reading(subject_kind: str, subject_id: UUID, **over: Any) -> dict[str, Any]:
    r = {
        "subject_kind": subject_kind,
        "subject_id": str(subject_id),
        "timestamp": datetime.now(UTC).isoformat(),
        "metric_name": "battery_pct",
        "metric_value": 87.0,
    }
    r.update(over)
    return r


async def _post(app: FastAPI, readings: list[dict[str, Any]]) -> Any:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.post("/telemetry/readings/ingest", json={"readings": readings})


@pytest.mark.asyncio
async def test_device_own_subject_ok_and_coerces_provenance() -> None:
    did, tid = uuid4(), uuid4()
    repo = _FakeRepo()
    app = _app(_device(did, tid), repo)
    # Device claims source="tag" and a foreign device_id — both must be coerced.
    resp = await _post(
        app,
        [_reading("device", did, source="tag", device_id=str(uuid4()))],
    )
    assert resp.status_code == 201
    (call,) = repo.inserts
    assert call["tenant_id"] == tid
    assert call["source"] == "external"
    assert call["device_id"] == did
    body = resp.json()
    assert body[0]["source"] == "external"
    assert body[0]["device_id"] == str(did)


@pytest.mark.asyncio
async def test_device_foreign_device_subject_403() -> None:
    did, tid = uuid4(), uuid4()
    repo = _FakeRepo()
    app = _app(_device(did, tid), repo)
    resp = await _post(app, [_reading("device", uuid4())])  # someone else's device
    assert resp.status_code == 403
    assert repo.inserts == []


@pytest.mark.asyncio
async def test_device_non_device_subject_403() -> None:
    did, tid = uuid4(), uuid4()
    repo = _FakeRepo()
    app = _app(_device(did, tid), repo)
    resp = await _post(app, [_reading("asset", uuid4())])
    assert resp.status_code == 403
    assert repo.inserts == []


@pytest.mark.asyncio
async def test_device_granted_asset_subject_ok() -> None:
    did, tid = uuid4(), uuid4()
    asset_id = uuid4()
    repo = _FakeRepo()
    # Grant the gateway relay authority for (asset, asset_id).
    app = _app(_device(did, tid), repo, grants={("asset", asset_id)})
    resp = await _post(app, [_reading("asset", asset_id, source="tag")])
    assert resp.status_code == 201
    (call,) = repo.inserts
    assert call["subject_kind"] == "asset"
    assert call["subject_id"] == asset_id
    # Relay provenance still coerced for the granted subject.
    assert call["source"] == "external"
    assert call["device_id"] == did


@pytest.mark.asyncio
async def test_device_ungranted_asset_subject_403() -> None:
    did, tid = uuid4(), uuid4()
    repo = _FakeRepo()
    # Grant is for a DIFFERENT asset than the one posted.
    app = _app(_device(did, tid), repo, grants={("asset", uuid4())})
    resp = await _post(app, [_reading("asset", uuid4())])
    assert resp.status_code == 403
    assert repo.inserts == []


@pytest.mark.asyncio
async def test_device_out_of_window_batch_rejected_400_before_any_insert() -> None:
    did, tid = uuid4(), uuid4()
    repo = _FakeRepo()
    app = _app(_device(did, tid), repo)
    good = _reading("device", did)
    future = _reading("device", did, timestamp=(datetime.now(UTC) + timedelta(hours=1)).isoformat())
    resp = await _post(app, [good, future])
    assert resp.status_code == 400
    # Whole batch rejected before any row is written.
    assert repo.inserts == []


@pytest.mark.asyncio
async def test_admin_unaffected_arbitrary_subject_and_source() -> None:
    tid = uuid4()
    repo = _FakeRepo()
    app = _app(_human("admin", tid), repo)
    dev_id = uuid4()
    resp = await _post(
        app,
        [_reading("asset", uuid4(), source="derived", device_id=str(dev_id))],
    )
    assert resp.status_code == 201
    (call,) = repo.inserts
    # Human path: no coercion, arbitrary subject allowed.
    assert call["source"] == "derived"
    assert call["device_id"] == dev_id
    assert call["subject_kind"] == "asset"


@pytest.mark.asyncio
async def test_viewer_still_forbidden_403() -> None:
    tid = uuid4()
    repo = _FakeRepo()
    app = _app(_human("viewer", tid), repo)
    resp = await _post(app, [_reading("asset", uuid4())])
    assert resp.status_code == 403
