"""Unit tests for the Phase E tag-registry reconciliation surface.

Two layers covered without a live Postgres:

- Pure CSV serialization (``rows_to_csv``): header order, empty
  result emits header only, datetime/None cell rendering.
- Route handler: dispatch on ``{view}``, ``?format=csv`` toggles
  Content-Type, role gating, query validation. The three service
  functions are monkeypatched so the test never opens a DB
  connection.

DB-touching paths for the SQL queries themselves are covered by
the integration suite (Phase G).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tagpulse.api.routes import tags as tags_routes
from tagpulse.core.user_auth import AuthenticatedUser, get_current_user
from tagpulse.models.schemas import (
    BindingOnRetiredRow,
    RegisteredUnreadRow,
    UnregisteredReadingRow,
)
from tagpulse.repositories.timescaledb.session import get_session
from tagpulse.services import tag_reconciliation

# ---------------------------------------------------------------------------
# rows_to_csv pure helpers
# ---------------------------------------------------------------------------


class TestRowsToCsv:
    def test_registered_unread_header_and_row(self) -> None:
        row = RegisteredUnreadRow(
            tag_id=UUID("00000000-0000-0000-0000-000000000001"),
            epc_hex="ABCDEF0123456789",
            status="registered",
            source="csv_import",
            first_seen_at=None,
            last_seen_at=None,
            created_at=datetime(2026, 5, 23, 12, 0, tzinfo=UTC),
        )
        out = tag_reconciliation.rows_to_csv("registered-unread", [row])
        lines = out.strip().split("\n")
        assert lines[0] == "tag_id,epc_hex,status,source,first_seen_at,last_seen_at,created_at"
        assert lines[1].startswith("00000000-0000-0000-0000-000000000001,ABCDEF0123456789,")
        # NULLs render as empty cells, not the string "None".
        assert ",,," in lines[1]
        assert "None" not in lines[1]

    def test_unregistered_reading_header(self) -> None:
        out = tag_reconciliation.rows_to_csv("unregistered-reading", [])
        # Header-only on empty result so spreadsheet consumers get a schema.
        assert out.strip() == "tag_id,last_seen_at,read_count"

    def test_bindings_on_retired_header_and_row(self) -> None:
        row = BindingOnRetiredRow(
            stock_item_id=uuid4(),
            epc_hex="ABCDEF0123456789",
            product_id=uuid4(),
            lot_id=None,
            stock_item_state="in_stock",
            tag_id=uuid4(),
            tag_status="retired",
            tag_updated_at=datetime(2026, 5, 23, 12, 0, tzinfo=UTC),
        )
        out = tag_reconciliation.rows_to_csv("bindings-on-retired", [row])
        header = out.split("\n", 1)[0]
        assert header == (
            "stock_item_id,epc_hex,product_id,lot_id,"
            "stock_item_state,tag_id,tag_status,tag_updated_at"
        )

    def test_datetime_rendered_iso8601(self) -> None:
        row = UnregisteredReadingRow(
            tag_id="ABCDEF",
            last_seen_at=datetime(2026, 5, 23, 12, 30, 45, tzinfo=UTC),
            read_count=7,
        )
        out = tag_reconciliation.rows_to_csv("unregistered-reading", [row])
        assert "2026-05-23T12:30:45+00:00" in out
        assert ",7" in out

    def test_empty_input_still_emits_header(self) -> None:
        # All three views — guard against future header omission.
        for view in (
            "registered-unread",
            "unregistered-reading",
            "bindings-on-retired",
        ):
            out = tag_reconciliation.rows_to_csv(view, [])  # type: ignore[arg-type]
            assert out.count("\n") == 1
            assert out.endswith("\n")


# ---------------------------------------------------------------------------
# Route — dispatch + format negotiation + role gating
# ---------------------------------------------------------------------------


def _make_app(role: str = "viewer") -> tuple[FastAPI, UUID]:
    tenant_id = uuid4()
    user_id = uuid4()
    app = FastAPI()
    app.include_router(tags_routes.router)

    def _user() -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=user_id,
            tenant_id=tenant_id,
            tenant_name="T",
            tenant_slug="t",
            role=role,
        )

    async def _session():  # type: ignore[no-untyped-def]
        # Sentinel — the route hands this to the service functions
        # which we monkeypatch, so it never reaches SQLAlchemy.
        yield object()

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_session] = _session
    return app, tenant_id


@pytest.mark.asyncio
async def test_registered_unread_json_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _stub(
        session: Any,
        tenant_id: uuid.UUID,
        *,
        days: int,
        limit: int,
        offset: int,
        q: str | None = None,
    ) -> list[RegisteredUnreadRow]:
        captured["tenant_id"] = tenant_id
        captured["days"] = days
        captured["limit"] = limit
        captured["offset"] = offset
        captured["q"] = q
        return [
            RegisteredUnreadRow(
                tag_id=UUID("00000000-0000-0000-0000-000000000001"),
                epc_hex="ABCDEF0123456789",
                status="registered",
                source="csv_import",
                first_seen_at=None,
                last_seen_at=None,
                created_at=datetime(2026, 5, 23, tzinfo=UTC),
            )
        ]

    monkeypatch.setattr(tag_reconciliation, "query_registered_unread", _stub)
    app, tenant_id = _make_app(role="viewer")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/tags/reconciliation/registered-unread?days=14&limit=50&q=ABC*")

    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["epc_hex"] == "ABCDEF0123456789"
    assert captured["tenant_id"] == tenant_id
    assert captured["days"] == 14
    assert captured["limit"] == 50
    assert captured["offset"] == 0
    assert captured["q"] == "ABC*"


@pytest.mark.asyncio
async def test_unregistered_reading_csv_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _stub(*args: Any, **kwargs: Any) -> list[UnregisteredReadingRow]:
        return [
            UnregisteredReadingRow(
                tag_id="DEADBEEF",
                last_seen_at=datetime(2026, 5, 23, 12, 0, tzinfo=UTC),
                read_count=42,
            )
        ]

    monkeypatch.setattr(tag_reconciliation, "query_unregistered_reading", _stub)
    app, _ = _make_app()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/tags/reconciliation/unregistered-reading?format=csv")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert "tags-unregistered-reading.csv" in r.headers["content-disposition"]
    body = r.text
    assert body.startswith("tag_id,last_seen_at,read_count")
    assert "DEADBEEF" in body
    assert ",42" in body


@pytest.mark.asyncio
async def test_bindings_on_retired_ignores_days_param(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _stub(
        session: Any,
        tenant_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        q: str | None = None,
    ) -> list[BindingOnRetiredRow]:
        # Critically — no ``days`` kwarg. The route must not forward it.
        captured["limit"] = limit
        captured["offset"] = offset
        return []

    monkeypatch.setattr(tag_reconciliation, "query_bindings_on_retired", _stub)
    app, _ = _make_app()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # ?days=999 is accepted (bounded validation passes) but ignored.
        r = await client.get("/tags/reconciliation/bindings-on-retired?days=200&limit=10")

    assert r.status_code == 200
    assert captured == {"limit": 10, "offset": 0}


@pytest.mark.asyncio
async def test_unknown_view_returns_422() -> None:
    app, _ = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/tags/reconciliation/nonsense-view")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_days_out_of_range_returns_422() -> None:
    app, _ = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/tags/reconciliation/registered-unread?days=0")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_invalid_format_returns_422() -> None:
    app, _ = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/tags/reconciliation/registered-unread?format=xml")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_viewer_role_permitted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _stub(*args: Any, **kwargs: Any) -> list[RegisteredUnreadRow]:
        return []

    monkeypatch.setattr(tag_reconciliation, "query_registered_unread", _stub)
    app, _ = _make_app(role="viewer")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/tags/reconciliation/registered-unread")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Sprint 53 Phase D — tagpulse_tag_reconciliation_rows_returned_total
# ---------------------------------------------------------------------------


class _RecordingCounter:
    """Drop-in stand-in for the OTel Counter used by the route handler."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, dict[str, Any]]] = []

    def add(self, amount: int, attributes: dict[str, Any] | None = None) -> None:
        self.calls.append((amount, dict(attributes or {})))


@pytest.mark.asyncio
async def test_reconciliation_counter_bumped_by_row_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each successful query bumps the counter by ``len(rows)`` with
    the view label, so operators can chart per-view throughput."""

    async def _stub(*args: Any, **kwargs: Any) -> list[RegisteredUnreadRow]:
        return [
            RegisteredUnreadRow(
                tag_id=uuid4(),
                epc_hex=f"ABCDEF012345678{i}",
                status="registered",
                source="csv_import",
                first_seen_at=None,
                last_seen_at=None,
                created_at=datetime(2026, 5, 23, tzinfo=UTC),
            )
            for i in range(3)
        ]

    recorder = _RecordingCounter()
    monkeypatch.setattr(tags_routes, "tag_reconciliation_rows_returned_counter", recorder)
    monkeypatch.setattr(tag_reconciliation, "query_registered_unread", _stub)

    app, _ = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/tags/reconciliation/registered-unread")

    assert r.status_code == 200
    assert recorder.calls == [(3, {"view": "registered-unread"})]


@pytest.mark.asyncio
async def test_reconciliation_counter_failure_does_not_break_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Instrumentation must never surface as a 500 — the route swallows
    OTel exceptions per Sprint 46 Phase E convention."""

    async def _stub(*args: Any, **kwargs: Any) -> list[UnregisteredReadingRow]:
        return [
            UnregisteredReadingRow(
                tag_id="DEADBEEF",
                last_seen_at=datetime(2026, 5, 23, tzinfo=UTC),
                read_count=1,
            )
        ]

    class _ExplodingCounter:
        def add(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("otel sdk down")

    monkeypatch.setattr(
        tags_routes, "tag_reconciliation_rows_returned_counter", _ExplodingCounter()
    )
    monkeypatch.setattr(tag_reconciliation, "query_unregistered_reading", _stub)

    app, _ = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/tags/reconciliation/unregistered-reading")

    assert r.status_code == 200
    assert r.json()[0]["tag_id"] == "DEADBEEF"
