"""Unit tests for the two-person-rule plumbing (Sprint 50 C3, ADR 028).

Covers :mod:`tagpulse.services.pending_bulk_operations` end-to-end
with an in-memory fake session — no Postgres dependency. Route-level
tests (status codes, JSON shape, RBAC) live in the integration suite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tagpulse.models.database import PendingBulkOperationModel
from tagpulse.services import pending_bulk_operations as pending_ops
from tagpulse.services.pending_bulk_operations import (
    PENDING_BULK_OP_TTL_SECONDS,
    PendingDecisionOutcome,
)

# ---------------------------------------------------------------------------
# Fake session — supports session.add(), session.flush(), session.execute()
# for the two SELECTs the service issues.
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, row: PendingBulkOperationModel | None) -> None:
        self._row = row

    def scalar_one_or_none(self) -> PendingBulkOperationModel | None:
        return self._row


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[PendingBulkOperationModel] = []
        self.flushed = 0

    def add(self, obj: PendingBulkOperationModel) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed += 1

    async def execute(self, _stmt: Any) -> _FakeResult:
        # The service's only SELECT is "load by (id, tenant_id)" — we
        # return the most recently added row that matches. Good enough
        # for the single-row tests below.
        if not self.added:
            return _FakeResult(None)
        return _FakeResult(self.added[-1])


def _hash_ok(_payload: bytes) -> str:
    return "abc123"


def _hash_bad(_payload: bytes) -> str:
    return "different-hash"


@pytest.fixture(autouse=True)
def _isolated_registry() -> Any:
    """Clear the executor registry between tests so registrations
    from one test don't leak into another. The production app
    registers ``tags.import`` at import time; tests register their
    own dummy executors as needed."""
    pending_ops.reset_executors()
    yield
    pending_ops.reset_executors()


async def _make_pending(
    session: _FakeSession,
    *,
    tenant_id: uuid.UUID | None = None,
    requested_by: uuid.UUID | None = None,
    content_hash: str = "abc123",
    operation: str = "tags.import",
    ttl_seconds: int = PENDING_BULK_OP_TTL_SECONDS,
    now: datetime | None = None,
) -> PendingBulkOperationModel:
    return await pending_ops.create_pending(
        session,  # type: ignore[arg-type]
        tenant_id=tenant_id or uuid.uuid4(),
        operation=operation,
        requested_by=requested_by,
        content_hash=content_hash,
        row_count=10_000,
        sample=["AAA", "BBB"],
        payload=b"epc_hex\nAAA\nBBB\n",
        ttl_seconds=ttl_seconds,
        now=now,
    )


# ---------------------------------------------------------------------------
# create_pending
# ---------------------------------------------------------------------------


class TestCreatePending:
    @pytest.mark.asyncio
    async def test_shape(self) -> None:
        session = _FakeSession()
        tenant_id = uuid.uuid4()
        requester = uuid.uuid4()
        row = await _make_pending(
            session,
            tenant_id=tenant_id,
            requested_by=requester,
        )

        assert row.tenant_id == tenant_id
        assert row.requested_by == requester
        assert row.operation == "tags.import"
        assert row.status == "pending"
        assert row.decided_by is None
        assert row.decided_at is None
        assert row.executed_at is None
        assert row.request_id is None
        assert row.expires_at > row.created_at
        assert session.flushed == 1
        assert session.added == [row]

    @pytest.mark.asyncio
    async def test_ttl_zero_rejected(self) -> None:
        session = _FakeSession()
        with pytest.raises(ValueError, match="ttl_seconds"):
            await _make_pending(session, ttl_seconds=0)

    @pytest.mark.asyncio
    async def test_requested_by_none_accepted(self) -> None:
        """Tenant-API-key actors have no Entra user_id."""
        session = _FakeSession()
        row = await _make_pending(session, requested_by=None)
        assert row.requested_by is None


# ---------------------------------------------------------------------------
# approve — happy path + every mismatch outcome
# ---------------------------------------------------------------------------


class TestApprove:
    @pytest.mark.asyncio
    async def test_happy_path_executes_and_marks_executed(self) -> None:
        session = _FakeSession()
        tenant_id = uuid.uuid4()
        requester = uuid.uuid4()
        approver = uuid.uuid4()
        row = await _make_pending(session, tenant_id=tenant_id, requested_by=requester)

        captured: dict[str, Any] = {}

        async def executor(
            _session: Any,
            row_: PendingBulkOperationModel,
            request_id: uuid.UUID,
        ) -> dict[str, Any]:
            captured["row_id"] = row_.id
            captured["request_id"] = request_id
            return {"rows_created": 10, "rows_skipped": 0}

        pending_ops.register_executor("tags.import", executor)

        outcome, returned, summary = await pending_ops.approve(
            session,  # type: ignore[arg-type]
            pending_id=row.id,
            tenant_id=tenant_id,
            decided_by=approver,
            content_hasher=_hash_ok,
        )

        assert outcome is PendingDecisionOutcome.OK
        assert returned is row
        assert summary == {"rows_created": 10, "rows_skipped": 0}
        assert row.status == "executed"
        assert row.decided_by == approver
        assert row.decided_at is not None
        assert row.executed_at is not None
        assert row.request_id is not None
        assert captured["row_id"] == row.id
        assert captured["request_id"] == row.request_id

    @pytest.mark.asyncio
    async def test_not_found(self) -> None:
        session = _FakeSession()  # empty
        outcome, row, summary = await pending_ops.approve(
            session,  # type: ignore[arg-type]
            pending_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            decided_by=uuid.uuid4(),
            content_hasher=_hash_ok,
        )
        assert outcome is PendingDecisionOutcome.NOT_FOUND
        assert row is None
        assert summary is None

    @pytest.mark.asyncio
    async def test_self_approval_blocked(self) -> None:
        session = _FakeSession()
        tenant_id = uuid.uuid4()
        actor = uuid.uuid4()
        row = await _make_pending(session, tenant_id=tenant_id, requested_by=actor)

        async def executor(*_a: Any, **_k: Any) -> dict[str, Any]:
            raise AssertionError("executor must NOT run on self-approval")

        pending_ops.register_executor("tags.import", executor)

        outcome, returned, summary = await pending_ops.approve(
            session,  # type: ignore[arg-type]
            pending_id=row.id,
            tenant_id=tenant_id,
            decided_by=actor,
            content_hasher=_hash_ok,
        )
        assert outcome is PendingDecisionOutcome.SELF_APPROVAL
        assert returned is row
        assert summary is None
        assert row.status == "pending"  # unchanged

    @pytest.mark.asyncio
    async def test_expired_flips_status_in_place(self) -> None:
        session = _FakeSession()
        tenant_id = uuid.uuid4()
        # Create with a TTL that's already in the past at decide time.
        created_at = datetime.now(UTC) - timedelta(hours=48)
        row = await _make_pending(
            session,
            tenant_id=tenant_id,
            requested_by=uuid.uuid4(),
            ttl_seconds=3600,
            now=created_at,
        )
        outcome, returned, summary = await pending_ops.approve(
            session,  # type: ignore[arg-type]
            pending_id=row.id,
            tenant_id=tenant_id,
            decided_by=uuid.uuid4(),
            content_hasher=_hash_ok,
        )
        assert outcome is PendingDecisionOutcome.EXPIRED
        assert returned is row
        assert summary is None
        assert row.status == "expired"

    @pytest.mark.asyncio
    async def test_already_decided(self) -> None:
        session = _FakeSession()
        tenant_id = uuid.uuid4()
        row = await _make_pending(session, tenant_id=tenant_id, requested_by=uuid.uuid4())
        row.status = "rejected"  # simulate prior decision

        outcome, returned, _ = await pending_ops.approve(
            session,  # type: ignore[arg-type]
            pending_id=row.id,
            tenant_id=tenant_id,
            decided_by=uuid.uuid4(),
            content_hasher=_hash_ok,
        )
        assert outcome is PendingDecisionOutcome.ALREADY_DECIDED
        assert returned is row
        assert row.status == "rejected"  # unchanged

    @pytest.mark.asyncio
    async def test_content_tamper(self) -> None:
        session = _FakeSession()
        tenant_id = uuid.uuid4()
        row = await _make_pending(
            session,
            tenant_id=tenant_id,
            requested_by=uuid.uuid4(),
            content_hash="original-hash",
        )

        async def executor(*_a: Any, **_k: Any) -> dict[str, Any]:
            raise AssertionError("executor must NOT run on tamper")

        pending_ops.register_executor("tags.import", executor)

        outcome, returned, summary = await pending_ops.approve(
            session,  # type: ignore[arg-type]
            pending_id=row.id,
            tenant_id=tenant_id,
            decided_by=uuid.uuid4(),
            content_hasher=_hash_bad,  # returns "different-hash"
        )
        assert outcome is PendingDecisionOutcome.CONTENT_TAMPER
        assert returned is row
        assert summary is None
        assert row.status == "pending"

    @pytest.mark.asyncio
    async def test_no_executor_raises(self) -> None:
        session = _FakeSession()
        tenant_id = uuid.uuid4()
        row = await _make_pending(session, tenant_id=tenant_id, requested_by=uuid.uuid4())
        # Registry is empty (the autouse fixture cleared it).
        with pytest.raises(LookupError, match="tags.import"):
            await pending_ops.approve(
                session,  # type: ignore[arg-type]
                pending_id=row.id,
                tenant_id=tenant_id,
                decided_by=uuid.uuid4(),
                content_hasher=_hash_ok,
            )


# ---------------------------------------------------------------------------
# reject
# ---------------------------------------------------------------------------


class TestReject:
    @pytest.mark.asyncio
    async def test_happy_path(self) -> None:
        session = _FakeSession()
        tenant_id = uuid.uuid4()
        row = await _make_pending(session, tenant_id=tenant_id, requested_by=uuid.uuid4())
        approver = uuid.uuid4()

        outcome, returned = await pending_ops.reject(
            session,  # type: ignore[arg-type]
            pending_id=row.id,
            tenant_id=tenant_id,
            decided_by=approver,
        )
        assert outcome is PendingDecisionOutcome.OK
        assert returned is row
        assert row.status == "rejected"
        assert row.decided_by == approver
        assert row.decided_at is not None
        assert row.executed_at is None  # never executed

    @pytest.mark.asyncio
    async def test_self_rejection_blocked(self) -> None:
        session = _FakeSession()
        tenant_id = uuid.uuid4()
        actor = uuid.uuid4()
        row = await _make_pending(session, tenant_id=tenant_id, requested_by=actor)
        outcome, returned = await pending_ops.reject(
            session,  # type: ignore[arg-type]
            pending_id=row.id,
            tenant_id=tenant_id,
            decided_by=actor,
        )
        assert outcome is PendingDecisionOutcome.SELF_APPROVAL
        assert returned is row
        assert row.status == "pending"

    @pytest.mark.asyncio
    async def test_not_found(self) -> None:
        session = _FakeSession()
        outcome, row = await pending_ops.reject(
            session,  # type: ignore[arg-type]
            pending_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            decided_by=uuid.uuid4(),
        )
        assert outcome is PendingDecisionOutcome.NOT_FOUND
        assert row is None

    @pytest.mark.asyncio
    async def test_already_decided(self) -> None:
        session = _FakeSession()
        tenant_id = uuid.uuid4()
        row = await _make_pending(session, tenant_id=tenant_id, requested_by=uuid.uuid4())
        row.status = "executed"
        outcome, returned = await pending_ops.reject(
            session,  # type: ignore[arg-type]
            pending_id=row.id,
            tenant_id=tenant_id,
            decided_by=uuid.uuid4(),
        )
        assert outcome is PendingDecisionOutcome.ALREADY_DECIDED
        assert returned is row
        assert row.status == "executed"

    @pytest.mark.asyncio
    async def test_expired(self) -> None:
        session = _FakeSession()
        tenant_id = uuid.uuid4()
        created_at = datetime.now(UTC) - timedelta(hours=48)
        row = await _make_pending(
            session,
            tenant_id=tenant_id,
            requested_by=uuid.uuid4(),
            ttl_seconds=3600,
            now=created_at,
        )
        outcome, returned = await pending_ops.reject(
            session,  # type: ignore[arg-type]
            pending_id=row.id,
            tenant_id=tenant_id,
            decided_by=uuid.uuid4(),
        )
        assert outcome is PendingDecisionOutcome.EXPIRED
        assert returned is row
        assert row.status == "expired"


# ---------------------------------------------------------------------------
# Executor registry sanity
# ---------------------------------------------------------------------------


class TestExecutorRegistry:
    def test_register_and_list(self) -> None:
        async def dummy(*_a: Any, **_k: Any) -> dict[str, Any]:
            return {}

        pending_ops.register_executor("foo.op", dummy)
        pending_ops.register_executor("bar.op", dummy)
        assert pending_ops.get_registered_operations() == ["bar.op", "foo.op"]

    def test_reset_clears(self) -> None:
        async def dummy(*_a: Any, **_k: Any) -> dict[str, Any]:
            return {}

        pending_ops.register_executor("foo.op", dummy)
        pending_ops.reset_executors()
        assert pending_ops.get_registered_operations() == []
