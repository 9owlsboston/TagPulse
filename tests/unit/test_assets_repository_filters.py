"""Unit tests for ``TimescaleAssetRepository.list`` query construction.

Sprint 37 (row 3.3a / row 2.8 follow-up in
``docs/design/reference-design-remediation.md``): promotes the
Categories filter from client-side (UI-only) to server-side. The route
layer accepts ``?category_id=<uuid>``; this file asserts the repo's
SQL contains the ``assets.category_id = :category_id_1`` predicate when
the kwarg is set, and emits no such predicate when it is omitted.

Compile-only — no DB. The label-filter unit tests use the same dialect-
compile pattern (see ``tests/unit/test_label_filter.py``).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from tagpulse.repositories.timescaledb.assets import TimescaleAssetRepository


class _CapturingSession:
    """Async session that captures the executed statement instead of
    talking to PostgreSQL."""

    def __init__(self) -> None:
        self.captured: Any = None

    async def execute(self, stmt: Any) -> Any:
        self.captured = stmt

        class _Result:
            def scalars(self) -> list[Any]:
                return []

        return _Result()


def _compiled_sql(stmt: Any) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": False},
        )
    ).lower()


@pytest.mark.asyncio
async def test_list_without_category_id_emits_no_category_predicate() -> None:
    session = _CapturingSession()
    repo = TimescaleAssetRepository(session)  # type: ignore[arg-type]
    await repo.list(uuid.uuid4())
    sql = _compiled_sql(session.captured)
    # ``assets.category_id`` appears in the SELECT column list regardless
    # — we only care that the WHERE predicate is absent. The predicate is
    # the only place the ``=`` operator follows the column.
    assert "assets.category_id =" not in sql
    assert "where assets.tenant_id" in sql


@pytest.mark.asyncio
async def test_list_with_category_id_adds_where_predicate() -> None:
    session = _CapturingSession()
    repo = TimescaleAssetRepository(session)  # type: ignore[arg-type]
    cid = uuid.uuid4()
    await repo.list(uuid.uuid4(), category_id=cid)
    sql = _compiled_sql(session.captured)
    assert "assets.category_id =" in sql


@pytest.mark.asyncio
async def test_list_category_id_combines_with_other_filters() -> None:
    """The new predicate must AND with the existing ones, not replace
    them."""
    session = _CapturingSession()
    repo = TimescaleAssetRepository(session)  # type: ignore[arg-type]
    await repo.list(
        uuid.uuid4(),
        asset_type="pallet",
        status="active",
        category_id=uuid.uuid4(),
        q="forklift",
    )
    sql = _compiled_sql(session.captured)
    assert "assets.asset_type =" in sql
    assert "assets.status =" in sql
    assert "assets.category_id =" in sql
    # Substring search emits an ILIKE on name OR external_ref.
    assert "ilike" in sql


@pytest.mark.asyncio
async def test_list_category_id_literal_binds_the_uuid() -> None:
    """Compile with literal_binds=True and verify the UUID lands in the
    SQL string (defense against accidental string coercion / typos)."""
    session = _CapturingSession()
    repo = TimescaleAssetRepository(session)  # type: ignore[arg-type]
    cid = uuid.uuid4()
    await repo.list(uuid.uuid4(), category_id=cid)
    compiled = session.captured.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    sql = str(compiled).lower()
    assert str(cid) in sql
