"""Two-person-rule approve/reject endpoints (Sprint 50 C3, ADR 028).

Implements [ADR-028 §"Governance" rule 4](../../../../docs/adr/028-tags-as-first-class-entity.md):
a second admin reviews and approves (or rejects) a bulk op that
operator A queued via ``POST /tags/import`` (or any future bulk
endpoint whose payload meets ``tenants.tag_bulk_two_person_threshold``).

Endpoints (all admin-only, all tenant-scoped via the standard
``require_role("admin")`` + RLS plumbing):

- ``GET  /bulk-operations/{id}``         — fetch the pending row so
  operator B can review it before deciding. Returns the row's
  metadata (operation, requester, sample, row_count) but
  deliberately not the raw payload bytes.
- ``POST /bulk-operations/{id}/approve`` — execute the queued op.
  Self-approval is rejected. The stored CSV payload is re-parsed +
  re-hashed and compared against the persisted ``content_hash``;
  a mismatch is a 409 tamper-guard (should be impossible without
  DB tampering, but surface it loudly if it ever fires).
- ``POST /bulk-operations/{id}/reject``  — deny. Records
  ``decided_by`` / ``decided_at`` and flips ``status='rejected'``.
  Self-rejection is also blocked (symmetry with approve; the
  requester should ask their colleague to reject for them, which
  keeps the audit trail clean).

The list endpoint (``GET /bulk-operations?status=pending``) is
intentionally out of scope for C3 — operators query
``pending_bulk_operations`` directly via the admin UI's existing
audit-log surface until the C5 unified shape lands.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.api.routes.tags import import_payload_content_hash
from tagpulse.core.audit import AuditLogger
from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.models.database import PendingBulkOperationModel
from tagpulse.models.schemas import PendingBulkOperationResponse
from tagpulse.repositories.timescaledb.session import get_session
from tagpulse.services import pending_bulk_operations as pending_ops

router = APIRouter(prefix="/bulk-operations", tags=["bulk-operations"])


def _to_response(row: PendingBulkOperationModel) -> PendingBulkOperationResponse:
    return PendingBulkOperationResponse.model_validate(row)


async def _load_or_404(
    session: AsyncSession,
    pending_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> PendingBulkOperationModel:
    stmt = select(PendingBulkOperationModel).where(
        PendingBulkOperationModel.id == pending_id,
        PendingBulkOperationModel.tenant_id == tenant_id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"pending bulk operation '{pending_id}' not found",
        )
    return row


def _outcome_to_http(outcome: pending_ops.PendingDecisionOutcome) -> int:
    """Map a non-OK decision outcome to its HTTP status code.

    - ``NOT_FOUND``        → 404 (handled out-of-band; see :func:`_load_or_404`).
    - ``EXPIRED``,
      ``ALREADY_DECIDED``,
      ``CONTENT_TAMPER``   → **409** (state conflict).
    - ``SELF_APPROVAL``    → **403** (authorisation, not state).
    """
    if outcome is pending_ops.PendingDecisionOutcome.SELF_APPROVAL:
        return status.HTTP_403_FORBIDDEN
    return status.HTTP_409_CONFLICT


@router.get("/{pending_id}", response_model=PendingBulkOperationResponse)
async def get_pending_bulk_operation(
    pending_id: uuid.UUID,
    user: AuthenticatedUser = require_role("admin", "editor"),
    session: AsyncSession = Depends(get_session),
) -> PendingBulkOperationResponse:
    """Fetch a queued bulk op so the reviewing admin can inspect it."""
    row = await _load_or_404(session, pending_id, user.tenant_id)
    return _to_response(row)


@router.post(
    "/{pending_id}/approve",
    response_model=PendingBulkOperationResponse,
)
async def approve_pending_bulk_operation(
    pending_id: uuid.UUID,
    response: Response,
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> PendingBulkOperationResponse:
    """Second admin approves; the queued op executes immediately.

    Per ADR 028 §Governance #4 the approver MUST differ from the
    requester (``SELF_APPROVAL`` → 403). The stored payload's
    content hash is re-verified before execution (tamper guard;
    409 on mismatch).
    """
    if user.user_id is None:
        # Approver must be a real Entra user, not a tenant-API-key
        # actor — the whole point of two-person review is named
        # accountability and API keys are shared credentials.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="approval requires a named user; API-key actors cannot approve",
        )

    # 404 fast-fail outside the service so the standard not-found
    # mapping stays uniform across GET/approve/reject.
    await _load_or_404(session, pending_id, user.tenant_id)

    outcome, row, summary = await pending_ops.approve(
        session,
        pending_id=pending_id,
        tenant_id=user.tenant_id,
        decided_by=user.user_id,
        content_hasher=import_payload_content_hash,
    )

    if outcome is not pending_ops.PendingDecisionOutcome.OK:
        assert row is not None  # not-found was handled above
        raise HTTPException(
            status_code=_outcome_to_http(outcome),
            detail={
                "message": ("pending bulk operation cannot be approved in its current state"),
                "reason": outcome.value,
                "status": row.status,
            },
        )

    assert row is not None
    assert summary is not None
    await AuditLogger(session=session).log(
        user.tenant_id,
        f"{row.operation}.approved",
        "pending_bulk_operation",
        row.id,
        changes={
            "operation": row.operation,
            "requested_by": str(row.requested_by) if row.requested_by else None,
            "approved_by": str(user.user_id),
            "row_count": row.row_count,
            "request_id": str(row.request_id) if row.request_id else None,
            **summary,
        },
        user_id=user.user_id,
    )
    response.status_code = status.HTTP_200_OK
    return _to_response(row)


@router.post(
    "/{pending_id}/reject",
    response_model=PendingBulkOperationResponse,
)
async def reject_pending_bulk_operation(
    pending_id: uuid.UUID,
    response: Response,
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> PendingBulkOperationResponse:
    """Second admin denies; the queued op never executes.

    Self-rejection is blocked for symmetry with approve. If the
    original requester wants to back out, ask the same colleague
    they were asking to review.
    """
    if user.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="rejection requires a named user; API-key actors cannot reject",
        )

    await _load_or_404(session, pending_id, user.tenant_id)

    outcome, row = await pending_ops.reject(
        session,
        pending_id=pending_id,
        tenant_id=user.tenant_id,
        decided_by=user.user_id,
    )

    if outcome is not pending_ops.PendingDecisionOutcome.OK:
        assert row is not None
        raise HTTPException(
            status_code=_outcome_to_http(outcome),
            detail={
                "message": ("pending bulk operation cannot be rejected in its current state"),
                "reason": outcome.value,
                "status": row.status,
            },
        )

    assert row is not None
    await AuditLogger(session=session).log(
        user.tenant_id,
        f"{row.operation}.rejected",
        "pending_bulk_operation",
        row.id,
        changes={
            "operation": row.operation,
            "requested_by": str(row.requested_by) if row.requested_by else None,
            "rejected_by": str(user.user_id),
            "row_count": row.row_count,
        },
        user_id=user.user_id,
    )
    response.status_code = status.HTTP_200_OK
    return _to_response(row)
