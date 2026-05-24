"""Two-person-rule plumbing for bulk ops (Sprint 50 C3, ADR 028).

Implements [ADR-028 §"Governance" rule 4](../../../docs/adr/028-tags-as-first-class-entity.md):
bulk ops over ``tenants.tag_bulk_two_person_threshold`` create a
``pending_bulk_operations`` row that a second admin must approve
before execution.

Surface:

- :data:`PENDING_BULK_OP_TTL_SECONDS` — 24 h. Two-person review
  needs more wall-clock than the C2 token's 15-min TTL (a token
  is "I'll re-submit this in five minutes after reading it";
  a pending request is "I'll get back to my colleague tomorrow").
- :func:`create_pending` — stash the CSV + metadata for later
  approval. Returns the persisted row.
- :func:`approve` — second admin OKs the queued op. Verifies the
  payload still hashes to the stored ``content_hash`` (tamper
  guard), checks self-approval, checks not-expired-or-already-decided,
  then hands off to the ``operation``-specific executor and marks
  the row ``executed``.
- :func:`reject` — second admin denies. Records ``decided_by`` /
  ``decided_at`` and flips ``status='rejected'``. No execution.

Mismatch outcomes (mirrors the C2 token store's enum):

- ``NOT_FOUND``     — id not in this tenant.
- ``EXPIRED``       — ``expires_at`` in the past (lazy-evicted to
  ``status='expired'`` on the spot).
- ``ALREADY_DECIDED`` — the row was already approved / rejected /
  executed / expired by an earlier call (single-decision semantics).
- ``SELF_APPROVAL`` — the deciding user is the same one who
  requested. The whole point of the two-person rule.
- ``CONTENT_TAMPER`` — the stored payload no longer hashes to
  ``content_hash``. Should be impossible without DB tampering;
  surface as 409 anyway so an auditor sees the discrepancy.

The ``operation`` discriminator currently dispatches only
``tags.import``; C4 will register ``tags.bulk_patch`` /
``tags.bulk_retire`` via the same module-scoped registry.
"""

from __future__ import annotations

import enum
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import PendingBulkOperationModel

# 24 hours. Long enough for "I'll ask my colleague tomorrow";
# short enough that forgotten requests can't be weaponized weeks later.
PENDING_BULK_OP_TTL_SECONDS = 24 * 60 * 60


class PendingDecisionOutcome(enum.Enum):
    """Result of an :func:`approve` / :func:`reject` call.

    Mirrors :class:`tagpulse.core.bulk_confirmation_tokens.ConfirmationOutcome`
    in shape so the route layer's 409 handling stays uniform across C2 + C3.
    """

    OK = "ok"
    NOT_FOUND = "not_found"
    EXPIRED = "expired"
    ALREADY_DECIDED = "already_decided"
    SELF_APPROVAL = "self_approval"
    CONTENT_TAMPER = "content_tamper"


# Executor signature. Returns a JSON-serialisable dict of summary
# fields that the route echoes back to the approver (e.g.
# ``{"rows_created": N, "rows_skipped": M, "request_id": "..."}``).
PendingExecutor = Callable[
    [AsyncSession, PendingBulkOperationModel, uuid.UUID],
    Awaitable[dict[str, Any]],
]


_EXECUTORS: dict[str, PendingExecutor] = {}


def register_executor(operation: str, executor: PendingExecutor) -> None:
    """Register the executor for an ``operation`` string.

    Called from route modules at import time so the registry is
    populated before any approve call lands. C3 registers
    ``tags.import``; C4 will add ``tags.bulk_patch`` /
    ``tags.bulk_retire``.
    """
    _EXECUTORS[operation] = executor


def get_registered_operations() -> list[str]:
    """Diagnostic helper — what operations can be approved today?"""
    return sorted(_EXECUTORS)


def reset_executors() -> None:
    """Test-only: clear the registry so tests start from a known state."""
    _EXECUTORS.clear()


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def list_pending(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    status: str | None = None,
    operation: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[PendingBulkOperationModel]:
    """List ``pending_bulk_operations`` rows for a tenant.

    Powers the Phase F admin inbox (``GET /bulk-operations``). Filters
    are AND-combined; both are optional. Results are ordered
    ``created_at DESC`` so the inbox shows newest first. ``payload``
    bytes are NOT projected away here — the route serialises through
    :class:`PendingBulkOperationResponse` which omits ``payload``, so
    the bytes never leave the process.
    """
    stmt = select(PendingBulkOperationModel).where(
        PendingBulkOperationModel.tenant_id == tenant_id,
    )
    if status is not None:
        stmt = stmt.where(PendingBulkOperationModel.status == status)
    if operation is not None:
        stmt = stmt.where(PendingBulkOperationModel.operation == operation)
    stmt = stmt.order_by(PendingBulkOperationModel.created_at.desc()).limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars().all())


async def create_pending(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    operation: str,
    requested_by: uuid.UUID | None,
    content_hash: str,
    row_count: int,
    sample: list[str],
    payload: bytes,
    ttl_seconds: int = PENDING_BULK_OP_TTL_SECONDS,
    now: datetime | None = None,
) -> PendingBulkOperationModel:
    """Persist a ``pending_bulk_operations`` row.

    Caller is responsible for the surrounding transaction
    (``session.commit()`` happens at the route boundary as usual).
    """
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    timestamp = now or _utcnow()
    row = PendingBulkOperationModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        operation=operation,
        status="pending",
        requested_by=requested_by,
        decided_by=None,
        content_hash=content_hash,
        row_count=row_count,
        sample=sample,
        payload=payload,
        request_id=None,
        created_at=timestamp,
        decided_at=None,
        executed_at=None,
        expires_at=timestamp + timedelta(seconds=ttl_seconds),
    )
    session.add(row)
    await session.flush()
    return row


async def _load(
    session: AsyncSession,
    pending_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> PendingBulkOperationModel | None:
    stmt = select(PendingBulkOperationModel).where(
        PendingBulkOperationModel.id == pending_id,
        PendingBulkOperationModel.tenant_id == tenant_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _expire_if_due(
    row: PendingBulkOperationModel,
    now: datetime,
) -> bool:
    """Flip a stale pending row to ``expired`` in place. Returns True on flip."""
    if row.status != "pending":
        return False
    if row.expires_at <= now:
        row.status = "expired"
        return True
    return False


async def approve(
    session: AsyncSession,
    *,
    pending_id: uuid.UUID,
    tenant_id: uuid.UUID,
    decided_by: uuid.UUID,
    content_hasher: Callable[[bytes], str],
    now: datetime | None = None,
) -> tuple[PendingDecisionOutcome, PendingBulkOperationModel | None, dict[str, Any] | None]:
    """Second admin approves the pending row and the op executes.

    Returns ``(outcome, row, executor_summary)``. On non-OK
    outcomes ``executor_summary`` is None; on OK the executor's
    return dict is passed through so the route can include it in
    the response body / audit log.

    ``content_hasher`` is injected (rather than imported) so the
    service module stays generic across operations — for
    ``tags.import`` the route passes a closure that re-parses the
    CSV and applies :func:`tagpulse.api.routes.tags._content_hash`
    to the resulting EPC list.
    """
    timestamp = now or _utcnow()
    row = await _load(session, pending_id, tenant_id)
    if row is None:
        return PendingDecisionOutcome.NOT_FOUND, None, None

    await _expire_if_due(row, timestamp)

    if row.status == "expired":
        return PendingDecisionOutcome.EXPIRED, row, None
    if row.status != "pending":
        return PendingDecisionOutcome.ALREADY_DECIDED, row, None
    if row.requested_by is not None and row.requested_by == decided_by:
        return PendingDecisionOutcome.SELF_APPROVAL, row, None

    recomputed = content_hasher(row.payload)
    if recomputed != row.content_hash:
        return PendingDecisionOutcome.CONTENT_TAMPER, row, None

    executor = _EXECUTORS.get(row.operation)
    if executor is None:
        # Misconfiguration, not a user error — let the route surface 500.
        raise LookupError(f"no executor registered for operation '{row.operation}'")

    request_id = uuid.uuid4()
    summary = await executor(session, row, request_id)

    row.status = "executed"
    row.decided_by = decided_by
    row.decided_at = timestamp
    row.executed_at = timestamp
    row.request_id = request_id
    return PendingDecisionOutcome.OK, row, summary


async def reject(
    session: AsyncSession,
    *,
    pending_id: uuid.UUID,
    tenant_id: uuid.UUID,
    decided_by: uuid.UUID,
    now: datetime | None = None,
) -> tuple[PendingDecisionOutcome, PendingBulkOperationModel | None]:
    """Second admin denies. No execution; row flips to ``rejected``.

    Self-rejection is *also* blocked. Operationally a requester who
    changes their mind should ask the same second admin to reject;
    it keeps the audit trail clean. (Allowing self-cancel would
    create an asymmetry between approve and reject that's hard to
    reason about.)
    """
    timestamp = now or _utcnow()
    row = await _load(session, pending_id, tenant_id)
    if row is None:
        return PendingDecisionOutcome.NOT_FOUND, None

    await _expire_if_due(row, timestamp)

    if row.status == "expired":
        return PendingDecisionOutcome.EXPIRED, row
    if row.status != "pending":
        return PendingDecisionOutcome.ALREADY_DECIDED, row
    if row.requested_by is not None and row.requested_by == decided_by:
        return PendingDecisionOutcome.SELF_APPROVAL, row

    row.status = "rejected"
    row.decided_by = decided_by
    row.decided_at = timestamp
    return PendingDecisionOutcome.OK, row
