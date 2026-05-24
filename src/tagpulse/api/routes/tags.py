"""Tenant-scoped Tag registry CRUD + cross-tenant transfer requests.

Sprint 50; implements [ADR-028](../../../../docs/adr/028-tag-registry.md).

Endpoints (current-tenant scope only — no global admin variant this sprint):

**Registry**

- ``GET    /tags``                — viewer+. Filters: ``status``,
  ``epc_prefix``, ``bound``, ``labels[<key>]=<value>`` (repeatable).
- ``POST   /tags``                — editor / admin. Creates one
  registry row in ``status='registered'``.
- ``GET    /tags/{epc_hex}``      — viewer+. Path lookup by canonical
  EPC hex (uppercase, no separators).
- ``PATCH  /tags/{tag_id}``       — editor / admin. ``status`` +
  ``metadata`` only; status transitions validated by
  :func:`tagpulse.services.tags.validate_status_transition`.
- ``DELETE /tags/{tag_id}``       — admin. 409 with ``binding_count``
  if any ``stock_items`` row still binds to this EPC.

**Transfers**

- ``POST /tag-transfers``         — admin. Initiate a cross-tenant
  transfer of one or more EPCs. Server-generates one
  ``request_id`` covering all EPCs in the request.
- ``GET  /tag-transfers``         — viewer+. Lists transfers
  visible to the caller (either side). Filters: ``direction``
  (``in`` / ``out``), ``status``.
- ``GET  /tag-transfers/{id}``    — viewer+.

The two-person approval shape from ADR 028 §Governance #4 lives
in :mod:`tagpulse.api.routes.bulk_operations` (Sprint 50 C3). The
transfer-initiation path here remains single-actor; second-party
acknowledgement for transfers lands in a later phase.

ADR 028 originally specified ``/v1/tenants/{slug}/...`` paths. As
with ``labels.py`` and ``categories.py``, TagPulse threads tenant
scope through ``get_current_tenant`` and skips the slug in the URL.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.audit import AuditLogger
from tagpulse.core.bulk_confirmation_tokens import (
    BULK_CONFIRMATION_TOKENS,
    DEFAULT_TTL_SECONDS,
    ConfirmationOutcome,
)
from tagpulse.core.tag_import_rate_limit import TAG_IMPORT_LIMITER
from tagpulse.core.tenant_auth import Tenant, get_current_tenant
from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.models.database import PendingBulkOperationModel, TenantModel
from tagpulse.models.schemas import (
    TagCreate,
    TagImportResult,
    TagResponse,
    TagTransferRequest,
    TagTransferResponse,
    TagUpdate,
)
from tagpulse.repositories.timescaledb.session import get_session
from tagpulse.repositories.timescaledb.tags import (
    StatusTransitionError,
    TagEpcConflictError,
    TimescaleTagRepository,
    TimescaleTagTransferRepository,
)
from tagpulse.services import pending_bulk_operations as pending_ops
from tagpulse.services.tags import normalize_epc_hex, parse_tag_import_csv

router = APIRouter(tags=["tags"])


_LABEL_FILTER_KEY = re.compile(r"^labels\[([A-Za-z][A-Za-z0-9._-]{0,63})\]$")


def _repo(session: AsyncSession) -> TimescaleTagRepository:
    return TimescaleTagRepository(session)


def _transfer_repo(session: AsyncSession) -> TimescaleTagTransferRepository:
    return TimescaleTagTransferRepository(session)


def _extract_label_filters(request: Request) -> dict[str, str]:
    """Parse ``?labels[batch]=B-001&labels[zone]=A12`` from query string.

    FastAPI's ``Query`` can't bind bracketed keys directly, so we
    walk ``request.query_params`` ourselves. The regex enforces the
    same key charset as the ``labels.key`` CHECK in migration 039.
    Unknown bracketed forms (``labels[]``, ``labels[abc][def]``) are
    silently ignored to keep the filter surface conservative.
    """
    out: dict[str, str] = {}
    for raw_key, value in request.query_params.multi_items():
        match = _LABEL_FILTER_KEY.match(raw_key)
        if match is None:
            continue
        out[match.group(1)] = value
    return out


# ---------------------------------------------------------------------------
# Registry endpoints
# ---------------------------------------------------------------------------


@router.get("/tags", response_model=list[TagResponse])
async def list_tags(
    request: Request,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    epc_prefix: str | None = Query(default=None, max_length=128),
    bound: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> list[TagResponse]:
    """List the calling tenant's tag registry rows."""
    normalised_prefix = normalize_epc_hex(epc_prefix) if epc_prefix else None
    return await _repo(session).list_for_tenant(
        tenant.id,
        status=status_filter,
        epc_prefix=normalised_prefix,
        bound=bound,
        label_filters=_extract_label_filters(request) or None,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/tags",
    response_model=TagResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_tag(
    body: TagCreate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    session: AsyncSession = Depends(get_session),
) -> TagResponse:
    """Create a tag in ``status='registered'``.

    The schema's regex runs against the *normalised* value so we
    rewrite the payload before handing it to the repo. ``gs1_uri``
    is derived in the repo from the same normalised value.
    """
    normalised = normalize_epc_hex(body.epc_hex)
    if normalised != body.epc_hex:
        body = body.model_copy(update={"epc_hex": normalised})
    try:
        created = await _repo(session).create(user.tenant_id, body)
    except TagEpcConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await AuditLogger(session=session).log(
        user.tenant_id,
        "tag.created",
        "tag",
        created.id,
        changes={
            "epc_hex": created.epc_hex,
            "source": created.source,
            "gs1_uri": created.gs1_uri,
        },
        user_id=user.user_id,
    )
    return created


@router.get("/tags/{epc_hex}", response_model=TagResponse)
async def get_tag_by_epc(
    epc_hex: str,
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> TagResponse:
    """Lookup by canonical EPC hex (case-insensitive in path)."""
    row = await _repo(session).get_by_epc(tenant.id, normalize_epc_hex(epc_hex))
    if row is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    return row


@router.patch("/tags/{tag_id}", response_model=TagResponse)
async def update_tag(
    tag_id: uuid.UUID,
    body: TagUpdate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    session: AsyncSession = Depends(get_session),
) -> TagResponse:
    """Patch ``status`` and/or ``metadata``.

    ``epc_hex`` is intentionally absent from :class:`TagUpdate` —
    it's the natural key. ``batch_id`` / category-style grouping
    goes through ``POST /tags/{id}/labels`` (per ADR 028 OQ 5).
    """
    repo = _repo(session)
    before = await repo.get(user.tenant_id, tag_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    try:
        updated = await repo.update(user.tenant_id, tag_id, body)
    except StatusTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Tag not found")

    changes: dict[str, dict[str, object]] = {}
    if before.status != updated.status:
        changes["status"] = {"from": before.status, "to": updated.status}
    if before.metadata != updated.metadata:
        changes["metadata"] = {"from": before.metadata, "to": updated.metadata}
    if changes:
        await AuditLogger(session=session).log(
            user.tenant_id,
            "tag.updated",
            "tag",
            tag_id,
            changes=changes,
            user_id=user.user_id,
        )
    return updated


@router.delete("/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    tag_id: uuid.UUID,
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Hard-delete a tag. Admin only. 409 if still bound to a stock item."""
    repo = _repo(session)
    existing = await repo.get(user.tenant_id, tag_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    binding_count = await repo.count_bindings(user.tenant_id, existing.epc_hex)
    if binding_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "tag is bound to one or more stock items",
                "binding_count": binding_count,
            },
        )
    await repo.delete(user.tenant_id, tag_id)
    await AuditLogger(session=session).log(
        user.tenant_id,
        "tag.deleted",
        "tag",
        tag_id,
        changes={"epc_hex": existing.epc_hex},
        user_id=user.user_id,
    )


# ---------------------------------------------------------------------------
# Bulk CSV import (Sprint 50 C1)
# ---------------------------------------------------------------------------

# Sized to comfortably hold the ADR-028 OQ-4 cap (10 000 rows * ~140 bytes
# average = 1.4 MB); the 8 MiB ceiling absorbs accidental BOM/whitespace.
MAX_TAG_IMPORT_BYTES = 8 * 1024 * 1024
# Hard cap per ADR-028 OQ 4. Importers above this must chunk client-side.
MAX_TAG_IMPORT_ROWS = 10_000
# How many EPCs the dry-run preview echoes back so the operator can
# eyeball "did I paste the right reel?" without scrolling 10 000 rows.
TAG_IMPORT_SAMPLE_SIZE = 10
# Operation tag for the confirmation-token store. If we ever add a
# second bulk endpoint sharing the same store (bulk PATCH in C4,
# transfers in C3), each gets its own constant so the store's
# operation-mismatch guard catches cross-endpoint token reuse.
_IMPORT_OPERATION = "tags.import"


def _content_hash(epc_hexes: list[str]) -> str:
    """Stable hash of the canonical EPC set in a CSV.

    Sorted + joined so re-ordered rows hash identically (operators
    legitimately re-sort spreadsheets between dry-run and commit).
    Duplicate-within-CSV is already a 422 in :func:`parse_tag_import_csv`,
    so this list is unique by construction.
    """
    payload = "\n".join(sorted(epc_hexes)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@router.post(
    "/tags/import",
    response_model=TagImportResult,
)
async def import_tags(
    upload: UploadFile = File(
        ...,
        description=(
            "CSV with required column 'epc_hex'. Extra columns are ignored."
            " Max 10 000 rows per import (413 above). Max 10 imports/hour"
            " per tenant (configurable via tenants.tag_bulk_import_rate_limit)."
        ),
    ),
    dry_run: bool = Query(
        default=False,
        description=(
            "Preview mode. Validate the CSV and (on success) mint a"
            " single-use confirmation token bound to this CSV's content,"
            " tenant, and operator. Re-submit the same CSV with"
            " ?confirm=<token> to apply. Per ADR 028 §Governance #2"
            " every bulk op is dry-run-first."
        ),
    ),
    confirm: str | None = Query(
        default=None,
        description=(
            "Confirmation token from a prior successful dry-run."
            " Mutually exclusive with dry_run=true. The token binds to"
            " (tenant, user, CSV content) — confirming a different CSV"
            " with the same token returns 409."
        ),
    ),
    response: Response = None,  # type: ignore[assignment]
    user: AuthenticatedUser = require_role("admin", "editor"),
    session: AsyncSession = Depends(get_session),
) -> TagImportResult:
    """Bulk-register tags from a CSV.

    Per ADR-028 OQ 4 + §Governance #2:

    - **400** if neither ``dry_run`` nor ``confirm`` is supplied, or
      if both are supplied (mutually exclusive — preview first, then
      commit with the returned token).
    - **413** if file >8 MiB *or* row count >10 000.
    - **429** if the tenant has already issued
      ``tag_bulk_import_rate_limit`` imports in the trailing hour.
      The counter advances *before* parsing so a malformed CSV
      still counts toward the cap (catches the runaway-script
      threat model exactly). Dry-runs and confirms each consume
      one slot — the cap is on operator activity, not on writes.
    - **422** if any row fails validation. Per the all-or-nothing
      rule nothing is written and no token is minted; the response
      body lists every offending row.
    - **200** on a successful ``dry_run=true``. The response
      includes ``token``, ``expires_in``, and a 10-EPC ``sample``.
    - **409** if ``?confirm=<token>`` is supplied but the token
      doesn't match this CSV (content drift, wrong operator,
      wrong tenant, expired, or already consumed).
    - **202** on a confirmed import whose row count meets or
      exceeds ``tenants.tag_bulk_two_person_threshold`` (default
      10 000) per ADR 028 §Governance #4. The CSV is stashed in
      ``pending_bulk_operations`` and ``pending_id`` is returned;
      a second admin must POST ``/bulk-operations/{pending_id}/approve``
      to execute. Nothing is written to ``tags`` yet.
    - **201** on a successful confirmed import below the threshold.

    Every confirmed import writes one ``tag.bulk_imported`` audit
    log entry covering the whole batch. Phase C5 unifies this
    with the other bulk-op audit shapes; the keys we already emit
    (``count``, ``request_id``) are forward-compatible.
    """
    # --- 0. Reject mutually-exclusive / missing intent ---
    if dry_run and confirm is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": (
                    "dry_run and confirm are mutually exclusive;"
                    " preview first, then submit ?confirm=<token>"
                ),
            },
        )
    if not dry_run and confirm is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": (
                    "confirmation required: POST with ?dry_run=true first,"
                    " then re-POST with ?confirm=<token> (ADR 028"
                    " §Governance #2)"
                ),
            },
        )

    # --- 1. Per-tenant hourly counter (advance before parsing) ---
    tenant_row = (
        await session.execute(select(TenantModel).where(TenantModel.id == user.tenant_id))
    ).scalar_one()
    cap = tenant_row.tag_bulk_import_rate_limit
    if not TAG_IMPORT_LIMITER.check_and_record(user.tenant_id, cap):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "message": "tag-import rate limit exceeded for this tenant",
                "limit_per_hour": cap,
            },
        )

    # --- 2. Read + size cap ---
    raw = await upload.read()
    if len(raw) > MAX_TAG_IMPORT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "message": "CSV exceeds maximum size",
                "max_bytes": MAX_TAG_IMPORT_BYTES,
                "actual_bytes": len(raw),
            },
        )

    # --- 3. Parse + per-row validate ---
    valid_rows, errors = parse_tag_import_csv(raw)
    total = len(valid_rows) + len(errors)
    if total > MAX_TAG_IMPORT_ROWS:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "message": "CSV exceeds maximum row count",
                "max_rows": MAX_TAG_IMPORT_ROWS,
                "actual_rows": total,
            },
        )

    # --- 4. All-or-nothing: any error -> 422, nothing written, no token ---
    if errors:
        if response is not None:
            response.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        return TagImportResult(
            rows_total=total,
            rows_created=0,
            rows_skipped=0,
            dry_run=dry_run,
            errors=errors,
        )

    epc_hexes = [r.epc_hex for r in valid_rows]
    content_hash = _content_hash(epc_hexes)
    sample = epc_hexes[:TAG_IMPORT_SAMPLE_SIZE]

    # --- 5. Dry-run success: 200, mint token, no write ---
    if dry_run:
        token, expires_in = BULK_CONFIRMATION_TOKENS.mint(
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            operation=_IMPORT_OPERATION,
            content_hash=content_hash,
            ttl_seconds=DEFAULT_TTL_SECONDS,
        )
        return TagImportResult(
            rows_total=total,
            rows_created=len(valid_rows),
            rows_skipped=0,
            dry_run=True,
            errors=[],
            token=token,
            expires_in=expires_in,
            sample=sample,
        )

    # --- 6. Confirm path: validate the token against this CSV ---
    assert confirm is not None  # narrowed by step 0
    outcome = BULK_CONFIRMATION_TOKENS.consume(
        confirm,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        operation=_IMPORT_OPERATION,
        content_hash=content_hash,
    )
    if outcome is not ConfirmationOutcome.OK:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    "confirmation token did not match this submission;"
                    " re-run with ?dry_run=true to mint a fresh token"
                ),
                "reason": outcome.value,
            },
        )

    # --- 7a. Two-person rule (ADR 028 §Governance #4) ---
    # If this CSV is at or above the tenant's threshold, don't
    # execute now — persist a pending row and return 202. The
    # second admin completes the op via POST /bulk-operations/{id}/approve.
    threshold = tenant_row.tag_bulk_two_person_threshold
    if len(valid_rows) >= threshold:
        pending = await pending_ops.create_pending(
            session,
            tenant_id=user.tenant_id,
            operation=_IMPORT_OPERATION,
            requested_by=user.user_id,
            content_hash=content_hash,
            row_count=len(valid_rows),
            sample=sample,
            payload=raw,
        )
        await AuditLogger(session=session).log(
            user.tenant_id,
            "tag.bulk_import_requested",
            "pending_bulk_operation",
            pending.id,
            changes={
                "rows_total": total,
                "row_count": len(valid_rows),
                "threshold": threshold,
                "operation": _IMPORT_OPERATION,
                "confirmation_token": confirm,
                "content_hash": content_hash,
            },
            user_id=user.user_id,
        )
        if response is not None:
            response.status_code = status.HTTP_202_ACCEPTED
        return TagImportResult(
            rows_total=total,
            rows_created=0,
            rows_skipped=0,
            dry_run=False,
            errors=[],
            token=confirm,
            sample=sample,
            requires_approval=True,
            pending_id=pending.id,
        )

    # --- 7b. Sub-threshold: real import, 201, single bulk insert ---
    created, skipped, request_id = await _execute_tag_import(
        session,
        tenant_id=user.tenant_id,
        epc_hexes=epc_hexes,
        actor_user_id=user.user_id,
        confirmation_token=confirm,
        approved_by=None,
        pending_id=None,
    )
    if response is not None:
        response.status_code = status.HTTP_201_CREATED
    return TagImportResult(
        rows_total=total,
        rows_created=created,
        rows_skipped=skipped,
        dry_run=False,
        errors=[],
        token=confirm,
        sample=sample,
    )


async def _execute_tag_import(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    epc_hexes: list[str],
    actor_user_id: uuid.UUID | None,
    confirmation_token: str | None,
    approved_by: uuid.UUID | None,
    pending_id: uuid.UUID | None,
    request_id: uuid.UUID | None = None,
) -> tuple[int, int, uuid.UUID]:
    """Perform the actual CSV-backed bulk insert + audit-log entry.

    Shared between the direct sub-threshold path (called from
    :func:`import_tags`) and the two-person approve path (called
    from :mod:`tagpulse.api.routes.bulk_operations` via the
    ``tags.import`` executor registered below). Keeps the audit
    log shape in one place so Phase C5's unified shape is a one-line
    change.
    """
    repo = _repo(session)
    created, skipped = await repo.bulk_create(tenant_id, epc_hexes)
    rid = request_id or uuid.uuid4()
    changes: dict[str, Any] = {
        "rows_total": len(epc_hexes),
        "rows_created": created,
        "rows_skipped": skipped,
        "request_id": str(rid),
        "source": "csv_import",
        "confirmation_token": confirmation_token,
    }
    if approved_by is not None:
        changes["approved_by"] = str(approved_by)
    if pending_id is not None:
        changes["pending_id"] = str(pending_id)
    await AuditLogger(session=session).log(
        tenant_id,
        "tag.bulk_imported",
        "tag",
        rid,
        changes=changes,
        user_id=actor_user_id,
    )
    return created, skipped, rid


async def _tag_import_executor(
    session: AsyncSession,
    row: PendingBulkOperationModel,
    request_id: uuid.UUID,
) -> dict[str, Any]:
    """Pending-bulk-op executor for ``tags.import``.

    Called from :func:`pending_ops.approve` after the approval-side
    invariants pass (not-self-approval, not-expired, content not
    tampered). Re-parses the stored CSV bytes and runs the same
    bulk insert path as the direct route. ``row.requested_by`` is
    the audit actor (the original requester is the one whose intent
    the system is honouring); ``approved_by`` is plumbed through as
    a separate audit-log key.
    """
    valid_rows, errors = parse_tag_import_csv(row.payload)
    if errors:
        # Should be impossible — the pending row only exists because
        # the original parse was clean and the content hash matched
        # on approve. Surface loudly if it ever fires.
        raise RuntimeError(f"pending tag-import payload failed re-parse: {len(errors)} errors")
    epc_hexes = [r.epc_hex for r in valid_rows]
    created, skipped, _ = await _execute_tag_import(
        session,
        tenant_id=row.tenant_id,
        epc_hexes=epc_hexes,
        actor_user_id=row.requested_by,
        confirmation_token=None,
        approved_by=row.decided_by,
        pending_id=row.id,
        request_id=request_id,
    )
    return {
        "rows_created": created,
        "rows_skipped": skipped,
        "request_id": str(request_id),
    }


# Register the executor at import time so /bulk-operations/{id}/approve
# can find it as soon as the app starts. Sprint 50 C4 will add
# ``tags.bulk_patch`` / ``tags.bulk_retire`` next to this line.
pending_ops.register_executor(_IMPORT_OPERATION, _tag_import_executor)


def import_payload_content_hash(payload: bytes) -> str:
    """Re-hash a stored CSV payload for the approve-path tamper guard.

    Exposed for :mod:`tagpulse.api.routes.bulk_operations` (which
    passes it as ``content_hasher`` into :func:`pending_ops.approve`)
    so the hashing logic lives next to :func:`_content_hash` rather
    than being duplicated in the bulk-operations module.
    """
    valid_rows, _errors = parse_tag_import_csv(payload)
    return _content_hash([r.epc_hex for r in valid_rows])


# ---------------------------------------------------------------------------
# Transfer endpoints
# ---------------------------------------------------------------------------


async def _resolve_tenant_by_slug(session: AsyncSession, slug: str) -> TenantModel:
    stmt = select(TenantModel).where(TenantModel.slug == slug)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None or row.status != "active":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Receiving tenant '{slug}' not found",
        )
    return row


@router.post(
    "/tag-transfers",
    response_model=list[TagTransferResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_tag_transfer(
    body: TagTransferRequest,
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> list[TagTransferResponse]:
    """Initiate a cross-tenant transfer.

    Validation:
      - Every EPC must be owned by the calling tenant and in
        ``status='active'`` (only active tags can transfer out;
        ``registered`` tags haven't been observed yet and
        terminal-state tags can't move).
      - The receiving tenant must exist and be ``active``.
      - Self-transfers (``from == to``) are rejected.

    On success, writes one ``tag_transfers`` row per EPC, all
    sharing one server-generated ``request_id``, in
    ``status='requested'``. Phase B does **not** flip the source
    tag's status — that happens at acknowledgement / completion in
    the receiving-tenant flow (Phase C3).
    """
    to_tenant = await _resolve_tenant_by_slug(session, body.to_tenant_slug)
    if to_tenant.id == user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cannot transfer tags to the same tenant",
        )
    if user.user_id is None:
        # require_role("admin") guarantees a service-principal style
        # caller is rare here; we still defend against the typed
        # ``UUID | None`` to keep the FK insert safe.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="anonymous admin tokens cannot initiate transfers",
        )

    normalised = [normalize_epc_hex(epc) for epc in body.epcs]
    if len(set(normalised)) != len(normalised):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="duplicate EPCs in request",
        )

    repo = _repo(session)
    missing: list[str] = []
    not_active: list[str] = []
    for epc in normalised:
        tag = await repo.get_by_epc(user.tenant_id, epc)
        if tag is None:
            missing.append(epc)
        elif tag.status != "active":
            not_active.append(epc)
    if missing or not_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "one or more EPCs are not eligible for transfer",
                "not_found": missing,
                "not_active": not_active,
            },
        )

    rows = await _transfer_repo(session).create_request(
        from_tenant_id=user.tenant_id,
        to_tenant_id=to_tenant.id,
        epcs=normalised,
        requested_by=user.user_id,
    )
    await AuditLogger(session=session).log(
        user.tenant_id,
        "tag_transfer.requested",
        "tag_transfer",
        rows[0].request_id,
        changes={
            "to_tenant_id": str(to_tenant.id),
            "to_tenant_slug": to_tenant.slug,
            "epc_count": len(rows),
        },
        user_id=user.user_id,
    )
    return rows


@router.get("/tag-transfers", response_model=list[TagTransferResponse])
async def list_tag_transfers(
    direction: Annotated[str | None, Query(pattern="^(in|out)$")] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> list[TagTransferResponse]:
    return await _transfer_repo(session).list_for_tenant(
        tenant.id,
        direction=direction,
        status=status_filter,
        limit=limit,
        offset=offset,
    )


@router.get("/tag-transfers/{transfer_id}", response_model=TagTransferResponse)
async def get_tag_transfer(
    transfer_id: uuid.UUID,
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> TagTransferResponse:
    row = await _transfer_repo(session).get(tenant.id, transfer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Tag transfer not found")
    return row
