"""Dashboard summary aggregate (Sprint 54 Phase 54.3).

One tenant-scoped read powering the operator landing page's KPI
tiles. Eleven aggregate queries plus a per-tenant config lookup
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

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import (
    AlertModel,
    AssetModel,
    DeviceModel,
    SiteModel,
    StockItemModel,
    TagModel,
    TagReadModel,
    TagTransferModel,
    TenantModel,
    ZoneModel,
)
from tagpulse.models.schemas import (
    DashboardSparklines,
    DashboardSummary,
    SparklinePoint,
    SparklineSeries,
)

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

    Issues twelve queries (eleven aggregates + one tenant row for the
    low-stock threshold and tag-counting mode). All filter on
    ``tenant_id`` — RLS would catch a miss, but explicit predicates
    keep the plans tight and let the tests assert isolation without
    RLS.
    """
    now = datetime.now(UTC)
    online_cutoff = now - _ONLINE_WINDOW
    reads_cutoff = now - _READS_WINDOW
    alerts_cutoff = now - _ALERTS_WINDOW
    recon_cutoff = now - timedelta(days=_RECON_LOOKBACK_DAYS)

    # Tenant row first — controls the low-stock threshold + the
    # tag-counting predicate selected for ``tags_total`` below.
    tenant_stmt = select(
        TenantModel.low_stock_threshold,
        TenantModel.dashboard_tags_count_mode,
    ).where(TenantModel.id == tenant_id)
    low_stock_threshold, tags_count_mode = (await session.execute(tenant_stmt)).one()

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
    unregistered_reading_stmt = select(func.count(func.distinct(TagReadModel.tag_id))).where(
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

    # tags_total — predicate picked by tenant config. Default ``"live"``
    # matches the Tags page's default filter; ``"all"`` and
    # ``"non_terminal"`` are the documented alternatives.
    tags_total_stmt = select(func.count()).where(TagModel.tenant_id == tenant_id)
    if tags_count_mode == "live":
        tags_total_stmt = tags_total_stmt.where(TagModel.status.in_(_LIVE_TAG_STATUSES))
    elif tags_count_mode == "non_terminal":
        tags_total_stmt = tags_total_stmt.where(TagModel.status.notin_(_TERMINAL_TAG_STATUSES))
    # ``"all"`` falls through with no extra predicate.

    sites_total_stmt = select(func.count()).where(SiteModel.tenant_id == tenant_id)
    zones_total_stmt = select(func.count()).where(ZoneModel.tenant_id == tenant_id)

    devices_online = (await session.execute(devices_online_stmt)).scalar_one()
    devices_total = (await session.execute(devices_total_stmt)).scalar_one()
    alerts_open_24h = (await session.execute(alerts_open_stmt)).scalar_one()
    reads_per_hour_now = (await session.execute(reads_per_hour_stmt)).scalar_one()
    assets_active = (await session.execute(assets_active_stmt)).scalar_one()
    tag_transfers_in_flight = (await session.execute(transfers_stmt)).scalar_one()
    registered_unread = (await session.execute(registered_unread_stmt)).scalar_one()
    unregistered_reading = (await session.execute(unregistered_reading_stmt)).scalar_one()
    bindings_on_retired = (await session.execute(bindings_on_retired_stmt)).scalar_one()
    low_stock_count = (await session.execute(low_stock_stmt)).scalar_one()
    tags_total = (await session.execute(tags_total_stmt)).scalar_one()
    sites_total = (await session.execute(sites_total_stmt)).scalar_one()
    zones_total = (await session.execute(zones_total_stmt)).scalar_one()

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
        tags_total=int(tags_total),
        sites_total=int(sites_total),
        zones_total=int(zones_total),
    )


# --- Sprint 57 Phase 57.6 — Dashboard KPI tile sparklines -----------------

_DEFAULT_SPARKLINE_DAYS = 7
_DEFAULT_SPARKLINE_BUCKET_HOURS = 6
# 7 days x 4 buckets/day = 28 points per tile — small enough to ship in
# one round-trip, granular enough to surface a real trend.

# Trend classification thresholds: compare mean of last quarter of the
# window vs mean of first quarter. Anything inside +/-5% reads as
# "flat" so noisy series don't flicker between up/down on each refresh.
_TREND_DELTA_THRESHOLD = 0.05

_SPARKLINE_READS_SQL = text(
    """
    SELECT
        date_bin(:stride, "timestamp", :origin) AS bucket_start,
        COUNT(*)::bigint                         AS v
    FROM tag_reads
    WHERE tenant_id = :tenant_id
      AND "timestamp" >= :since
      AND "timestamp" <  :until
    GROUP BY bucket_start
    ORDER BY bucket_start
    """
)

_SPARKLINE_ALERTS_SQL = text(
    """
    SELECT
        date_bin(:stride, triggered_at, :origin) AS bucket_start,
        COUNT(*)::bigint                          AS v
    FROM alerts
    WHERE tenant_id = :tenant_id
      AND triggered_at >= :since
      AND triggered_at <  :until
    GROUP BY bucket_start
    ORDER BY bucket_start
    """
)


def _classify_trend(values: list[int]) -> str:
    """Compare last-quarter mean vs first-quarter mean.

    Returns ``"up"`` / ``"down"`` / ``"flat"``. Quarter slicing keeps
    each bucket of the comparison ~7 points wide on the default 28-point
    series, which damps single-bucket spikes without losing real moves.
    """
    if not values:
        return "flat"
    q = max(1, len(values) // 4)
    head = values[:q]
    tail = values[-q:]
    head_mean = sum(head) / len(head)
    tail_mean = sum(tail) / len(tail)
    if head_mean == 0:
        return "up" if tail_mean > 0 else "flat"
    delta = (tail_mean - head_mean) / head_mean
    if delta > _TREND_DELTA_THRESHOLD:
        return "up"
    if delta < -_TREND_DELTA_THRESHOLD:
        return "down"
    return "flat"


def _bucket_starts(since: datetime, until: datetime, bucket: timedelta) -> list[datetime]:
    """Generate the canonical bucket boundary list for the window."""
    starts: list[datetime] = []
    cur = since
    while cur < until:
        starts.append(cur)
        cur = cur + bucket
    return starts


def _gap_filled_series(
    starts: list[datetime],
    row_map: dict[datetime, int],
) -> list[SparklinePoint]:
    """Fill missing buckets with zero so the client renders evenly."""
    return [SparklinePoint(t=ts, v=row_map.get(ts, 0)) for ts in starts]


def _flat_series(starts: list[datetime], value: int) -> SparklineSeries:
    """Repeat ``value`` across every bucket; trend is always ``"flat"``.

    Used for tiles whose schema is point-in-time only (no history).
    """
    series = [SparklinePoint(t=ts, v=value) for ts in starts]
    return SparklineSeries(series=series, trend="flat")


async def get_sparklines(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    days: int = _DEFAULT_SPARKLINE_DAYS,
    bucket_hours: int = _DEFAULT_SPARKLINE_BUCKET_HOURS,
) -> DashboardSparklines:
    """Compute 7-day downsampled sparkline series for each KPI tile.

    Two tiles (``reads-per-hour``, ``alerts-open``) run real
    ``date_bin``-bucketed queries against the live tables. The other
    seven reuse the current point-in-time counts from
    :func:`get_summary`, repeated across every bucket as a flat
    series. Honest about what's a true time series in our schema and
    keeps the per-Dashboard-load cost to ``get_summary`` + 2 extra
    bucket queries.

    Tile keys match the ``id`` field in ``src/pages/Dashboard.tsx``
    (``TILES``) so client lookup is a direct dict access. New UI
    tiles without a matching series render without a sparkline.
    """
    bucket = timedelta(hours=bucket_hours)
    until = datetime.now(UTC)
    since = until - timedelta(days=days)
    # Anchor bucket boundaries at ``since`` so the last bucket always
    # ends at ``until`` regardless of wall-clock when called.
    origin = since

    starts = _bucket_starts(since, until, bucket)

    # Reuse the full summary for current counts feeding the flat tiles.
    summary = await get_summary(session, tenant_id)

    reads_rows = await session.execute(
        _SPARKLINE_READS_SQL,
        {
            "stride": bucket,
            "origin": origin,
            "tenant_id": tenant_id,
            "since": since,
            "until": until,
        },
    )
    reads_map = {row.bucket_start: int(row.v) for row in reads_rows}
    reads_points = _gap_filled_series(starts, reads_map)
    reads_series = SparklineSeries(
        series=reads_points,
        trend=_classify_trend([p.v for p in reads_points]),
    )

    alerts_rows = await session.execute(
        _SPARKLINE_ALERTS_SQL,
        {
            "stride": bucket,
            "origin": origin,
            "tenant_id": tenant_id,
            "since": since,
            "until": until,
        },
    )
    alerts_map = {row.bucket_start: int(row.v) for row in alerts_rows}
    alerts_points = _gap_filled_series(starts, alerts_map)
    alerts_series = SparklineSeries(
        series=alerts_points,
        trend=_classify_trend([p.v for p in alerts_points]),
    )

    tiles: dict[str, SparklineSeries] = {
        "devices": _flat_series(starts, summary.devices_total),
        "alerts-open": alerts_series,
        "reads-per-hour": reads_series,
        "assets-active": _flat_series(starts, summary.assets_active),
        "tags": _flat_series(starts, summary.tags_total),
        "locations": _flat_series(starts, summary.sites_total + summary.zones_total),
        "transfers-in-flight": _flat_series(starts, summary.tag_transfers_in_flight),
        "recon-backlog": _flat_series(starts, summary.tag_recon_backlog),
        "low-stock": _flat_series(starts, summary.low_stock_count),
    }

    return DashboardSparklines(
        generated_at=until,
        bucket_hours=bucket_hours,
        days=days,
        tiles=tiles,
    )
