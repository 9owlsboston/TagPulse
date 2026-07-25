"""Sprint 78 — device-token HTTP auth + ingest guards (I-K6D1).

Covers:
- ``_authenticate_device_token`` accept/reject matrix (valid active, bad hash,
  pending, decommissioned, no candidate, inactive tenant).
- ``get_current_tenant`` rejects device principals (least-privilege).
- ``enforce_device_ingest`` binding + backfill rules (pure logic).
- Ingest route short-circuits a device posting a foreign device_id / backfill.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from tagpulse.api.dependencies import get_ingestion_service
from tagpulse.api.routes import ingestion as ingestion_route
from tagpulse.core.tenant_auth import (
    IngestAuth,
    Tenant,
    enforce_device_ingest,
    get_current_tenant,
    get_ingest_auth,
)
from tagpulse.core.user_auth import (
    AuthenticatedUser,
    _authenticate_device_token,
    generate_device_token,
)


class _FakeResult:
    def __init__(self, *, scalar: object = None, rows: list | None = None) -> None:
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one_or_none(self) -> object:
        return self._scalar

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list:
        return self._rows


class _FakeSession:
    def __init__(self, *, result: _FakeResult, get_return: object = None) -> None:
        self._result = result
        self._get_return = get_return

    async def execute(self, _stmt: object) -> _FakeResult:
        return self._result

    async def get(self, _model: object, _pk: object) -> object:
        return self._get_return


def _device(raw_token: str, *, status: str = "active", tenant_id=None) -> SimpleNamespace:
    _, prefix, token_hash = _split(raw_token)
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id or uuid4(),
        token_prefix=prefix,
        token_hash=token_hash,
        status=status,
    )


def _split(raw_token: str) -> tuple[str, str, str]:
    import hashlib

    return raw_token, raw_token[:10], hashlib.sha256(raw_token.encode()).hexdigest()


def _tenant(*, status: str = "active") -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), name="Acme", slug="acme", status=status)


# --------------------------------------------------------------------------- #
# _authenticate_device_token
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_device_token_active_ok() -> None:
    raw, _, _ = generate_device_token("acme")
    tid = uuid4()
    device = _device(raw, status="active", tenant_id=tid)
    session = _FakeSession(result=_FakeResult(rows=[device]), get_return=_tenant())

    principal = await _authenticate_device_token(raw, session)  # type: ignore[arg-type]

    assert principal.role == "device"
    assert principal.user_id is None
    assert principal.device_id == device.id


@pytest.mark.asyncio
async def test_device_token_bad_hash_401() -> None:
    raw, _, _ = generate_device_token("acme")
    device = _device(raw, status="active")
    session = _FakeSession(result=_FakeResult(rows=[device]), get_return=_tenant())

    with pytest.raises(HTTPException) as exc:
        await _authenticate_device_token(raw + "tamper", session)  # type: ignore[arg-type]
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_device_token_no_candidate_401() -> None:
    raw, _, _ = generate_device_token("acme")
    session = _FakeSession(result=_FakeResult(rows=[]), get_return=_tenant())

    with pytest.raises(HTTPException) as exc:
        await _authenticate_device_token(raw, session)  # type: ignore[arg-type]
    assert exc.value.status_code == 401


@pytest.mark.parametrize("status", ["pending", "rejected", "decommissioned"])
@pytest.mark.asyncio
async def test_device_token_not_active_403(status: str) -> None:
    raw, _, _ = generate_device_token("acme")
    device = _device(raw, status=status)
    session = _FakeSession(result=_FakeResult(rows=[device]), get_return=_tenant())

    with pytest.raises(HTTPException) as exc:
        await _authenticate_device_token(raw, session)  # type: ignore[arg-type]
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_device_token_inactive_tenant_401() -> None:
    raw, _, _ = generate_device_token("acme")
    device = _device(raw, status="active")
    session = _FakeSession(
        result=_FakeResult(rows=[device]), get_return=_tenant(status="suspended")
    )

    with pytest.raises(HTTPException) as exc:
        await _authenticate_device_token(raw, session)  # type: ignore[arg-type]
    assert exc.value.status_code == 401


# --------------------------------------------------------------------------- #
# get_current_tenant rejects devices
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_get_current_tenant_rejects_device() -> None:
    device_user = AuthenticatedUser(
        user_id=None,
        tenant_id=uuid4(),
        tenant_name="Acme",
        tenant_slug="acme",
        role="device",
        device_id=uuid4(),
    )
    session = _FakeSession(result=_FakeResult(scalar=_tenant()))

    with pytest.raises(HTTPException) as exc:
        await get_current_tenant(device_user, session)  # type: ignore[arg-type]
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_console_user_rejects_device() -> None:
    from tagpulse.core.user_auth import get_console_user

    device_user = AuthenticatedUser(
        user_id=None,
        tenant_id=uuid4(),
        tenant_name="Acme",
        tenant_slug="acme",
        role="device",
        device_id=uuid4(),
    )
    with pytest.raises(HTTPException) as exc:
        await get_console_user(device_user)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_console_user_allows_human() -> None:
    from tagpulse.core.user_auth import get_console_user

    human = AuthenticatedUser(
        user_id=uuid4(),
        tenant_id=uuid4(),
        tenant_name="Acme",
        tenant_slug="acme",
        role="viewer",
    )
    assert await get_console_user(human) is human


# --------------------------------------------------------------------------- #
# enforce_device_ingest (pure)
# --------------------------------------------------------------------------- #


def _principal(role: str, device_id=None) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=None if role == "device" else uuid4(),
        tenant_id=uuid4(),
        tenant_name="Acme",
        tenant_slug="acme",
        role=role,
        device_id=device_id,
    )


def test_enforce_human_is_noop() -> None:
    human = _principal("editor")
    # Foreign device_id + backfill must NOT raise for a human principal.
    enforce_device_ingest(human, [uuid4(), uuid4()], backfill=True)


def test_enforce_device_own_id_ok() -> None:
    did = uuid4()
    dev = _principal("device", device_id=did)
    enforce_device_ingest(dev, [did, did], backfill=False)


def test_enforce_device_backfill_403() -> None:
    did = uuid4()
    dev = _principal("device", device_id=did)
    with pytest.raises(HTTPException) as exc:
        enforce_device_ingest(dev, [did], backfill=True)
    assert exc.value.status_code == 403


def test_enforce_device_foreign_id_403() -> None:
    dev = _principal("device", device_id=uuid4())
    with pytest.raises(HTTPException) as exc:
        enforce_device_ingest(dev, [uuid4()], backfill=False)
    assert exc.value.status_code == 403


def test_enforce_device_foreign_id_in_batch_403() -> None:
    did = uuid4()
    dev = _principal("device", device_id=did)
    with pytest.raises(HTTPException) as exc:
        enforce_device_ingest(dev, [did, uuid4()], backfill=False)
    assert exc.value.status_code == 403


# --------------------------------------------------------------------------- #
# Ingest route short-circuits device violations before the service runs
# --------------------------------------------------------------------------- #


def _ingest_app(principal: AuthenticatedUser) -> FastAPI:
    app = FastAPI()
    app.include_router(ingestion_route.router)

    def _auth() -> IngestAuth:
        return IngestAuth(
            tenant=Tenant(id=principal.tenant_id, name="Acme", slug="acme", plan="pro"),
            principal=principal,
        )

    class _ExplodingService:
        async def ingest(self, *_a: object, **_k: object) -> object:
            raise AssertionError("service must not be reached on a 403")

        async def ingest_batch(self, *_a: object, **_k: object) -> object:
            raise AssertionError("service must not be reached on a 403")

    app.dependency_overrides[get_ingest_auth] = _auth
    app.dependency_overrides[get_ingestion_service] = lambda: _ExplodingService()
    return app


@pytest.mark.asyncio
async def test_ingest_route_device_foreign_id_403() -> None:
    dev = _principal("device", device_id=uuid4())
    app = _ingest_app(dev)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.post(
            "/tag-reads",
            json={
                "device_id": str(uuid4()),  # not the authenticated device
                "tag_id": "E280",
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_ingest_route_device_backfill_403() -> None:
    did = uuid4()
    dev = _principal("device", device_id=did)
    app = _ingest_app(dev)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.post(
            "/tag-reads?backfill=true",
            json={
                "device_id": str(did),
                "tag_id": "E280",
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
    assert resp.status_code == 403
