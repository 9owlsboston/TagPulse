"""Sprint 64: ``/devices/{device_id}/antennas`` placement API.

Route → repo contract via the TestClient + stub-repo pattern (no DB). Covers
the port-0 model, RBAC (viewer read / admin write), validation, and the
device-not-found vs antenna-not-found 404 split.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tagpulse.api.dependencies import get_antenna_repo
from tagpulse.api.routes.antennas import router
from tagpulse.core.user_auth import AuthenticatedUser, get_current_user
from tagpulse.models.schemas import AntennaResponse, AntennaUpsert


class _StubAntennaRepo:
    """Captures calls and returns canned values per the configured outcome."""

    def __init__(self) -> None:
        self.list_result: list[AntennaResponse] | None = []
        self.upsert_result: AntennaResponse | None = None
        self.delete_result: bool | None = True
        self.calls: list[tuple[str, Any]] = []

    async def list_for_device(
        self, tenant_id: UUID, device_id: UUID
    ) -> list[AntennaResponse] | None:
        self.calls.append(("list", device_id))
        return self.list_result

    async def upsert(
        self, tenant_id: UUID, device_id: UUID, port: int, payload: AntennaUpsert
    ) -> AntennaResponse | None:
        self.calls.append(("upsert", (device_id, port, payload)))
        return self.upsert_result

    async def delete(self, tenant_id: UUID, device_id: UUID, port: int) -> bool | None:
        self.calls.append(("delete", (device_id, port)))
        return self.delete_result


def _antenna(device_id: UUID, port: int) -> AntennaResponse:
    return AntennaResponse(
        id=uuid4(),
        device_id=device_id,
        port=port,
        x=2.0,
        y=3.0,
        z=None,
        label="port-0",
        gain_dbi=None,
    )


def _make_client(stub: _StubAntennaRepo, role: Literal["admin", "editor", "viewer"]) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/v1")

    def _fake_user() -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=uuid4(),
            tenant_id=uuid4(),
            tenant_name="t",
            tenant_slug="t",
            role=role,
        )

    app.dependency_overrides[get_current_user] = _fake_user
    app.dependency_overrides[get_antenna_repo] = lambda: stub
    return TestClient(app)


@pytest.fixture
def stub() -> _StubAntennaRepo:
    return _StubAntennaRepo()


# -- GET (read: viewer+) --


def test_list_antennas_ok(stub: _StubAntennaRepo) -> None:
    did = uuid4()
    stub.list_result = [_antenna(did, 0), _antenna(did, 1)]
    client = _make_client(stub, "viewer")
    resp = client.get(f"/v1/devices/{did}/antennas")
    assert resp.status_code == 200
    body = resp.json()
    assert [a["port"] for a in body] == [0, 1]


def test_list_antennas_device_not_found(stub: _StubAntennaRepo) -> None:
    stub.list_result = None  # device not owned
    client = _make_client(stub, "viewer")
    resp = client.get(f"/v1/devices/{uuid4()}/antennas")
    assert resp.status_code == 404


# -- PUT (write: admin only) --


def test_upsert_antenna_ok(stub: _StubAntennaRepo) -> None:
    did = uuid4()
    stub.upsert_result = _antenna(did, 0)
    client = _make_client(stub, "admin")
    resp = client.put(f"/v1/devices/{did}/antennas/0", json={"x": 2.0, "y": 3.0, "label": "port-0"})
    assert resp.status_code == 200
    assert resp.json()["port"] == 0
    assert stub.calls[0][0] == "upsert"


def test_upsert_antenna_device_not_found(stub: _StubAntennaRepo) -> None:
    stub.upsert_result = None
    client = _make_client(stub, "admin")
    resp = client.put(f"/v1/devices/{uuid4()}/antennas/1", json={"x": 1.0})
    assert resp.status_code == 404


def test_upsert_rejects_viewer(stub: _StubAntennaRepo) -> None:
    client = _make_client(stub, "viewer")
    resp = client.put(f"/v1/devices/{uuid4()}/antennas/0", json={"x": 1.0})
    assert resp.status_code == 403
    assert stub.calls == []  # never reached the repo


def test_upsert_rejects_out_of_range_port(stub: _StubAntennaRepo) -> None:
    client = _make_client(stub, "admin")
    resp = client.put(f"/v1/devices/{uuid4()}/antennas/300", json={"x": 1.0})
    assert resp.status_code == 422


def test_upsert_rejects_unknown_field(stub: _StubAntennaRepo) -> None:
    client = _make_client(stub, "admin")
    resp = client.put(f"/v1/devices/{uuid4()}/antennas/0", json={"x": 1.0, "bogus": 9})
    assert resp.status_code == 422


# -- DELETE (write: admin only) --


def test_delete_antenna_ok(stub: _StubAntennaRepo) -> None:
    stub.delete_result = True
    client = _make_client(stub, "admin")
    resp = client.delete(f"/v1/devices/{uuid4()}/antennas/0")
    assert resp.status_code == 204


def test_delete_device_not_found(stub: _StubAntennaRepo) -> None:
    stub.delete_result = None  # device not owned
    client = _make_client(stub, "admin")
    resp = client.delete(f"/v1/devices/{uuid4()}/antennas/0")
    assert resp.status_code == 404


def test_delete_antenna_not_found(stub: _StubAntennaRepo) -> None:
    stub.delete_result = False  # port had no antenna
    client = _make_client(stub, "admin")
    resp = client.delete(f"/v1/devices/{uuid4()}/antennas/2")
    assert resp.status_code == 404


def test_delete_rejects_viewer(stub: _StubAntennaRepo) -> None:
    client = _make_client(stub, "viewer")
    resp = client.delete(f"/v1/devices/{uuid4()}/antennas/0")
    assert resp.status_code == 403
