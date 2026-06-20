"""OverlappingZones attribution processor (Sprint 41 Phase D2 / ADR-021 v2).

Aggregates the last ``aggregation_window_s`` seconds of ``tag_reads`` for
a tenant, attributes each read to **all** zones it satisfies (reader-bound
OR geofence), filters by RSSI floor, weights by aging, and emits one
:attr:`tagpulse.events.protocol.Topic.SIGNALING_ATTRIBUTION_SETTLED`
event per ``(asset, zone)`` pair whose confidence clears the rule's
``confidence_threshold``. Sibling to :mod:`tagpulse.signaling.isolated_zones`;
the difference is that IsolatedZones forces a single-zone outcome per
read, while OverlappingZones lets a single asset be attributed to
multiple overlapping zones simultaneously (e.g. an asset in "Loading Bay
2" that is also in the containing "Building A" zone gets *both*
attributions in the same window).

Two layers
----------

* :func:`aggregate` — **pure function**. Takes materialised reads + zones
  + an :class:`AggregationConfig`, returns a list of
  :class:`ZoneAttribution`. No DB, no IO, no event publishing. The whole
  algorithm is unit-testable from a hand-built fixture.
* :class:`OverlappingZonesProcessor` — **runtime wrapper**. Queries
  ``tag_reads``, resolves ``tag_id`` → ``asset_id`` via
  ``asset_tag_bindings``, loads the tenant's zones, calls
  :func:`aggregate`, and publishes the events. Invoked by the
  :class:`tagpulse.signaling.periodic_dispatcher.PeriodicSignalingDispatcher`
  on each cadence tick for rules whose ``processor='overlapping_zones'``.

Algorithm (per :doc:`docs/adr/021-configurable-sensing-events.md` §"Processor implementation")
----------------------------------------------------------------------------------------------

For one rule's run cycle:

1. **Window cut.** Reads outside ``[now - aggregation_window_s, now]``
   are dropped. If ``time_error_filter`` > 0 the window is extended by
   that many seconds on the *trailing* edge to absorb clock-skew from
   readers whose system time runs slow — the extension is intentionally
   not on the leading edge so a misconfigured reader can't poison the
   *next* window's results.
2. **RSSI floor.** Reads with ``signal_strength < min_rssi_dbm`` are
   dropped. ``None`` (= no RSSI reported) is treated as "always passes"
   so non-RSSI-reporting hardware (e.g. mobile GPS-only readers) still
   contributes. Set ``min_rssi_dbm = None`` to disable the floor
   entirely (the default ``-80 dBm`` is the warehouse-pallet baseline).
3. **Per-read zone fan-out.** Each surviving read is attributed to
   **every** zone it satisfies (reader-bound: ``reader_id`` in
   ``fixed_reader_ids``; geofence: ``(lat, lon)`` inside polygon). The
   same read can produce 0, 1, or N (zone, read) pairs.
4. **Aging weight.** Each pair contributes a weight of
   ``aging_weight ** age_buckets`` where ``age_buckets`` is the integer
   number of full ``aging_bucket_s``-second buckets between the read's
   timestamp and the window end. ``aging_weight = 1.0`` (the default)
   disables aging and every pair contributes weight ``1.0`` regardless
   of timestamp.
5. **Per-(asset, zone) aggregation.** Sum the weights, count the
   contributing reads, collect the distinct contributing readers.
6. **Zone-bleed filter.** When enabled, drop ``(asset, zone)`` pairs
   whose weight share of the asset's total weight is below
   ``zone_bleed_share_threshold`` (default 0.10 = 10 %). This is the
   defence against a "ghost zone" being attributed because two or three
   far-side reads bled into a neighbouring zone's coverage.
7. **Confidence.** Each surviving pair's confidence is its share of the
   asset's total surviving weight after the bleed filter, clamped to
   ``[0.0, 1.0]``. An asset that resolved cleanly to a single zone
   produces confidence ``1.0`` for that zone; an asset that genuinely
   straddles two zones with equal evidence produces ``0.5`` for each.

The implementation never references ``(x, y)`` local coordinates — see
the :doc:`docs/adr/021-configurable-sensing-events.md` §"``signaling.attribution_settled``
payload — coordinate-system-agnostic" pin and the Sprint 41 Phase D
coordinate-system blockquote in :doc:`docs/roadmap.md`. True indoor
trilateration (Sprint 45 / ADR 024) is a separate processor with its
own output table.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.events.protocol import Event, EventBus, Topic
from tagpulse.geo import point_in_polygon
from tagpulse.models.database import ZoneModel
from tagpulse.signaling.isolated_zones import ZoneCandidate

if TYPE_CHECKING:
    from tagpulse.models.rule_schemas import RuleResponse

logger = logging.getLogger(__name__)


__all__ = [
    "AggregationConfig",
    "AttributionRead",
    "OverlappingZonesProcessor",
    "ZoneAttribution",
    "aggregate",
]


# Default zone-bleed share — a zone whose weighted share is below this
# fraction of the asset's total weight is treated as bleed and dropped.
# 10 % is the value the ADR-021 worked example uses; operators tune it
# per-rule once they see false positives in production.
_DEFAULT_ZONE_BLEED_SHARE = 0.10


# Default aging bucket size. The aging weight is applied per-bucket so
# operators reason about "half-weight every minute" rather than "half-
# weight every second" — sub-minute aging granularity in a 30-second
# window is noise. The bucket is internal; the public knob is the
# weight itself.
_DEFAULT_AGING_BUCKET_S = 60


@dataclasses.dataclass(frozen=True)
class AttributionRead:
    """One tag read fed into the aggregator.

    Minimum subset of the ``tag_reads`` row the algorithm needs. The
    processor projection in :meth:`OverlappingZonesProcessor._load_reads`
    constructs these directly from the SQL row to avoid materialising
    full :class:`tagpulse.models.database.TagReadModel` instances for
    every read in the window.
    """

    asset_id: UUID
    reader_id: UUID
    timestamp: datetime
    signal_strength: float | None = None
    latitude: float | None = None
    longitude: float | None = None


@dataclasses.dataclass(frozen=True)
class AggregationConfig:
    """Materialised ``processor_config`` block for one OverlappingZones rule.

    Constructed by :meth:`from_rule_config` from the rule's
    ``condition_config['processor_config']`` JSONB. Validation has
    already happened at write-time via
    :class:`tagpulse.models.rule_schemas.SignalingOverlappingZonesProcessorConfig`;
    this dataclass is the runtime shape with cheap-to-call defaults so
    a rule whose JSONB is missing the ``processor_config`` block (e.g.
    a rule predating the migration, or a hand-crafted test row) still
    produces deterministic output.
    """

    aggregation_window_s: int = 60
    min_rssi_dbm: float | None = -80.0
    zone_bleed_filter: bool = True
    zone_bleed_share_threshold: float = _DEFAULT_ZONE_BLEED_SHARE
    aging_weight: float = 1.0
    aging_bucket_s: int = _DEFAULT_AGING_BUCKET_S
    time_error_filter_s: int = 5

    @classmethod
    def from_rule_config(cls, condition_config: dict[str, Any]) -> AggregationConfig:
        """Build a config from the rule's ``condition_config`` JSONB.

        Reads from ``processor_config`` sub-block per ADR-021 §"Processor
        implementation". Missing keys fall back to the dataclass
        defaults so callers don't have to short-circuit on partial
        configs — every knob has a documented safe default.
        """

        proc = condition_config.get("processor_config") or {}
        # ``min_rssi_dbm`` is tri-state: missing key → default -80 dBm,
        # explicit ``null`` → floor disabled, numeric → use as floor.
        if "min_rssi_dbm" in proc:
            min_rssi_dbm = None if proc["min_rssi_dbm"] is None else float(proc["min_rssi_dbm"])
        else:
            min_rssi_dbm = -80.0
        return cls(
            aggregation_window_s=int(proc.get("aggregation_window_s", 60)),
            min_rssi_dbm=min_rssi_dbm,
            zone_bleed_filter=bool(proc.get("zone_bleed_filter", True)),
            zone_bleed_share_threshold=float(
                proc.get("zone_bleed_share_threshold", _DEFAULT_ZONE_BLEED_SHARE)
            ),
            aging_weight=float(proc.get("aging_weight", 1.0)),
            aging_bucket_s=int(proc.get("aging_bucket_s", _DEFAULT_AGING_BUCKET_S)),
            time_error_filter_s=int(proc.get("time_error_filter", 5)),
        )


@dataclasses.dataclass(frozen=True)
class ZoneAttribution:
    """One (asset, zone) attribution produced by :func:`aggregate`.

    Mirrors the on-the-wire shape pinned in
    :doc:`docs/adr/021-configurable-sensing-events.md` §"``signaling.attribution_settled``
    payload — coordinate-system-agnostic". The runtime publisher in
    :meth:`OverlappingZonesProcessor._publish_attribution` projects this
    dataclass into the JSON payload that lands on the event bus.
    """

    asset_id: UUID
    zone_id: UUID
    site_id: UUID
    confidence: float  # 0.0..1.0
    window_start: datetime
    window_end: datetime
    contributing_reads: int
    contributing_readers: tuple[UUID, ...]


def _attributable_zones_for_read(
    read: AttributionRead, zones: Sequence[ZoneCandidate]
) -> list[ZoneCandidate]:
    """Return every zone the read satisfies (reader-bound or geofence).

    The OverlappingZones variant of :func:`tagpulse.signaling.isolated_zones.attribute`:
    *every* satisfying zone is returned, not just the first. Reader-bound
    and geofence matches are both included; an asset whose tag was read
    by a fixed reader inside a building zone that itself contains a
    GPS-bearing geofence sub-zone will produce two attributions for that
    one read.
    """

    matched: list[ZoneCandidate] = []
    reader_target = str(read.reader_id)
    for zone in zones:
        if zone.kind == "reader_bound":
            if reader_target in (zone.fixed_reader_ids or ()):
                matched.append(zone)
            continue
        if zone.kind == "geofence":
            if read.latitude is None or read.longitude is None:
                continue
            # Bbox prefilter — same cheap short-circuit as the ingestion
            # geofence loop. A zone with no bbox stored skips the
            # prefilter and goes straight to ray-cast.
            if zone.bbox_min_lat is not None and not (
                zone.bbox_min_lat <= read.latitude <= (zone.bbox_max_lat or read.latitude)
                and (zone.bbox_min_lon or read.longitude)
                <= read.longitude
                <= (zone.bbox_max_lon or read.longitude)
            ):
                continue
            polygon = zone.polygon_geojson
            if not polygon:
                continue
            try:
                ring_raw = polygon["coordinates"][0]
                ring = [(float(p[0]), float(p[1])) for p in ring_raw]
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            if point_in_polygon(read.latitude, read.longitude, ring):
                matched.append(zone)
    return matched


def _age_weight(timestamp: datetime, window_end: datetime, config: AggregationConfig) -> float:
    """Compute the per-read aging weight.

    Returns ``aging_weight ** age_buckets`` where ``age_buckets`` is the
    integer count of full ``aging_bucket_s``-second buckets between the
    read and the window end. ``aging_weight = 1.0`` short-circuits to
    weight ``1.0`` so the "no aging" case skips an unnecessary exponent.
    """

    if config.aging_weight == 1.0:
        return 1.0
    age_s = max(0.0, (window_end - timestamp).total_seconds())
    buckets = int(age_s // max(1, config.aging_bucket_s))
    return config.aging_weight**buckets


def aggregate(
    *,
    reads: Iterable[AttributionRead],
    zones: Sequence[ZoneCandidate],
    site_by_zone: dict[UUID, UUID],
    config: AggregationConfig,
    window_end: datetime,
) -> list[ZoneAttribution]:
    """Aggregate reads into ``(asset, zone, confidence)`` attributions.

    Pure function. ``site_by_zone`` is a precomputed lookup of each
    zone's owning ``site_id`` so the result can carry it without an
    extra round-trip; build it from the same ``zones`` query.
    ``window_end`` is the upper bound of the aggregation window
    (typically "now" at the start of the dispatcher tick). The lower
    bound is computed from ``config.aggregation_window_s`` plus the
    ``time_error_filter_s`` skew tolerance.

    Returns one :class:`ZoneAttribution` per surviving ``(asset, zone)``
    pair. Confidence is the share of the asset's total weight after the
    bleed filter. An empty input set returns an empty list.
    """

    window_start = window_end - timedelta(
        seconds=config.aggregation_window_s + max(0, config.time_error_filter_s)
    )

    # Bucket per (asset, zone): weight sum, read count, reader set.
    @dataclasses.dataclass
    class _Bucket:
        weight: float = 0.0
        reads: int = 0
        readers: set[UUID] = dataclasses.field(default_factory=set)

    buckets: dict[tuple[UUID, UUID], _Bucket] = {}
    asset_totals: dict[UUID, float] = {}

    for read in reads:
        # Window cut (skew tolerance already baked into window_start).
        if read.timestamp < window_start or read.timestamp > window_end:
            continue
        # RSSI floor — ``None`` always passes (non-RSSI hardware).
        if (
            config.min_rssi_dbm is not None
            and read.signal_strength is not None
            and read.signal_strength < config.min_rssi_dbm
        ):
            continue
        matched_zones = _attributable_zones_for_read(read, zones)
        if not matched_zones:
            continue
        weight = _age_weight(read.timestamp, window_end, config)
        if weight <= 0.0:
            continue
        for zone in matched_zones:
            key = (read.asset_id, zone.id)
            bucket = buckets.get(key)
            if bucket is None:
                bucket = _Bucket()
                buckets[key] = bucket
            bucket.weight += weight
            bucket.reads += 1
            bucket.readers.add(read.reader_id)
            asset_totals[read.asset_id] = asset_totals.get(read.asset_id, 0.0) + weight

    if not buckets:
        return []

    # Zone-bleed filter pass: drop buckets whose share is below the
    # threshold, recompute asset totals on the survivors so the
    # confidence numbers add up to 1.0 across remaining zones.
    if config.zone_bleed_filter:
        survivors: dict[tuple[UUID, UUID], _Bucket] = {}
        for key, bucket in buckets.items():
            asset_id, _ = key
            total = asset_totals.get(asset_id, 0.0)
            if total <= 0.0:
                continue
            share = bucket.weight / total
            if share < config.zone_bleed_share_threshold:
                continue
            survivors[key] = bucket
        # Recompute totals from survivors.
        surviving_totals: dict[UUID, float] = {}
        for (asset_id, _), bucket in survivors.items():
            surviving_totals[asset_id] = surviving_totals.get(asset_id, 0.0) + bucket.weight
        buckets = survivors
        asset_totals = surviving_totals

    # Confidence = share of asset's surviving weight, clamped.
    out: list[ZoneAttribution] = []
    for (asset_id, zone_id), bucket in buckets.items():
        total = asset_totals.get(asset_id, 0.0)
        if total <= 0.0:
            continue
        confidence = min(1.0, max(0.0, bucket.weight / total))
        site_id = site_by_zone.get(zone_id)
        if site_id is None:
            # Defensive: a zone in the candidate list with no site
            # lookup entry means the caller built ``site_by_zone``
            # inconsistently. Skip rather than emit a half-formed
            # attribution.
            logger.warning(
                "OverlappingZones: zone %s missing from site_by_zone; dropping attribution",
                zone_id,
            )
            continue
        out.append(
            ZoneAttribution(
                asset_id=asset_id,
                zone_id=zone_id,
                site_id=site_id,
                confidence=confidence,
                window_start=window_end - timedelta(seconds=config.aggregation_window_s),
                window_end=window_end,
                contributing_reads=bucket.reads,
                contributing_readers=tuple(sorted(bucket.readers, key=str)),
            )
        )
    # Deterministic output order so tests can assert on it directly.
    out.sort(key=lambda a: (str(a.asset_id), str(a.zone_id)))
    return out


# ---------------------------------------------------------------------------
# Runtime processor
# ---------------------------------------------------------------------------


class OverlappingZonesProcessor:
    """DB-backed runtime wrapper around :func:`aggregate`.

    One instance per :class:`tagpulse.signaling.periodic_dispatcher.PeriodicSignalingDispatcher`;
    re-used across all OverlappingZones rules. Holds the event bus
    reference but takes the session from the dispatcher's per-tick
    context so per-rule errors can't corrupt the dispatcher's outer
    transaction.

    The processor never creates :class:`tagpulse.models.database.AlertModel`
    rows itself — its only output is the :attr:`Topic.SIGNALING_ATTRIBUTION_SETTLED`
    event stream. ``signaling.<event_type>.on_inference`` rules subscribe
    to that topic and fire alerts when ``confidence >= rule.confidence_threshold``;
    see :meth:`tagpulse.rules.evaluator.RuleEvaluator.on_attribution_settled`.
    """

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus

    async def run_once_for_rule(
        self,
        session: AsyncSession,
        rule: RuleResponse,
        *,
        now: datetime | None = None,
    ) -> int:
        """Run one aggregation cycle for the given rule.

        Returns the number of ``SIGNALING_ATTRIBUTION_SETTLED`` events
        published. Catches the per-read DB lookup errors so a malformed
        zone row or an unbindable tag id doesn't take down the
        dispatcher tick.

        ``now`` is injectable so tests can pin the window without
        monkeypatching :func:`datetime.now`.
        """

        config = AggregationConfig.from_rule_config(rule.condition_config)
        window_end = now or datetime.now(UTC)
        window_start_with_skew = window_end - timedelta(
            seconds=config.aggregation_window_s + max(0, config.time_error_filter_s)
        )

        zones, site_by_zone = await self._load_zones(session, rule.tenant_id)
        if not zones:
            logger.debug(
                "OverlappingZones rule=%s tenant=%s: no zones; skipping",
                rule.id,
                rule.tenant_id,
            )
            return 0

        reads = await self._load_reads(session, rule.tenant_id, window_start_with_skew, window_end)
        attributions = aggregate(
            reads=reads,
            zones=zones,
            site_by_zone=site_by_zone,
            config=config,
            window_end=window_end,
        )

        published = 0
        for attribution in attributions:
            await self._publish_attribution(rule, attribution)
            published += 1
        logger.info(
            "OverlappingZones rule=%s tenant=%s window=[%s, %s] reads_in=%d zones=%d emitted=%d",
            rule.id,
            rule.tenant_id,
            window_start_with_skew.isoformat(),
            window_end.isoformat(),
            len(reads),
            len(zones),
            published,
        )
        return published

    async def _load_zones(
        self, session: AsyncSession, tenant_id: UUID
    ) -> tuple[list[ZoneCandidate], dict[UUID, UUID]]:
        """Fetch all reader-bound + geofence zones for the tenant.

        Returns ``(candidates, site_by_zone)``. We pull the whole set
        rather than try to bbox-prefilter at SQL time because the
        OverlappingZones loop already iterates per-read and the zone
        count per tenant is small (single-digit hundreds in the worst
        case observed).
        """

        stmt = (
            select(ZoneModel)
            .where(
                ZoneModel.tenant_id == tenant_id,
                ZoneModel.kind.in_(("reader_bound", "geofence")),
            )
            .order_by(ZoneModel.created_at.asc())
        )
        result = await session.execute(stmt)
        zones: list[ZoneCandidate] = []
        site_by_zone: dict[UUID, UUID] = {}
        for row in result.scalars():
            zones.append(
                ZoneCandidate(
                    id=row.id,
                    kind=row.kind,
                    created_at=row.created_at,
                    fixed_reader_ids=(
                        tuple(str(r) for r in row.fixed_reader_ids)
                        if row.fixed_reader_ids
                        else None
                    ),
                    polygon_geojson=row.polygon_geojson,
                    bbox_min_lat=row.bbox_min_lat,
                    bbox_max_lat=row.bbox_max_lat,
                    bbox_min_lon=row.bbox_min_lon,
                    bbox_max_lon=row.bbox_max_lon,
                )
            )
            site_by_zone[row.id] = row.site_id
        return zones, site_by_zone

    _READS_SQL = text(
        """
        SELECT
            b.asset_id              AS asset_id,
            tr.device_id            AS reader_id,
            tr."timestamp"          AS timestamp,
            tr.signal_strength      AS signal_strength,
            tr.latitude             AS latitude,
            tr.longitude            AS longitude
        FROM tag_reads tr
        JOIN asset_tag_bindings b
          ON b.tenant_id = tr.tenant_id
         AND b.unbound_at IS NULL
         AND (
                (b.binding_kind = 'epc'
                 AND (tr.epc = b.binding_value OR tr.epc_hex = b.binding_value)) OR
                (b.binding_kind = 'tid'    AND tr.tid    = b.binding_value) OR
                (b.binding_kind = 'device' AND tr.tag_id = b.binding_value)
             )
        WHERE tr.tenant_id = :tenant_id
          AND tr."timestamp" >= :window_start
          AND tr."timestamp" <= :window_end
        """
    )

    async def _load_reads(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        window_start: datetime,
        window_end: datetime,
    ) -> list[AttributionRead]:
        """Project ``tag_reads`` ⋈ ``asset_tag_bindings`` into the window.

        The SQL mirrors the existing
        :data:`tagpulse.repositories.timescaledb.asset_location._PATH_SQL`
        join pattern so the OverlappingZones processor reuses the
        established three-way ``binding_kind`` match (epc / tid /
        device). Reads whose tag is unbound at the window time are
        silently excluded — they have no asset to attribute to.
        """

        result = await session.execute(
            self._READS_SQL,
            {
                "tenant_id": tenant_id,
                "window_start": window_start,
                "window_end": window_end,
            },
        )
        return [
            AttributionRead(
                asset_id=row.asset_id,
                reader_id=row.reader_id,
                timestamp=row.timestamp,
                signal_strength=row.signal_strength,
                latitude=row.latitude,
                longitude=row.longitude,
            )
            for row in result.all()
        ]

    async def _publish_attribution(self, rule: RuleResponse, attribution: ZoneAttribution) -> None:
        """Emit one ``SIGNALING_ATTRIBUTION_SETTLED`` event.

        Payload shape matches the
        :doc:`docs/adr/021-configurable-sensing-events.md` §"``signaling.attribution_settled``
        payload — coordinate-system-agnostic" pin: zone identity +
        confidence + window bounds + provenance, no coordinates.
        """

        await self._event_bus.publish(
            Topic.SIGNALING_ATTRIBUTION_SETTLED,
            Event(
                id=uuid4(),
                topic=Topic.SIGNALING_ATTRIBUTION_SETTLED,
                timestamp=attribution.window_end,
                payload={
                    "tenant_id": str(rule.tenant_id),
                    "asset_id": str(attribution.asset_id),
                    "zone_id": str(attribution.zone_id),
                    "site_id": str(attribution.site_id),
                    "confidence": attribution.confidence,
                    "window_start": attribution.window_start.isoformat(),
                    "window_end": attribution.window_end.isoformat(),
                    "contributing_reads": attribution.contributing_reads,
                    "contributing_readers": [str(r) for r in attribution.contributing_readers],
                    "rule_id": str(rule.id),
                },
            ),
        )
