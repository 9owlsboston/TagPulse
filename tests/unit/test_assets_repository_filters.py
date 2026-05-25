"""Unit tests for ``TimescaleAssetRepository.list`` query construction.

Sprint 37 (row 3.3a / row 2.8 follow-up in
``docs/design/reference-design-remediation.md``) promoted the Categories
filter from client-side (UI-only) to server-side. The route layer
accepted ``?category_id=<uuid>`` and the repo emitted
``assets.category_id = :category_id_1``.

Sprint 42 generalises that to a multi-category filter: the route layer
accepts ``?category_ids=A&category_ids=B`` (and keeps legacy
``?category_id=`` for one release), the service layer collapses both
into a single deduplicated list, and the repo emits
``assets.category_id IN (...)`` regardless of cardinality (single
category becomes ``IN (X)``).

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
async def test_list_without_category_ids_emits_no_category_predicate() -> None:
    session = _CapturingSession()
    repo = TimescaleAssetRepository(session)  # type: ignore[arg-type]
    await repo.list(uuid.uuid4())
    sql = _compiled_sql(session.captured)
    # ``assets.category_id`` appears in the SELECT column list regardless
    # — we only care that the WHERE predicate is absent.
    assert "assets.category_id in" not in sql
    assert "assets.category_id =" not in sql
    assert "where assets.tenant_id" in sql


@pytest.mark.asyncio
async def test_list_with_single_category_id_emits_in_predicate() -> None:
    """Single-element list still compiles to ``IN (...)`` — service layer
    collapses legacy ``?category_id=X`` to ``[X]``."""
    session = _CapturingSession()
    repo = TimescaleAssetRepository(session)  # type: ignore[arg-type]
    cid = uuid.uuid4()
    await repo.list(uuid.uuid4(), category_ids=[cid])
    sql = _compiled_sql(session.captured)
    assert "assets.category_id in" in sql


@pytest.mark.asyncio
async def test_list_with_multiple_category_ids_emits_in_predicate() -> None:
    """Sprint 42: ``?category_ids=A&category_ids=B`` ⇒ ``IN (A, B)``."""
    session = _CapturingSession()
    repo = TimescaleAssetRepository(session)  # type: ignore[arg-type]
    cids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    await repo.list(uuid.uuid4(), category_ids=cids)
    sql = _compiled_sql(session.captured)
    assert "assets.category_id in" in sql


@pytest.mark.asyncio
async def test_list_with_empty_category_ids_emits_no_predicate() -> None:
    """An explicit empty list is treated as 'no filter' — service layer
    converts the empty-args case to ``None`` so the route still works
    when neither query param is supplied."""
    session = _CapturingSession()
    repo = TimescaleAssetRepository(session)  # type: ignore[arg-type]
    await repo.list(uuid.uuid4(), category_ids=[])
    sql = _compiled_sql(session.captured)
    assert "assets.category_id in" not in sql


@pytest.mark.asyncio
async def test_list_category_ids_combines_with_other_filters() -> None:
    """The new predicate must AND with the existing ones, not replace
    them."""
    session = _CapturingSession()
    repo = TimescaleAssetRepository(session)  # type: ignore[arg-type]
    await repo.list(
        uuid.uuid4(),
        status="active",
        category_ids=[uuid.uuid4(), uuid.uuid4()],
        q="forklift",
    )
    sql = _compiled_sql(session.captured)
    assert "assets.status =" in sql
    assert "assets.category_id in" in sql
    # Substring search emits an ILIKE on name OR external_ref.
    assert "ilike" in sql


@pytest.mark.asyncio
async def test_list_category_ids_literal_binds_each_uuid() -> None:
    """Compile with literal_binds=True and verify every UUID lands in the
    SQL string (defense against accidental string coercion / typos and
    against the IN-list silently truncating)."""
    session = _CapturingSession()
    repo = TimescaleAssetRepository(session)  # type: ignore[arg-type]
    cids = [uuid.uuid4(), uuid.uuid4()]
    await repo.list(uuid.uuid4(), category_ids=cids)
    compiled = session.captured.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    sql = str(compiled).lower()
    for cid in cids:
        assert str(cid) in sql
