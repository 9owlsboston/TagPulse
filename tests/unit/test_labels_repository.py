"""Unit tests for Sprint 35 Labels repository — pure-Python helpers
(SQLSTATE extraction + exception classes). DB-touching paths are
covered by integration tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from tagpulse.repositories.timescaledb.labels import (
    LabelCapExceededError,
    LabelInUseError,
    LabelKeyConflictError,
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
