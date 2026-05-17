"""Tenant-scoped Category CRUD (Sprint 34, [ADR-019](../../../../docs/adr/019-categories.md)).

Endpoints (current-tenant scope only — no global admin variant this
sprint):

- ``GET /categories``         — any role (viewer+). Paginated list of
                                the calling tenant's categories.
- ``POST /categories``        — editor / admin. Creates a category.
                                ``category_type`` is required and
                                immutable thereafter.
- ``GET /categories/{id}``    — any role. Returns one category.
- ``PATCH /categories/{id}``  — editor / admin. Partial update. Any
                                attempt to change ``category_type``
                                is rejected with 400 ("category_type
                                is immutable") per the ADR.
- ``DELETE /categories/{id}`` — admin only. Fails with 409 if any
                                asset still references the category;
                                payload includes the referencing
                                count so the UI can guide the user.

ADR 019 originally specified ``/v1/tenants/{slug}/categories`` paths.
This codebase does not currently version routes and routes tenant
scope through ``get_current_tenant`` rather than the URL (see also
``tenant_branding.py``); the deviation matches the established
pattern.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.core.audit import AuditLogger
from tagpulse.core.tenant_auth import Tenant, get_current_tenant
from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.models.schemas import (
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
)
from tagpulse.repositories.timescaledb.categories import (
    CategoryInUseError,
    CategoryNameConflictError,
    TimescaleCategoryRepository,
)
from tagpulse.repositories.timescaledb.session import get_session

router = APIRouter(tags=["categories"])


def _repo(session: AsyncSession) -> TimescaleCategoryRepository:
    return TimescaleCategoryRepository(session)


def _diff(old: CategoryResponse, new: CategoryResponse) -> dict[str, dict[str, object]]:
    fields = ("name", "sku_upc", "description", "required_pixels")
    changes: dict[str, dict[str, object]] = {}
    for field in fields:
        old_value = getattr(old, field)
        new_value = getattr(new, field)
        if old_value != new_value:
            changes[field] = {"from": old_value, "to": new_value}
    return changes


@router.get("/categories", response_model=list[CategoryResponse])
async def list_categories(
    category_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> list[CategoryResponse]:
    """List the calling tenant's categories."""
    return await _repo(session).list_for_tenant(
        tenant.id, category_type=category_type, limit=limit, offset=offset
    )


@router.post(
    "/categories",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_category(
    body: CategoryCreate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    session: AsyncSession = Depends(get_session),
) -> CategoryResponse:
    """Create a new category (editor / admin). ``category_type`` is set here and
    cannot be changed later."""
    try:
        created = await _repo(session).create(user.tenant_id, body)
    except CategoryNameConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await AuditLogger(session=session).log(
        user.tenant_id,
        "category.created",
        "category",
        created.id,
        changes={
            "name": created.name,
            "category_type": created.category_type,
            "required_pixels": created.required_pixels,
            "sku_upc": created.sku_upc,
        },
        user_id=user.user_id,
    )
    return created


@router.get("/categories/{category_id}", response_model=CategoryResponse)
async def get_category(
    category_id: uuid.UUID,
    tenant: Tenant = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> CategoryResponse:
    """Get one category by id."""
    row = await _repo(session).get(tenant.id, category_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Category not found")
    return row


@router.patch("/categories/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: uuid.UUID,
    body: CategoryUpdate,
    user: AuthenticatedUser = require_role("admin", "editor"),
    session: AsyncSession = Depends(get_session),
) -> CategoryResponse:
    """Partial update. ``category_type`` is immutable — any attempt to
    change it is rejected with 400."""
    # Pydantic drops unknown fields by default; defend against the
    # caller smuggling category_type in via model_extra or future
    # schema changes by checking the raw payload.
    raw = body.model_dump(exclude_unset=True)
    if "category_type" in raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="category_type is immutable",
        )
    repo = _repo(session)
    before = await repo.get(user.tenant_id, category_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Category not found")
    try:
        updated = await repo.update(user.tenant_id, category_id, body)
    except CategoryNameConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    # Repo returned ``None`` would mean the row vanished between get
    # and update; treat as 404 rather than 500.
    if updated is None:
        raise HTTPException(status_code=404, detail="Category not found")
    changes = _diff(before, updated)
    if changes:
        await AuditLogger(session=session).log(
            user.tenant_id,
            "category.updated",
            "category",
            updated.id,
            changes=changes,
            user_id=user.user_id,
        )
    return updated


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: uuid.UUID,
    user: AuthenticatedUser = require_role("admin"),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a category. Admin only. 409 if any asset still
    references it; the payload includes the count so the UI can show
    a guarded confirmation flow."""
    repo = _repo(session)
    try:
        deleted = await repo.delete(user.tenant_id, category_id)
    except CategoryInUseError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "category is referenced by one or more assets",
                "asset_count": exc.asset_count,
            },
        ) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Category not found")
    await AuditLogger(session=session).log(
        user.tenant_id,
        "category.deleted",
        "category",
        category_id,
        user_id=user.user_id,
    )
