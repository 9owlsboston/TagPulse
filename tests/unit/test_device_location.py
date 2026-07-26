"""Sprint 80 — external_locations subject generalization + device-self endpoint (I-9HQA)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tagpulse.api.dependencies import get_external_location_repo, get_gateway_grant_repo
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
                "metadata": position.metadata,
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


class _FakeGrantRepo:
    """Returns a configurable active grant set; empty by default."""

    def __init__(self, grants: set[tuple[str, UUID]] | None = None) -> None:
        self._grants = grants or set()

    async def active_subject_set(self, _tenant: UUID, _gw: UUID) -> set[tuple[str, UUID]]:
        return self._grants


def _app(
    principal: AuthenticatedUser,
    repo: _FakeRepo,
    grants: set[tuple[str, UUID]] | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(device_location_route.router)
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_external_location_repo] = lambda: repo
    app.dependency_overrides[get_gateway_grant_repo] = lambda: _FakeGrantRepo(grants)
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


# --------------------------------------------------------------------------- #
# Gateway relay for granted subjects (C-4Z66)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_relay_granted_asset_sets_asset_id_and_provenance() -> None:
    did, asset_id = uuid4(), uuid4()
    repo = _FakeRepo()
    app = _app(_device(did), repo, grants={("asset", asset_id)})
    resp = await _post(app, _body(subject_kind="asset", subject_id=str(asset_id)))
    assert resp.status_code == 201
    (call,) = repo.calls
    assert call["subject_kind"] == "asset"
    assert call["subject_id"] == asset_id
    # Asset target MUST populate asset_id so asset location reads (which filter on
    # asset_id, not subject_id) can see the relayed row.
    assert call["asset_id"] == asset_id
    # Server-stamped, spoof-proof relay provenance.
    assert call["metadata"]["relayed_by_device_id"] == str(did)


@pytest.mark.asyncio
async def test_relay_granted_non_asset_leaves_asset_id_null() -> None:
    did, lot_id = uuid4(), uuid4()
    repo = _FakeRepo()
    app = _app(_device(did), repo, grants={("lot", lot_id)})
    resp = await _post(app, _body(subject_kind="lot", subject_id=str(lot_id)))
    assert resp.status_code == 201
    (call,) = repo.calls
    assert call["subject_kind"] == "lot"
    assert call["asset_id"] is None


@pytest.mark.asyncio
async def test_relay_ungranted_subject_403() -> None:
    did = uuid4()
    repo = _FakeRepo()
    # Grant is for a different asset than the one being stamped.
    app = _app(_device(did), repo, grants={("asset", uuid4())})
    resp = await _post(app, _body(subject_kind="asset", subject_id=str(uuid4())))
    assert resp.status_code == 403
    assert repo.calls == []


@pytest.mark.asyncio
async def test_relay_no_grants_403() -> None:
    did = uuid4()
    repo = _FakeRepo()
    app = _app(_device(did), repo)  # empty grant set
    resp = await _post(app, _body(subject_kind="zone", subject_id=str(uuid4())))
    assert resp.status_code == 403
    assert repo.calls == []


@pytest.mark.asyncio
async def test_explicit_self_target_needs_no_grant() -> None:
    did = uuid4()
    repo = _FakeRepo()
    # Naming the own device explicitly is the self case — allowed with no grant.
    app = _app(_device(did), repo)
    resp = await _post(app, _body(subject_kind="device", subject_id=str(did)))
    assert resp.status_code == 201
    (call,) = repo.calls
    assert call["subject_kind"] == "device"
    assert call["subject_id"] == did
    assert call["asset_id"] is None
    # Self writes are not tagged as relayed.
    assert call["metadata"] is None or "relayed_by_device_id" not in (call["metadata"] or {})


@pytest.mark.asyncio
async def test_relay_lone_subject_kind_422() -> None:
    repo = _FakeRepo()
    app = _app(_device(uuid4()), repo, grants={("asset", uuid4())})
    resp = await _post(app, _body(subject_kind="asset"))  # subject_id missing
    assert resp.status_code == 422
    assert repo.calls == []


@pytest.mark.asyncio
async def test_relay_out_of_window_still_400_before_grant_check() -> None:
    from datetime import timedelta

    did, asset_id = uuid4(), uuid4()
    repo = _FakeRepo()
    app = _app(_device(did), repo, grants={("asset", asset_id)})
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    resp = await _post(
        app, _body(subject_kind="asset", subject_id=str(asset_id), recorded_at=future)
    )
    assert resp.status_code == 400
    assert repo.calls == []
