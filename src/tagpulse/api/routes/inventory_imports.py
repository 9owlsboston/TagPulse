"""Bulk CSV import endpoints (Sprint 15b Phase E).

Streams a CSV upload, validates each row, and creates rows row-by-row.
Returns a JSON summary so callers can surface successes / errors in the UI.

Endpoints (admin-only — bulk catalog onboarding is a privileged op):
- ``POST /products/import``           — header: sku,gtin,name,category,unit
- ``POST /lots/import``               — header: product_sku,lot_code,manufactured_at,expires_at
- ``POST /stock-items/import``        — header: product_sku,lot_code,binding_value,binding_kind
                                       Includes optional preflight collision check
                                       (``?preflight=true``) that reports cross-tenant
                                       collisions without writing any rows.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ValidationError

from tagpulse.api.dependencies import get_inventory_service
from tagpulse.api.services.inventory_service import (
    InventoryService,
    ProductNotFoundError,
)
from tagpulse.core.user_auth import AuthenticatedUser, require_role
from tagpulse.models.schemas import (
    LotCreate,
    ProductCreate,
    StockItemCreate,
)
from tagpulse.repositories.timescaledb.assets import (
    TimescaleAssetTagBindingRepository,
)
from tagpulse.repositories.timescaledb.session import get_session

logger = logging.getLogger(__name__)
router = APIRouter(tags=["inventory"])

MAX_CSV_BYTES = 5 * 1024 * 1024  # 5 MiB upload cap
MAX_ROWS = 10_000


class RowError(BaseModel):
    """Per-row error description."""

    row: int
    sku: str | None = None
    error: str


class ImportSummary(BaseModel):
    rows_total: int
    rows_created: int
    rows_skipped: int
    errors: list[RowError]


class CollisionPreflightRow(BaseModel):
    row: int
    binding_value: str
    other_tenant_collisions: int


class CollisionPreflight(BaseModel):
    rows_total: int
    collisions: list[CollisionPreflightRow]


async def _read_csv(upload: UploadFile) -> list[dict[str, str]]:
    raw = await upload.read()
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"CSV exceeds {MAX_CSV_BYTES} byte cap",
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400, detail=f"CSV is not valid UTF-8: {exc}"
        ) from None
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if len(rows) > MAX_ROWS:
        raise HTTPException(
            status_code=413,
            detail=f"CSV exceeds {MAX_ROWS} row cap (got {len(rows)})",
        )
    return rows


def _norm(s: str | None) -> str | None:
    if s is None:
        return None
    s = s.strip()
    return s if s else None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid ISO timestamp: {value!r}") from exc


@router.post("/products/import", response_model=ImportSummary)
async def import_products(
    upload: UploadFile = File(..., description="CSV: sku,gtin,name,category,unit"),
    user: AuthenticatedUser = require_role("admin"),
    service: InventoryService = Depends(get_inventory_service),
) -> ImportSummary:
    rows = await _read_csv(upload)
    created = 0
    skipped = 0
    errors: list[RowError] = []
    for idx, row in enumerate(rows, start=1):
        sku = _norm(row.get("sku"))
        try:
            payload = ProductCreate(
                sku=sku or "",
                gtin=_norm(row.get("gtin")),
                name=_norm(row.get("name")) or "",
                category=_norm(row.get("category")),
                unit=(_norm(row.get("unit")) or "each"),  # type: ignore[arg-type]
            )
        except ValidationError as exc:
            errors.append(RowError(row=idx, sku=sku, error=str(exc)))
            continue
        try:
            await service.create_product(user.tenant_id, user.user_id, payload)
            created += 1
        except ValueError as exc:
            # Duplicate SKU — treat as skip, not error (idempotent re-import).
            skipped += 1
            logger.debug("product import skip row %d: %s", idx, exc)
    return ImportSummary(
        rows_total=len(rows),
        rows_created=created,
        rows_skipped=skipped,
        errors=errors,
    )


@router.post("/lots/import", response_model=ImportSummary)
async def import_lots(
    upload: UploadFile = File(
        ..., description="CSV: product_sku,lot_code,manufactured_at,expires_at"
    ),
    user: AuthenticatedUser = require_role("admin"),
    service: InventoryService = Depends(get_inventory_service),
) -> ImportSummary:
    rows = await _read_csv(upload)
    # Pre-resolve unique product_skus to product_ids.
    sku_to_id: dict[str, UUID | None] = {}
    products = await service.list_products(user.tenant_id, limit=1000, offset=0)
    by_sku = {p.sku: p.id for p in products}
    created = 0
    skipped = 0
    errors: list[RowError] = []
    for idx, row in enumerate(rows, start=1):
        sku = _norm(row.get("product_sku"))
        lot_code = _norm(row.get("lot_code"))
        if not sku or not lot_code:
            errors.append(
                RowError(
                    row=idx,
                    sku=sku,
                    error="product_sku and lot_code are required",
                )
            )
            continue
        product_id = sku_to_id.get(sku) if sku in sku_to_id else by_sku.get(sku)
        sku_to_id[sku] = product_id
        if product_id is None:
            errors.append(
                RowError(
                    row=idx, sku=sku, error=f"unknown product_sku: {sku}"
                )
            )
            continue
        try:
            manufactured_at = _parse_dt(_norm(row.get("manufactured_at")))
            expires_at = _parse_dt(_norm(row.get("expires_at")))
            payload = LotCreate(
                lot_code=lot_code,
                manufactured_at=manufactured_at,
                expires_at=expires_at,
            )
        except (ValueError, ValidationError) as exc:
            errors.append(RowError(row=idx, sku=sku, error=str(exc)))
            continue
        try:
            await service.create_lot(
                user.tenant_id, user.user_id, product_id, payload
            )
            created += 1
        except ProductNotFoundError:
            errors.append(
                RowError(
                    row=idx, sku=sku, error=f"product not found: {sku}"
                )
            )
        except ValueError as exc:
            skipped += 1
            logger.debug("lot import skip row %d: %s", idx, exc)
    return ImportSummary(
        rows_total=len(rows),
        rows_created=created,
        rows_skipped=skipped,
        errors=errors,
    )


@router.post(
    "/stock-items/import",
    response_model=ImportSummary | CollisionPreflight,
)
async def import_stock_items(
    upload: UploadFile = File(
        ...,
        description=(
            "CSV: product_sku,lot_code,binding_value,binding_kind"
        ),
    ),
    preflight: bool = Query(
        default=False,
        description="If true, return cross-tenant collision report and create nothing.",
    ),
    user: AuthenticatedUser = require_role("admin"),
    service: InventoryService = Depends(get_inventory_service),
    session: Any = Depends(get_session),
) -> ImportSummary | CollisionPreflight:
    rows = await _read_csv(upload)
    products = await service.list_products(user.tenant_id, limit=1000, offset=0)
    by_sku = {p.sku: p.id for p in products}
    binding_repo = TimescaleAssetTagBindingRepository(session)

    if preflight:
        collisions: list[CollisionPreflightRow] = []
        for idx, row in enumerate(rows, start=1):
            binding_value = _norm(row.get("binding_value"))
            if not binding_value:
                continue
            count = await binding_repo.count_other_tenant_collisions(
                user.tenant_id, binding_value
            )
            if count > 0:
                collisions.append(
                    CollisionPreflightRow(
                        row=idx,
                        binding_value=binding_value,
                        other_tenant_collisions=count,
                    )
                )
        return CollisionPreflight(rows_total=len(rows), collisions=collisions)

    created = 0
    skipped = 0
    errors: list[RowError] = []
    # product_sku -> {lot_code -> lot_id}
    lot_cache: dict[UUID, dict[str, UUID]] = {}
    for idx, row in enumerate(rows, start=1):
        sku = _norm(row.get("product_sku"))
        binding_value = _norm(row.get("binding_value"))
        binding_kind = _norm(row.get("binding_kind")) or "epc"
        lot_code = _norm(row.get("lot_code"))
        if not sku or not binding_value:
            errors.append(
                RowError(
                    row=idx,
                    sku=sku,
                    error="product_sku and binding_value are required",
                )
            )
            continue
        product_id = by_sku.get(sku)
        if product_id is None:
            errors.append(
                RowError(
                    row=idx, sku=sku, error=f"unknown product_sku: {sku}"
                )
            )
            continue
        lot_id: UUID | None = None
        if lot_code:
            if product_id not in lot_cache:
                lots = await service.list_lots_for_product(
                    user.tenant_id, product_id, limit=1000, offset=0
                )
                lot_cache[product_id] = {lot.lot_code: lot.id for lot in lots}
            lot_id = lot_cache[product_id].get(lot_code)
            if lot_id is None:
                errors.append(
                    RowError(
                        row=idx,
                        sku=sku,
                        error=f"unknown lot_code for product: {lot_code}",
                    )
                )
                continue
        try:
            payload = StockItemCreate(
                product_id=product_id,
                lot_id=lot_id,
                binding_value=binding_value,
                binding_kind=binding_kind,  # type: ignore[arg-type]
            )
        except ValidationError as exc:
            errors.append(RowError(row=idx, sku=sku, error=str(exc)))
            continue
        try:
            await service.create_stock_item(
                user.tenant_id, user.user_id, payload
            )
            created += 1
        except ProductNotFoundError:
            errors.append(
                RowError(
                    row=idx, sku=sku, error=f"product not found: {sku}"
                )
            )
        except ValueError as exc:
            # Active binding collision in this tenant — skip.
            skipped += 1
            logger.debug("stock-item import skip row %d: %s", idx, exc)
    return ImportSummary(
        rows_total=len(rows),
        rows_created=created,
        rows_skipped=skipped,
        errors=errors,
    )
