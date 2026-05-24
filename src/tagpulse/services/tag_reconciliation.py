"""Tag-registry reconciliation report queries (Sprint 50 Phase E).

Implements the three read-only exception views ratified in
[ADR 028](../../../docs/adr/028-tags-as-first-class-entity.md)
§"Governance" rule #5. Each query is a pure async function over an
``AsyncSession`` — never mutates state. The route layer in
:mod:`tagpulse.api.routes.tags` exposes them at
``GET /v1/tenants/{slug}/tags/reconciliation/{view}`` with optional
CSV export.

The three views:

- ``registered-unread`` — tags in operator-meaningful status
  (``registered`` or ``active``) that have never been seen by the
  registrar worker (``last_seen_at IS NULL``) or whose last
  observation is older than the configured staleness window.
  Surfaces tags that were registered up-front but whose physical
  carriers may be lost / discarded.
- ``unregistered-reading`` — distinct ``tag_id`` values appearing
  in ``tag_reads`` with ``tag_known=FALSE`` inside the lookback
  window. Surfaces EPCs that are reading at the edge but are not
  in the tenant's registry — the operator-onboarding gap that the
  registrar worker reports but never auto-fixes (per ADR 028 OQ 3).
- ``bindings-on-retired`` — ``stock_items`` rows that bind via EPC
  to a registry tag in a terminal status (``retired``,
  ``defective``, ``transferred_out``) and that have not been
  consumed. Surfaces inventory bound to tags the operator has
  decommissioned — a data-integrity signal, not an alert.

A future scheduled worker can call these same functions to emit
OTel gauges per tenant. Phase E ships the API surface only; the
worker is deferred to Phase G or a later sprint per
:doc:`docs/roadmap.md` Sprint 50.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import StockItemModel, TagModel, TagReadModel
from tagpulse.models.schemas import (
    BindingOnRetiredRow,
    RegisteredUnreadRow,
    TagSource,
    TagStatus,
    UnregisteredReadingRow,
)

ReconciliationView = Literal[
    "registered-unread",
    "unregistered-reading",
    "bindings-on-retired",
]

# Terminal statuses for view 3. Mirrors ADR 028 §"Status lifecycle"
# — tags here are no longer expected to read but inventory rows may
# still legitimately point at them while consumption catches up.
_TERMINAL_TAG_STATUSES: tuple[str, ...] = ("retired", "defective", "transferred_out")

# Status values that view 1 considers "should be reading". Excludes
# the terminal set above + ``registered`` rows that have never been
# observed are *expected* to be unread until first scan, but we
# surface them too so the operator can spot reels that were
# registered but never deployed. The route layer's ``days`` filter
# does the heavy lifting on the "should have been seen by now" check.
_LIVE_TAG_STATUSES: tuple[str, ...] = ("registered", "active")


async def query_registered_unread(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    days: int,
    limit: int,
    offset: int,
) -> list[RegisteredUnreadRow]:
    """Tags expected to be reading but aren't.

    A row is included when both hold:

    - ``status`` ∈ ``{registered, active}`` (terminal statuses are
      legitimately silent and excluded).
    - ``last_seen_at IS NULL`` OR ``last_seen_at < now() - days``.

    Order: never-seen first (``NULLS FIRST``), then oldest
    ``last_seen_at``, then ``epc_hex`` for stable pagination.
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)
    stmt = (
        select(TagModel)
        .where(
            TagModel.tenant_id == tenant_id,
            TagModel.status.in_(_LIVE_TAG_STATUSES),
            (TagModel.last_seen_at.is_(None)) | (TagModel.last_seen_at < cutoff),
        )
        .order_by(
            TagModel.last_seen_at.asc().nulls_first(),
            TagModel.epc_hex.asc(),
        )
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [
        RegisteredUnreadRow(
            tag_id=row.id,
            epc_hex=row.epc_hex,
            status=cast(TagStatus, row.status),
            source=cast(TagSource, row.source),
            first_seen_at=row.first_seen_at,
            last_seen_at=row.last_seen_at,
            created_at=row.created_at,
        )
        for row in result.scalars()
    ]


async def query_unregistered_reading(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    days: int,
    limit: int,
    offset: int,
) -> list[UnregisteredReadingRow]:
    """EPCs reading at the edge but absent from the tenant registry.

    Sourced from ``tag_reads`` where the registrar worker has
    classified the row ``tag_known=FALSE``. Grouped by ``tag_id``
    (the EPC the reader emitted) with ``MAX(timestamp)`` and
    ``COUNT(*)``. The lookback ``days`` bounds the scan to a small
    set of recent hypertable chunks; large values may degrade query
    time on busy tenants — operator-visible behaviour, no index
    added by Phase E (deferred to Phase G if measurements warrant).
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)
    stmt = (
        select(
            TagReadModel.tag_id.label("tag_id"),
            func.max(TagReadModel.timestamp).label("last_seen_at"),
            func.count().label("read_count"),
        )
        .where(
            TagReadModel.tenant_id == tenant_id,
            TagReadModel.tag_known.is_(False),
            TagReadModel.timestamp >= cutoff,
        )
        .group_by(TagReadModel.tag_id)
        .order_by(func.max(TagReadModel.timestamp).desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [
        UnregisteredReadingRow(
            tag_id=row.tag_id,
            last_seen_at=row.last_seen_at,
            read_count=row.read_count,
        )
        for row in result.all()
    ]


async def query_bindings_on_retired(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    limit: int,
    offset: int,
) -> list[BindingOnRetiredRow]:
    """Stock items bound via EPC to a tag in a terminal status.

    Joins ``stock_items`` (``binding_kind='epc'``,
    ``consumed_at IS NULL``) to ``tags`` on ``(tenant_id, epc_hex)``
    and filters to terminal tag statuses. Ordered by tag
    ``updated_at`` descending — most-recently-retired tags surface
    first so operators see fresh inconsistencies promptly.
    """
    stmt = (
        select(
            StockItemModel.id.label("stock_item_id"),
            StockItemModel.binding_value.label("epc_hex"),
            StockItemModel.product_id.label("product_id"),
            StockItemModel.lot_id.label("lot_id"),
            StockItemModel.state.label("stock_item_state"),
            TagModel.id.label("tag_id"),
            TagModel.status.label("tag_status"),
            TagModel.updated_at.label("tag_updated_at"),
        )
        .join(
            TagModel,
            and_(
                TagModel.tenant_id == StockItemModel.tenant_id,
                TagModel.epc_hex == StockItemModel.binding_value,
            ),
        )
        .where(
            StockItemModel.tenant_id == tenant_id,
            StockItemModel.binding_kind == "epc",
            StockItemModel.consumed_at.is_(None),
            TagModel.status.in_(_TERMINAL_TAG_STATUSES),
        )
        .order_by(TagModel.updated_at.desc(), StockItemModel.id.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [
        BindingOnRetiredRow(
            stock_item_id=row.stock_item_id,
            epc_hex=row.epc_hex,
            product_id=row.product_id,
            lot_id=row.lot_id,
            stock_item_state=row.stock_item_state,
            tag_id=row.tag_id,
            tag_status=cast(TagStatus, row.tag_status),
            tag_updated_at=row.tag_updated_at,
        )
        for row in result.all()
    ]


# ---------------------------------------------------------------------------
# CSV serialization
# ---------------------------------------------------------------------------

# Header order is part of the CSV contract — operators bind
# spreadsheets to these columns. Adding fields is OK; renaming or
# reordering is a breaking change.
_HEADERS: dict[ReconciliationView, tuple[str, ...]] = {
    "registered-unread": (
        "tag_id",
        "epc_hex",
        "status",
        "source",
        "first_seen_at",
        "last_seen_at",
        "created_at",
    ),
    "unregistered-reading": (
        "tag_id",
        "last_seen_at",
        "read_count",
    ),
    "bindings-on-retired": (
        "stock_item_id",
        "epc_hex",
        "product_id",
        "lot_id",
        "stock_item_state",
        "tag_id",
        "tag_status",
        "tag_updated_at",
    ),
}


def _stringify(value: object) -> str:
    """Render a cell value for CSV output.

    ``datetime`` → ISO-8601 with timezone; ``None`` → empty cell;
    everything else → ``str()``. Mirrors the convention used by the
    existing ``inventory_imports`` export path.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def rows_to_csv(
    view: ReconciliationView,
    rows: Sequence[RegisteredUnreadRow | UnregisteredReadingRow | BindingOnRetiredRow],
) -> str:
    """Serialize reconciliation rows to a CSV string.

    Header order is fixed per view (see ``_HEADERS``). Empty result
    sets still emit the header row so CSV consumers (Excel,
    pandas.read_csv) get a valid schema.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    headers = _HEADERS[view]
    writer.writerow(headers)
    for row in rows:
        payload = row.model_dump()
        writer.writerow([_stringify(payload[h]) for h in headers])
    return buf.getvalue()
