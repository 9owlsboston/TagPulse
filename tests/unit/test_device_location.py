"""Sprint 80 — external_locations subject generalization + device-self endpoint (I-9HQA)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tagpulse.api.dependencies import get_external_location_repo
from tagpulse.api.routes import device_location as device_location_route
from tagpulse.core.user_auth import AuthenticatedUser, get_current_user
from tagpulse.models.schemas import ExternalLocationCreate, ExternalLocationResponse
from tagpulse.repositories.timescaledb.external_locations import (
    TimescaleExternalLocationRepository,
)

# --------------------------------------------------------------------------- #
# Repository generalization
# --------------------------------------------------------------------------- #


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


def _position() -> ExternalLocationCreate:
    return ExternalLocationCreate(
        latitude=51.5,
        longitude=-0.12,
        recorded_at=datetime.now(UTC),
        source="gps",
        accuracy_meters=8.0,
    )


@pytest.mark.asyncio
async def test_insert_for_subject_device_has_no_asset_id() -> None:
    repo = TimescaleExternalLocationRepository(_FakeSession())  # type: ignore[arg-type]
    did = uuid4()
    resp = await repo.insert_for_subject(uuid4(), "device", did, _position())
    assert resp.subject_kind == "device"
    assert resp.subject_id == did
    assert resp.asset_id is None


@pytest.mark.asyncio
async def test_insert_asset_shim_sets_subject_and_asset_id() -> None:
    repo = TimescaleExternalLocationRepository(_FakeSession())  # type: ignore[arg-type]
    aid = uuid4()
    resp = await repo.insert(uuid4(), aid, _position())
    # Backward-compat asset path: both asset_id and subject_* populated.
    assert resp.asset_id == aid
    assert resp.subject_kind == "asset"
    assert resp.subject_id == aid


# --------------------------------------------------------------------------- #
# Device-self endpoint
# --------------------------------------------------------------------------- #


class _FakeRepo:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def insert_for_subject(
        self,
        tenant_id: UUID,
        subject_kind: str,
        subject_id: UUID,
        position: ExternalLocationCreate,
        *,
        asset_id: UUID | None = None,
    ) -> ExternalLocationResponse:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "subject_kind": subject_kind,
                "subject_id": subject_id,
                "asset_id": asset_id,
            }
        )
        return ExternalLocationResponse(
            id=uuid4(),
            tenant_id=tenant_id,
            asset_id=asset_id,
            subject_kind=subject_kind,
            subject_id=subject_id,
            recorded_at=position.recorded_at,
            latitude=position.latitude,
            longitude=position.longitude,
            source=position.source,
            accuracy_meters=position.accuracy_meters,
            speed_kph=position.speed_kph,
            heading_deg=position.heading_deg,
            metadata=position.metadata,
        )


def _app(principal: AuthenticatedUser, repo: _FakeRepo) -> FastAPI:
    app = FastAPI()
    app.include_router(device_location_route.router)
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_external_location_repo] = lambda: repo
    return app


def _device(device_id: UUID | None) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=None,
        tenant_id=uuid4(),
        tenant_name="Acme",
        tenant_slug="acme",
        role="device",
        device_id=device_id,
    )


def _body(**over: Any) -> dict[str, Any]:
    b = {
        "latitude": 51.5,
        "longitude": -0.12,
        "recorded_at": datetime.now(UTC).isoformat(),
        "source": "gps",
    }
    b.update(over)
    return b


async def _post(app: FastAPI, body: dict[str, Any]) -> Any:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.post("/device-location", json=body)


@pytest.mark.asyncio
async def test_device_records_own_location() -> None:
    did = uuid4()
    repo = _FakeRepo()
    resp = await _post(_app(_device(did), repo), _body())
    assert resp.status_code == 201
    (call,) = repo.calls
    assert call["subject_kind"] == "device"
    assert call["subject_id"] == did
    assert call["asset_id"] is None
    assert resp.json()["subject_id"] == str(did)


@pytest.mark.asyncio
async def test_device_out_of_window_400() -> None:
    repo = _FakeRepo()
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    resp = await _post(_app(_device(uuid4()), repo), _body(recorded_at=future))
    assert resp.status_code == 400
    assert repo.calls == []


@pytest.mark.asyncio
async def test_device_missing_device_id_403() -> None:
    repo = _FakeRepo()
    resp = await _post(_app(_device(None), repo), _body())
    assert resp.status_code == 403
    assert repo.calls == []


@pytest.mark.asyncio
async def test_non_device_role_403() -> None:
    repo = _FakeRepo()
    human = AuthenticatedUser(
        user_id=uuid4(),
        tenant_id=uuid4(),
        tenant_name="Acme",
        tenant_slug="acme",
        role="admin",
    )
    resp = await _post(_app(human, repo), _body())
    assert resp.status_code == 403
    assert repo.calls == []
