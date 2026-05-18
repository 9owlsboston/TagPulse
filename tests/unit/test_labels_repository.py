"""Unit tests for Sprint 35 Labels repository — pure-Python helpers
(SQLSTATE extraction + exception classes). DB-touching paths are
covered by integration tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from tagpulse.repositories.timescaledb.labels import (
    LabelCapExceededError,
    LabelInUseError,
    LabelKeyConflictError,
    TimescaleLabelRepository,
    _pg_sqlstate,
)


class TestPgSqlstateExtractor:
    """``_pg_sqlstate`` normalises asyncpg's ``.sqlstate`` and
    psycopg2's ``.pgcode`` so error mapping works under either
    driver."""

    def _wrap(self, orig: object) -> IntegrityError:
        # IntegrityError(statement, params, orig). We only care
        # about ``.orig`` for sqlstate extraction.
        return IntegrityError("INSERT ...", {}, orig)  # type: ignore[arg-type]

    def test_asyncpg_sqlstate_attr(self) -> None:
        # asyncpg exposes the 5-char code as ``.sqlstate``.
        orig = SimpleNamespace(sqlstate="23505")
        exc = self._wrap(orig)
        assert _pg_sqlstate(exc) == "23505"

    def test_psycopg2_pgcode_attr(self) -> None:
        # psycopg2 exposes the 5-char code as ``.pgcode``.
        orig = SimpleNamespace(pgcode="23514")
        exc = self._wrap(orig)
        assert _pg_sqlstate(exc) == "23514"

    def test_asyncpg_preferred_when_both_present(self) -> None:
        # Defensive: if some wrapper sets both, prefer asyncpg's
        # canonical attribute.
        orig = SimpleNamespace(sqlstate="23505", pgcode="23514")
        exc = self._wrap(orig)
        assert _pg_sqlstate(exc) == "23505"

    def test_returns_none_when_neither_present(self) -> None:
        orig = SimpleNamespace()
        exc = self._wrap(orig)
        assert _pg_sqlstate(exc) is None


class TestLabelKeyConflictError:
    """Domain exception for case-insensitive key collisions."""

    def test_is_value_error_subclass(self) -> None:
        # Subclass of ValueError so call sites that catch ValueError
        # broadly (rare; usually we catch the specific class)
        # don't accidentally surface it as a 500.
        assert issubclass(LabelKeyConflictError, ValueError)

    def test_carries_message(self) -> None:
        exc = LabelKeyConflictError("duplicate")
        assert str(exc) == "duplicate"


class TestLabelInUseError:
    """Raised when DELETE would orphan associations."""

    def test_carries_label_id_and_count(self) -> None:
        label_id = uuid.uuid4()
        exc = LabelInUseError(label_id, 5)
        assert exc.label_id == label_id
        assert exc.association_count == 5
        assert "5 association" in str(exc)


class TestLabelCapExceededError:
    """Raised on the 31st INSERT into entity_labels for one entity."""

    def test_cap_constant_matches_migration(self) -> None:
        # The trigger ``trg_enforce_label_cap`` hard-codes 30. If we
        # ever bump the cap, both this constant AND the migration
        # must change in lockstep.
        assert LabelCapExceededError.CAP == 30

    def test_carries_entity_id(self) -> None:
        entity_id = uuid.uuid4()
        exc = LabelCapExceededError(entity_id)
        assert exc.entity_id == entity_id
        assert "30 labels" in str(exc)


@pytest.mark.parametrize(
    "exc_cls,expected_base",
    [
        (LabelKeyConflictError, ValueError),
        (LabelInUseError, RuntimeError),
        (LabelCapExceededError, RuntimeError),
    ],
)
def test_exception_hierarchy(exc_cls: type[Exception], expected_base: type[Exception]) -> None:
    """Sanity-check that future refactors don't accidentally
    re-parent these to ``Exception`` (which would defeat the catch
    blocks in routes)."""
    assert issubclass(exc_cls, expected_base)


# ---------------------------------------------------------------------------
# ADR-020 Phase B: orphan entity_labels cleanup
# ---------------------------------------------------------------------------


class _CapturingSession:
    """Async session double that records executed statements and
    delete()'d rows. Mirrors the pattern in
    ``tests/unit/test_assets_repository_filters.py``."""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self.executed: list[Any] = []
        self.deleted: list[Any] = []
        self.flushes = 0
        self._rows = rows or []

    async def execute(self, stmt: Any) -> Any:
        self.executed.append(stmt)
        captured_rows = self._rows

        class _Result:
            def scalars(self) -> Any:
                class _Scalars:
                    def all(self) -> list[Any]:
                        return captured_rows

                return _Scalars()

        return _Result()

    async def delete(self, row: Any) -> None:
        self.deleted.append(row)

    async def flush(self) -> None:
        self.flushes += 1


def _compiled_sql(stmt: Any) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": False},
        )
    ).lower()


class TestDeleteForEntity:
    """ADR-020 Phase B: when an entity is hard-deleted, the
    repository's ``delete_for_entity`` cascades the
    ``entity_labels`` rows that pointed at it. Without this, the
    parent label's ``count_associations`` would forever count the
    orphan rows and block ``DELETE /labels/{id}`` with a 409 the
    operator cannot resolve."""

    @pytest.mark.asyncio
    async def test_select_is_tenant_scoped_via_labels_join(self) -> None:
        """Tenant scoping comes from the parent ``labels`` table —
        the association table has no ``tenant_id`` column. The
        SELECT must JOIN labels and filter on ``labels.tenant_id``."""
        session = _CapturingSession()
        repo = TimescaleLabelRepository(session)  # type: ignore[arg-type]
        await repo.delete_for_entity(uuid.uuid4(), "site", uuid.uuid4())
        assert len(session.executed) == 1
        sql = _compiled_sql(session.executed[0])
        assert "join labels" in sql
        assert "labels.tenant_id" in sql
        assert "labels.entity_type" in sql
        assert "entity_labels.entity_id" in sql

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_orphans(self) -> None:
        """Empty result → returns 0 and skips the flush. Cheap
        path: most entity deletes have no labels."""
        session = _CapturingSession(rows=[])
        repo = TimescaleLabelRepository(session)  # type: ignore[arg-type]
        n = await repo.delete_for_entity(uuid.uuid4(), "zone", uuid.uuid4())
        assert n == 0
        assert session.deleted == []
        assert session.flushes == 0

    @pytest.mark.asyncio
    async def test_deletes_each_row_and_flushes_once(self) -> None:
        """Returned count matches rows touched, each row is
        passed to session.delete, and we flush exactly once even
        with multiple rows."""
        rows = [object(), object(), object()]
        session = _CapturingSession(rows=rows)
        repo = TimescaleLabelRepository(session)  # type: ignore[arg-type]
        n = await repo.delete_for_entity(uuid.uuid4(), "category", uuid.uuid4())
        assert n == 3
        assert session.deleted == rows
        assert session.flushes == 1

    @pytest.mark.asyncio
    async def test_predicates_bind_entity_type_and_id(self) -> None:
        """The entity_type literal and entity_id parameter must
        both appear in the compiled WHERE clause — a regression
        here (e.g. dropping one of the predicates) would let one
        entity's delete sweep another entity's labels or another
        entity_type's labels."""
        session = _CapturingSession()
        repo = TimescaleLabelRepository(session)  # type: ignore[arg-type]
        eid = uuid.uuid4()
        await repo.delete_for_entity(uuid.uuid4(), "device", eid)
        stmt = session.executed[0]
        sql = str(
            stmt.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        assert "'device'" in sql.lower()
        assert str(eid) in sql
