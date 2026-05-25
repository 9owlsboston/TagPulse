"""Dashboard summary aggregate (Sprint 54 Phase 54.3).

One tenant-scoped read powering the operator landing page's KPI
tiles. Eight aggregate queries plus a per-tenant threshold lookup
returned as :class:`DashboardSummary`. Field semantics are
documented on the schema; this module owns the SQL.

Design notes:

- All counts run against the live tables. No caching, no
  materialised view — at p95 page-load frequency the eight
  aggregates fit inside the dashboard SLO budget. If a future
  tenant grows past that we'll cache the slow ones individually,
  not bolt on a global cache.
- ``tag_recon_backlog`` mirrors the three reconciliation views in
  :mod:`tagpulse.services.tag_reconciliation` as ``COUNT(*)``
  subqueries — staleness window fixed at 7 days, matching the
  default the route layer uses for ``registered-unread`` /
  ``unregistered-reading``. The two services share predicate
  semantics; if those drift, update both.
- ``low_stock_count`` reads ``tenants.low_stock_threshold`` and
  computes ``COUNT(DISTINCT product_id)`` over a HAVING clause —
  one round-trip, no Python-side fan-out.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import (
    AlertModel,
    AssetModel,
    DeviceModel,
    StockItemModel,
    TagModel,
    TagReadModel,
    TagTransferModel,
    TenantModel,
)
from tagpulse.models.schemas import DashboardSummary

# Matches the operator-facing default in tag_reconciliation route
# handlers. Bump in lockstep if Sprint 54 follow-ups change the
# staleness window on the reconciliation surface.
_RECON_LOOKBACK_DAYS = 7

# Strict-AND online definition per Sprint 54 design discussion: the
# stringly-typed ``connection_state`` column drifts when MQTT misses
# a disconnect, so we require both fresh ``last_seen`` AND the column
# saying ``connected`` before we light up the tile.
_ONLINE_WINDOW = timedelta(minutes=5)

_READS_WINDOW = timedelta(hours=1)
_ALERTS_WINDOW = timedelta(hours=24)

_TERMINAL_TAG_STATUSES: tuple[str, ...] = ("retired", "defective", "transferred_out")
_LIVE_TAG_STATUSES: tuple[str, ...] = ("registered", "active")


async def get_summary(
    session: AsyncSession,
    tenant_id: uuid.UUID,
) -> DashboardSummary:
    """Compute one ``DashboardSummary`` for the calling tenant.

    Issues nine queries (eight aggregates + one tenant row for the
    low-stock threshold). All filter on ``tenant_id`` — RLS would
    catch a miss, but explicit predicates keep the plans tight and
    let the tests assert isolation without RLS.
    """
    now = datetime.now(UTC)
    online_cutoff = now - _ONLINE_WINDOW
    reads_cutoff = now - _READS_WINDOW
    alerts_cutoff = now - _ALERTS_WINDOW
    recon_cutoff = now - timedelta(days=_RECON_LOOKBACK_DAYS)

    # Tenant row first — controls the low-stock threshold below.
    threshold_stmt = select(TenantModel.low_stock_threshold).where(
        TenantModel.id == tenant_id
    )
    threshold_row = await session.execute(threshold_stmt)
    low_stock_threshold = threshold_row.scalar_one()

    devices_online_stmt = select(func.count()).where(
        DeviceModel.tenant_id == tenant_id,
        DeviceModel.last_seen.is_not(None),
        DeviceModel.last_seen > online_cutoff,
        DeviceModel.connection_state == "connected",
    )
    devices_total_stmt = select(func.count()).where(
        DeviceModel.tenant_id == tenant_id,
    )
    alerts_open_stmt = select(func.count()).where(
        AlertModel.tenant_id == tenant_id,
        AlertModel.status == "open",
        AlertModel.triggered_at > alerts_cutoff,
    )
    reads_per_hour_stmt = select(func.count()).where(
        TagReadModel.tenant_id == tenant_id,
        TagReadModel.timestamp > reads_cutoff,
    )
    assets_active_stmt = select(func.count()).where(
        AssetModel.tenant_id == tenant_id,
        AssetModel.status == "active",
    )
    transfers_stmt = select(func.count()).where(
        TagTransferModel.status == "requested",
        or_(
            TagTransferModel.from_tenant_id == tenant_id,
            TagTransferModel.to_tenant_id == tenant_id,
        ),
    )

    # tag_recon_backlog = sum of the three reconciliation views.
    # Each is COUNT(*) of the same predicates the per-view route
    # uses, so the tile and the drill-down agree.
    registered_unread_stmt = select(func.count()).where(
        TagModel.tenant_id == tenant_id,
        TagModel.status.in_(_LIVE_TAG_STATUSES),
        or_(
            TagModel.last_seen_at.is_(None),
            TagModel.last_seen_at < recon_cutoff,
        ),
    )
    unregistered_reading_stmt = select(
        func.count(func.distinct(TagReadModel.tag_id))
    ).where(
        TagReadModel.tenant_id == tenant_id,
        TagReadModel.tag_known.is_(False),
        TagReadModel.timestamp >= recon_cutoff,
    )
    bindings_on_retired_stmt = (
        select(func.count())
        .select_from(StockItemModel)
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
    )

    # low_stock_count: distinct products with active stock below
    # the tenant threshold. Active = ``state='in_stock' AND
    # consumed_at IS NULL`` — same predicates the inventory UI uses.
    low_stock_inner = (
        select(StockItemModel.product_id)
        .where(
            StockItemModel.tenant_id == tenant_id,
            StockItemModel.state == "in_stock",
            StockItemModel.consumed_at.is_(None),
        )
        .group_by(StockItemModel.product_id)
        .having(func.count() < low_stock_threshold)
        .subquery()
    )
    low_stock_stmt = select(func.count()).select_from(low_stock_inner)

    devices_online = (await session.execute(devices_online_stmt)).scalar_one()
    devices_total = (await session.execute(devices_total_stmt)).scalar_one()
    alerts_open_24h = (await session.execute(alerts_open_stmt)).scalar_one()
    reads_per_hour_now = (await session.execute(reads_per_hour_stmt)).scalar_one()
    assets_active = (await session.execute(assets_active_stmt)).scalar_one()
    tag_transfers_in_flight = (await session.execute(transfers_stmt)).scalar_one()
    registered_unread = (await session.execute(registered_unread_stmt)).scalar_one()
    unregistered_reading = (
        await session.execute(unregistered_reading_stmt)
    ).scalar_one()
    bindings_on_retired = (
        await session.execute(bindings_on_retired_stmt)
    ).scalar_one()
    low_stock_count = (await session.execute(low_stock_stmt)).scalar_one()

    tag_recon_backlog = (
        int(registered_unread) + int(unregistered_reading) + int(bindings_on_retired)
    )

    return DashboardSummary(
        generated_at=now,
        devices_online=int(devices_online),
        devices_total=int(devices_total),
        alerts_open_24h=int(alerts_open_24h),
        reads_per_hour_now=int(reads_per_hour_now),
        assets_active=int(assets_active),
        tag_transfers_in_flight=int(tag_transfers_in_flight),
        tag_recon_backlog=tag_recon_backlog,
        low_stock_count=int(low_stock_count),
    )
