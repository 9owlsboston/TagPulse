"""Unit tests for Sprint 50 Phase C5 — unified audit log for bulk ops.

Per ADR 028 §Governance #7, every bulk operation MUST land in
``audit_logs`` keyed on the same tuple
``(actor, action, batch, count, request_id)`` so analysts can answer
"who, what, how big, which batch, which request" with a single
``WHERE`` clause instead of digging through the legacy
``audit_logs.changes`` JSONB blob.

Migration 048 hoists those keys out of the JSON and into first-class
columns (``request_id``, ``batch``, ``count``, ``pending_id``,
``approved_by``). This module exercises:

- :meth:`AuditLogger.log` plumbs all five new keyword-only params
  into the persisted :class:`AuditLogModel` row.
- :meth:`AuditLogger.log` defaults keep the legacy signature
  byte-compatible (every existing caller that doesn't pass the new
  kwargs still produces ``None`` for the new columns).
- :meth:`AuditLogger.list_logs` serializes the new columns into
  the returned dicts and applies ``request_id`` / ``batch`` filters
  when given.

Route-level wiring (the 7 bulk-op call sites in ``routes/tags.py``
and ``routes/bulk_operations.py``) is covered by the existing
integration suite — those tests already assert end-to-end audit
shape and will catch any regression introduced by C5.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from tagpulse.core.audit import AuditLogger
from tagpulse.models.database import AuditLogModel


class _CaptureSession:
    """Minimal fake that records every ``.add()`` call.

    The real ``AuditLogger.log`` is a thin wrapper: it constructs
    an :class:`AuditLogModel` and hands it to ``session.add``. We
    capture that object and inspect its attributes — no DB needed.
    """

    def __init__(self) -> None:
        self.added: list[AuditLogModel] = []

    def add(self, obj: AuditLogModel) -> None:
        self.added.append(obj)


# ---------------------------------------------------------------------------
# AuditLogger.log — new kwargs
# ---------------------------------------------------------------------------


class TestLogNewColumns:
    @pytest.mark.asyncio
    async def test_all_new_kwargs_persisted(self) -> None:
        session = _CaptureSession()
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        approver_id = uuid.uuid4()
        pending_id = uuid.uuid4()
        request_id = uuid.uuid4()

        await AuditLogger(session=session).log(  # type: ignore[arg-type]
            tenant_id,
            "tag.bulk_imported",
            "tag",
            request_id,
            changes={"rows_created": 5_000},
            user_id=user_id,
            request_id=request_id,
            batch="B-2026-Q1-001",
            count=5_000,
            pending_id=pending_id,
            approved_by=approver_id,
        )

        assert len(session.added) == 1
        row = session.added[0]
        assert row.request_id == request_id
        assert row.batch == "B-2026-Q1-001"
        assert row.count == 5_000
        assert row.pending_id == pending_id
        assert row.approved_by == approver_id
        # Legacy JSON blob still populated for backward compat.
        assert row.changes == {"rows_created": 5_000}

    @pytest.mark.asyncio
    async def test_defaults_to_none_for_legacy_callers(self) -> None:
        """Existing call sites that don't pass the new kwargs must
        continue to work and produce ``NULL`` for every new column."""
        session = _CaptureSession()
        await AuditLogger(session=session).log(  # type: ignore[arg-type]
            uuid.uuid4(),
            "device.token_rotated",
            "device",
            uuid.uuid4(),
            changes={"rotated_at": "now"},
        )

        row = session.added[0]
        assert row.request_id is None
        assert row.batch is None
        assert row.count is None
        assert row.pending_id is None
        assert row.approved_by is None

    @pytest.mark.asyncio
    async def test_partial_kwargs_only_set_what_was_passed(self) -> None:
        """The C3 ``rejected`` call site passes ``count`` + ``pending_id``
        but no ``request_id`` (the op was never executed). Verify partial
        adoption works."""
        session = _CaptureSession()
        pending_id = uuid.uuid4()
        await AuditLogger(session=session).log(  # type: ignore[arg-type]
            uuid.uuid4(),
            "tags.import.rejected",
            "pending_bulk_operation",
            pending_id,
            changes={"operation": "tags.import"},
            count=42,
            pending_id=pending_id,
        )

        row = session.added[0]
        assert row.count == 42
        assert row.pending_id == pending_id
        assert row.request_id is None
        assert row.approved_by is None
        assert row.batch is None


# ---------------------------------------------------------------------------
# AuditLogger.list_logs — new filters + serialization
# ---------------------------------------------------------------------------


class _FakeExecResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> Any:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def __iter__(self) -> Any:
        return iter(self._rows)


class _StmtCapturingSession:
    """Captures the last SQL statement passed to ``execute`` so we
    can introspect the ``WHERE`` clause without a real database."""

    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.last_stmt: Any = None

    async def execute(self, stmt: Any) -> _FakeExecResult:
        self.last_stmt = stmt
        return _FakeExecResult(self.rows)


def _make_row(**overrides: Any) -> Any:
    """Build a stand-in for :class:`AuditLogModel` carrying just
    the attrs ``list_logs`` reads. SQLAlchemy isn't involved."""
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "action": "tag.bulk_imported",
        "resource_type": "tag",
        "resource_id": uuid.uuid4(),
        "changes": {"rows_created": 1},
        "request_id": uuid.uuid4(),
        "batch": "B-2026-Q1-001",
        "count": 1,
        "pending_id": uuid.uuid4(),
        "approved_by": uuid.uuid4(),
        "created_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return type("_Row", (), defaults)()


class TestListLogsSerialization:
    @pytest.mark.asyncio
    async def test_new_columns_serialized(self) -> None:
        row = _make_row()
        session = _StmtCapturingSession([row])
        result = await AuditLogger(session=session).list_logs(  # type: ignore[arg-type]
            uuid.uuid4(),
        )
        assert len(result) == 1
        entry = result[0]
        assert entry["request_id"] == str(row.request_id)
        assert entry["batch"] == row.batch
        assert entry["count"] == row.count
        assert entry["pending_id"] == str(row.pending_id)
        assert entry["approved_by"] == str(row.approved_by)

    @pytest.mark.asyncio
    async def test_none_fields_pass_through_as_none(self) -> None:
        row = _make_row(
            request_id=None,
            batch=None,
            count=None,
            pending_id=None,
            approved_by=None,
        )
        session = _StmtCapturingSession([row])
        result = await AuditLogger(session=session).list_logs(  # type: ignore[arg-type]
            uuid.uuid4(),
        )
        entry = result[0]
        assert entry["request_id"] is None
        assert entry["batch"] is None
        assert entry["count"] is None
        assert entry["pending_id"] is None
        assert entry["approved_by"] is None


class TestListLogsFilters:
    @pytest.mark.asyncio
    async def test_request_id_filter_in_where_clause(self) -> None:
        session = _StmtCapturingSession([])
        rid = uuid.uuid4()
        await AuditLogger(session=session).list_logs(  # type: ignore[arg-type]
            uuid.uuid4(),
            request_id=rid,
        )
        compiled = str(session.last_stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "request_id" in compiled
        assert rid.hex in compiled

    @pytest.mark.asyncio
    async def test_batch_filter_in_where_clause(self) -> None:
        session = _StmtCapturingSession([])
        await AuditLogger(session=session).list_logs(  # type: ignore[arg-type]
            uuid.uuid4(),
            batch="B-2026-Q1-001",
        )
        compiled = str(session.last_stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "batch" in compiled
        assert "B-2026-Q1-001" in compiled

    @pytest.mark.asyncio
    async def test_no_filters_yields_no_extra_where(self) -> None:
        session = _StmtCapturingSession([])
        await AuditLogger(session=session).list_logs(  # type: ignore[arg-type]
            uuid.uuid4(),
        )
        compiled = str(session.last_stmt.compile(compile_kwargs={"literal_binds": True}))
        # Only the tenant-scoped WHERE survives.
        assert compiled.count("WHERE") == 1
