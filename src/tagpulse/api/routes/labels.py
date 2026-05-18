"""Tenant-scoped Label CRUD and entity association.

Sprint 35; implements [ADR-020](../../../../docs/adr/020-labels-first-class.md).

Endpoints (current-tenant scope only — no global admin variant this sprint):

**Catalog**

- ``GET /labels``              — viewer+. Optional ``entity_type`` filter.
- ``POST /labels``             — editor / admin. Creates one catalog row.
- ``GET /labels/{id}``         — viewer+. Returns one catalog row.
- ``PATCH /labels/{id}``       — editor / admin. ``entity_type`` is immutable.
- ``DELETE /labels/{id}``      — admin only. 409 with ``association_count`` if in use.

**Per-entity associations**

- ``GET    /{entity_type}/{id}/labels``                — viewer+.
- ``POST   /{entity_type}/{id}/labels``                — editor / admin.
  409 on cap (30) or duplicate.
- ``DELETE /{entity_type}/{id}/labels/{label_id}``     — editor / admin.

The ``entity_type`` path segment is the *plural* URL form
(``assets`` / ``sites`` / ``zones`` / ``devices`` / ``categories``)
that aligns with the rest of the codebase's REST conventions. The
``LabelModel.entity_type`` column stores the *singular* form
(``asset`` / ``site`` / ``zone`` / ``device`` / ``category``); the
two are bridged by :data:`_ENTITY_TYPE_FROM_URL`.

ADR 020 originally specified ``/v1/tenants/{slug}/...`` paths.
TagPulse does not version routes and threads tenant scope through
``get_current_tenant``; this matches the precedent set by
``categories.py`` and ``tenant_branding.py``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.audit import AuditLogger
from tagpulse.core.otel_metrics import labels_associations_total
from tagpulse.core.tenant_auth import Tenant, get_current_tenant
from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.models.schemas import (
    LabelAssociationCreate,
    LabelAssociationResponse,
    LabelCreate,
    LabelEntityType,
    LabelResponse,
    LabelUpdate,
)
from tagpulse.repositories.timescaledb.labels import (
    LabelCapExceededError,
    LabelInUseError,
    LabelKeyConflictError,
    TimescaleLabelRepository,
)
from tagpulse.repositories.timescaledb.session import get_session

router = APIRouter(tags=["labels"])


# URL segments → DB ``entity_type`` value. Keep singular-form values
# in lockstep with the CHECK constraint in migration 039.
_ENTITY_TYPE_FROM_URL: dict[str, LabelEntityType] = {
    "assets": "asset",
    "sites": "site",
    "zones": "zone",
    "devices": "device",
    "categories": "category",
}


def _repo(session: AsyncSession) -> TimescaleLabelRepository:
    return TimescaleLabelRepository(session)


def _diff(old: LabelResponse, new: LabelResponse) -> dict[str, dict[str, object]]:
    fields = ("key", "color")
    changes: dict[str, dict[str, object]] = {}
    for field in fields:
        old_value = getattr(old, field)
        new_value = getattr(new, field)
        if old_value != new_value:
            changes[field] = {"from": old_value, "to": new_value}
    return changes


# ---------------------------------------------------------------------------
# Catalog endpoints
# ---------------------------------------------------------------------------


@router.get("/labels", response_model=list[LabelResponse])
async def list_labels(
    entity_type: LabelEntityType | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> list[LabelResponse]:
    """List the calling tenant's label catalog rows."""
    return await _repo(session).list_for_tenant(
        tenant.id, entity_type=entity_type, limit=limit, offset=offset
    )


@router.post(
    "/labels",
    response_model=LabelResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_label(
    body: LabelCreate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    session: AsyncSession = Depends(get_session),
) -> LabelResponse:
    """Create a new label catalog row. ``entity_type`` is fixed here
    and cannot be changed later."""
    try:
        created = await _repo(session).create(user.tenant_id, body, user_id=user.user_id)
    except LabelKeyConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await AuditLogger(session=session).log(
        user.tenant_id,
        "label.created",
        "label",
        created.id,
        changes={
            "entity_type": created.entity_type,
            "key": created.key,
            "color": created.color,
        },
        user_id=user.user_id,
    )
    return created


@router.get("/labels/{label_id}", response_model=LabelResponse)
async def get_label(
    label_id: uuid.UUID,
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> LabelResponse:
    """Get one label by id."""
    row = await _repo(session).get(tenant.id, label_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Label not found")
    return row


@router.patch("/labels/{label_id}", response_model=LabelResponse)
async def update_label(
    label_id: uuid.UUID,
    body: LabelUpdate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    session: AsyncSession = Depends(get_session),
) -> LabelResponse:
    """Partial update. ``entity_type`` is immutable per ADR 020.

    Pydantic drops unknown fields by default; the explicit check
    against the raw payload defends against future schema changes
    that might inadvertently re-add the field.
    """
    raw = body.model_dump(exclude_unset=True)
    if "entity_type" in raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="entity_type is immutable",
        )
    repo = _repo(session)
    before = await repo.get(user.tenant_id, label_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Label not found")
    try:
        updated = await repo.update(user.tenant_id, label_id, body, user_id=user.user_id)
    except LabelKeyConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if updated is None:
        # Row vanished between get and update — race window, treat
        # as 404 rather than 500.
        raise HTTPException(status_code=404, detail="Label not found")
    changes = _diff(before, updated)
    if changes:
        await AuditLogger(session=session).log(
            user.tenant_id,
            "label.updated",
            "label",
            updated.id,
            changes=changes,
            user_id=user.user_id,
        )
    return updated


@router.delete("/labels/{label_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_label(
    label_id: uuid.UUID,
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a label. Admin only. 409 if any entity still references
    it; payload includes ``association_count`` so the UI can render
    a guarded confirmation flow ("This label is in use on N items.
    Detach them first.")."""
    repo = _repo(session)
    try:
        deleted = await repo.delete(user.tenant_id, label_id)
    except LabelInUseError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "label is referenced by one or more entities",
                "association_count": exc.association_count,
            },
        ) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Label not found")
    await AuditLogger(session=session).log(
        user.tenant_id,
        "label.deleted",
        "label",
        label_id,
        user_id=user.user_id,
    )


# ---------------------------------------------------------------------------
# Per-entity association endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{entity_segment}/{entity_id}/labels",
    response_model=list[LabelAssociationResponse],
)
async def list_entity_labels(
    entity_segment: str,
    entity_id: uuid.UUID,
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> list[LabelAssociationResponse]:
    """List all label-value pairs attached to one entity."""
    entity_type = _ENTITY_TYPE_FROM_URL.get(entity_segment)
    if entity_type is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown entity kind")
    return await _repo(session).list_for_entity(tenant.id, entity_type, entity_id)


@router.post(
    "/{entity_segment}/{entity_id}/labels",
    response_model=LabelAssociationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def associate_label(
    entity_segment: str,
    entity_id: uuid.UUID,
    body: LabelAssociationCreate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    session: AsyncSession = Depends(get_session),
) -> LabelAssociationResponse:
    """Attach a label-value pair to an entity. The label is identified
    by ``key`` (scoped to the URL's entity_type); a 404 is returned
    if no matching catalog row exists. 409 on cap (30) or duplicate
    association."""
    entity_type = _ENTITY_TYPE_FROM_URL.get(entity_segment)
    if entity_type is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown entity kind")
    repo = _repo(session)
    label = await repo.find_by_key(user.tenant_id, entity_type, body.key)
    if label is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Label '{body.key}' not found for entity_type '{entity_type}'",
        )
    try:
        association = await repo.associate(
            user.tenant_id,
            entity_type,
            entity_id,
            label=label,
            value=body.value,
            user_id=user.user_id,
        )
    except LabelCapExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "entity already has the maximum number of labels",
                "cap": LabelCapExceededError.CAP,
            },
        ) from exc
    except LabelKeyConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    labels_associations_total.add(1, {"tenant_id": str(user.tenant_id), "entity_type": entity_type})
    await AuditLogger(session=session).log(
        user.tenant_id,
        "label.associated",
        "entity_label",
        label.id,
        changes={
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "key": label.key,
            "value": body.value,
        },
        user_id=user.user_id,
    )
    return association


@router.delete(
    "/{entity_segment}/{entity_id}/labels/{label_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def disassociate_label(
    entity_segment: str,
    entity_id: uuid.UUID,
    label_id: uuid.UUID,
    user: AuthenticatedUser = require_role("admin", "editor"),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Remove a label-value pair from an entity."""
    entity_type = _ENTITY_TYPE_FROM_URL.get(entity_segment)
    if entity_type is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown entity kind")
    repo = _repo(session)
    deleted = await repo.disassociate(user.tenant_id, entity_type, entity_id, label_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Association not found")
    await AuditLogger(session=session).log(
        user.tenant_id,
        "label.disassociated",
        "entity_label",
        label_id,
        changes={"entity_type": entity_type, "entity_id": str(entity_id)},
        user_id=user.user_id,
    )
