"""Sprint 50 audit G-1 + G-4 — labels API governance regression tests.

Migration 045's docstring is binding: "the labels API rejects
user-initiated CREATE / UPDATE / DELETE for any key matching the
reserved namespace regardless of entity_type". These tests guard
that contract at the route layer and the related URL-mapping fix
(G-4) that wires ``/tags/{id}/labels`` through to ``entity_type='tag'``.

DB is fully mocked: ``TimescaleLabelRepository`` methods are
monkeypatched, ``get_session`` is overridden with a sentinel.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tagpulse.api.routes import labels as labels_route
from tagpulse.core.tenant_auth import Tenant, get_current_tenant
from tagpulse.core.user_auth import AuthenticatedUser, get_current_user
from tagpulse.models.schemas import LabelResponse
from tagpulse.repositories.timescaledb.labels import TimescaleLabelRepository
from tagpulse.repositories.timescaledb.session import get_session
from tagpulse.services.tags import RESERVED_LABEL_KEYS, is_reserved_label_key

# ---------------------------------------------------------------------------
# Pure helper
# ---------------------------------------------------------------------------


class TestIsReservedLabelKey:
    @pytest.mark.parametrize(
        "key",
        ["batch", "batch.received_at", "batch.description", "batch.supplier"],
    )
    def test_seeded_keys_are_reserved(self, key: str) -> None:
        assert is_reserved_label_key(key) is True
        assert key in RESERVED_LABEL_KEYS

    @pytest.mark.parametrize(
        "key",
        # Migration 045 seeds an exact-match set, not a wildcard. A
        # forward-compatible operator key like ``batch.foo`` is NOT
        # reserved — only the seeded four are. This intentional
        # tightness keeps the namespace small; widen via a new ADR
        # if a future sprint needs prefix matching.
        ["zone", "Batch", "batch.foo", "batches", "", "category"],
    )
    def test_non_reserved_keys_pass(self, key: str) -> None:
        assert is_reserved_label_key(key) is False


# ---------------------------------------------------------------------------
# Route-layer guards (G-1)
# ---------------------------------------------------------------------------


def _make_app(role: str = "admin") -> tuple[FastAPI, uuid.UUID, uuid.UUID]:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    app = FastAPI()
    app.include_router(labels_route.router)

    def _user() -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=user_id,
            tenant_id=tenant_id,
            tenant_name="T",
            tenant_slug="t",
            role=role,
        )

    def _tenant() -> Tenant:
        return Tenant(id=tenant_id, name="T", slug="t", plan="standard")

    async def _session():  # type: ignore[no-untyped-def]
        yield object()

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_current_tenant] = _tenant
    app.dependency_overrides[get_session] = _session
    return app, tenant_id, user_id


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _seeded_batch_row(tenant_id: uuid.UUID) -> LabelResponse:
    now = datetime(2026, 5, 23, tzinfo=UTC)
    return LabelResponse(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        entity_type="tag",
        key="batch",
        color="#cccccc",
        created_by=uuid.uuid4(),
        updated_by=uuid.uuid4(),
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entity_type",
    # Per migration 045 docstring: refusal is "regardless of
    # entity_type". An admin who tries to shadow ``batch`` under
    # ``asset`` (where no migration-seeded conflict exists) must
    # still be refused.
    ["tag", "asset"],
)
async def test_create_label_refuses_reserved_key(
    monkeypatch: pytest.MonkeyPatch, entity_type: str
) -> None:
    app, _, _ = _make_app(role="admin")

    async def _should_not_run(*a: Any, **kw: Any) -> Any:  # pragma: no cover
        raise AssertionError("repo.create must not be called for reserved key")

    monkeypatch.setattr(TimescaleLabelRepository, "create", _should_not_run)

    async with _client(app) as client:
        r = await client.post(
            "/labels",
            json={"key": "batch", "entity_type": entity_type, "color": "#abcdef"},
        )

    assert r.status_code == 403
    body = r.json()
    assert "reserved" in body["detail"]["message"].lower()
    assert "batch" in body["detail"]["reserved_keys"]


@pytest.mark.asyncio
async def test_create_label_allows_non_reserved_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, tenant_id, _ = _make_app(role="admin")
    now = datetime(2026, 5, 23, tzinfo=UTC)
    created = LabelResponse(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        entity_type="tag",
        key="zone",
        color="#abcdef",
        created_by=uuid.uuid4(),
        updated_by=uuid.uuid4(),
        created_at=now,
        updated_at=now,
    )

    async def _create(self: Any, *a: Any, **kw: Any) -> LabelResponse:
        return created

    monkeypatch.setattr(TimescaleLabelRepository, "create", _create)
    # AuditLogger writes are no-ops against the sentinel session in
    # this test, but the logger calls ``session.add``; stub it.
    monkeypatch.setattr(
        "tagpulse.api.routes.labels.AuditLogger",
        lambda session: type("L", (), {"log": _noop_log})(),
    )

    async with _client(app) as client:
        r = await client.post(
            "/labels",
            json={"key": "zone", "entity_type": "tag", "color": "#abcdef"},
        )

    assert r.status_code == 201, r.text
    assert r.json()["key"] == "zone"


@pytest.mark.asyncio
async def test_update_label_refuses_when_existing_key_is_reserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, tenant_id, _ = _make_app(role="admin")
    existing = _seeded_batch_row(tenant_id)

    async def _get(self: Any, *a: Any, **kw: Any) -> LabelResponse:
        return existing

    async def _update_should_not_run(*a: Any, **kw: Any) -> Any:  # pragma: no cover
        raise AssertionError("repo.update must not be called for reserved row")

    monkeypatch.setattr(TimescaleLabelRepository, "get", _get)
    monkeypatch.setattr(TimescaleLabelRepository, "update", _update_should_not_run)

    async with _client(app) as client:
        r = await client.patch(
            f"/labels/{existing.id}",
            json={"color": "#000000"},
        )

    assert r.status_code == 403
    assert "reserved" in r.json()["detail"]["message"].lower()


@pytest.mark.asyncio
async def test_delete_label_refuses_when_existing_key_is_reserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, tenant_id, _ = _make_app(role="admin")
    existing = _seeded_batch_row(tenant_id)

    async def _get(self: Any, *a: Any, **kw: Any) -> LabelResponse:
        return existing

    async def _delete_should_not_run(*a: Any, **kw: Any) -> Any:  # pragma: no cover
        raise AssertionError("repo.delete must not be called for reserved row")

    monkeypatch.setattr(TimescaleLabelRepository, "get", _get)
    monkeypatch.setattr(TimescaleLabelRepository, "delete", _delete_should_not_run)

    async with _client(app) as client:
        r = await client.delete(f"/labels/{existing.id}")

    assert r.status_code == 403
    assert "reserved" in r.json()["detail"]["message"].lower()


# ---------------------------------------------------------------------------
# URL mapping (G-4) — POST /tags/{id}/labels must resolve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tags_entity_segment_resolves_to_tag_entity_type() -> None:
    """Before remediation this returned 404 'Unknown entity kind'
    because ``tags`` was missing from the URL\u2192entity_type map.
    Now the request reaches the repo lookup (which 404s because
    the sentinel session has no ``find_by_key`` data) \u2014 status
    must be 404 with a *label-not-found* message, NOT the
    entity-kind message."""
    app, _, _ = _make_app(role="admin")
    tag_id = uuid.uuid4()

    async def _find_by_key(self: Any, *a: Any, **kw: Any) -> None:
        return None

    # Patch the repo so the route reaches the post-mapping
    # codepath without touching SQLAlchemy.
    import pytest as _pytest  # local alias avoids shadowing

    monkeypatch = _pytest.MonkeyPatch()
    monkeypatch.setattr(TimescaleLabelRepository, "find_by_key", _find_by_key)
    try:
        async with _client(app) as client:
            r = await client.post(
                f"/tags/{tag_id}/labels",
                json={"key": "batch", "value": "reel-008rT"},
            )
    finally:
        monkeypatch.undo()

    assert r.status_code == 404
    # The fix means we get past the URL-mapping guard. The error
    # message therefore comes from the *label lookup*, not from
    # ``Unknown entity kind``.
    detail = r.json()["detail"]
    assert "Unknown entity kind" not in detail
    assert "batch" in detail and "tag" in detail


# ---------------------------------------------------------------------------
# Local stubs
# ---------------------------------------------------------------------------


async def _noop_log(*a: Any, **kw: Any) -> None:
    return None
